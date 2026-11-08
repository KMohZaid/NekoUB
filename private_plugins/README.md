# Private Plugins

This folder is for your **private plugins** that you don't want to commit to git.

## Usage

1. Place managed `Plugin` subclasses here (same API as plugins in `plugins/`)
2. Valid declarations load automatically on bot start
3. Use `.reload` to reload public and private plugins

## Why use this folder?

- Keep personal/sensitive plugins private
- Test experimental plugins before moving to main `plugins/` folder
- Share your userbot repo without exposing custom commands

## Important

- This folder is **ignored by git** except `__init__.py`, this README, and `example.py`
- Add your own `.py` files here; they stay untracked by git
- Use the managed `Plugin` structure shown in the example below

## Example: Managed `.private` command

Create `private_plugins/example.py` and restart the bot. This command replies
with `I am from private plugin` when you send `.private`:

```python
from core.plugins import Plugin, handlers as h


class PrivateExample(Plugin):
    id = "private_example"

    @h.command("private", description="Confirm private plugin loading.")
    async def private(self, client, message, ctx, args):
        await ctx.edit("I am from private plugin", message=message)
```

Private plugins use the same managed `Plugin` API as public plugins. Their
modules load under `private_plugins.*`, so private helpers can be imported
without colliding with public plugin names. Invalid files are skipped and
logged with their path and exception; loading continues for other private
plugins.
