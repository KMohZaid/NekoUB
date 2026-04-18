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

# Spam settings
MAX_SPAM_COUNT = int(os.getenv("MAX_SPAM_COUNT", "100"))
SPAM_DELAY = float(os.getenv("SPAM_DELAY", "0.5"))

# Client customization
CLIENT_NAME = os.getenv("CLIENT_NAME", "NekoUB")
APP_VERSION = os.getenv("APP_VERSION", "NekoUB 1.0.0")
WORKERS = int(os.getenv("WORKERS", "8"))
PARSE_MODE = os.getenv("PARSE_MODE", "markdown").lower()

# Validate required settings
if API_ID == 0 or not API_HASH:
    raise ValueError(
        "API_ID and API_HASH are required! "
        "Please copy .env.example to .env and fill in your credentials."
    )
