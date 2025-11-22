# 🐱 NekoUB - Neko Userbot

A cute and powerful Telegram Userbot based on pyrotgfork with a modular plugin system and colored logging!

## ✨ Features

- 🔌 **Plugin-based architecture** - Add/remove features by simply adding/deleting plugin files
- 🔄 **Hot reload** - Reload plugins without restarting the bot (`.reload` command)
- 🔐 **Private plugins** - Keep your custom plugins private with `private_plugins/` folder
- 🎨 **Colored logging** - Beautiful colored terminal output for better debugging
- 🐱 **Neko-themed** - Cute waifu aesthetic
- 🛡️ **Safety features** - Built-in rate limiting and spam protection
- ⚡ **Fast response** - Check bot latency with `.ping` command
- 💾 **Smart save** - Save messages with fallback (forward → copy) for restricted chats

## 📦 Installation

### Option 1: Docker (Recommended for VPS)

1. Clone the repository:
```bash
git clone <your-repo-url>
cd NekoUB
```

2. Configure and run:
```bash
cp .env.example .env
# Edit .env with your credentials
docker-compose up -d
```

See [DOCKER.md](DOCKER.md) for detailed Docker deployment guide.

### Option 2: Manual Installation

1. Clone the repository:
```bash
git clone <your-repo-url>
cd NekoUB
```

2. Use the auto-install script (detects Python version):
```bash
./run.sh
```

Or manually create virtual environment:
```bash
# Uses any available: python3.13, 3.12, 3.11, or python3
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. Generate your session string:
```bash
python generate_session_string.py
```
Follow the prompts to generate your SESSION_STRING.

4. Configure the bot:
```bash
cp .env.example .env
# Edit .env with your API_ID, API_HASH, and SESSION_STRING
```

5. Get your API credentials from https://my.telegram.org/apps and add them to `.env`

6. Run the bot:
```bash
python main.py
```

## 🎮 Available Commands

### Core Commands

- **`.alive`** - Check if userbot is running and view system info
- **`.ping`** - Check bot latency (response time in milliseconds)
- **`.reload`** - Hot reload all plugins without restarting (auto-deletes after 10s)

### Utility Commands

- **`.exec <command>`** - Execute shell commands with formatted output
  - Example: `.exec ls -la`
  - Shows command, stdout, stderr, and return code in separate code blocks

- **`.eval <python code>`** - Evaluate Python code with formatted output
  - Example: `.eval print("Hello World")`
  - Shows code and output in separate code blocks

- **`.info`** - Get information about current chat or replied message
  - Usage: `.info` (get chat info)
  - Usage: Reply to a message and use `.info` (get user info)
  - Privacy: Does not expose phone numbers

- **`.save`** - Save replied message to Saved Messages
  - Smart fallback: Tries forward first, then copy for restricted chats
  - Auto-deletes confirmation after 2 seconds

### Fun Commands

- **`.spam <count> <text>`** - Spam messages
  - Example: `.spam 10 Hello World`
  - Example: Reply to a message and use `.spam 5` (copies the message)
  - Safety: Max 100 messages, 0.5s delay between messages
  - Copy mode preserves stickers, media, etc.

## 🔧 Configuration

Edit `.env` file:

```env
API_ID=12345                    # Your Telegram API ID
API_HASH=your_api_hash          # Your Telegram API Hash
SESSION_STRING=                 # Optional: session string (leave empty for phone login)
CMD_PREFIX=.                    # Command prefix (default: .)
MAX_SPAM_COUNT=100              # Maximum spam messages
SPAM_DELAY=0.5                  # Delay between spam messages (seconds)
```

## 📁 Project Structure

```
NekoUB/
├── main.py                    # Entry point & plugin loader
├── config.py                  # Configuration loader
├── requirements.txt           # Python dependencies
├── run.sh                     # Quick start script
├── generate_session_string.py # Session string generator
├── .env                       # Your credentials (create from .env.example)
├── userbot/
│   └── utils.py               # Helper functions & colored logging
├── plugins/                   # Public plugins (tracked in git)
│   ├── alive.py               # .alive command
│   ├── exec.py                # .exec command
│   ├── eval.py                # .eval command
│   ├── info.py                # .info command
│   ├── ping.py                # .ping command
│   ├── reload.py              # .reload command
│   ├── save.py                # .save command
│   └── spam.py                # .spam command
└── private_plugins/           # Private plugins (ignored by git)
    ├── __init__.py            # (tracked)
    ├── README.md              # (tracked)
    └── *.py                   # Your private plugins (not tracked)
```

## 🔌 Adding Plugins

### Public Plugins (tracked in git)

Create a new Python file in the `plugins/` directory:

```python
# plugins/myplugin.py
from pyrogram import filters
from pyrogram.types import Message
import config
import main
from userbot.utils import get_logger

logger = get_logger(__name__)

@main.app.on_message(filters.me & filters.command("mycommand", prefixes=config.CMD_PREFIX))
async def my_command(client, message: Message):
    """My custom command."""
    await message.edit("Hello from my plugin!")
    logger.info("My command executed")
```

### Private Plugins (not tracked in git)

For personal/sensitive plugins, create them in `private_plugins/` directory:

```python
# private_plugins/secret.py
# Same structure as public plugins
# These files are automatically ignored by git
```

### Reload Plugins

After adding/modifying plugins:
- Use `.reload` command in Telegram, or
- Restart the bot with `./run.sh`

## ⚠️ Disclaimer

This userbot is for educational purposes. Using userbots may violate Telegram's Terms of Service. Use at your own risk.

## 📝 License

MIT License - Feel free to modify and distribute!

---

**Made with 💝 by NekoUB Team**

Nya~ 🐱
