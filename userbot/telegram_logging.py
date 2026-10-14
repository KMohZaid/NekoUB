"""Forward the userbot's logs to a Telegram chat.

Enable it by setting ``DEBUG_CHAT_ID`` (the target chat/group id) in ``.env``.
Logs produced before the client connects are buffered and flushed once the
client is online. Forwarding is guarded so that the act of sending a log
never forwards its own logs (no feedback loop).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import deque
from datetime import datetime

from pyrogram.enums import ParseMode

import config
from userbot.utils import get_logger

logger = get_logger(__name__)

MAX_BUFFER = 2000  # remember this many lines while offline
MAX_LINE = 3000
MAX_MESSAGE = 3500
POLL_SECONDS = 2
# Never forward these loggers (avoids loops / connection+HTTP spam).
SKIP_LOGGERS = ("pyrogram", "httpx", "httpcore", "userbot.telegram_logging")


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        name = record.name
        if name.startswith("plugins."):
            name = name[len("plugins.") :]
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        line = f"[{stamp}] {record.levelname:<8} {name}: {message}"
        if len(line) > MAX_LINE:
            line = line[: MAX_LINE - 1] + "…"
        return line


class TelegramLogHandler(logging.Handler):
    def __init__(self, chat_id: int, level: int = logging.INFO):
        super().__init__(level)
        self.chat_id = chat_id
        self.setFormatter(_Formatter())
        self._buffer: deque[str] = deque(maxlen=MAX_BUFFER)
        self._sending = False

    def filter(self, record: logging.LogRecord) -> bool:
        if self._sending:
            return False
        for prefix in SKIP_LOGGERS:
            if record.name.startswith(prefix):
                return False
        return True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._buffer.append(self.format(record))
        except Exception:
            pass

    def drain(self) -> list[str]:
        batch: list[str] = []
        size = 0
        while self._buffer and size < MAX_MESSAGE:
            line = self._buffer.popleft()
            batch.append(line)
            size += len(line) + 1
        return batch

    async def run(self, client) -> None:
        while True:
            try:
                if getattr(client, "is_connected", False):
                    batch = self.drain()
                    if batch:
                        self._sending = True
                        try:
                            await client.send_message(
                                self.chat_id,
                                "\n".join(batch)[:MAX_MESSAGE],
                                parse_mode=ParseMode.DISABLED,
                            )
                        finally:
                            self._sending = False
            except Exception as exc:  # never let forwarding crash the bot
                print(
                    f"[telegram_logging] send failed: {exc}", file=sys.stderr
                )
            await asyncio.sleep(POLL_SECONDS)


def install(loop, client) -> TelegramLogHandler | None:
    """Attach the log forwarder (no-op when DEBUG_CHAT_ID is unset)."""
    chat_id = getattr(config, "DEBUG_CHAT_ID", 0)
    if not chat_id:
        return None

    level = getattr(
        logging, getattr(config, "DEBUG_LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    handler = TelegramLogHandler(chat_id, level)
    handler.setLevel(level)

    root = logging.getLogger()
    if level < root.level:
        root.setLevel(level)
    root.addHandler(handler)

    loop.create_task(handler.run(client))
    logger.info(
        f"🐱 Log forwarding -> chat {chat_id} "
        f"(level {logging.getLevelName(level)})"
    )
    return handler
