# ⚙️ NekoUB Configuration Guide

This guide explains all available configuration options for customizing your NekoUB userbot.

## 📝 Configuration File (.env)

All settings are configured in the `.env` file. Copy `.env.example` to `.env` and customize:

```bash
cp .env.example .env
```

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

Generate using: `python generate_session_string.py`

## 🎨 Client Customization

### Client Name
```env
CLIENT_NAME=NekoUB
```
The internal name for your client. This is used for session file naming and internal references.

**Options:**
- `NekoUB` - Default, matches the theme
- Any custom name you want

### App Version
```env
APP_VERSION=NekoUB 1.0.0
```
The version string displayed to Telegram servers.

**Options:**
- `NekoUB 1.0.0` - Default custom version
- `Telegram Desktop 5.8.4` - Mimic official client
- Any custom version string

### Workers
```env
WORKERS=8
```
Number of concurrent workers for handling updates. Higher = faster but more resource usage.

**Recommended values:**
- `4` - Low resource usage (VPS/shared hosting)
- `8` - Balanced (default)
- `16` - High performance (dedicated server)
- `32` - Maximum performance (powerful systems only)

### Parse Mode
```env
PARSE_MODE=markdown
```
Default text formatting mode for messages.

**Options:**
- `markdown` or `md` - Markdown syntax (default)
- `html` - HTML tags
- `combined` or `default` - Both Markdown and HTML
- `disabled` or `none` - No automatic parsing

## 🎮 Bot Settings

### Command Prefix
```env
CMD_PREFIX=.
```
The prefix character for bot commands.

**Options:**
- `.` - Dot (default)
- `!` - Exclamation mark
- `/` - Slash
- Any single character

## 🔌 Integrations

### Todoist API Token
```env
TODOIST_API_TOKEN=your_todoist_token_here
```

Optional token used by the `.todo` command plugin to create tasks in Todoist.
Generate or copy your API token from Todoist settings.

## 🛡️ Safety Settings

### Spam Limits
```env
MAX_SPAM_COUNT=100
SPAM_DELAY=0.5
```

- `MAX_SPAM_COUNT` - Maximum messages for `.spam` command (default: 100)
- `SPAM_DELAY` - Delay between spam messages in seconds (default: 0.5)

**Recommendations:**
- Don't set MAX_SPAM_COUNT above 200 to avoid flood bans
- Keep SPAM_DELAY at least 0.3 seconds
- Lower delays = higher flood risk

## 🔧 Advanced Settings

These settings are available but not included in `.env.example`. Add them if needed:

### Device Model
```env
DEVICE_MODEL=Custom Device
```
The device model reported to Telegram. Defaults to Python version if not set.

### System Version
```env
SYSTEM_VERSION=Linux 6.0
```
Operating system version. Defaults to actual OS if not set.

### Language Settings
```env
LANG_CODE=en
SYSTEM_LANG_CODE=en
```
Language codes in ISO 639-1 format (default: en).

### IPv6
```env
USE_IPV6=False
```
Set to `True` to use IPv6 instead of IPv4.

### Proxy
```env
PROXY_SCHEME=socks5
PROXY_HOSTNAME=127.0.0.1
PROXY_PORT=1080
PROXY_USERNAME=user
PROXY_PASSWORD=pass
```
Configure SOCKS5 or HTTP proxy (optional).

### Sleep Threshold
```env
SLEEP_THRESHOLD=10
```
Flood wait threshold in seconds. Requests with lower flood waits will auto-retry (default: 10).

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
```

### Stealth Mode (Mimic Official Client)
```env
# Required
API_ID=12345
API_HASH=abc123def456
SESSION_STRING=your_session_string

# Mimic Telegram Desktop
CLIENT_NAME=Telegram Desktop
APP_VERSION=Telegram Desktop 5.8.4
DEVICE_MODEL=PC
SYSTEM_VERSION=Windows 10
WORKERS=8
PARSE_MODE=default
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

**Note:** Configuration changes in `.env` require a full restart. The `.reload` command only reloads plugins, not core configuration.

## ⚠️ Important Notes

- Never commit your `.env` file to version control (it's in `.gitignore`)
- Keep your `SESSION_STRING` secure - it provides full account access
- Invalid settings will cause the bot to fail at startup with an error message
- Some settings (like `CLIENT_NAME`) affect the session file name

---

**Made with 💝 by NekoUB**

Nya~ 🐱
