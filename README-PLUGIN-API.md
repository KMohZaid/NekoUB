# Managed plugin API

This is the initial authoring API for managed public plugins. Plugin modules must declare handlers instead of registering them through `main.app` at import time. The manager owns activation and records handlers for rollback and reload.

## Typed plugin template

Copy `templates/typed_plugin.py` into `plugins/` after customizing the plugin id and handlers. Import Kurigram types directly for editor completion and type checking:

```python
from pyrogram.types import Message
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

Handlers receive the real Kurigram client, the real event object, the required `ctx`, then parsed command options when applicable and any additional handler arguments. Annotate `Message`, `CallbackQuery`, `InlineQuery`, and application-specific values to improve completion and catch mistakes in an IDE. `F` is a convenience alias for Kurigram filters; `from pyrogram import filters` remains supported.

## Handler types

- `@h.command(...)` declares a prefix command and its help/parser metadata.
- `@h.message(filters=...)` handles matching new messages.
- `@h.edited_message(filters=...)` handles edited messages.
- `@h.callback_query(filters=...)` handles callback queries.
- `@h.inline_query(filters=...)` handles inline queries.
- `@h.bot_command`, `@h.bot_callback_query`, and `@h.bot_inline_query` declare assistant-bot handlers. They must be attached to a bot client by a future bot-capability-aware manager; userbot startup does not make bot handlers available automatically.

Stacking message decorators on one method creates distinct registrations that invoke the same method. For example, stack `@h.message` and `@h.edited_message` when the method supports both event forms. The update argument remains the original Kurigram type for that event.

All managed callbacks continue propagation after running. In the verified dispatcher, this continues to the next matching handler in the same group. Group selection and stop propagation are deferred TODOs; they are not part of the current API.

## Filters and handler kwargs

Pass Kurigram filter expressions directly, for example `F.text & F.me` or `F.reply`. Use Kurigram types and errors directly inside handlers. Decorator keywords for framework metadata are explicit; filters are supplied as `filters=`. Unsupported extra handler options are rejected during plugin activation rather than silently ignored.

## Examples and options

`Example` values support `{cmd}` for the prefixed command and `{prefix}` for the configured prefix. `Option` declarations drive command parsing and help. Available option fields include `name`, `short`, `type`, `required`, `default`, `action`, `append`, and `help`.

The parser supports long options, short options, boolean clusters, typed values, repeatable append options, and `--` end-of-options. Parser errors are returned before handler invocation. Commands without declared options do not get framework parsing.

## Context helpers

`ctx.get_config(key, default)` reads a configuration setting. `ctx.get_config_typed(key, type)` converts a configured value and raises a clear `ValueError` for an invalid value. `ctx.send` defaults to HTML and can split long text with `split=True`; set `split=False` to reject oversized text. Raw client and message methods remain available.

`ctx.edit`, `ctx.react_or_send`, and `ctx.react_or_edit` are convenience methods. Use raw Kurigram calls where exact behavior or options are required. The context database service is a future extension, not implemented.

## Loader boundary

Only `plugins/` is discovered. The current integration does not scan or import `private_plugins/`. A managed module must declare exactly one `Plugin` subclass. Legacy public modules still load through their existing import-side-effect path during migration. The demo plugin demonstrates the new API but is not a replacement for migrating all current public plugins.
