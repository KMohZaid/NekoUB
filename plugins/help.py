# NOTE: Telegram only renders inline keyboards on messages sent by bots. A
# userbot sends as a regular user account, so buttons are silently ignored;
# this menu navigates with plain text commands instead.

from pyrogram import filters
from pyrogram.types import Message

import config
import main
from userbot.utils import continue_propagation, get_logger

logger = get_logger(__name__)

MAX_TEXT = 3800  # keep safely under Telegram's 4096 limit


# Each command: cmd, args (display only), desc, ex (examples; {p} = prefix)
CATEGORIES: dict[str, dict] = {
    "core": {
        "emoji": "🧭",
        "title": "Core",
        "desc": "Basic bot commands.",
        "commands": [
            {
                "cmd": "alive",
                "args": "",
                "desc": "Show userbot status, uptime, plugins and system info.",
                "ex": ["{p}alive"],
            },
            {
                "cmd": "ping",
                "args": "",
                "desc": "Check bot latency (round-trip time in ms).",
                "ex": ["{p}ping"],
            },
            {
                "cmd": "reload",
                "args": "",
                "desc": "Hot-reload all plugins without restarting the bot. "
                "Auto-deletes after 10s.",
                "ex": ["{p}reload"],
            },
            {
                "cmd": "help",
                "args": "",
                "desc": "Show this help menu. Aliases: commands, cmds, menu.",
                "ex": ["{p}help", "{p}help stickers", "{p}help kang", "{p}help all"],
            },
        ],
    },
    "ai": {
        "emoji": "🤖",
        "title": "AI",
        "desc": "Chat with the configured AI model (.ai).",
        "commands": [
            {
                "cmd": "ai",
                "args": "<prompt>",
                "desc": "Ask the AI. Keeps a short conversation per chat. "
                "Alias: ask.",
                "ex": ["{p}ai explain quantum tunneling simply"],
            },
            {
                "cmd": "ai",
                "args": "(reply)",
                "desc": "Reply to any message with .ai to send it as the "
                "prompt; add a prompt to give extra instruction.",
                "ex": ["{p}ai (reply to a message)", "{p}ai summarize (reply)"],
            },
            {
                "cmd": "ai",
                "args": "search <query>",
                "desc": "Search the web and have the AI summarize the "
                "results with sources.",
                "ex": ["{p}ai search latest ai news"],
            },
            {
                "cmd": "ai",
                "args": "reset",
                "desc": "Clear this chat's conversation history.",
                "ex": ["{p}ai reset"],
            },
            {
                "cmd": "ai",
                "args": "model [name]",
                "desc": "Show or set the model for this chat.",
                "ex": ["{p}ai model", "{p}ai model groq/openai/gpt-oss-120b"],
            },
            {
                "cmd": "ai",
                "args": "sys [text]",
                "desc": "Show or set the system prompt for this chat.",
                "ex": ["{p}ai sys You are a pirate."],
            },
        ],
    },
    "web": {
        "emoji": "🌐",
        "title": "Web",
        "desc": "Search the web and read pages from chat.",
        "commands": [
            {
                "cmd": "web",
                "args": "<query>",
                "desc": "Search the web and list the results. Alias: search.",
                "ex": ["{p}web latest ai news"],
            },
            {
                "cmd": "web",
                "args": "next | prev",
                "desc": "Go to the next/previous page of the last search. "
                "Aliases: .next, .prev.",
                "ex": ["{p}web next", "{p}web prev"],
            },
            {
                "cmd": "open",
                "args": "<n>",
                "desc": "Read result number <n> from the last search. "
                "You can also reply to the results message with just a "
                "number.",
                "ex": ["{p}open 3"],
            },
            {
                "cmd": "read",
                "args": "<url>",
                "desc": "Fetch any URL and show it as plain text. "
                "Aliases: browse, url.",
                "ex": ["{p}read https://example.com"],
            },
        ],
    },
    "utility": {
        "emoji": "🧰",
        "title": "Utility",
        "desc": "Tools, info and message helpers.",
        "commands": [
            {
                "cmd": "exec",
                "args": "<command>",
                "desc": "Run a shell command and show stdout/stderr/return code.",
                "ex": ["{p}exec ls -la", "{p}exec uptime"],
            },
            {
                "cmd": "eval",
                "args": "<python>",
                "desc": "Evaluate Python code. `await` is supported.",
                "ex": ['{p}eval print("hello")'],
            },
            {
                "cmd": "kill",
                "args": "",
                "desc": "Stop all currently running .exec / .eval tasks.",
                "ex": ["{p}kill"],
            },
            {
                "cmd": "info",
                "args": "",
                "desc": "Show info about this chat, or the sender of the "
                "replied message (no phone numbers).",
                "ex": ["{p}info", "{p}info (reply to a message)"],
            },
            {
                "cmd": "id",
                "args": "",
                "desc": "Show chat ID, user ID, and media file IDs of the "
                "replied message.",
                "ex": ["{p}id", "{p}id (reply to media)"],
            },
            {
                "cmd": "json",
                "args": "",
                "desc": "Export the replied message as a JSON file.",
                "ex": ["{p}json (reply to a message)"],
            },
            {
                "cmd": "save",
                "args": "",
                "desc": "Save the replied message to Saved Messages "
                "(forward, falls back to copy).",
                "ex": ["{p}save (reply to a message)"],
            },
            {
                "cmd": "sg",
                "args": "",
                "desc": "Look up a user's past names and usernames via "
                "SangMata. Reply to their message or pass an id/username. "
                "Alias: sangmata.",
                "ex": ["{p}sg (reply to a user)", "{p}sg @username", "{p}sg 12345678"],
            },
            {
                "cmd": "tr",
                "args": "[lang] <text>",
                "desc": "Translate text, a replied message, or a replied "
                "voice/audio note (voice uses Groq Whisper). Pass a "
                "language code first (e.g. `hi`, `es`), otherwise the "
                "configured default is used. Alias: translate.",
                "ex": ["{p}tr Hello", "{p}tr es Hello", "{p}tr hi (reply)"],
            },
            {
                "cmd": "tts",
                "args": "<text>",
                "desc": "Convert text or the replied message into a voice "
                "note. Voice is set by TTS_VOICE. Alias: say.",
                "ex": ["{p}tts Hello there", "{p}tts (reply)"],
            },
        ],
    },
    "automations": {
        "emoji": "⏰",
        "title": "Automations",
        "desc": "Automated messages: keyword, reply, tag and time triggers.",
        "commands": [
            {
                "cmd": "am",
                "args": "<match> on <output>",
                "desc": "Keyword trigger: send <output> when someone says "
                "<match> in this chat. Flags: `-tag`/`-notag`, `-reply`, "
                "`-exact`.",
                "ex": [
                    "{p}am hello on Hi there!",
                    "{p}am hi on Hello! -tag",
                ],
            },
            {
                "cmd": "am",
                "args": "<match> reply <output>",
                "desc": "Reply trigger: send <output> when someone replies to "
                "you (optionally only if their message contains <match>). "
                "Alias trigger: `tag`/`mention` also fires when you are "
                "tagged.",
                "ex": [
                    "{p}am reply On it!",
                    "{p}am boss reply Yes sir?",
                    "{p}am /ft tag /plant hdsfshfg -notag",
                ],
            },
            {
                "cmd": "am",
                "args": "every <time> <output>",
                "desc": "Repeat <output> every <time> (`30s`, `15m`, `1h`, "
                "`1d`, or `30 minutes`).",
                "ex": ["{p}am every 1h Stand up!", "{p}am every 30m Drink water"],
            },
            {
                "cmd": "am",
                "args": "at <HH:MM> <output>",
                "desc": "Send <output> every day at <HH:MM> (24h) or "
                "`9:00 am`. Uses the server's local timezone.",
                "ex": ["{p}am at 09:00 Good morning", "{p}am at 22:30 Sleep!"],
            },
            {
                "cmd": "am",
                "args": "in <time> <output>",
                "desc": "Send <output> once, after <time>.",
                "ex": ["{p}am in 10m On my way", "{p}am in 2h Ping me"],
            },
            {
                "cmd": "am",
                "args": "list | del <id> | on <id> | off <id> | clear",
                "desc": "Manage this chat's automations. Automations are per "
                "chat and survive restarts.",
                "ex": [
                    "{p}am list",
                    "{p}am off 2",
                    "{p}am del 2",
                    "{p}am clear",
                ],
            },
        ],
    },
    "fun": {
        "emoji": "🎉",
        "title": "Fun / Spam",
        "desc": "Repeat sending. Use responsibly.",
        "commands": [
            {
                "cmd": "spam",
                "args": "[flags] <count> <text>",
                "desc": "Send a message repeatedly, or copy the replied "
                "message. Flags: `-c/-count <n>`, `-d/-delay <10s|5m|1h>`, "
                "`-del/-delete` (auto-delete after 2.5s), `-r/-reply`, "
                "`-ignore/-nolimit` (bypass max count).",
                "ex": [
                    "{p}spam 10 Hello World",
                    "{p}spam -d 5s 3 Hi",
                    "{p}spam -del -c 5 Test",
                    "{p}spam -r 5 Hi (reply to a message)",
                ],
            },
            {
                "cmd": "spam_stop",
                "args": "",
                "desc": "Stop the active spam in this chat.",
                "ex": ["{p}spam_stop"],
            },
            {
                "cmd": "spam_stat",
                "args": "",
                "desc": "Show progress of the active spam in this chat.",
                "ex": ["{p}spam_stat"],
            },
            {
                "cmd": "spam_help",
                "args": "",
                "desc": "Show detailed spam help.",
                "ex": ["{p}spam_help"],
            },
        ],
    },
    "stickers": {
        "emoji": "🖼️",
        "title": "Stickers",
        "desc": "Kang stickers into your own packs.",
        "commands": [
            {
                "cmd": "kang",
                "args": "",
                "desc": "Copy the replied sticker, image, GIF or video into "
                "your default auto pack. Photos become static stickers; GIFs "
                "and videos become video stickers. Add emojis to override the "
                "sticker's emoji.",
                "ex": ["{p}kang", "{p}kang 🐱", "{p}kang -a 🐱 (append emoji)"],
            },
            {
                "cmd": "kang",
                "args": "<pack>",
                "desc": "Add the replied sticker, image, GIF or video to a "
                "specific pack. `<pack>` can be a number from `.packs`, a "
                "short name, a numeric id, or a suffix to create/reuse a "
                "themed pack.",
                "ex": [
                    "{p}kang 1",
                    "{p}kang fun",
                    "{p}kang fun 🐱",
                    "{p}kang oppeace_fun_123 🐱",
                ],
            },
            {
                "cmd": "packs",
                "args": "",
                "desc": "List every sticker pack you own with its number, "
                "short name, id and sticker count. Alias: mypacks.",
                "ex": ["{p}packs"],
            },
            {
                "cmd": "packs search",
                "args": "<keyword>",
                "desc": "Search all public Telegram sticker packs by keyword "
                "and get their add links.",
                "ex": ["{p}packs search cats", "{p}packs search meme"],
            },
            {
                "cmd": "start_sticker_monitor",
                "args": "",
                "desc": "Auto-kang every sticker you send in this chat.",
                "ex": ["{p}start_sticker_monitor"],
            },
            {
                "cmd": "stop_sticker_monitor",
                "args": "",
                "desc": "Stop the sticker monitor in all chats.",
                "ex": ["{p}stop_sticker_monitor"],
            },
            {
                "cmd": "help_sticker",
                "args": "",
                "desc": "Show the sticker plugin's detailed help.",
                "ex": ["{p}help_sticker"],
            },
        ],
    },
    "todoist": {
        "emoji": "✅",
        "title": "Todoist",
        "desc": "Requires TODOIST_API_TOKEN in .env. Tasks have a title, "
        "an optional description and optional tags.",
        "commands": [
            {
                "cmd": "todo",
                "args": "<title>",
                "desc": "Create a task with just a title. Reply to a message "
                "to attach its text, sender and link to the description.",
                "ex": [
                    "{p}todo Buy milk",
                    "{p}todo Pay bill (reply to the bill message)",
                ],
            },
            {
                "cmd": "todo",
                "args": "<title> #desc |text|",
                "desc": "Add your own description above any replied-message "
                "context. Can span multiple lines.",
                "ex": [
                    "{p}todo Fix login #desc |Repro: 500 on submit|",
                ],
            },
            {
                "cmd": "todo",
                "args": "<title> #tags |a, b|",
                "desc": "Attach Todoist labels. Missing labels are created "
                "automatically; the single `#tag <name>` form also works.",
                "ex": [
                    "{p}todo Fix login #tags |backend, urgent|",
                    "{p}todo Call back #tag phone",
                ],
            },
            {
                "cmd": "todo",
                "args": "<title> [#pN] [#due |<date>|]",
                "desc": "Set priority (`#p1` very urgent … `#p4` low) and/or "
                "a due date parsed by Todoist.",
                "ex": [
                    "{p}todo Pay bill #p1",
                    "{p}todo Meeting #due |tomorrow 5pm|",
                ],
            },
            {
                "cmd": "todo",
                "args": "<title> [#tags |a, b|] [#desc |text|] [#pN] [#due |<date>|]",
                "desc": "Everything combined into one rich task.",
                "ex": [
                    "{p}todo Fix login #tags |backend, urgent| "
                    "#desc |500 on submit| #p1 #due |tomorrow|",
                ],
            },
            {
                "cmd": "todo list",
                "args": "[page]",
                "desc": "List the tasks added from Telegram (10 per page) "
                "with labels and a description preview. Pass a page number "
                "to jump to that page.",
                "ex": [
                    "{p}todo list",
                    "{p}todo list 2",
                ],
            },
        ],
    },
}


def _prefix() -> str:
    return config.CMD_PREFIX


def _usage_plain(command: dict) -> str:
    text = f"{_prefix()}{command['cmd']}"
    if command.get("args"):
        text += f" {command['args']}"
    return text


def _usage_code(command: dict) -> str:
    return f"`{_usage_plain(command)}`"


def _total_commands() -> int:
    return sum(len(cat["commands"]) for cat in CATEGORIES.values())


def _header() -> str:
    return (
        "**🐱 NekoUB — Help**\n"
        f"Prefix: `{_prefix()}`  •  Plugins: `{len(main.loaded_plugins)}`"
        f"  •  Commands: `{_total_commands()}`"
    )


def render_root() -> str:
    lines = [_header(), "", "**Categories**", ""]
    for index, key in enumerate(CATEGORIES, start=1):
        cat = CATEGORIES[key]
        lines.append(
            f"{index}. {cat['emoji']} **{cat['title']}** "
            f"— {len(cat['commands'])} cmds"
        )
    lines += [
        "",
        f"Open: `{_prefix()}help <n>` or `{_prefix()}help <name>`",
        f"Everything: `{_prefix()}help all`",
    ]
    return "\n".join(lines)


def render_category(key: str) -> str:
    cat = CATEGORIES[key]
    lines = [f"{cat['emoji']} **{cat['title']}**", cat.get("desc", ""), ""]
    for index, command in enumerate(cat["commands"], start=1):
        lines.append(f"{index}. {_usage_code(command)} — {command['desc']}")
    lines += [
        "",
        f"Details: `{_prefix()}help {key} <n>`  •  Menu: `{_prefix()}help`",
    ]
    return "\n".join(lines)


def render_detail(key: str, offset: int) -> str:
    commands = CATEGORIES[key]["commands"]
    if offset < 0 or offset >= len(commands):
        return render_category(key)
    command = commands[offset]
    lines = [f"**{_usage_code(command)}**", "", command["desc"]]
    if command.get("ex"):
        lines.append("")
        lines.append("**Examples:**")
        for example in command["ex"]:
            lines.append(f"• `{example.format(p=_prefix())}`")
    lines += [
        "",
        f"Category: `{_prefix()}help {key}`  •  Menu: `{_prefix()}help`",
    ]
    return "\n".join(lines)


def _summarize(desc: str, limit: int = 90) -> str:
    text = desc.split(". ")[0].rstrip(".")
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def render_all() -> str:
    lines = ["**🐱 NekoUB — All commands**", ""]
    for cat in CATEGORIES.values():
        lines.append(f"{cat['emoji']} **{cat['title']}**")
        for command in cat["commands"]:
            lines.append(
                f"• {_usage_code(command)} — {_summarize(command['desc'])}"
            )
        lines.append("")
    text = "\n".join(lines).strip()
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "\n… (truncated)"
    return text


def render_command_lookup(name: str) -> str:
    matches = []
    for key, cat in CATEGORIES.items():
        for index, command in enumerate(cat["commands"]):
            if command["cmd"].lower() == name:
                matches.append((key, index))

    if len(matches) == 1:
        return render_detail(*matches[0])
    if len(matches) > 1:
        lines = [f"**Multiple commands named `{name}`**", ""]
        for key, index in matches:
            command = CATEGORIES[key]["commands"][index]
            lines.append(f"• {_usage_code(command)} — {command['desc']}")
        lines += [
            "",
            f"Details: `{_prefix()}help {matches[0][0]} <n>`",
        ]
        return "\n".join(lines)
    return f"❓ Unknown command `{name}`.\n\n{render_root()}"


def render(args: str) -> str:
    """Turn a help argument string into the right view."""
    keys = list(CATEGORIES)
    arg = args.strip()

    if not arg:
        return render_root()
    if arg.lower() == "all":
        return render_all()

    tokens = arg.split()
    first = tokens[0].lower()

    if first.isdigit():
        index = int(first)
        if 1 <= index <= len(keys):
            key = keys[index - 1]
            if len(tokens) > 1 and tokens[1].isdigit():
                return render_detail(key, int(tokens[1]) - 1)
            return render_category(key)
        return render_root()

    if first in CATEGORIES:
        if len(tokens) > 1 and tokens[1].isdigit():
            return render_detail(first, int(tokens[1]) - 1)
        return render_category(first)

    return render_command_lookup(first)


@main.app.on_message(
    filters.me
    & filters.command(
        ["help", "commands", "cmds", "menu"], prefixes=config.CMD_PREFIX
    )
)
@continue_propagation
async def help_command(client, message: Message):
    """Show the help menu.

    Usage:
        .help [<category>|all] [<n>]
    """
    try:
        parts = (message.text or "").split(maxsplit=1)
        args = parts[1] if len(parts) > 1 else ""
        await message.edit(render(args))
    except Exception as exc:
        logger.error(f"[HELP] Failed to render menu: {exc}")
        await message.edit(f"❌ Failed to render help: {type(exc).__name__}")
