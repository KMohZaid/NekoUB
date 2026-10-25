"""Automated messages (``.am``).

Define per-chat automations that either react to incoming messages or fire
on a schedule.

Grammar
-------
    .am [flags] <match> <trigger> <output>

``<trigger>`` is one of:

    on      keyword trigger   - someone says <match>          -> send <output>
    reply   reply trigger     - someone replies to you
                               (optionally matching <match>)  -> send <output>
    tag     mention trigger   - someone tags you or replies to you
                               (optionally matching <match>)  -> send <output>
    every   interval trigger  - repeat every <time>            -> send <output>
    at      daily trigger     - daily at <HH:MM>               -> send <output>
    in      one-shot trigger  - once, after <time>             -> send <output>

The first token that equals a trigger keyword acts as the separator:
everything before it is ``<match>`` and everything after it is ``<output>``.
The token right after a time trigger (``every`` / ``at`` / ``in``) is the
time value.  ``mention`` is an alias of ``tag``.

Flags (standalone ``-tokens``, anywhere in the line):

    -tag      mention (tag) the sender in the reply   (default: off)
    -notag    never tag the sender                     (default)
    -reply    send the output as a reply to the trigger message
    -exact    match <match> as a whole word (on/reply/tag)

Management
----------
    .am                 show this help
    .am list            list this chat's automations
    .am del <id>        delete automation <id>
    .am on <id>         enable automation <id>
    .am off <id>        disable automation <id>
    .am clear           delete every automation in this chat

Outputs are stored per chat in ``.automessages.json`` and reloaded on
startup, so schedules survive restarts.  Time triggers use the server's
local timezone.
"""

import asyncio
import html
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

import config
import main
from utils import continue_propagation, get_logger

logger = get_logger(__name__)

STORE_PATH = Path(__file__).resolve().parent.parent / ".automessages.json"
TICK_SECONDS = 10
MISSED_WINDOW = 3600  # don't fire a daily trigger more than 1h late
MAX_OUTPUT = 4000

# Trigger keyword -> internal kind.  ``mention`` aliases ``tag``.
TRIGGERS = {
    "on": "keyword",
    "reply": "reply",
    "tag": "tag",
    "mention": "tag",
    "every": "every",
    "at": "at",
    "in": "in",
}
TIME_TRIGGERS = {"every", "at", "in"}

FLAGS = {
    "-tag": ("tag", True),
    "-notag": ("tag", False),
    "-reply": ("reply", True),
    "-exact": ("exact", True),
}

UNIT_WORDS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
}

MANAGEMENT = {"list", "ls", "del", "rm", "delete", "remove", "clear",
              "clearall", "help", "?", "enable", "disable"}

# Set on ``main`` so a hot reload does not spawn a second scheduler.
_TASK_ATTR = "_automessages_scheduler_task"


# ============================================================
# Persistence (file based so a reload never desyncs state)
# ============================================================


def load_store() -> dict:
    if STORE_PATH.exists():
        try:
            data = json.loads(STORE_PATH.read_text())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_store(store: dict) -> None:
    try:
        tmp = STORE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(store, indent=2))
        tmp.replace(STORE_PATH)
    except OSError as exc:
        logger.error(f"[AM] Could not save store: {exc}")


def chat_items(store: dict, chat_id: int) -> list:
    return store.setdefault(str(chat_id), [])


def next_id(items: list) -> int:
    return max((item.get("id", 0) for item in items), default=0) + 1


# ============================================================
# Tokenizer / parsers
# ============================================================


def tokenize(text: str) -> list[tuple[str, bool]]:
    """Split ``text`` into (value, was_quoted) tokens.

    Quoting lets a match/output contain trigger keywords as literal words,
    e.g. .am "look at me" on hello
    """
    tokens: list[tuple[str, bool]] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        if text[i] in ("'", '"'):
            quote = text[i]
            i += 1
            start = i
            while i < n and text[i] != quote:
                i += 1
            tokens.append((text[start:i], True))
            i += 1  # skip closing quote
        else:
            start = i
            while i < n and not text[i].isspace():
                i += 1
            tokens.append((text[start:i], False))
    return tokens


def parse_duration_token(token: str) -> float | None:
    """Parse ``1h``, ``30m``, ``1h30m`` ... into seconds."""
    token = token.strip()
    single = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd])", token, re.IGNORECASE)
    if single:
        return float(single.group(1)) * UNIT_WORDS[single.group(2).lower()]

    total, pos = 0.0, 0
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*([smhd])", token, re.IGNORECASE):
        if match.start() != pos:
            return None
        total += float(match.group(1)) * UNIT_WORDS[match.group(2).lower()]
        pos = match.end()
    if pos and pos == len(token):
        return total
    return None


def consume_duration(tokens: list[str]) -> tuple[float | None, list[str]]:
    """Consume leading duration tokens (``1h`` / ``30 minutes`` / ``1h 30m``)."""
    total, i = 0.0, 0
    while i < len(tokens):
        token = tokens[i]
        parsed = parse_duration_token(token)
        if parsed is not None:
            total += parsed
            i += 1
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", token) and i + 1 < len(tokens):
            unit = tokens[i + 1].lower()
            if unit in UNIT_WORDS:
                total += float(token) * UNIT_WORDS[unit]
                i += 2
                continue
        break
    if total <= 0:
        return None, tokens
    return total, tokens[i:]


def parse_clock(tokens: list[str]) -> tuple[tuple[int, int] | None, list[str]]:
    """Parse ``09:00`` / ``9:00`` / ``9:00 am`` / ``9pm`` into (hour, minute)."""
    if not tokens:
        return None, tokens
    match = re.fullmatch(
        r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", tokens[0], re.IGNORECASE
    )
    if not match:
        return None, tokens
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    ampm = match.group(3)
    rest = tokens[1:]
    if ampm is None and rest and rest[0].lower() in ("am", "pm"):
        ampm = rest[0].lower()
        rest = rest[1:]
    if ampm:
        if not 1 <= hour <= 12:
            return None, tokens
        if ampm.lower() == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, tokens
    return (hour, minute), rest


def next_clock_run(hour: int, minute: int, now_ts: float) -> float:
    now = datetime.fromtimestamp(now_ts)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target.timestamp() <= now_ts:
        target += timedelta(days=1)
    return target.timestamp()


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs and not (days or hours):
        parts.append(f"{secs}s")
    return " ".join(parts) or "0s"


def human_delta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 86400:
        return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


# ============================================================
# Automation parsing
# ============================================================


def parse_automation(raw: str) -> tuple[dict | None, str | None]:
    """Turn the argument string into an automation dict.

    Returns (item, None) on success or (None, error_message) on failure.
    """
    tokens = tokenize(raw)
    if not tokens:
        return None, "No automation provided."

    flags = {"tag": False, "reply": False, "exact": False}
    core: list[tuple[str, bool]] = []
    for value, quoted in tokens:
        if not quoted and value.lower() in FLAGS:
            key, val = FLAGS[value.lower()]
            flags[key] = val
            continue
        core.append((value, quoted))

    if not core:
        return None, "No automation provided."

    index = None
    for i, (value, quoted) in enumerate(core):
        if not quoted and value.lower() in TRIGGERS:
            index = i
            break
    if index is None:
        return None, _help_missing_trigger()

    trigger_token = core[index][0].lower()
    kind = TRIGGERS[trigger_token]
    match = " ".join(v for v, _ in core[:index]).strip()
    tail = [v for v, _ in core[index + 1:]]

    item: dict = {
        "kind": kind,
        "match": match,
        "flags": flags,
        "enabled": True,
        "created": time.time(),
    }

    if trigger_token in TIME_TRIGGERS:
        if trigger_token == "at":
            clock, rest = parse_clock(tail)
            if clock is None:
                return None, (
                    "Time must be `HH:MM` (24h) or `9:00 am`, e.g. "
                    f"`{config.CMD_PREFIX}am at 09:00 Good morning`."
                )
            output = " ".join(rest).strip()
            item["at"] = f"{clock[0]:02d}:{clock[1]:02d}"
            item["next_run"] = next_clock_run(clock[0], clock[1], time.time())
        else:
            seconds, rest = consume_duration(tail)
            if seconds is None:
                return None, (
                    "Time is missing or invalid. Use a unit like `30s`, `15m`, "
                    "`1h`, `1d` (or `30 minutes`), e.g. "
                    f"`{config.CMD_PREFIX}am every 1h Stand up!`."
                )
            output = " ".join(rest).strip()
            item["interval" if trigger_token == "every" else "delay"] = seconds
            item["next_run"] = time.time() + seconds
    else:
        output = " ".join(tail).strip()

    if not output:
        return None, "The automation has no output message."

    if kind == "keyword" and not match:
        return None, (
            "A keyword trigger needs a match, e.g. "
            f"`{config.CMD_PREFIX}am hello on Hi there!`."
        )

    item["output"] = output[:MAX_OUTPUT]
    return item, None


def _help_missing_trigger() -> str:
    prefix = config.CMD_PREFIX
    return (
        "Could not find a trigger keyword (`on`, `reply`, `tag`, `every`, "
        f"`at`, `in`).\n\nType `{prefix}am` for help."
    )


# ============================================================
# Matching helpers
# ============================================================


def match_text(text: str, needle: str, exact: bool) -> bool:
    if not needle:
        return True
    if exact:
        pattern = r"(?<!\w)" + re.escape(needle) + r"(?!\w)"
        return re.search(pattern, text, re.IGNORECASE) is not None
    return needle.lower() in text.lower()


def is_reply_to_me(message: Message, me_id: int | None) -> bool:
    reply = message.reply_to_message
    if not reply:
        return False
    if getattr(reply, "outgoing", False):
        return True
    sender = reply.from_user
    return bool(sender and me_id is not None and sender.id == me_id)


def tagged_me(message: Message) -> bool:
    return bool(getattr(message, "mentioned", False))


# ============================================================
# Sending
# ============================================================


async def send_output(
    client,
    source: Message | None,
    chat_id: int,
    output: str,
    flags: dict,
    sender=None,
) -> None:
    parse_mode = None
    body = output
    if flags.get("tag") and sender is not None and sender.id:
        name = html.escape(sender.first_name or sender.username or "there")
        body = f'<a href="tg://user?id={sender.id}">{name}</a> ' + html.escape(
            output
        )
        parse_mode = ParseMode.HTML

    try:
        if flags.get("reply") and source is not None:
            await source.reply(body, parse_mode=parse_mode)
        else:
            await client.send_message(chat_id, body, parse_mode=parse_mode)
    except Exception as exc:
        logger.debug(f"[AM] Send failed ({exc}); retrying as plain text")
        plain = (
            f"{(sender.first_name or '')} {output}".strip()
            if flags.get("tag") and sender is not None
            else output
        )
        if flags.get("reply") and source is not None:
            await source.reply(plain, parse_mode=ParseMode.DISABLED)
        else:
            await client.send_message(
                chat_id, plain, parse_mode=ParseMode.DISABLED
            )


# ============================================================
# Trigger listeners
# ============================================================


@main.app.on_message(~filters.me & ~filters.service, group=1)
@continue_propagation
async def automation_listener(client, message: Message):
    try:
        if message.outgoing:
            return
        sender = message.from_user
        if sender is not None and (sender.is_self or sender.is_bot):
            return

        store = load_store()
        items = store.get(str(message.chat.id))
        if not items:
            return

        text = message.text or message.caption or ""
        me_id = client.me.id if client.me else None

        replied_to_me = is_reply_to_me(message, me_id)
        mentioned = tagged_me(message)

        for item in items:
            if not item.get("enabled", True):
                continue
            kind = item.get("kind")
            if kind not in ("keyword", "reply", "tag"):
                continue
            flags = item.get("flags") or {}
            exact = bool(flags.get("exact"))
            needle = item.get("match", "")

            if kind == "keyword":
                hit = match_text(text, needle, exact)
            elif kind == "reply":
                hit = replied_to_me and match_text(text, needle, exact)
            else:  # tag
                hit = (replied_to_me or mentioned) and match_text(
                    text, needle, exact
                )

            if hit:
                await send_output(
                    client,
                    message,
                    message.chat.id,
                    item["output"],
                    flags,
                    sender,
                )
    except Exception as exc:
        logger.error(f"[AM] Listener error: {exc}", exc_info=True)


# ============================================================
# Scheduler (time triggers)
# ============================================================


def _reschedule(item: dict, now_ts: float) -> float:
    if item["kind"] == "every":
        return now_ts + max(1.0, float(item.get("interval", 60)))
    hour, minute = (int(p) for p in item["at"].split(":"))
    return next_clock_run(hour, minute, now_ts)


async def _scheduler_loop() -> None:
    await asyncio.sleep(3)  # let the client finish connecting
    while True:
        try:
            await _tick()
        except Exception as exc:
            logger.error(f"[AM] Scheduler error: {exc}", exc_info=True)
        await asyncio.sleep(TICK_SECONDS)


async def _tick() -> None:
    store = load_store()
    if not store:
        return
    now = time.time()
    changed = False
    client = main.app

    for chat_key, items in store.items():
        for item in list(items):
            if not item.get("enabled", True):
                continue
            if item.get("kind") not in TIME_TRIGGERS:
                continue
            next_run = item.get("next_run")
            if next_run is None:
                item["next_run"] = _reschedule(item, now)
                changed = True
                continue
            if now < next_run:
                continue

            if now - next_run <= MISSED_WINDOW:
                try:
                    await send_output(
                        client, None, int(chat_key), item["output"], {}
                    )
                    item["last_fired"] = now
                except Exception as exc:
                    logger.error(f"[AM] Could not fire automation: {exc}")

            if item["kind"] == "in":
                items.remove(item)
            else:
                item["next_run"] = _reschedule(item, now)
                item["last_fired"] = now
            changed = True

    if changed:
        save_store(store)


def ensure_scheduler() -> None:
    existing = getattr(main, _TASK_ATTR, None)
    if existing is not None and not existing.done():
        return
    loop = getattr(getattr(main, "app", None), "loop", None)
    if loop is None:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
    try:
        setattr(main, _TASK_ATTR, loop.create_task(_scheduler_loop()))
        logger.info("[AM] Scheduler started")
    except Exception as exc:
        logger.error(f"[AM] Could not start scheduler: {exc}")


# ============================================================
# Rendering
# ============================================================


def help_text() -> str:
    prefix = config.CMD_PREFIX
    return (
        "**⏰ Automated messages**\n\n"
        "**Add** (match first, output last; flags anywhere):\n"
        f"• `{prefix}am hello on Hi there!`\n"
        "  someone says **hello** → send *Hi there!*\n"
        f"• `{prefix}am reply On it!` / `{prefix}am boss reply On it!`\n"
        "  someone replies to you (optionally with **boss**) → send *On it!*\n"
        f"• `{prefix}am /ft tag /plant hdsfshfg -notag`\n"
        "  someone tags you or replies with **/ft** → send *…* without "
        "tagging them\n"
        f"• `{prefix}am every 1h Stand up!` — repeat every hour\n"
        f"• `{prefix}am at 09:00 Good morning` — daily at 09:00\n"
        f"• `{prefix}am in 10m On my way` — once, after 10 minutes\n\n"
        "**Time input:**\n"
        "• `every`/`in`: `30s`, `15m`, `1h`, `2h`, `1d` …\n"
        "  (also `1 hour`, `30 minutes`, `1h 30m`)\n"
        "• `at`: `HH:MM` (24h) or `9:00 am` / `9pm` (server local time)\n\n"
        "**Flags:**\n"
        "• `-tag` / `-notag` — tag (mention) the sender, or not (default)\n"
        "• `-reply` — send the output as a reply to the trigger message\n"
        "• `-exact` — match the keyword as a whole word\n\n"
        "**Management:**\n"
        f"• `{prefix}am list` — list this chat's automations\n"
        f"• `{prefix}am del <id>` — delete one\n"
        f"• `{prefix}am on <id>` / `{prefix}am off <id>` — enable / disable\n"
        f"• `{prefix}am clear` — delete all in this chat\n\n"
        "Automations are **per chat** and survive restarts."
    )


def _preview(text: str, limit: int = 40) -> str:
    text = text.replace("\n", " ⏎ ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _describe(item: dict) -> str:
    kind = item.get("kind")
    if kind == "keyword":
        head = f"on `{item.get('match', '')}`"
    elif kind == "reply":
        head = "reply" + (
            f" `{item['match']}`" if item.get("match") else ""
        )
    elif kind == "tag":
        head = "tag" + (f" `{item['match']}`" if item.get("match") else "")
    elif kind == "every":
        head = f"every {human_duration(item.get('interval', 0))}"
    elif kind == "at":
        head = f"at {item.get('at', '')}"
    elif kind == "in":
        head = f"in {human_duration(item.get('delay', 0))}"
    else:
        head = str(kind)

    flags = item.get("flags") or {}
    extra = [name for name in ("tag", "reply", "exact") if flags.get(name)]
    if extra:
        head += "  " + " ".join(f"-{name}" for name in extra)

    line = f"[{head}] → {_preview(item.get('output', ''))}"
    if not item.get("enabled", True):
        line += "  (off)"
    return line


def render_list(chat_id: int) -> str:
    prefix = config.CMD_PREFIX
    items = load_store().get(str(chat_id), [])
    if not items:
        return (
            f"No automations in this chat yet.\n\nType `{prefix}am` for help."
        )
    lines = [f"**⏰ Automations** (`{len(items)}`)"]
    now = time.time()
    for item in sorted(items, key=lambda it: it.get("id", 0)):
        marker = "" if item.get("enabled", True) else "⏸ "
        lines.append(f"{marker}`{item['id']}.` {_describe(item)}")
        if (
            item.get("enabled", True)
            and item.get("kind") in TIME_TRIGGERS
            and item.get("next_run")
        ):
            lines.append(
                f"    ↳ next in {human_delta(item['next_run'] - now)}"
            )
    lines.append("")
    lines.append(
        f"`{prefix}am del <id>` · `{prefix}am on|off <id>` · "
        f"`{prefix}am clear`"
    )
    return "\n".join(lines)


# ============================================================
# Command
# ============================================================


def _management(raw: str) -> tuple[str, str] | None:
    """Return (action, argument) when ``raw`` is a management command."""
    tokens = raw.split()
    if not tokens:
        return None
    first = tokens[0].lower()

    if first in ("list", "ls") and len(tokens) == 1:
        return "list", ""
    if first in ("help", "?") and len(tokens) == 1:
        return "help", ""
    if first in ("clear", "clearall") and len(tokens) == 1:
        return "clear", ""
    if first in ("del", "rm", "delete", "remove") and len(tokens) == 2:
        if tokens[1].isdigit():
            return "del", tokens[1]
    if first in ("on", "enable") and len(tokens) == 2 and tokens[1].isdigit():
        return "on", tokens[1]
    if first in ("off", "disable") and len(tokens) == 2 and tokens[1].isdigit():
        return "off", tokens[1]
    return None


@main.app.on_message(
    filters.me
    & filters.command(
        ["am", "auto", "automessage", "automessages"],
        prefixes=config.CMD_PREFIX,
    ),
    group=1,
)
@continue_propagation
async def automessages_command(client, message: Message):
    raw = (message.text or "").split(maxsplit=1)
    args = raw[1].strip() if len(raw) > 1 else ""
    chat_id = message.chat.id
    prefix = config.CMD_PREFIX

    try:
        if not args:
            await message.edit(help_text())
            return

        action = _management(args)
        if action is not None:
            await _handle_management(message, chat_id, prefix, *action)
            return

        item, error = parse_automation(args)
        if error:
            await message.edit(f"❌ {error}")
            return

        store = load_store()
        items = chat_items(store, chat_id)
        item["id"] = next_id(items)
        items.append(item)
        save_store(store)

        await message.edit(
            f"✅ Added automation `{item['id']}`: {_describe(item)}"
        )
        logger.info(f"[AM] Added #{item['id']} in {chat_id}: {_describe(item)}")
    except Exception as exc:
        logger.error(f"[AM] Command error: {exc}", exc_info=True)
        await message.edit(f"❌ Failed: {type(exc).__name__}")


async def _handle_management(
    message: Message, chat_id: int, prefix: str, action: str, arg: str
) -> None:
    if action == "help":
        await message.edit(help_text())
        return

    store = load_store()
    items = chat_items(store, chat_id)

    if action == "list":
        await message.edit(render_list(chat_id))
        return

    if action == "clear":
        count = len(items)
        store[str(chat_id)] = []
        save_store(store)
        await message.edit(f"🧹 Removed `{count}` automation(s) from this chat.")
        return

    target = int(arg)
    item = next((it for it in items if it.get("id") == target), None)
    if item is None:
        await message.edit(f"❓ No automation `{target}` in this chat.")
        return

    if action == "del":
        items.remove(item)
        save_store(store)
        await message.edit(f"🗑 Deleted automation `{target}`.")
    elif action == "on":
        item["enabled"] = True
        save_store(store)
        await message.edit(f"▶️ Enabled automation `{target}`.")
    elif action == "off":
        item["enabled"] = False
        save_store(store)
        await message.edit(f"⏸ Disabled automation `{target}`.")


# Start the scheduler when the plugin is (re)loaded.
ensure_scheduler()
