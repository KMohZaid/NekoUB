"""Read provider API keys from the local synckey store (best effort).

synckey seals its provider credentials with Fernet in ~/.synckey/synckey.db,
using the key in ~/.synckey/secret.key. This module decrypts enabled keys for a
provider so the userbot can reuse them (e.g. Groq keys for Whisper in `.tr`).

Everything is wrapped so a missing store, a changed secret key or a schema
change simply yields no keys instead of breaking the plugin.
"""

import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from utils import get_logger

logger = get_logger(__name__)

SYNCKEY_HOME = Path.home() / ".synckey"
SECRET_PATH = SYNCKEY_HOME / "secret.key"
DB_PATH = SYNCKEY_HOME / "synckey.db"


def load_keys(provider: str = "groq") -> list[str]:
    """Return enabled, decryptable API keys for a provider from synckey."""
    if not (SECRET_PATH.exists() and DB_PATH.exists()):
        return []

    try:
        fernet = Fernet(SECRET_PATH.read_bytes())
        connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT secret FROM keys "
                "WHERE provider = ? AND enabled = 1 "
                "ORDER BY weight DESC, id ASC",
                (provider,),
            ).fetchall()
        finally:
            connection.close()
    except Exception as exc:
        logger.debug(f"[SYNCKEY] Could not read keys: {exc}")
        return []

    keys: list[str] = []
    for (secret,) in rows:
        try:
            value = fernet.decrypt(secret).decode("utf-8").strip()
        except (InvalidToken, TypeError, AttributeError):
            continue
        if value:
            keys.append(value)
    return keys
