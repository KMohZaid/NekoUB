import asyncio
import tempfile
from pathlib import Path

import edge_tts
from pyrogram import filters
from pyrogram.types import Message

import config
import main
from userbot.media import _ffmpeg
from userbot.utils import continue_propagation, get_logger

logger = get_logger(__name__)

MAX_CHARS = 3000


async def synthesize(text: str) -> bytes:
    communicate = edge_tts.Communicate(text, config.TTS_VOICE)
    audio = b""
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio":
            audio += chunk.get("data", b"")
    return audio


async def to_voice(mp3_path: str) -> str | None:
    ogg_path = f"{mp3_path}.ogg"
    process = await asyncio.create_subprocess_exec(
        _ffmpeg(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        mp3_path,
        "-c:a",
        "libopus",
        "-b:a",
        "48k",
        "-ar",
        "48000",
        "-ac",
        "1",
        ogg_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        logger.debug(f"[TTS] ffmpeg failed: {stderr.decode(errors='ignore')[-200:]}")
        return None
    return ogg_path


@main.app.on_message(
    filters.me & filters.command(["tts", "say"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def tts_command(client, message: Message):
    parts = (message.text or "").split(maxsplit=1)
    text = parts[1].strip() if len(parts) > 1 else ""
    reply = message.reply_to_message
    if not text and reply:
        text = reply.text or reply.caption or ""

    if not text:
        await message.edit(
            "Provide text or reply to a message.\n"
            f"`{config.CMD_PREFIX}tts <text>`"
        )
        return

    await message.edit("🗣️ Generating speech...")
    try:
        audio = await synthesize(text[:MAX_CHARS])
    except Exception as exc:
        logger.error(f"[TTS] Synthesis failed: {exc}")
        await message.edit(f"TTS failed: {type(exc).__name__}")
        return

    if not audio:
        await message.edit("TTS produced no audio.")
        return

    reply_to = reply.id if reply else None
    try:
        with tempfile.TemporaryDirectory() as folder:
            mp3_path = f"{folder}/tts.mp3"
            Path(mp3_path).write_bytes(audio)
            voice_path = await to_voice(mp3_path)
            if voice_path:
                await client.send_voice(
                    message.chat.id, voice_path, reply_to_message_id=reply_to
                )
            else:
                await client.send_audio(
                    message.chat.id, mp3_path, reply_to_message_id=reply_to
                )
    except Exception as exc:
        logger.error(f"[TTS] Send failed: {exc}")
        await message.edit(f"TTS send failed: {type(exc).__name__}")
        return

    try:
        await message.delete()
    except Exception:
        pass
