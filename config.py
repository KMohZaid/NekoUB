import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Telegram API credentials
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")

# Bot settings
CMD_PREFIX = os.getenv("CMD_PREFIX", ".")
TODOIST_API_TOKEN = os.getenv("TODOIST_API_TOKEN", "")

# Web search / page reader (.web, .open, .read)
BROWSER_API_URL = os.getenv(
    "BROWSER_API_URL", "https://ydc-index.io/v1/search"
)
BROWSER_API_KEY = os.getenv("BROWSER_API_KEY", "")
BROWSER_API_KEY_HEADER = os.getenv("BROWSER_API_KEY_HEADER", "X-API-Key")

# AI chat (.ai) - any OpenAI-compatible /chat/completions endpoint
AI_API_URL = os.getenv("AI_API_URL", "http://127.0.0.1:8787/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "groq/openai/gpt-oss-120b")
AI_SYSTEM_PROMPT = os.getenv(
    "AI_SYSTEM_PROMPT", "You are a helpful, concise assistant."
)

# .kang media conversion (image/GIF/video -> sticker). Empty = auto-detect.
FFMPEG_BINARY = os.getenv("FFMPEG_BINARY", "")

# SangMata name-history lookup (.sg)
SANGMATA_BOT = os.getenv("SANGMATA_BOT", "SangMataInfo_bot")

# Quotly quote stickers (.q)
QUOTE_API_URL = os.getenv(
    "QUOTE_API_URL", "https://quote.nubcoders.com/generate"
)

# Translate (.tr) - Google gtx endpoint (no API key required)
TRANSLATE_API_URL = os.getenv(
    "TRANSLATE_API_URL",
    "https://translate.googleapis.com/translate_a/single",
)
TRANSLATE_TARGET = os.getenv("TRANSLATE_TARGET", "en")

# Text to speech (.tts) - Microsoft Edge neural voices via edge-tts
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-AriaNeural")

# Spam settings
MAX_SPAM_COUNT = int(os.getenv("MAX_SPAM_COUNT", "100"))
SPAM_DELAY = float(os.getenv("SPAM_DELAY", "0.5"))

# Client customization
CLIENT_NAME = os.getenv("CLIENT_NAME", "NekoUB")
APP_VERSION = os.getenv("APP_VERSION", "NekoUB 1.0.0")
WORKERS = int(os.getenv("WORKERS", "8"))
PARSE_MODE = os.getenv("PARSE_MODE", "markdown").lower()

# Sticker set name/title format
# Placeholders: {user_id}, {first_name}, {username}, {base}, {suffix}, {index}
# Base defaults to "@<username>" (or first name); per-command suffixes give
# titles like "@username fun".
STICKERSET_NAME_FORMAT = os.getenv("STICKERSET_NAME_FORMAT", "")
STICKERSET_TITLE_FORMAT = os.getenv("STICKERSET_TITLE_FORMAT", "")
# Optional override for the base pack name/prefix (empty => @username).
STICKERSET_BASE = os.getenv("STICKERSET_BASE", "")

# Validate required settings
if API_ID == 0 or not API_HASH:
    raise ValueError(
        "API_ID and API_HASH are required! "
        "Please copy .env.example to .env and fill in your credentials."
    )
