import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
from typing import Optional

import httpx
from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message
from todoist_api_python.api import TodoistAPI

import config
import main
from userbot.utils import continue_propagation, get_logger

logger = get_logger(__name__)

SECTION = None
LABEL = None
PROJECT = None
API = None
INIT_ERROR = None

TODOIST_SECTION_NAME = "📩 From Telegram [UserBot]"
TODOIST_LABEL_NAME = "📩 From Telegram [UserBot]"
TODOIST_LABEL_COLOR = "blue"

PRIORITY_PATTERN = re.compile(r"(?i)(?:^|\s)#p([1-4])\b")
DUE_PATTERN = re.compile(r"(?i)(?:^|\s)#due\s*\|([^|]+)\|")
DESCRIPTION_PATTERN = re.compile(r"(?is)(?:^|\s)#desc\s*\|(.*?)\|")
TAGS_PATTERN = re.compile(r"(?is)(?:^|\s)#tags?\s*\|([^|]*)\|")
TAG_PATTERN = re.compile(r"(?i)(?:^|\s)#tag\s+([^\s#|]+)")
PRIORITY_MAP = {1: 4, 2: 3, 3: 2, 4: 1}
MAX_TASK_CONTENT_LEN = 500
MAX_TASK_DESCRIPTION_LEN = 16383
MAX_LABEL_LEN = 60
MAX_TASK_LABELS = 20

LIST_PAGE_SIZE = 10
LIST_CONTENT_PREVIEW = 200
LIST_DESCRIPTION_PREVIEW = 120
MAX_LIST_TEXT = 3500

USAGE_TEXT = (
    "Usage:\n"
    "`.todo <title>`\n"
    "`.todo <title> #desc |text|`\n"
    "`.todo <title> #tags |a, b|`\n"
    "`.todo <title> [#pN] [#due |when|]`\n"
    "`.todo <title> #tags |a, b| #desc |text| #pN #due |when|`\n"
    "`.todo list [page]`\n\n"
    "Examples:\n"
    "`.todo Buy milk`\n"
    "`.todo Fix login #desc |500 on submit| #tags |backend| #p1`\n"
    "`.todo Meeting #due |tomorrow 5pm|`\n"
    "`.todo list`\n\n"
    "Help:\n"
    "Reply: attach the quoted message to the description\n"
    "`#desc |...|`: your own description\n"
    "`#tags |a, b|` or `#tag <name>`: labels, created if missing\n"
    "`#pN`: priority, #p1->very urgent, urgent, normal, low\n"
    "`#due |...|`: specify due date ...."
)


@dataclass
class ParsedTodo:
    title: str
    description: Optional[str] = None
    tags: list = field(default_factory=list)
    priority: Optional[int] = None
    due_string: Optional[str] = None


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _iter_items(payload):
    for item in payload:
        if isinstance(item, (list, tuple, set)):
            for nested in item:
                yield nested
            continue
        yield item


def _get_chat_link_id(chat_id: int) -> str:
    chat_id_text = str(chat_id)
    if chat_id_text.startswith("-100"):
        return chat_id_text[4:]
    return chat_id_text.lstrip("-")


def _build_message_link(chat_id: int, message_id: int) -> str:
    return f"https://t.me/c/{_get_chat_link_id(chat_id)}/{message_id}"


def _extract_message_text(message: Message) -> str:
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    return ""


def find_project():
    global PROJECT
    projects = API.get_projects()
    for project in _iter_items(projects):
        if getattr(project, "is_inbox_project", False):
            PROJECT = project
            return
    raise RuntimeError("Todoist inbox project was not found")


def create_section(name: str = TODOIST_SECTION_NAME):
    global SECTION
    sections = API.get_sections()
    for section in _iter_items(sections):
        if getattr(section, "name", "") == name:
            SECTION = section
            return

    SECTION = API.add_section(name=name, project_id=PROJECT.id)
    logger.info(f"[TODOIST] Created section: {SECTION}")


def create_label(
    name: str = TODOIST_LABEL_NAME, color: str = TODOIST_LABEL_COLOR
):
    global LABEL
    labels = API.get_labels()
    for label in _iter_items(labels):
        if getattr(label, "name", "") == name:
            LABEL = label
            return

    LABEL = API.add_label(
        name=name, color=color, item_order=0, is_favorite=True
    )
    logger.info(f"[TODOIST] Created label: {LABEL}")


def resolve_labels(names: list) -> list:
    """Map user tags to existing label names, creating missing ones."""
    if not names:
        return []

    existing = {}
    try:
        for label in _iter_items(API.get_labels(limit=200)):
            name = getattr(label, "name", None)
            if name:
                existing[name.casefold()] = name
    except Exception as exc:
        logger.warning(f"[TODOIST] Could not fetch labels: {exc}")

    resolved = []
    for name in names:
        key = name.casefold()
        if key in existing:
            resolved.append(existing[key])
            continue
        try:
            label = API.add_label(name=name)
            existing[key] = label.name
            resolved.append(label.name)
            logger.info(f"[TODOIST] Created label: {label.name}")
        except Exception as exc:
            # Todoist also accepts plain names, so keep the raw tag on failure.
            logger.warning(f"[TODOIST] Could not create label '{name}': {exc}")
            resolved.append(name)
    return resolved


def initialize_todoist():
    global API, INIT_ERROR, PROJECT, SECTION, LABEL
    INIT_ERROR = None

    if not config.TODOIST_API_TOKEN:
        INIT_ERROR = "TODOIST_API_TOKEN is missing"
        logger.warning("[TODOIST] TODOIST_API_TOKEN is not configured")
        return

    try:
        API = TodoistAPI(config.TODOIST_API_TOKEN)
        PROJECT = None
        SECTION = None
        LABEL = None
        find_project()
        create_section()
        create_label()
        logger.info("[TODOIST] Initialization completed")
    except Exception as exc:
        INIT_ERROR = str(exc)
        logger.error(f"[TODOIST] Initialization failed: {exc}", exc_info=True)


def ensure_initialized():
    if API and PROJECT and SECTION and LABEL and not INIT_ERROR:
        return
    initialize_todoist()
    if INIT_ERROR:
        raise RuntimeError(INIT_ERROR)


def _clean_label(label: str) -> Optional[str]:
    label = label.strip().lstrip("#").strip()
    if not label:
        return None
    if label.casefold() == TODOIST_LABEL_NAME.casefold():
        return None
    return label[:MAX_LABEL_LEN].strip()


def _dedupe_labels(labels: list) -> list:
    seen = set()
    deduped = []
    for label in labels:
        cleaned = _clean_label(label)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
        if len(deduped) >= MAX_TASK_LABELS:
            break
    return deduped


def parse_todo_input(raw_content: str) -> ParsedTodo:
    remaining = raw_content

    description_blocks = [
        block.strip()
        for block in DESCRIPTION_PATTERN.findall(remaining)
        if block.strip()
    ]
    remaining = DESCRIPTION_PATTERN.sub(" ", remaining)
    description = "\n\n".join(description_blocks) or None

    tag_values = []
    for block in TAGS_PATTERN.findall(remaining):
        tag_values.extend(re.split(r"[,\n]", block))
    remaining = TAGS_PATTERN.sub(" ", remaining)
    tag_values.extend(TAG_PATTERN.findall(remaining))
    remaining = TAG_PATTERN.sub(" ", remaining)
    tags = _dedupe_labels(tag_values)

    priority = None
    priority_matches = PRIORITY_PATTERN.findall(remaining)
    if priority_matches:
        parsed_priority = int(priority_matches[-1])
        priority = PRIORITY_MAP.get(parsed_priority)
    remaining = PRIORITY_PATTERN.sub(" ", remaining)

    due_string = None
    due_matches = DUE_PATTERN.findall(remaining)
    if due_matches:
        due_string = due_matches[-1].strip()
    remaining = DUE_PATTERN.sub(" ", remaining)

    title = " ".join(remaining.split()).strip()
    return ParsedTodo(
        title=title,
        description=description,
        tags=tags,
        priority=priority,
        due_string=due_string,
    )


def _build_reply_sender_line(reply: Message) -> str:
    sender = getattr(reply, "from_user", None)
    if sender:
        full_name = sender.first_name or ""
        if sender.last_name:
            full_name = f"{full_name} {sender.last_name}".strip()
        line = f"Sender: {full_name} #{sender.id}"
        if sender.username:
            line += f" @{sender.username}"
        return line

    sender_chat = getattr(reply, "sender_chat", None)
    if sender_chat:
        return f"Sender: {sender_chat.title or 'Unknown'} #{sender_chat.id}"

    return "Sender: Unknown"


def _truncate(text: str, limit: int, suffix: str = "\n... (truncated)") -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    keep_len = limit - len(suffix)
    if keep_len <= 0:
        return text[:limit]
    return text[:keep_len].rstrip() + suffix


def build_description(
    message: Message, user_description: Optional[str] = None
) -> Optional[str]:
    user_description = (user_description or "").strip()
    reply = message.reply_to_message
    chat_id = message.chat.id
    chat_title = message.chat.title or message.chat.first_name or "Chat"
    message_link = _build_message_link(chat_id, message.id)
    timestamp_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    description_prefix = (
        f"Chat [Task originates from] : {chat_title} #{chat_id}\n"
        f"Message [Task cmd message] : [Message #{message.id}]({message_link})\n"
    )
    replied_content = ""
    if reply:
        replied_message_link = _build_message_link(chat_id, reply.id)
        sender_line = _build_reply_sender_line(reply)
        replied_text = _extract_message_text(reply).strip()
        replied_content = replied_text if replied_text else "(non-text message)"
        description_prefix += (
            "\n=== Replied Message (If any) ===\n"
            f"[Message #{reply.id}]({replied_message_link})\n\n"
            f"{sender_line}\n\n"
            "=========================================\n\n"
        )

    description_suffix = (
        "\n\n"
        "=========================================\n\n\n"
        f"Task added on : {timestamp_utc}"
    )

    max_replied_len = max(
        0,
        MAX_TASK_DESCRIPTION_LEN
        - len(description_prefix)
        - len(description_suffix)
        - len(user_description)
        - (2 if user_description else 0),
    )
    replied_content = _truncate(replied_content, max_replied_len)
    user_block = f"{user_description}\n\n" if user_description else ""
    description = user_block + description_prefix + replied_content
    return _truncate(
        description + description_suffix, MAX_TASK_DESCRIPTION_LEN
    )


def _format_todoist_error(error: Exception) -> str:
    if not isinstance(error, httpx.HTTPStatusError):
        return f"{type(error).__name__}: {error}"

    status = "unknown"
    body = ""
    try:
        status = str(error.response.status_code)
        body = error.response.text or ""
    except Exception:
        pass

    if len(body) > 1200:
        body = body[:1200].rstrip() + "... (truncated)"
    if body:
        return f"HTTP {status}: {body}"
    return f"HTTP {status}: {error}"


async def log_error_to_saved_messages(client, context: str, error: Exception):
    try:
        await client.send_message(
            "me",
            (
                "[TODOIST ERROR]\n"
                f"Context: {context}\n"
                f"Error: {type(error).__name__}: {error}"
            ),
        )
    except Exception as log_error:
        logger.error(
            f"[TODOIST] Failed to log error to Saved Messages: {log_error}"
        )


async def send_result_feedback(client, message: Message, success: bool):
    reaction = "👍" if success else "👎"
    fallback_text = "Done" if success else "Failed"

    try:
        if hasattr(message, "react"):
            await message.react(reaction)
            return

        if hasattr(client, "send_reaction"):
            await client.send_reaction(message.chat.id, message.id, reaction)
            return

        raise RuntimeError("Reaction API is unavailable")
    except Exception as reaction_error:
        logger.warning(f"[TODOIST] Reaction failed: {reaction_error}")
        try:
            await message.reply_text(fallback_text)
        except Exception as reply_error:
            logger.error(f"[TODOIST] Feedback reply failed: {reply_error}")


def _format_due_line(task) -> Optional[str]:
    due = getattr(task, "due", None)
    due_date = getattr(due, "date", None) if due else None
    if not due_date:
        return None

    due_timezone = getattr(due, "timezone", None) or "UTC"
    try:
        zone = ZoneInfo(due_timezone)
    except (KeyError, ValueError):
        zone = timezone.utc
        due_timezone = "UTC"

    if isinstance(due_date, datetime) or "T" in str(due_date):
        due_at = (
            due_date
            if isinstance(due_date, datetime)
            else datetime.fromisoformat(str(due_date))
        )
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=zone)
        else:
            due_at = due_at.astimezone(zone)
        due_unix = int(due_at.timestamp())
        display_date = due_at.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")
        time_text = (
            f'<tg-time unix="{due_unix}" format="dt">'
            f'{_esc(display_date)}</tg-time>'
        )
        offset = due_at.utcoffset() or timezone.utc.utcoffset(due_at)
        offset_minutes = int(offset.total_seconds() // 60)
        sign = "+" if offset_minutes >= 0 else "-"
        hours, minutes = divmod(abs(offset_minutes), 60)
        utc_offset = f"UTC{sign}{hours:02d}:{minutes:02d}"
        timezone_label = due_at.tzname() or due_timezone
        zone_display = (
            f"{utc_offset} ({_esc(timezone_label)})"
            if offset_minutes != 0
            else ""
        )
        due_text = f"{time_text} {zone_display}"
        is_past_due = due_at < datetime.now(zone)
    else:
        due_day = (
            due_date
            if isinstance(due_date, date)
            else date.fromisoformat(str(due_date)[:10])
        )
        due_at = datetime.combine(due_day, time.max, tzinfo=zone)
        due_unix = int(due_at.timestamp())
        display_date = due_day.strftime("%b %d, %Y")
        due_text = (
            f'<tg-time unix="{due_unix}" format="d">{_esc(display_date)}</tg-time>'
        )
        is_past_due = due_day < datetime.now(zone).date()

    icon = "🔁" if getattr(due, "is_recurring", False) else "🗓"
    prefix = "Past Due 💔 " if is_past_due else ""
    return f"{prefix}{icon} {due_text}"


def _task_meta(task) -> str:
    parts = []
    due_line = _format_due_line(task)
    if due_line:
        parts.append(due_line)

    # Todoist priority 4..1 maps to user-facing p1..p4; skip p4 (low).
    user_priority = 5 - int(getattr(task, "priority", 1) or 1)
    if user_priority in (1, 2, 3):
        parts.append(f"⚡ p{user_priority}")

    labels = [
        label
        for label in (getattr(task, "labels", None) or [])
        if label != TODOIST_LABEL_NAME
    ]
    if labels:
        parts.append(f"🏷 {_esc(', '.join(labels))}")

    return "  ·  ".join(parts)


def _task_message_link(task) -> Optional[str]:
    description = getattr(task, "description", "") or ""
    match = re.search(
        r"Message \[Task cmd message\].*?\((https://[^)]+)\)",
        description,
        re.DOTALL,
    )
    return match.group(1) if match else None


def fetch_section_tasks() -> list:
    tasks = [
        task
        for task in _iter_items(API.get_tasks(section_id=SECTION.id))
        if not getattr(task, "is_completed", False)
        and not getattr(task, "completed_at", None)
    ]
    tasks.sort(
        key=lambda task: (
            -int(getattr(task, "priority", 1) or 1),
            int(getattr(task, "order", 0) or 0),
        )
    )
    return tasks


def render_todo_list(tasks: list, page: int) -> str:
    total = len(tasks)
    total_pages = max(1, (total + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * LIST_PAGE_SIZE
    chunk = tasks[start : start + LIST_PAGE_SIZE]

    lines = [
        "📋 <b>Todoist · From Telegram</b>",
        f"<i>page {page}/{total_pages} · {total} task(s)</i>",
        "",
    ]
    for number, task in enumerate(chunk, start=start + 1):
        content = task.content or "(untitled)"
        if len(content) > LIST_CONTENT_PREVIEW:
            content = content[:LIST_CONTENT_PREVIEW].rstrip() + "…"
        message_link = _task_message_link(task)
        link = f' [<a href="{_esc(message_link)}">Go to msg</a>]' if message_link else ""
        lines.append(f"<b>{number}.</b> {_esc(content)}{link}")
        meta = _task_meta(task)
        if meta:
            lines.append(f"    {meta}")
        description = " ".join((task.description or "").split())
        if description:
            if len(description) > LIST_DESCRIPTION_PREVIEW:
                description = (
                    description[:LIST_DESCRIPTION_PREVIEW].rstrip() + "…"
                )
            lines.append(f"    <i>{_esc(description)}</i>")
        lines.append("")

    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(
                f"◀ <code>{config.CMD_PREFIX}todo list {page - 1}</code>"
            )
        if page < total_pages:
            nav.append(
                f"<code>{config.CMD_PREFIX}todo list {page + 1}</code> ▶"
            )
        if nav:
            lines.append("")
            lines.append("  ·  ".join(nav))

    text = "\n".join(lines)
    if len(text) > MAX_LIST_TEXT:
        text = text[:MAX_LIST_TEXT].rstrip() + "\n… (truncated)"
    return text


async def handle_todo_list(client, message: Message, page: int) -> None:
    try:
        ensure_initialized()
    except RuntimeError as init_error:
        logger.warning(f"[TODOIST] Not configured: {init_error}")
        await message.edit(
            "❌ Todoist is not configured.\n\n"
            f"Reason: <code>{_esc(str(init_error))}</code>\n\n"
            "Add <code>TODOIST_API_TOKEN</code> to <code>.env</code> "
            "(Settings → Integrations → Developer in Todoist), then restart "
            "the bot.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        tasks = fetch_section_tasks()
    except Exception as exc:
        error_details = _format_todoist_error(exc)
        logger.error(
            f"[TODOIST] Failed to list tasks: {error_details}", exc_info=True
        )
        await message.edit(
            "❌ Could not load tasks.\n"
            f"<code>{_esc(error_details)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not tasks:
        await message.edit(
            "📭 <b>Todoist · From Telegram</b>\n\n"
            "No tasks yet. Add one with "
            f"<code>{config.CMD_PREFIX}todo &lt;content&gt;</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    total_pages = max(1, (len(tasks) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    if page < 1 or page > total_pages:
        await message.edit(
            f"❌ Page {page} is out of range (1–{total_pages}).",
            parse_mode=ParseMode.HTML,
        )
        return

    await message.edit(
        render_todo_list(tasks, page), parse_mode=ParseMode.HTML
    )


@main.app.on_message(
    filters.me & filters.command("todo", prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def todo_command(client, message: Message):
    raw_text = _extract_message_text(message).strip()
    raw_content = raw_text.split(maxsplit=1)

    if len(raw_content) < 2:
        await message.edit(USAGE_TEXT)
        return

    arg = raw_content[1].strip()
    list_match = re.match(r"(?i)^list(?:\s+(\S+))?$", arg)
    if list_match:
        page_token = list_match.group(1)
        if page_token is None:
            page = 1
        elif page_token.isdigit():
            page = int(page_token)
        else:
            await message.edit(
                f"Usage: <code>{config.CMD_PREFIX}todo list [page]</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        await handle_todo_list(client, message, page)
        return

    parsed = parse_todo_input(arg)
    if not parsed.title:
        await message.edit(USAGE_TEXT)
        return
    if len(parsed.title) > MAX_TASK_CONTENT_LEN:
        await message.reply_text("title is more than 500 char")
        await send_result_feedback(client, message, success=False)
        return

    try:
        ensure_initialized()
    except RuntimeError as init_error:
        logger.warning(f"[TODOIST] Not configured: {init_error}")
        await message.edit(
            "❌ Todoist is not configured.\n\n"
            f"Reason: <code>{_esc(str(init_error))}</code>\n\n"
            "Add <code>TODOIST_API_TOKEN</code> to <code>.env</code> "
            "(Settings → Integrations → Developer in Todoist), then restart "
            "the bot.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        description = build_description(message, parsed.description)
        todo_message_link = _build_message_link(message.chat.id, message.id)
        labels = [LABEL.name]
        for label in resolve_labels(parsed.tags):
            if label.casefold() != LABEL.name.casefold():
                labels.append(label)

        create_task_kwargs = {
            "content": parsed.title,
            "section_id": SECTION.id,
            "labels": labels,
        }
        if description:
            create_task_kwargs["description"] = description
        if parsed.priority:
            create_task_kwargs["priority"] = parsed.priority
        if parsed.due_string:
            create_task_kwargs["due_string"] = parsed.due_string
            create_task_kwargs["due_lang"] = "en"

        API.add_task(**create_task_kwargs)
        await send_result_feedback(client, message, success=True)
    except Exception as exc:
        error_details = _format_todoist_error(exc)
        logger.error(
            f"[TODOIST] Failed to create task: {error_details}", exc_info=True
        )
        await log_error_to_saved_messages(
            client,
            context=(
                f"chat={message.chat.id} message={message.id}\n"
                f"todo_message={todo_message_link}"
            ),
            error=RuntimeError(error_details),
        )
        await send_result_feedback(client, message, success=False)


initialize_todoist()
