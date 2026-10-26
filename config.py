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
SANGMATA_BOT = os.getenv("SANGMATA_BOT", "SangMata_BOT")

# Translate (.tr) - Google gtx endpoint (no API key required)
TRANSLATE_API_URL = os.getenv(
    "TRANSLATE_API_URL",
    "https://translate.googleapis.com/translate_a/single",
)
TRANSLATE_TARGET = os.getenv("TRANSLATE_TARGET", "en")

# Text to speech (.tts) - Microsoft Edge neural voices via edge-tts
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-AriaNeural")

# Voice-note transcription for .tr (Groq Whisper).
# GROQ_API_URL -> Groq OpenAI-compatible transcriptions endpoint.
# TRANSCRIBE_MODEL -> whisper-large-v3 (multilingual) or
#   whisper-large-v3-turbo (faster/cheaper).
GROQ_API_URL = os.getenv(
    "GROQ_API_URL", "https://api.groq.com/openai/v1/audio/transcriptions"
)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API_KEYS = [
    key.strip()
    for key in os.getenv("GROQ_API_KEYS", "").split(",")
    if key.strip()
]
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "whisper-large-v3")

# Spam settings
MAX_SPAM_COUNT = int(os.getenv("MAX_SPAM_COUNT", "100"))
SPAM_DELAY = float(os.getenv("SPAM_DELAY", "0.5"))

# Client customization
CLIENT_NAME = os.getenv("CLIENT_NAME", "NekoUB")
APP_VERSION = os.getenv("APP_VERSION", "NekoUB 1.0.0")
WORKERS = int(os.getenv("WORKERS", "8"))
PARSE_MODE = os.getenv("PARSE_MODE", "markdown").lower()

# Debug logging: forward logs to a Telegram chat (empty/0 => disabled).
_debug_chat = os.getenv("DEBUG_CHAT_ID", "").strip()
DEBUG_CHAT_ID = int(_debug_chat) if _debug_chat else 0
DEBUG_LOG_LEVEL = os.getenv("DEBUG_LOG_LEVEL", "INFO").upper()

# Sticker set name/title format
# Placeholders: {user_id}, {first_name}, {username}, {base}, {suffix}, {index}
# STICKERSET_NAME_FORMAT is used exactly as formatted; no volume or user-id
# suffix is added automatically. Base defaults to "@<username>" (or first name).
STICKERSET_NAME_FORMAT = os.getenv("STICKERSET_NAME_FORMAT", "")
STICKERSET_TITLE_FORMAT = os.getenv("STICKERSET_TITLE_FORMAT", "")
THEMED_STICKERSET_SHORTNAME = os.getenv("THEMED_STICKERSET_SHORTNAME", "")
THEMED_STICKERSET_DISPLAY_NAME = os.getenv("THEMED_STICKERSET_DISPLAY_NAME", "")
# Optional override for the base pack name/prefix (empty => @username).
STICKERSET_BASE = os.getenv("STICKERSET_BASE", "")

# Validate required settings
if API_ID == 0 or not API_HASH:
    raise ValueError(
        "API_ID and API_HASH are required! "
        "Please copy .env.example to .env and fill in your credentials."
    )
