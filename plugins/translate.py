import httpx
from pyrogram import filters
from pyrogram.types import Message

import config
import main
from userbot.utils import continue_propagation, get_logger

logger = get_logger(__name__)

KNOWN_LANGS = {
    "af", "ar", "az", "be", "bg", "bn", "ca", "cs", "cy", "da", "de", "el",
    "en", "eo", "es", "et", "eu", "fa", "fi", "fr", "ga", "gl", "gu", "ha",
    "he", "hi", "hr", "hu", "hy", "id", "is", "it", "ja", "jv", "ka", "kk",
    "km", "kn", "ko", "ku", "ky", "la", "lo", "lt", "lv", "mk", "ml", "mn",
    "mr", "ms", "mt", "my", "ne", "nl", "no", "pa", "pl", "ps", "pt", "ro",
    "ru", "si", "sk", "sl", "so", "sq", "sr", "su", "sv", "sw", "ta", "te",
    "th", "tl", "tr", "uk", "ur", "uz", "vi", "zh", "zu", "zh-cn", "zh-tw",
}


async def translate_text(
    text: str, target: str, source: str = "auto"
) -> tuple[str, str | None]:
    params = {
        "client": "gtx",
        "sl": source,
        "tl": target,
        "dt": "t",
        "q": text,
    }
    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.get(config.TRANSLATE_API_URL, params=params)
        response.raise_for_status()
        data = response.json()

    segments = data[0] if data and data[0] else []
    translated = "".join(seg[0] for seg in segments if seg and seg[0])
    detected = data[2] if len(data) > 2 else None
    return translated, detected


@main.app.on_message(
    filters.me
    & filters.command(["tr", "translate"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def translate_command(client, message: Message):
    parts = (message.text or "").split(maxsplit=1)
    args = parts[1].strip() if len(parts) > 1 else ""

    target = config.TRANSLATE_TARGET
    text = args
    tokens = args.split(maxsplit=1)
    if tokens and tokens[0].lower() in KNOWN_LANGS:
        target = tokens[0].lower()
        text = tokens[1] if len(tokens) > 1 else ""

    reply = message.reply_to_message
    if not text and reply:
        text = reply.text or reply.caption or ""

    if not text:
        await message.edit(
            "Provide text or reply to a message.\n"
            f"`{config.CMD_PREFIX}tr [lang] <text>`"
        )
        return

    await message.edit("🌐 Translating...")
    try:
        translated, detected = await translate_text(text, target)
    except Exception as exc:
        logger.error(f"[TRANSLATE] Failed: {exc}")
        await message.edit(f"Translation failed: {type(exc).__name__}")
        return

    if not translated:
        await message.edit("Could not translate that text.")
        return

    header = f"**Translated to {target}**"
    if detected and detected != target:
        header += f" (from {detected})"
    await message.edit(f"{header}:\n\n{translated}")
