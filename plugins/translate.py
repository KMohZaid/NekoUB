import tempfile
from pathlib import Path

import httpx
from pyrogram import Client
from pyrogram.types import Message

import config
from core.plugins import Example, ParsedOptions, Plugin, PluginContext, handlers as h
from utils import get_logger

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


def groq_keys() -> list[str]:
    keys = [key.strip() for key in getattr(config, "GROQ_API_KEYS", []) if key.strip()]
    if getattr(config, "GROQ_API_KEY", ""):
        keys.insert(0, config.GROQ_API_KEY)
    if not keys:
        from utils.synckeys import load_keys

        keys = load_keys("groq")
    return keys


async def transcribe(client, message: Message) -> str | None:
    """Transcribe a voice / audio / video-note message with Groq Whisper.

    Returns the transcript, an empty string when transcription failed, or
    None when no Groq key is configured.
    """
    keys = groq_keys()
    if not keys:
        return None

    if message.voice:
        filename, mime = "audio.ogg", "audio/ogg"
    elif message.video_note:
        filename, mime = "video.mp4", "video/mp4"
    else:
        filename, mime = "audio.mp3", "audio/mpeg"

    with tempfile.TemporaryDirectory() as folder:
        path = await client.download_media(
            message, file_name=f"{folder}/{filename}"
        )
        if not path:
            return ""
        data = Path(path).read_bytes()

    for key in keys:
        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                response = await http.post(
                    config.GROQ_API_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    files={"file": (filename, data, mime)},
                    data={"model": config.TRANSCRIBE_MODEL},
                )
            if response.status_code == 200:
                return (response.json().get("text") or "").strip()
            logger.debug(
                f"[TRANSLATE] Groq {response.status_code}: {response.text[:120]}"
            )
        except Exception as exc:
            logger.debug(f"[TRANSLATE] Groq request failed: {exc}")
    return ""


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


class TranslatePlugin(Plugin):
    id = "translate"
    category = "AI & Tools"
    description = "Translate text, replied messages, and voice notes."

    @h.command(
        "tr",
        aliases=("translate",),
        description=description,
        examples=(
            Example("{cmd} Hello"),
            Example("{cmd} es Hello"),
            Example("{cmd} hi (reply)"),
        ),
        prefixes=config.CMD_PREFIX,
        parse_mode="markdown",
    )
    async def translate_command(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["TranslatePlugin"],
        parsed: ParsedOptions,
    ) -> None:
        parts = (message.text or "").split(maxsplit=1)
        args = parts[1].strip() if len(parts) > 1 else ""

        target = config.TRANSLATE_TARGET
        text = args
        tokens = args.split(maxsplit=1)
        if tokens and tokens[0].lower() in KNOWN_LANGS:
            target = tokens[0].lower()
            text = tokens[1] if len(tokens) > 1 else ""

        reply = message.reply_to_message
        transcript = ""
        if not text and reply:
            if reply.voice or reply.audio or reply.video_note:
                if not groq_keys():
                    await message.edit(
                        "Transcribing voice notes needs a Groq key. Set "
                        f"`GROQ_API_KEY` in .env (model: `{config.TRANSCRIBE_MODEL}`)."
                    )
                    return
                await message.edit("🎙️ Transcribing...")
                transcript = await transcribe(client, reply) or ""
                if not transcript:
                    await message.edit("Could not transcribe the audio.")
                    return
                text = transcript
            else:
                text = reply.text or reply.caption or ""

        if not text:
            await message.edit(
                "Provide text or reply to a message/voice note.\n"
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
        prefix = f"**Transcript:** {transcript}\n\n" if transcript else ""
        await message.edit(f"{prefix}{header}:\n\n{translated}")
