# Managed plugin API

Public plugin authors can declare handlers on a typed `Plugin` class instead of registering through `main.app` at import time. The manager owns managed activation and records handlers for rollback and reload. Existing public modules are being migrated in stages; `private_plugins/` is not discovered or imported.

## Typed plugin template

Copy `templates/typed_plugin.py` into `plugins/` after customizing the plugin id and handlers. Import Kurigram types directly for editor completion and type checking:

```python
from pyrogram import Client
from pyrogram.types import Message
from core.plugins import (
    Example, F, Option, ParsedOptions, Plugin, PluginContext, handlers as h
)

class GreetingPlugin(Plugin):
    id = "greeting"

    @h.command(
        "hello",
        description="Send a greeting.",
        examples=(Example("{cmd}"),),
        options=(Option("name", type=str, default="there"),),
        filters=F.text,
    )
    async def hello(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["GreetingPlugin"],
        args: ParsedOptions,
    ) -> None:
        await ctx.send(f"Hello, {args.name}!", reply_to=message)
```

Handlers receive the userbot as `client`, the real event object, the required `ctx`, then parsed command options when applicable and any additional handler arguments. For `@h.bot_*` declarations, the first client argument is the assistant bot as `bot`. In both cases `ctx.client` is the userbot and `ctx.bot` is the assistant bot (or `None` when disabled). Annotate `Message`, `CallbackQuery`, `InlineQuery`, and application-specific values to improve completion and catch mistakes in an IDE. `F` is a convenience alias for Kurigram filters; `from pyrogram import filters` remains supported.

## Handler types

- `@h.command(...)` declares a prefix command and its help/parser metadata.
- `@h.message(filters=...)` handles matching new messages.
- `@h.edited_message(filters=...)` handles edited messages.
- `@h.callback_query(filters=...)` handles callback queries.
- `@h.inline_query(filters=...)` handles inline queries.
- `@h.bot_command`, `@h.bot_callback_query`, and `@h.bot_inline_query` register handlers on the optional assistant bot client. Set `BOT_TOKEN` to enable it; leave it empty to run only the userbot. Bot command prefixes default to `/`.

Stacking message decorators on one method creates distinct registrations that invoke the same method. For example, stack `@h.message` and `@h.edited_message` when the method supports both event forms. The update argument remains the original Kurigram type for that event.

All managed callbacks continue propagation after running. In the verified dispatcher, this continues to the next matching handler in the same group. Group selection and stop propagation are deferred TODOs; they are not part of the current API.

## Filters and handler kwargs

Pass Kurigram filter expressions directly, for example `F.text & F.me` or `F.reply`. Use Kurigram types and errors directly inside handlers. Decorator keywords for framework metadata are explicit; filters are supplied as `filters=`. Unsupported extra handler options are rejected during plugin activation rather than silently ignored.

## Examples and options

`Example` values support `{cmd}` for the prefixed command and `{prefix}` for the configured prefix. `Option` declarations drive command parsing and help. Available option fields include `name`, `short`, `type`, `required`, `default`, `action`, `append`, and `help`.

The parser supports long options, short options, boolean clusters, typed values, repeatable append options, and `--` end-of-options. Parser errors are returned before handler invocation. Commands without declared option schemas receive a `ParsedOptions` value whose `positional` field contains the raw argument tail as one string, preserving free-form command text.

## Context helpers

`ctx.get_config(key, default)` reads a configuration setting. `ctx.get_config_typed(key, type)` converts a configured value and raises a clear `ValueError` for an invalid value. `ctx.send` defaults to HTML and can split long text with `split=True`; set `split=False` to reject oversized text. Raw client and message methods remain available.

`ctx.edit`, `ctx.react_or_send`, and `ctx.react_or_edit` are convenience methods. Use raw Kurigram calls where exact behavior or options are required. The context database service is a future extension, not implemented.

## Loader boundary

Only direct `*.py` files in `plugins/` are discovered. `private_plugins/` is intentionally excluded. A managed module must declare exactly one `Plugin` subclass. Migrated public feature modules include `ai`, `alive`, `automessages`, `eval`, `exec`, `help`, `id`, `info`, `json`, `kill`, `ping`, `reload`, `sangmata`, `save`, `spam`, `sticker`, `todoist`, `translate`, `tts`, `voice_note`, and `web`. `sticker_index.py` is an internal helper, not a handler module. The demo plugin demonstrates managed callbacks and event handlers.
