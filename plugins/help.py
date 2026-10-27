import html
import re
from pathlib import Path

from pyrogram import Client, filters as F
from pyrogram.enums import ParseMode
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    CallbackQuery,
    Message,
    ReplyParameters,
)

import config
import main
from core.plugins import Example, ParsedOptions, Plugin, PluginContext, handlers as h
from utils import get_logger

logger = get_logger(__name__)

MAX_TEXT = 3800  # keep safely under Telegram's 4096 limit


CATEGORIES: dict[str, dict] = {
    "Core": {"emoji": "🧭", "title": "Core", "desc": "Basic bot commands."},
    "AI & Tools": {"emoji": "🤖", "title": "AI & Tools", "desc": "AI chat, web search, page reading, and translation."},
    "Utility": {"emoji": "🧰", "title": "Utility", "desc": "Info and message helpers."},
    "Automations": {"emoji": "⏰", "title": "Automations", "desc": "Keyword and scheduled messages."},
    "Fun / Spam": {"emoji": "🎉", "title": "Fun / Spam", "desc": "Repeat sending. Use responsibly."},
    "Stickers": {"emoji": "🖼️", "title": "Stickers", "desc": "Kang stickers into your own packs."},
    "Productivity": {"emoji": "✅", "title": "Productivity", "desc": "Tasks and personal workflow tools."},
    "Media": {"emoji": "🎙️", "title": "Media", "desc": "Audio and media tools."},
    "Examples": {"emoji": "🧪", "title": "Examples", "desc": "Example managed plugin."},
}

BUILTIN_COMMANDS: dict[str, list[dict]] = {}


def _html(text: str) -> str:
    """Convert the help renderer's Markdown subset to escaped Telegram HTML."""
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", escaped)
    return escaped


def _category_key(category: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", category.casefold()).strip("_")


def _managed_commands() -> list[dict]:
    manager = getattr(main, "plugin_manager", None)
    if manager is None:
        return []
    statuses = getattr(main, "public_plugin_status", {})
    unavailable = {
        Path(filename).stem
        for filename, status in statuses.items()
        if status.get("state") != "loaded"
    }
    return [
        item for item in manager.command_help()
        if not item.get("hidden_from_help") and item["plugin"] not in unavailable
    ]


def _plugins_for_category(category: str) -> list[dict]:
    category = _category_key(category)
    plugins: dict[str, dict] = {}
    for command in _managed_commands():
        if _category_key(command.get("plugin_category") or "Other") != category:
            continue
        plugin_id = command["plugin"]
        plugin = plugins.setdefault(
            plugin_id,
            {
                "id": plugin_id,
                "name": command.get("plugin_display_name") or plugin_id.replace("_", " ").title(),
                "description": command.get("plugin_description", ""),
                "commands": [],
            },
        )
        plugin["commands"].append(command)
    return sorted(plugins.values(), key=lambda item: item["name"].casefold())


def _disabled_plugins() -> list[dict]:
    statuses = getattr(main, "public_plugin_status", {})
    return [
        {"id": Path(filename).stem, "name": Path(filename).stem.replace("_", " ").title(), "note": item.get("note") or item.get("state")}
        for filename, item in sorted(statuses.items())
        if item.get("state") != "loaded"
    ]


def _categories() -> list[dict]:
    grouped: dict[str, dict] = {}
    for command in _managed_commands():
        title = command.get("plugin_category") or "Other"
        key = _category_key(title)
        category = grouped.setdefault(key, {"key": key, "title": title, "emoji": "🧩"})
        category["count"] = category.get("count", 0) + 1
    if _disabled_plugins():
        grouped["disabled"] = {"key": "disabled", "title": "Disabled", "emoji": "⏸️", "count": len(_disabled_plugins())}
    for key, category in grouped.items():
        category.update(CATEGORIES.get(category["title"], {}))
        category["key"] = key
    return sorted(grouped.values(), key=lambda item: item["title"].casefold())


def _help_keyboard(current: str = "root") -> InlineKeyboardMarkup:
    parts = current.split(":")
    if parts[0] in {"detail", "command"} and len(parts) >= 3:
        category = parts[1]
        plugin_id = parts[2] if parts[0] == "command" and len(parts) >= 4 else None
        plugins = _plugins_for_category(category)
        plugin = next((item for item in plugins if item["id"] == plugin_id), None)
        if plugin is not None:
            if len(plugin["commands"]) == 1:
                return InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "⬅️ Back to plugins",
                        callback_data=f"help.category:{category}",
                    )
                ]])
            return InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"⬅️ Back to {plugin['name']}",
                    callback_data=f"help.plugin:{category}:{plugin_id}",
                )
            ]])
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Back to categories", callback_data="help.root")
        ]])
    if parts[0] == "category" and len(parts) >= 2:
        key = parts[1]
        plugins = _plugins_for_category(key)
        if len(plugins) == 1 and len(plugins[0]["commands"]) == 1:
            return InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⬅️ Back to categories", callback_data="help.root"
                )
            ]])
        buttons = []
        if key == "disabled":
            buttons = []
        elif len(plugins) > 1:
            buttons = [InlineKeyboardButton(plugin["name"], callback_data=f"help.plugin:{key}:{plugin['id']}") for plugin in plugins]
        elif plugins:
            buttons = [
                InlineKeyboardButton(
                    f"{index + 1}. {_usage_plain(item)}",
                    callback_data=f"help.command:{key}:{plugins[0]['id']}:{item['trigger']}",
                )
                for index, item in enumerate(plugins[0]["commands"])
            ]
        rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
        rows.append([InlineKeyboardButton("⬅️ Back to categories", callback_data="help.root")])
        return InlineKeyboardMarkup(rows)
    if parts[0] == "plugin" and len(parts) >= 3:
        plugin = next((item for item in _plugins_for_category(parts[1]) if item["id"] == parts[2]), None)
        buttons = [] if plugin is None else [InlineKeyboardButton(f"{index + 1}. {item.get('prefix', _prefix())}{item['trigger']}", callback_data=f"help.command:{parts[1]}:{parts[2]}:{item['trigger']}") for index, item in enumerate(plugin["commands"])]
        rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
        category_plugins = _plugins_for_category(parts[1])
        back_label = "⬅️ Back to plugins" if len(category_plugins) > 1 else "⬅️ Back to categories"
        rows.append([InlineKeyboardButton(back_label, callback_data=f"help.category:{parts[1]}")])
        return InlineKeyboardMarkup(rows)
    buttons = [InlineKeyboardButton(f"{category['emoji']} {category['title']}", callback_data=f"help.category:{category['key']}") for category in _categories()]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def _prefix() -> str:
    return config.CMD_PREFIX


def _usage_plain(command: dict) -> str:
    return f"{command.get('prefix', _prefix())}{command.get('trigger', command.get('cmd', ''))}{(' ' + command['signature'].strip()) if command.get('signature') else ''}"


def _usage_code(command: dict) -> str:
    return f"`{_usage_plain(command)}`"


def _total_commands() -> int:
    return len(_managed_commands())


def render_plugins() -> str:
    statuses = getattr(main, "public_plugin_status", {})
    loaded = sum(item["state"] == "loaded" for item in statuses.values())
    total = len(statuses)
    available_commands = len(_managed_commands())
    not_loaded = total - loaded
    lines = [
        "<b>Public plugins:</b>",
        f"Loaded: {loaded}",
        f"Not loaded: {not_loaded}",
        f"Total: {total}",
        f"Total commands available: {available_commands}",
    ]
    notes = [
        f"{index}. <b>{html.escape(filename)}</b>: "
        f"{html.escape(item['note'] or item['state'])}"
        for index, (filename, item) in enumerate(
            (
                (filename, item)
                for filename, item in sorted(statuses.items())
                if item["state"] != "loaded"
            ),
            start=1,
        )
    ]
    if notes:
        lines += ["", "<b>Public plugins notes, for not loaded plugins:</b>", "<blockquote>" + "\n".join(notes) + "</blockquote>"]
    lines += ["", "<b>Private plugins:</b>", "Status unavailable."]
    return "\n".join(lines)


def _header() -> str:
    return (
        "**🐱 NekoUB — Help**\n"
        f"Prefix: `{_prefix()}`  •  Plugins: `{len(main.loaded_plugins)}`"
        f"  •  Commands: `{_total_commands()}`"
    )


def render_root() -> str:
    categories = _categories()
    lines = [_header(), "", "**Categories**", ""]
    for index, category in enumerate(categories, start=1):
        if category["key"] == "disabled":
            lines.append(f"{index}. {category['emoji']} **{category['title']}** — {category['count']} disabled plugins")
        else:
            category_plugins = _plugins_for_category(category["key"])
            plugin_count = len(category_plugins)
            command_count = sum(len(plugin["commands"]) for plugin in category_plugins)
            plugin_unit = "plugin" if plugin_count == 1 else "plugins"
            command_unit = "command" if command_count == 1 else "commands"
            lines.append(
                f"{index}. {category['emoji']} **{category['title']}** — "
                f"{plugin_count} {plugin_unit} ({command_count} {command_unit})"
            )
    lines += ["", f"Everything: `{_prefix()}help all`", f"Managed commands: `{_prefix()}help managed`"]
    return "\n".join(lines)


def _commands_for_category(key: str) -> list[dict]:
    plugins = _plugins_for_category(key)
    return plugins[0]["commands"] if len(plugins) == 1 else []


def render_category(key: str) -> str:
    category = next((item for item in _categories() if item["key"] == key), None)
    if category is None:
        return render_root()
    if key == "disabled":
        lines = ["⏸️ **Disabled plugins**", "Plugins unavailable until configured:", ""]
        lines.extend(f"• **{item['name']}** — Disabled: {item['note']}" for item in _disabled_plugins())
        return "\n".join(lines)
    plugins = _plugins_for_category(key)
    if len(plugins) > 1:
        lines = [f"{category['emoji']} **{category['title']}**", category.get("desc", ""), "", "Choose a plugin:"]
        lines.extend(f"• **{plugin['name']}** — {plugin['description']}" for plugin in plugins)
        return "\n".join(lines)
    if not plugins:
        return render_root()
    if len(plugins[0]["commands"]) == 1:
        return _render_command_detail(key, plugins[0]["commands"], 0)
    return _render_plugin_commands(category, plugins[0])


def _render_plugin_commands(category: dict, plugin: dict) -> str:
    lines = [f"{category['emoji']} **{plugin['name']}**", plugin["description"], ""]
    for index, command in enumerate(plugin["commands"], start=1):
        usage = _usage_code(command)
        short = command.get("short_description") or command.get("description") or "Help details are not available yet."
        lines.append(f"{index}. {usage} — {short}")
        lines.append("")
    lines += [f"Select a command below for details. Or use `{_prefix()}help <command>`."]
    return "\n".join(lines)


def _plugin_commands(key: str, plugin_id: str) -> list[dict]:
    plugin = next((item for item in _plugins_for_category(key) if item["id"] == plugin_id), None)
    return plugin["commands"] if plugin else []


def render_detail(key: str, offset: int) -> str:
    commands = _commands_for_category(key)
    if not commands:
        plugins = _plugins_for_category(key)
        commands = plugins[0]["commands"] if len(plugins) == 1 else []
    return _render_command_detail(key, commands, offset)


def _render_command_detail(key: str, commands: list[dict], offset: int) -> str:
    if offset < 0 or offset >= len(commands):
        return render_category(key)
    command = commands[offset]
    lines = [f"**{_usage_code(command)}**", "", command.get("description") or "No detailed help available."]
    options = command.get("help_options") or command.get("options", ())
    if options:
        lines.extend(["", "**Options:**"])
        for option in options:
            if hasattr(option, "name"):
                long_name = f"--{option.name.replace('_', '-')}"
                labels = [option.short, long_name, *option.aliases]
                label = option.label or ", ".join(
                    dict.fromkeys(item for item in labels if item)
                )
                description = option.help
            else:
                label = option.get("label") or option.get("short") or (
                    "--" + option["name"].replace("_", "-")
                )
                description = option.get("help")
            lines.append(
                f"• `{label}` — {description or 'No description provided.'}"
            )
    if command.get("examples"):
        lines.extend(["", "**Examples:**"])
        for index, example in enumerate(command["examples"]):
            note = command.get("example_notes", ())
            suffix = f" — {note[index]}" if index < len(note) and note[index] else ""
            lines.append(f"• `{example}`{suffix}")
    lines.extend(["", f"Use `{_prefix()}help {key}` to go back."])
    return "\n".join(lines)


def _summarize(desc: str, limit: int = 90) -> str:
    text = desc.split(". ")[0].rstrip(".")
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def render_managed() -> str:
    lines = ["**Managed plugin commands**", ""]
    for item in _managed_commands():
        lines.append(f"• {_usage_code(item)} — {item.get('description') or item['plugin']}")
        for example in item["examples"]:
            lines.append(f"  Example: `{example}`")
    return "\n".join(lines)


def render_all() -> str:
    lines = ["**🐱 NekoUB — All commands**", ""]
    for category in _categories():
        if category["key"] == "disabled":
            continue
        lines.append(f"{category['emoji']} **{category['title']}**")
        for plugin in _plugins_for_category(category["key"]):
            for command in plugin["commands"]:
                lines.append(f"• {_usage_code(command)} — {command.get('short_description') or _summarize(command.get('description', ''))}")
        lines.append("")
    text = "\n".join(lines).strip()
    return text[:MAX_TEXT] + "\n… (truncated)" if len(text) > MAX_TEXT else text


def render_plugin_lookup(name: str) -> tuple[str, dict] | None:
    normalized = name.casefold()
    for category in _categories():
        if category["key"] == "disabled":
            continue
        for plugin in _plugins_for_category(category["key"]):
            names = {
                plugin["id"].casefold(),
                plugin["name"].casefold(),
                plugin["name"].replace(" ", "_").casefold(),
            }
            if normalized in names:
                return category["key"], plugin
    return None


def render_plugin_help(key: str, plugin: dict) -> str:
    category = next(item for item in _categories() if item["key"] == key)
    if len(plugin["commands"]) == 1:
        detail = _render_command_detail(key, plugin["commands"], 0)
        return f"{category['emoji']} **{plugin['name']}**\n\n{detail}"
    return _render_plugin_commands(category, plugin)


def _help_page_action(name: str) -> str:
    normalized = name.casefold()
    plugin_match = render_plugin_lookup(name)
    if plugin_match is not None:
        key, plugin = plugin_match
        if len(plugin["commands"]) == 1:
            return f"command:{key}:{plugin['id']}:{plugin['commands'][0]['trigger']}"
        return f"plugin:{key}:{plugin['id']}"
    matches = []
    for category in _categories():
        if category["key"] == "disabled":
            continue
        for plugin in _plugins_for_category(category["key"]):
            for command in plugin["commands"]:
                names = {command["trigger"].casefold(), *(alias.casefold() for alias in command["aliases"])}
                if normalized in names:
                    matches.append((category["key"], plugin["id"], command["trigger"]))
    if len(matches) == 1:
        key, plugin_id, trigger = matches[0]
        return f"command:{key}:{plugin_id}:{trigger}"
    return "root"


def render_command_lookup(name: str) -> str:
    matches = []
    for category in _categories():
        if category["key"] == "disabled":
            continue
        for plugin in _plugins_for_category(category["key"]):
            for command in plugin["commands"]:
                if name in {command["trigger"].casefold(), *(alias.casefold() for alias in command["aliases"])}:
                    matches.append((category["key"], plugin["id"], command))
    if len(matches) == 1:
        key, plugin_id, command = matches[0]
        commands = _plugin_commands(key, plugin_id)
        return _render_command_detail(key, commands, commands.index(command))
    if len(matches) > 1:
        return "\n".join([f"**Multiple commands named `{name}`**", "", *(f"• {_usage_code(item)} — {item.get('short_description') or item.get('description', '')}" for _, _, item in matches)])
    return f"❓ Unknown command `{name}`.\n\n{render_root()}"


def render(args: str) -> str:
    """Turn a help argument string into the right view."""
    categories = _categories()
    arg = args.strip()
    if not arg:
        return render_root()
    if arg.lower() == "all":
        return render_all()
    tokens = arg.split()
    first = tokens[0].casefold()
    plugin_match = render_plugin_lookup(first)
    if plugin_match is not None:
        key, plugin = plugin_match
        return render_plugin_help(key, plugin)
    if first == "plugins":
        return render_plugins()
    if first == "managed":
        return render_managed()
    if first.isdigit() and 1 <= int(first) <= len(categories):
        first = categories[int(first) - 1]["key"]
    category = next((item for item in categories if item["key"] == first or item["title"].casefold() == first), None)
    if category:
        key = category["key"]
        plugins = _plugins_for_category(key)
        if len(tokens) > 1 and tokens[1].isdigit():
            return render_detail(key, int(tokens[1]) - 1)
        if len(plugins) == 1:
            return render_category(key)
        return render_category(key)
    return render_command_lookup(first)


class HelpPlugin(Plugin):
    id = "help"
    category = "Core"
    description = "Show help for public and managed commands."

    @h.bot_callback_query(filters=F.regex(r"^help\."))
    async def help_callback(
        self,
        bot: Client,
        callback: CallbackQuery,
        ctx: PluginContext["HelpPlugin"],
    ) -> None:
        action = callback.data.removeprefix("help.")
        try:
            if action == "root":
                content = render_root()
            elif action.startswith("category:"):
                category = action.partition(":")[2]
                content = render_category(category)
            elif action.startswith("plugin:"):
                _, category, plugin_id = action.split(":", 2)
                plugin = next((item for item in _plugins_for_category(category) if item["id"] == plugin_id), None)
                if plugin is None:
                    raise ValueError("Unknown plugin")
                category_data = next(item for item in _categories() if item["key"] == category)
                content = _render_plugin_commands(category_data, plugin)
            elif action.startswith("command:"):
                _, category, plugin_id, trigger = action.split(":", 3)
                commands = _plugin_commands(category, plugin_id)
                command = next(item for item in commands if item["trigger"] == trigger)
                content = _render_command_detail(category, commands, commands.index(command))
            elif action.startswith("detail:"):
                _, category, index = action.split(":", 2)
                content = render_detail(category, int(index))
            else:
                raise ValueError("Unknown help page")
        except (ValueError, KeyError):
            await callback.answer("That help page is no longer available.", show_alert=True)
            return
        await callback.answer()
        if callback.inline_message_id:
            await bot.edit_inline_text(
                callback.inline_message_id,
                _html(content),
                parse_mode=ParseMode.HTML,
                reply_markup=_help_keyboard(action),
            )
        elif callback.message is not None:
            await callback.message.edit_text(
                _html(content),
                parse_mode=ParseMode.HTML,
                reply_markup=_help_keyboard(action),
            )
        else:
            await callback.answer("This help message can no longer be updated.", show_alert=True)

    @h.bot_inline_query(filters=F.regex(r"^(?:help(?:\s|$)|help\.)"))
    async def help_inline_query(
        self,
        bot: Client,
        query: InlineQuery,
        ctx: PluginContext["HelpPlugin"],
    ) -> None:
        text = query.query.partition(" ")[2].strip()
        rendered = render(text)
        await query.answer(
            [
                InlineQueryResultArticle(
                    title="NekoUB help",
                    description="Open help menu" if not text else f"Help: {text}",
                    input_message_content=InputTextMessageContent(
                        rendered if text.lower() == "plugins" else _html(rendered),
                        parse_mode=ParseMode.HTML,
                    ),
                    reply_markup=_help_keyboard(_help_page_action(text) if text else "root"),
                )
            ],
            cache_time=0,
            is_personal=True,
        )

    @h.bot_command(
        "help",
        description="Open the NekoUB inline help menu.",
        public=False,
        prefixes="/",
        filters=F.private,
    )
    async def bot_help_command(
        self,
        bot: Client,
        message: Message,
        ctx: PluginContext["HelpPlugin"],
        args: ParsedOptions,
    ) -> None:
        await message.reply_text(
            "Choose a help page:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Open help", switch_inline_query="help")]]
            ),
        )

    @h.command(
        "plugins",
        description="Show public plugin load status and available commands.",
        prefixes=config.CMD_PREFIX,
    )
    async def plugins_command(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["HelpPlugin"],
        args: ParsedOptions,
    ) -> None:
        await ctx.edit(render_plugins(), message=message, parse_mode=ParseMode.HTML)

    @h.command(
        "help",
        aliases=("commands", "cmds", "menu"),
        description=description,
        examples=(
            Example("{cmd}"),
            Example("{cmd} stickers"),
            Example("{cmd} managed"),
            Example("{cmd} all"),
        ),
        prefixes=config.CMD_PREFIX,
    )
    async def help_command(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["HelpPlugin"],
        args: ParsedOptions,
    ) -> None:
        """Show the help menu.

        Usage:
            .help [<category>|all] [<n>]
        """
        try:
            parts = (message.text or "").split(maxsplit=1)
            help_args = parts[1] if len(parts) > 1 else ""
            if ctx.bot is not None and ctx.client is not None:
                bot_info = await ctx.bot.get_me()
                results = await ctx.client.get_inline_bot_results(
                    bot_info.username,
                    f"help {help_args}".strip(),
                )
                if not results.results:
                    raise RuntimeError("Assistant bot returned no help results")
                await ctx.client.send_inline_bot_result(
                    message.chat.id,
                    results.query_id,
                    results.results[0].id,
                    reply_parameters=ReplyParameters(message_id=message.id),
                )
                return
            await ctx.edit(
                _html(render(help_args)),
                message=message,
                reply_markup=_help_keyboard(_help_page_action(help_args.split()[0]) if help_args else "root"),
            )
        except Exception as exc:
            logger.error(f"[HELP] Failed to render menu: {exc}")
            await ctx.edit(
                f"❌ Failed to render help: {type(exc).__name__}", message=message
            )
