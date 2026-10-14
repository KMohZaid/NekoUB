# ⚙️ NekoUB Configuration Guide

This guide explains all available configuration options for customizing your NekoUB userbot.

## 📝 Configuration File (.env)

All settings are configured in the `.env` file. Copy `.env.example` to `.env` and customize:

```bash
cp .env.example .env
```

Only two settings are required (`API_ID`, `API_HASH`); everything else has a
sensible default.

## 🔑 Required Settings

### Telegram API Credentials

```env
API_ID=12345
API_HASH=your_api_hash_here
```

Get these from: https://my.telegram.org/apps

### Session String

```env
SESSION_STRING=your_session_string_here
```

Generate using: `python generate_session_string.py` (it writes the value into
`.env` for you).

## 🎨 Client Customization

### Client Name
```env
CLIENT_NAME=NekoUB
```
The internal name for your client.

### App Version
```env
APP_VERSION=NekoUB 1.0.0
```
The version string reported to Telegram.

### Workers
```env
WORKERS=8
```
Concurrent workers for handling updates. `4` for a small VPS, `8` balanced
(default), `16`+ for a dedicated server.

### Parse Mode
```env
PARSE_MODE=markdown
```
Default formatting mode: `markdown`/`md` (default), `html`, `combined`/`default`,
or `disabled`/`none`.

## 🎮 Bot Settings

### Command Prefix
```env
CMD_PREFIX=.
```
The command prefix. Defaults to `.`; any character works (`!`, `/`, ...).

## 🛡️ Safety Settings

### Spam Limits
```env
MAX_SPAM_COUNT=100
SPAM_DELAY=0.5
```

- `MAX_SPAM_COUNT` - Maximum messages for `.spam` (default: 100)
- `SPAM_DELAY` - Delay between spam messages in seconds (default: 0.5)

Keep `MAX_SPAM_COUNT` at or below 200 and `SPAM_DELAY` at least `0.3` to avoid
flood bans.

## 🌐 Web Search (.web, .open, .read)

```env
BROWSER_API_URL=https://ydc-index.io/v1/search
BROWSER_API_KEY=
BROWSER_API_KEY_HEADER=X-API-Key
```

Any You.com-compatible search endpoint works. `BROWSER_API_KEY_HEADER` is the
header that carries the key (e.g. `X-API-Key`).

## 🤖 AI (.ai)

```env
AI_API_URL=http://127.0.0.1:8787/v1
AI_API_KEY=
AI_MODEL=groq/openai/gpt-oss-120b
AI_SYSTEM_PROMPT=You are a helpful, concise assistant.
```

Any OpenAI-compatible `chat/completions` endpoint. The default points at a local
[synckey](https://github.com/) gateway. Per-chat model and system prompt can be
overridden at runtime with `.ai model` and `.ai sys`.

## 🖼️ Stickers (.kang, .packs)

```env
STICKERSET_BASE=
STICKERSET_NAME_FORMAT=
STICKERSET_TITLE_FORMAT=
```

- `STICKERSET_BASE` - override the pack base name. Empty uses `@<username>`
  (falling back to the first name).
- `STICKERSET_NAME_FORMAT` / `STICKERSET_TITLE_FORMAT` - templates for pack
  short names / titles. Placeholders: `{user_id}`, `{first_name}`, `{username}`,
  `{base}`, `{suffix}`, `{index}`.

## 🎬 Media & TTS

```env
FFMPEG_BINARY=
TTS_VOICE=en-US-AriaNeural
```

- `FFMPEG_BINARY` - path to `ffmpeg`. Empty auto-detects it on `PATH` (then
  `~/.local/bin/ffmpeg`). Required for `.kang` video stickers and `.tts`.
- `TTS_VOICE` - Microsoft Edge neural voice for `.tts`. Run
  `edge-tts --list-voices` for options (e.g. `hi-IN-SwaraNeural`,
  `en-IN-NeerjaNeural`).

## 💬 Quote Stickers (.q)

```env
QUOTE_API_URL=https://quote.nubcoders.com/generate
```

A Quotly-compatible quote generator endpoint.

## 🌍 Translate (.tr)

```env
TRANSLATE_API_URL=https://translate.googleapis.com/translate_a/single
TRANSLATE_TARGET=en
```

- `TRANSLATE_API_URL` - Google `gtx` endpoint (no API key required).
- `TRANSLATE_TARGET` - default target language code (e.g. `en`, `hi`, `es`).
  A language code passed on the command line (`.tr hi ...`) overrides it.

### Voice transcription for `.tr`

Voice/audio notes are transcribed with **Groq Whisper** before translation.

```env
GROQ_API_URL=https://api.groq.com/openai/v1/audio/transcriptions
GROQ_API_KEY=
GROQ_API_KEYS=
TRANSCRIBE_MODEL=whisper-large-v3
```

- `TRANSCRIBE_MODEL` - `whisper-large-v3` (multilingual, default),
  `whisper-large-v3-turbo` (faster/cheaper), or `distil-whisper-large-v3-en`
  (English only).
- `GROQ_API_KEY` / `GROQ_API_KEYS` (comma-separated) - set explicitly, **or**
  leave empty to automatically reuse the enabled Groq keys already stored by
  the local synckey gateway (`~/.synckey`).

> The synckey gateway itself only proxies `chat/completions`, so audio is sent
> directly to Groq.

## 🕵️ SangMata (.sg)

```env
SANGMATA_BOT=SangMata_BOT
```

The bot used for name-history lookups. The userbot sends the target's numeric
id as a plain message. You may need to `/start` it from the userbot account once.

## ⏰ Automations (.am)

No configuration required. Automations are created per chat and stored in
`.automessages.json` at the project root (like `.sticker_cache.json`). Time
triggers (`every` / `at` / `in`) use the server's local timezone and keep
running across restarts.

## 🐞 Debug Log Forwarding

```env
DEBUG_CHAT_ID=
DEBUG_LOG_LEVEL=INFO
```

- `DEBUG_CHAT_ID` - a chat/group id to forward the bot's logs to (negative for
  groups). Leave empty to disable. Logs written before the client connects are
  buffered and flushed once it is online.
- `DEBUG_LOG_LEVEL` - minimum level to forward: `DEBUG`, `INFO` (default),
  `WARNING` or `ERROR`.

## ✅ Todoist (.todo)

```env
TODOIST_API_TOKEN=your_todoist_token_here
```

Optional token used by the `.todo` command to create tasks. Copy it from your
Todoist settings.

## 📊 Example Configurations

### Minimal Configuration (Default)
```env
API_ID=12345
API_HASH=abc123def456
SESSION_STRING=your_session_string
```

### Fully Customized
```env
# Required
API_ID=12345
API_HASH=abc123def456
SESSION_STRING=your_session_string

# Customization
CLIENT_NAME=MyNekoBot
APP_VERSION=NekoUB 2.0.0
WORKERS=16
PARSE_MODE=markdown

# Bot Settings
CMD_PREFIX=!

# Safety
MAX_SPAM_COUNT=50
SPAM_DELAY=1.0

# AI + web
BROWSER_API_KEY=your_you_api_key
AI_API_KEY=your_synckey_key

# Voice
TRANSLATE_TARGET=hi
TTS_VOICE=hi-IN-SwaraNeural
```

## 🔄 Applying Changes

After editing `.env`:

1. **Restart the bot:**
   ```bash
   # Stop current instance (Ctrl+C)
   python main.py
   ```

2. **Or use hot reload** (for plugin changes only, not config):
   ```
   .reload
   ```

**Note:** Configuration changes in `.env` require a full restart. The `.reload`
command only reloads plugins, not core configuration.

## ⚠️ Important Notes

- Never commit your `.env` file to version control (it's in `.gitignore`)
- Keep your `SESSION_STRING` secure - it provides full account access
- Invalid settings will cause the bot to fail at startup with an error message

---

**Made with 💝 by NekoUB**

Nya~ 🐱
