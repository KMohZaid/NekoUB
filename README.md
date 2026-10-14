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
- 🤖 **AI assistant** - `.ai` chat, web-search summarisation and per-chat model/system prompts
- 🌐 **Web tools** - `.web` search plus `.open` / `.read` page reading
- 🖼️ **Stickers** - `.kang` stickers, photos, GIFs and videos into your own packs
- 💬 **Quote stickers** - `.q` Quotly quote stickers from any message
- 🌍 **Translate** - `.tr` text, replies and voice notes
- 🗣️ **Text to speech** - `.tts` natural voice notes (Microsoft Edge voices)
- 🕵️ **SangMata** - `.sg` look up a user's past names and usernames
- ⏰ **Automations** - `.am` keyword, reply, tag and scheduled auto-messages
- ✅ **Todoist** - `.todo` create tasks from chat

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

> `.kang` video stickers and `.tts` voice notes need **ffmpeg** on your
> `PATH`. `ffmpeg` is auto-detected; override the path with `FFMPEG_BINARY`
> in `.env` if needed.

## 🎮 Available Commands

> Inline buttons are a bot-only Telegram feature; a userbot sends as a regular
> account, so `.help` is navigated with text commands.

### 🧭 Core

- **`.help`** - Text-based help menu with all commands (aliases: `.commands`, `.cmds`, `.menu`)
  - Usage: `.help`, `.help <n>`, `.help <category>`, `.help <category> <n>`, `.help <command>`, `.help all`
- **`.alive`** - Check if userbot is running and view system info
- **`.ping`** - Check bot latency (response time in milliseconds)
- **`.reload`** - Hot reload all plugins without restarting (auto-deletes after 10s)

### 🤖 AI

- **`.ai <prompt>`** - Chat with the configured AI model (alias: `.ask`)
  - Example: `.ai explain quantum tunneling simply`
  - Keeps a short per-chat conversation, so follow-ups have context
  - Reply to any message with `.ai` (optionally with extra instructions) to send it as the prompt
- **`.ai search <query>`** - Search the web and have the AI summarize the results with `[n]` citations and a source list
  - Example: `.ai search latest ai news`
- **`.ai reset`** - Clear this chat's conversation history
- **`.ai model [name]`** - Show or set the model for this chat
- **`.ai sys [text]`** - Show or set the system prompt for this chat

Talks to any OpenAI-compatible `chat/completions` endpoint. Defaults to a local
synckey gateway (`http://127.0.0.1:8787/v1`) with model `groq/openai/gpt-oss-120b`.

### 🌐 Web

- **`.web <query>`** - Search the web and list results (alias: `.search`)
  - Results are numbered; navigate with `.open <n>`, `.web next`, `.web prev`
  - Or reply to the results message with just a number to open that result
- **`.web next` / `.web prev`** - Page through the last search (aliases: `.next`, `.prev`)
- **`.open <n>`** - Read result number `<n>` from the last search as plain text
- **`.read <url>`** - Fetch any URL and show it as plain text (aliases: `.browse`, `.url`)

Uses a configurable search endpoint (defaults to a You.com-compatible API).

### 🧰 Utility

- **`.exec <command>`** - Execute shell commands with formatted output
  - Example: `.exec ls -la`
- **`.eval <python code>`** - Evaluate Python code (`await` is supported)
  - Example: `.eval print("Hello World")`
- **`.kill`** - Stop all currently running `.exec` / `.eval` tasks
- **`.info`** - Get info about the current chat, or the sender of the replied message (no phone numbers)
- **`.id`** - Show chat ID, user ID and media file IDs of the replied message
- **`.json`** - Export the replied message as a JSON file
- **`.save`** - Save the replied message to Saved Messages (forward, falls back to copy)
- **`.sg`** - Look up a user's past names and usernames via SangMata (alias: `.sangmata`)
  - Usage: reply to the user, or `.sg @username` / `.sg <user_id>`
- **`.tr [lang] <text>`** - Translate text, a replied message, or a replied voice/audio note (alias: `.translate`)
  - Voice notes are transcribed with Groq Whisper (see `GROQ_API_KEY` / synckey fallback)
  - Example: `.tr Hello`, `.tr es Hello`, `.tr hi` (reply)
- **`.tts <text>`** - Convert text or a replied message into a voice note (alias: `.say`)
  - Voice is set by `TTS_VOICE`; Example: `.tts Hello there`, `.tts` (reply)

### ⏰ Automations

- **`.am <match> on <output>`** - Keyword trigger: send `<output>` when someone says `<match>` in this chat
  - Flags: `-tag`/`-notag` (mention the sender or not, default off), `-reply` (send as a reply), `-exact` (whole-word match)
  - Example: `.am hello on Hi there!`, `.am hi on Hello! -tag`
- **`.am <match> reply <output>`** - Reply trigger: send `<output>` when someone replies to you (optionally only if their message contains `<match>`)
  - Use the `tag`/`mention` trigger instead to also fire when you are tagged: `.am /ft tag /plant hdsfshfg -notag`
  - Example: `.am reply On it!`, `.am boss reply Yes sir?`
- **`.am every <time> <output>`** - Repeat `<output>` every `<time>` (`30s`, `15m`, `1h`, `1d`, or `30 minutes`)
  - Example: `.am every 1h Stand up!`
- **`.am at <HH:MM> <output>`** - Send `<output>` every day at `<HH:MM>` (24h) or `9:00 am`, in the server's local timezone
  - Example: `.am at 09:00 Good morning`
- **`.am in <time> <output>`** - Send `<output>` once, after `<time>`
  - Example: `.am in 10m On my way`
- **`.am list`** - List this chat's automations
- **`.am del <id>`** - Delete one automation
- **`.am on <id>` / `.am off <id>`** - Enable / disable one automation
- **`.am clear`** - Delete all automations in this chat

Automations are **per chat**, stored in `.automessages.json`, and reloaded on restart so schedules survive. Run `.am` with no arguments for full help.

### 🎉 Fun / Spam

- **`.spam <count> <text>`** - Send a message repeatedly, or copy the replied message
  - Flags: `-c/-count <n>`, `-d/-delay <10s|5m|1h>`, `-del/-delete`, `-r/-reply`, `-ignore/-nolimit`
  - Example: `.spam 10 Hello World`, `.spam -d 5s 3 Hi`
- **`.spam_stop`** - Stop the active spam in this chat
- **`.spam_stat`** - Show progress of the active spam in this chat
- **`.spam_help`** - Show detailed spam help

### 🖼️ Stickers

- **`.kang`** - Copy the replied sticker, image, GIF or video into your default auto pack
  - Photos become static stickers; GIFs and videos become video stickers
  - Example: `.kang`, `.kang 🐱`, `.kang -a 🐱` (append emoji)
- **`.kang <pack>`** - Add to a specific pack (number from `.packs`, short name, id, or suffix)
  - Example: `.kang 1`, `.kang fun`, `.kang fun 🐱`
- **`.q`** - Turn the replied message into a quote sticker (alias: `.quote`)
  - `.qr` also includes the replied-to message; a `#hex` or named colour sets the background; extra text overrides the quoted text
  - Example: `.q` (reply), `.q #ff0000 Nice`, `.qr` (reply)
- **`.packs`** - List every sticker pack you own with number, short name, id and count (alias: `.mypacks`)
- **`.start_sticker_monitor`** - Auto-kang every sticker you send in this chat
- **`.stop_sticker_monitor`** - Stop the sticker monitor in all chats
- **`.help_sticker`** - Show the sticker plugin's detailed help

### ✅ Todoist

- **`.todo <content> [#pN] [#due |<date>|]`** - Create a Todoist task in your inbox
  - Reply to a message to attach its context to the task description
  - Example: `.todo Buy milk`, `.todo Pay bill #p1`
- **`.todo list [page]`** - List the tasks added from Telegram (10 per page)
  - Example: `.todo list`, `.todo list 2`

## 🔧 Configuration

Edit the `.env` file (see [CONFIGURATION.md](CONFIGURATION.md) for the full guide):

```env
# Required
API_ID=12345                     # Your Telegram API ID
API_HASH=your_api_hash           # Your Telegram API Hash
SESSION_STRING=                  # Session string (from generate_session_string.py)

# Client
CMD_PREFIX=.                     # Command prefix (default: .)
CLIENT_NAME=NekoUB
APP_VERSION=NekoUB 1.0.0
WORKERS=8
PARSE_MODE=markdown

# Spam safety
MAX_SPAM_COUNT=100
SPAM_DELAY=0.5

# Stickers
STICKERSET_BASE=                 # Optional pack base name (empty => @username)

# Web search (.web)
BROWSER_API_URL=https://ydc-index.io/v1/search
BROWSER_API_KEY=
BROWSER_API_KEY_HEADER=X-API-Key

# AI (.ai)
AI_API_URL=http://127.0.0.1:8787/v1
AI_API_KEY=
AI_MODEL=groq/openai/gpt-oss-120b

# Media / TTS
FFMPEG_BINARY=                   # Optional ffmpeg path (auto-detected)
TTS_VOICE=en-US-AriaNeural

# Quote stickers (.q)
QUOTE_API_URL=https://quote.nubcoders.com/generate

# Translate (.tr)
TRANSLATE_TARGET=en
GROQ_API_KEY=                    # Optional; falls back to synckey keys
TRANSCRIBE_MODEL=whisper-large-v3

# SangMata (.sg)
SANGMATA_BOT=SangMata_BOT

# Debug log forwarding
DEBUG_CHAT_ID=                   # Forward logs to this chat (empty = off)
DEBUG_LOG_LEVEL=INFO

# Todoist (.todo)
TODOIST_API_TOKEN=
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
│   ├── utils.py               # Helpers & colored logging
│   ├── media.py               # Image/video -> sticker conversion (.kang)
│   ├── websearch.py           # Web search / page reader (.web, .read)
│   └── synckeys.py            # Reuse synckey Groq keys (.tr voice)
├── plugins/                   # Public plugins (tracked in git)
│   ├── ai.py                  # .ai chat/search
│   ├── alive.py               # .alive
│   ├── automessages.py        # .am automations
│   ├── eval.py                # .eval
│   ├── exec.py                # .exec
│   ├── help.py                # .help menu
│   ├── id.py                  # .id
│   ├── info.py                # .info
│   ├── json.py                # .json
│   ├── kill.py                # .kill
│   ├── ping.py                # .ping
│   ├── quote.py               # .q quote stickers
│   ├── reload.py              # .reload
│   ├── sangmata.py            # .sg name history
│   ├── save.py                # .save
│   ├── spam.py                # .spam
│   ├── sticker.py             # .kang / .packs
│   ├── todoist.py             # .todo
│   ├── translate.py           # .tr
│   ├── tts.py                 # .tts
│   └── web.py                 # .web / .open / .read
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
