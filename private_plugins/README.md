# Private Plugins

This folder is for your **private plugins** that you don't want to commit to git.

## Usage

1. Place your custom plugins here (same format as `plugins/` folder)
2. They will be loaded automatically on bot start
3. Use `.reload` to reload all plugins (including private ones)

## Why use this folder?

- Keep personal/sensitive plugins private
- Test experimental plugins before moving to main `plugins/` folder
- Share your userbot repo without exposing custom commands

## Important

- This folder is **ignored by git** (except `__init__.py` and this README)
- All `.py` files here will be loaded as plugins
- Same structure as main `plugins/` folder

## Example

```python
# private_plugins/my_custom.py
from pyrogram import filters
from pyrogram.types import Message

import config
import main
from userbot.utils import get_logger

logger = get_logger(__name__)

@main.app.on_message(filters.me & filters.command("mycmd", prefixes=config.CMD_PREFIX))
async def my_custom_command(client, message: Message):
    """My custom command."""
    await message.edit("My custom response!")
```
