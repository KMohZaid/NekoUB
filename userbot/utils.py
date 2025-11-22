import logging
import sys
import os
from typing import Optional
from functools import wraps
from colorama import Fore, Style, init

# Initialize colorama for cross-platform colored output
# Force colors in Docker environments
force_color = os.getenv('FORCE_COLOR', '0') == '1'
init(autoreset=True, strip=not force_color if force_color else None)


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for different log levels."""

    COLORS = {
        'DEBUG': Fore.CYAN,
        'INFO': Fore.GREEN,
        'WARNING': Fore.YELLOW,
        'ERROR': Fore.RED,
        'CRITICAL': Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        # Add color to levelname
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{
                levelname}{Style.RESET_ALL}"

        # Add color to name (logger name)
        record.name = f"{Fore.MAGENTA}{record.name}{Style.RESET_ALL}"

        # Format timestamp
        if self.usesTime():
            record.asctime = f"{Fore.BLUE}{self.formatTime(record, self.datefmt)}{
                Style.RESET_ALL}"

        return super().format(record)


# Configure logging with colored formatter
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColoredFormatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))

logging.basicConfig(
    level=logging.INFO,
    handlers=[handler]
)

logger = logging.getLogger(__name__)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name."""
    return logging.getLogger(name)


def format_output(text: str, max_length: int = 4000) -> str:
    """Format output text, truncating if necessary."""
    if len(text) > max_length:
        return text[:max_length] + "\n... (output truncated)"
    return text


def code_format(text: str, language: str = "") -> str:
    """Format text as code block for Telegram."""
    return f"```{language}\n{text}\n```"


def continue_propagation(func):
    """Decorator to automatically continue message propagation after handler execution.

    This allows multiple handlers with overlapping filters to process the same update
    without needing to use different handler groups.

    Usage:
        @app.on_message(filters.text)
        @continue_propagation
        async def handler1(client, message):
            print("Handler 1")

        @app.on_message(filters.text)
        async def handler2(client, message):
            print("Handler 2")

    Both handlers will execute for text messages.
    """
    @wraps(func)
    async def wrapper(client, message):
        try:
            return await func(client, message)
        finally:
            message.continue_propagation()
    return wrapper
