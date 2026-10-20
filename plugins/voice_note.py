import asyncio
import re
import subprocess
import tempfile
from pathlib import Path

from pyrogram import filters
from pyrogram.enums import ChatAction
from pyrogram.types import Message

import config
import main
from userbot.media import _ffmpeg
from userbot.utils import continue_propagation, get_logger

logger = get_logger(__name__)

CHAT_ACTION_INTERVAL = 4.0
MAX_CHAT_ACTION_SENDS = 30
TEMP_DIR = Path("/tmp")


def _media_mime(message: Message) -> str:
    if message.audio or message.voice:
        return "audio"
    if message.video or message.video_note or message.animation:
        return "video"
    document = message.document
    mime_type = (getattr(document, "mime_type", None) or "").lower()
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    return ""


def _parse_delete_flag(message: Message) -> bool:
    command_text = message.text or message.caption or ""
    parts = command_text.split(maxsplit=1)
    args = parts[1].strip().split() if len(parts) > 1 else []
    if not args:
        return False
    if args == ["-d"]:
        return True
    raise ValueError("Usage: reply to audio/video with `.vn [-d]`, or send `.vn -d` with media.")


async def _recording_action(client, chat_id: int, stop: asyncio.Event) -> None:
    try:
        for index in range(MAX_CHAT_ACTION_SENDS):
            if stop.is_set():
                break
            await client.send_chat_action(chat_id, ChatAction.RECORD_AUDIO)
            if index + 1 < MAX_CHAT_ACTION_SENDS:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=CHAT_ACTION_INTERVAL)
                except asyncio.TimeoutError:
                    pass
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug(f"[VOICE_NOTE] Chat action stopped: {exc}")


async def _run_ffmpeg(args: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode:
        detail = stderr.decode(errors="ignore").strip().splitlines()
        raise RuntimeError(" ".join(detail[-3:]) or "ffmpeg failed")


def _voice_metadata(path: Path) -> tuple[int, bytes]:
    probe = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr)
    duration = 0
    if match:
        hours, minutes, seconds = match.groups()
        duration = round(int(hours) * 3600 + int(minutes) * 60 + float(seconds))

    pcm = subprocess.run(
        [
            _ffmpeg(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000",
            "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
        ],
        check=True,
        capture_output=True,
    ).stdout
    sample_count = len(pcm) // 2
    peaks = []
    for index in range(100):
        start = index * sample_count // 100
        end = max(start + 1, (index + 1) * sample_count // 100)
        peak = 0
        for sample_index in range(start, min(end, sample_count)):
            value = int.from_bytes(
                pcm[sample_index * 2:sample_index * 2 + 2],
                "little",
                signed=True,
            )
            peak = max(peak, abs(value))
        peaks.append(peak)

    maximum = max(peaks, default=0) or 1
    levels = [min(31, round(peak * 31 / maximum)) for peak in peaks]
    waveform = bytearray(63)
    for index, level in enumerate(levels):
        bit_offset = index * 5
        byte_index, shift = divmod(bit_offset, 8)
        value = level << shift
        waveform[byte_index] |= value & 0xFF
        if byte_index + 1 < len(waveform):
            waveform[byte_index + 1] |= (value >> 8) & 0xFF
    return duration, bytes(waveform)


async def _error(client, message: Message, text: str) -> None:
    await client.send_message(
        message.chat.id,
        text,
        reply_to_message_id=(message.reply_to_message.id if message.reply_to_message else None),
    )


@main.app.on_message(
    filters.me & filters.command(["voice", "vn"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def voice_note_command(client, message: Message):
    try:
        delete_command = _parse_delete_flag(message)
    except ValueError as exc:
        await _error(client, message, str(exc))
        return

    source = message if _media_mime(message) else message.reply_to_message
    if not source or not _media_mime(source):
        await _error(
            client,
            message,
            "Reply to an audio or video message, or use `.vn` / `.voice` on one.",
        )
        return

    chat_id = message.chat.id
    if source.id == message.id:
        reply_to_id = message.reply_to_message.id if message.reply_to_message else None
    else:
        reply_to_id = source.id

    with tempfile.TemporaryDirectory(
        prefix="nekoub-voice-note-", dir=TEMP_DIR
    ) as temp_dir:
        source_path = await client.download_media(
            source,
            file_name=str(Path(temp_dir) / "source"),
        )
        if not source_path:
            await _error(client, message, "Could not download the source media.")
            return

        if delete_command or source.id != message.id:
            try:
                await message.delete()
            except Exception as exc:
                logger.warning(f"[VOICE_NOTE] Could not delete command: {exc}")

        stop = asyncio.Event()
        action_task = asyncio.create_task(_recording_action(client, chat_id, stop))
        try:
            output_path = Path(temp_dir) / "voice-note.ogg"
            await _run_ffmpeg([
                _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source_path), "-vn", "-c:a", "libopus", "-b:a", "48k",
                "-ar", "48000", "-ac", "1", str(output_path),
            ])
            duration, waveform = _voice_metadata(output_path)
            await client.send_voice(
                chat_id,
                str(output_path),
                duration=duration,
                waveform=waveform,
                reply_to_message_id=reply_to_id,
            )
        except Exception as exc:
            logger.error(f"[VOICE_NOTE] Conversion or send failed: {exc}", exc_info=True)
            await _error(client, message, f"Could not make a voice note: {type(exc).__name__}")
        finally:
            stop.set()
            action_task.cancel()
            await asyncio.gather(action_task, return_exceptions=True)
