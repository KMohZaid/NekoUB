import asyncio
import html
import json
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

import edge_tts
import httpx
from pyrogram import filters
from pyrogram.enums import ChatAction, ParseMode
from pyrogram.types import Message

import config
import main
from userbot.media import _ffmpeg
from userbot.utils import (
    continue_propagation,
    get_logger,
    is_user_premium,
    split_text_into_chunks,
)

logger = get_logger(__name__)

KOKORO_URL = "https://kmohzaid-kokoro-tts.hf.space"
EDGE_VOICE = "edge"
MIN_SPEED = 0.5
MAX_SPEED = 2.0
FREE_MESSAGE_LIMIT = 3700
PREMIUM_MESSAGE_LIMIT = 7700

# Display name and flag as shown by the Kokoro Space.
VOICE_LIST = (
    ("Heart ❤️", "🇺🇸 🚺"),
    ("Bella 🔥", "🇺🇸 🚺"),
    ("Nicole 🎧", "🇺🇸 🚺"),
    ("Aoede", "🇺🇸 🚺"),
    ("Kore", "🇺🇸 🚺"),
    ("Sarah", "🇺🇸 🚺"),
    ("Nova", "🇺🇸 🚺"),
    ("Sky", "🇺🇸 🚺"),
    ("Alloy", "🇺🇸 🚺"),
    ("Jessica", "🇺🇸 🚺"),
    ("River", "🇺🇸 🚺"),
    ("Michael", "🇺🇸 🚹"),
    ("Fenrir", "🇺🇸 🚹"),
    ("Puck", "🇺🇸 🚹"),
    ("Echo", "🇺🇸 🚹"),
    ("Eric", "🇺🇸 🚹"),
    ("Liam", "🇺🇸 🚹"),
    ("Onyx", "🇺🇸 🚹"),
    ("Santa", "🇺🇸 🚹"),
    ("Adam", "🇺🇸 🚹"),
    ("Emma", "🇬🇧 🚺"),
    ("Isabella", "🇬🇧 🚺"),
    ("Alice", "🇬🇧 🚺"),
    ("Lily", "🇬🇧 🚺"),
    ("George", "🇬🇧 🚹"),
    ("Fable", "🇬🇧 🚹"),
    ("Lewis", "🇬🇧 🚹"),
    ("Daniel", "🇬🇧 🚹"),
)
VOICE_IDS = {
    "heart": "af_heart",
    "bella": "af_bella",
    "nicole": "af_nicole",
    "aoede": "af_aoede",
    "kore": "af_kore",
    "sarah": "af_sarah",
    "nova": "af_nova",
    "sky": "af_sky",
    "alloy": "af_alloy",
    "jessica": "af_jessica",
    "river": "af_river",
    "michael": "am_michael",
    "fenrir": "am_fenrir",
    "puck": "am_puck",
    "echo": "am_echo",
    "eric": "am_eric",
    "liam": "am_liam",
    "onyx": "am_onyx",
    "santa": "am_santa",
    "adam": "am_adam",
    "emma": "bf_emma",
    "isabella": "bf_isabella",
    "alice": "bf_alice",
    "lily": "bf_lily",
    "george": "bm_george",
    "fable": "bm_fable",
    "lewis": "bm_lewis",
    "daniel": "bm_daniel",
}
VOICE_NAMES = {voice_id: voice_id.split("_", 1)[1].title() for voice_id in VOICE_IDS.values()}

TOKEN_NOTE = """💡 Customize pronunciation with Markdown link syntax and /slashes/ like <code>[Kokoro](/kˈOkəɹO/)</code>
💬 Adjust intonation with punctuation <code>;:,.!?—…\"()“”</code> or stress marks <code>ˈ</code> and <code>ˌ</code>
⬇️ Lower stress with <code>[1 level](-1)</code> or <code>[2 levels](-2)</code>
⬆️ Raise stress with <code>[or](+2)</code> (works best on less-stressed, usually short words)"""

HELP_TEXT = """<b>🎙 Text to speech · .say / .tts</b>

<b>Single voice</b>
<code>.say [-v voice] [-s speed] [-i] text</code>
<b>Multi voice</b>
<code>.say -m [-i] speaker lines</code>

<blockquote><b>Put options before the text.</b> Once text starts, everything after it is spoken literally. For example, <code>.say Adam -s 0.1 hello</code> speaks “Adam -s 0.1 hello”.

<code>-v</code> / <code>-voice</code> selects a voice (default: Adam).
<code>-s</code> / <code>-speed</code> sets speed from 0.5 to 2 (default: 1; 0.9 means 90% speed).
<code>-m</code> / <code>-multi</code> enables speaker-labelled dialogue. Do not combine multi mode with global <code>-v</code> or <code>-s</code>; set speed per speaker, e.g. <code>Adam 0.8: Hello</code>.
<code>-i</code> / <code>-info</code> adds generation details (model, voices, CPU, input length, generation time, and speech duration) to the caption.
<code>-t</code> / <code>-transcript</code> sends the input text separately in a blockquote reply to the voice note.
When replying to a text message, you can omit the text; flags still work, e.g. <code>.say -v heart -i</code>.</blockquote>

<b>Examples</b>
<pre><code>1. .say Hello, how are you?
2. .tts -v heart -s 0.9 Hello there!
3. .say -voice George Tell me a story.
4. .say -voice Nicole -speed 1.2 Good morning!
5. .say -i This voice note includes its transcript.
6. .say -m
Adam: Hello bro, I am Adam. Who are you?
George: Bro, I am George.
7. .say -m
Adam 0.5: I will speak slowly.
George 1.2: And I will answer faster.
8. .say -m -i Adam: I am Adam. George: I am George.
9. .say -m -t Adam: [Kokoro](/kˈOkəɹO/)... ready? Heart: Ready!
10. .say -m -i -t Adam: Info goes in the caption. Heart: Transcript is in a reply.</code></pre>

<b>Kokoro voices</b>
<blockquote>{voice_list}</blockquote>
Adam is the most popular voice in many voice reels and shorts. George is the second most popular. I like those two.

<b>Kokoro pronunciation tips</b>
<blockquote>{token_note}</blockquote>

<b>Edge TTS</b>
Edge TTS is separate from Kokoro and may sound more synthetic. Kokoro pronunciation and intonation markup do not apply to Edge; speed can still be adjusted. Select it with <code>-v edge</code> or use <code>edge:</code> as a speaker in multi mode. The Edge voice comes from <code>TTS_VOICE</code> in configuration."""


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _format_help() -> str:
    voice_lines = [
        f"{index}. {flag} {name}"
        for index, (name, flag) in enumerate(VOICE_LIST, 1)
    ]
    voice_lines.append(f"{len(VOICE_LIST) + 1}. Edge TTS")
    return HELP_TEXT.format(
        voice_list="\n".join(voice_lines),
        token_note=_esc(TOKEN_NOTE),
    )


def _speed(value: str) -> float:
    try:
        speed = float(value)
    except ValueError as exc:
        raise ValueError("Speed must be a number from 0.5 to 2.") from exc
    if not MIN_SPEED <= speed <= MAX_SPEED:
        raise ValueError("Speed must be between 0.5 and 2.")
    return speed


def _resolve_voice(value: str) -> str:
    key = value.strip().lower()
    if key in ("edge", "edge tts"):
        return EDGE_VOICE
    try:
        return VOICE_IDS[key]
    except KeyError as exc:
        choices = ", ".join(name.split()[0] for name, _ in VOICE_LIST)
        raise ValueError(f"Unknown voice '{value}'. Kokoro voices: {choices}; Edge.") from exc


def _parse_command(raw: str) -> tuple[bool, str, float, bool, bool, str]:
    """Parse leading flags while preserving dialogue line boundaries."""
    multi = info = transcript = False
    voice = "am_adam"
    voice_selected = False
    speed = 1.0
    speed_selected = False
    position = 0
    while position < len(raw):
        while position < len(raw) and raw[position].isspace():
            position += 1
        match = re.match(r"\S+", raw[position:])
        if not match:
            break
        token = match.group(0).lower()
        next_position = position + match.end()
        if token in ("-m", "-multi"):
            multi = True
            position = next_position
        elif token in ("-i", "-info"):
            info = True
            position = next_position
        elif token in ("-t", "-transcript"):
            transcript = True
            position = next_position
        elif token in ("-v", "-voice", "-s", "-speed"):
            option = token
            while next_position < len(raw) and raw[next_position].isspace():
                next_position += 1
            value_match = re.match(r"\S+", raw[next_position:])
            if not value_match:
                raise ValueError(f"Specify a value after {option}.")
            value = value_match.group(0)
            position = next_position + value_match.end()
            if option in ("-v", "-voice"):
                voice = _resolve_voice(value)
                voice_selected = True
            else:
                speed = _speed(value)
                speed_selected = True
        else:
            break
    text = raw[position:].strip()
    if multi and (voice_selected or speed_selected):
        raise ValueError(
            "Multi mode cannot use global -v or -s; set the voice and optional speed on each speaker line."
        )
    return multi, voice, speed, info, transcript, text


def _parse_dialogue(text: str) -> list[tuple[str, float, str]]:
    turns: list[tuple[str, float, str]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError(f"Dialogue line {line_number} must use 'Speaker: text'.")
        label, speech = line.split(":", 1)
        label_parts = label.strip().split()
        if not label_parts:
            raise ValueError(f"Dialogue line {line_number} has no speaker name.")
        speaker = _resolve_voice(label_parts[0])
        speed = _speed(label_parts[1]) if len(label_parts) > 1 else 1.0
        if len(label_parts) > 2:
            raise ValueError(f"Dialogue line {line_number}: use 'Speaker [speed]: text'.")
        speech = speech.strip()
        if not speech:
            raise ValueError(f"Dialogue line {line_number} has no text after the speaker name.")
        turns.append((speaker, speed, speech))
    if not turns:
        raise ValueError("Add at least one 'Speaker: text' line after -m.")
    return turns


async def _edge_audio(text: str, speed: float) -> bytes:
    rate = f"{round((speed - 1) * 100):+d}%"
    communicate = edge_tts.Communicate(text, config.TTS_VOICE, rate=rate)
    audio = bytearray()
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio":
            audio.extend(chunk.get("data", b""))
    return bytes(audio)


async def _ffmpeg_run(args: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="ignore")[-500:] or "ffmpeg failed")


async def _generate_kokoro(
    client: httpx.AsyncClient, text: str, voice: str, speed: float
) -> list[bytes]:
    session_hash = uuid.uuid4().hex
    response = await client.post(
        f"{KOKORO_URL}/gradio_api/queue/join",
        json={
            "data": [text, voice, speed, False],
            "fn_index": 6,
            "session_hash": session_hash,
        },
    )
    response.raise_for_status()
    event_id = response.json().get("event_id")
    if not event_id:
        raise RuntimeError("Kokoro did not return a queue event id.")

    playlist_url = None
    async with client.stream(
        "GET",
        f"{KOKORO_URL}/gradio_api/queue/data",
        params={"session_hash": session_hash},
        timeout=httpx.Timeout(900, connect=30),
    ) as events:
        async for line in events.aiter_lines():
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if event.get("event_id") not in (None, event_id):
                continue
            if event.get("msg") in ("process_generating", "process_completed"):
                data = (event.get("output") or {}).get("data") or []
                if data and isinstance(data[0], dict):
                    playlist_url = data[0].get("url")
            if event.get("msg") == "process_failed":
                raise RuntimeError("Kokoro generation failed in the Space.")
            if event.get("msg") == "process_completed":
                if not (event.get("output") or {}).get("success", True):
                    raise RuntimeError("Kokoro generation failed in the Space.")
                break

    if not playlist_url:
        raise RuntimeError("Kokoro returned no audio stream.")
    manifest = await client.get(playlist_url)
    manifest.raise_for_status()
    parts = [
        httpx.URL(playlist_url).join(line.strip())
        for line in manifest.text.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not parts:
        raise RuntimeError("Kokoro returned an empty audio playlist.")
    audio = []
    for part in parts:
        response = await client.get(str(part))
        response.raise_for_status()
        audio.append(response.content)
    return audio


async def _synthesize_turns(
    turns: list[tuple[str, float, str]], folder: Path
) -> Path:
    async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=30)) as client:
        segment_rows: list[str] = []
        for turn_index, (voice, speed, text) in enumerate(turns):
            if voice == EDGE_VOICE:
                audio = await _edge_audio(text, speed)
                if not audio:
                    raise RuntimeError("Edge TTS returned no audio.")
                mp3_segment = folder / f"turn-{turn_index:03d}.mp3"
                mp3_segment.write_bytes(audio)
                segment = folder / f"turn-{turn_index:03d}.wav"
                await _ffmpeg_run([
                    _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(mp3_segment), "-ar", "24000", "-ac", "1",
                    "-c:a", "pcm_s16le", str(segment),
                ])
            else:
                audio_segments = await _generate_kokoro(client, text, voice, speed)
                turn_dir = folder / f"turn-{turn_index:03d}"
                turn_dir.mkdir()
                input_list = turn_dir / "input.txt"
                rows = []
                for audio_index, data in enumerate(audio_segments):
                    part = turn_dir / f"part-{audio_index:04d}.aac"
                    part.write_bytes(data)
                    rows.append(f"file '{part}'")
                input_list.write_text("\n".join(rows) + "\n")
                segment = folder / f"turn-{turn_index:03d}.wav"
                await _ffmpeg_run([
                    _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", str(input_list),
                    "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(segment),
                ])
            segment_rows.append(f"file '{segment}'")
            if turn_index + 1 < len(turns):
                silence = folder / f"silence-{turn_index:03d}.wav"
                await _ffmpeg_run([
                    _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                    "-t", "0.28", "-c:a", "pcm_s16le", str(silence),
                ])
                segment_rows.append(f"file '{silence}'")
        concat_file = folder / "all.txt"
        concat_file.write_text("\n".join(segment_rows) + "\n")
        output = folder / "tts.ogg"
        await _ffmpeg_run([
            _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c:a", "libopus", "-b:a", "48k", "-ar", "48000", "-ac", "1",
            str(output),
        ])
        return output


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

    raw = subprocess.run(
        [
            _ffmpeg(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000",
            "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
        ],
        check=True,
        capture_output=True,
    ).stdout
    sample_count = len(raw) // 2
    peaks = []
    for index in range(100):
        start = index * sample_count // 100
        end = max(start + 1, (index + 1) * sample_count // 100)
        peak = 0
        for sample_index in range(start, min(end, sample_count)):
            value = int.from_bytes(raw[sample_index * 2:sample_index * 2 + 2], "little", signed=True)
            peak = max(peak, abs(value))
        peaks.append(peak)
    maximum = max(peaks, default=0) or 1
    levels = [min(31, round(peak * 31 / maximum)) for peak in peaks]
    waveform = bytearray(63)
    for index, level in enumerate(levels):
        offset = index * 5
        byte_index, shift = divmod(offset, 8)
        value = level << shift
        waveform[byte_index] |= value & 255
        if byte_index + 1 < len(waveform):
            waveform[byte_index + 1] |= (value >> 8) & 255
    return duration, bytes(waveform)


CHAT_ACTION_INTERVAL = 4.0
MAX_CHAT_ACTION_SENDS = 30


async def _keep_recording_action(client, chat_id: int, stop: asyncio.Event) -> None:
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
        logger.debug(f"[TTS] Chat action stopped: {exc}")


async def _get_premium_status(client) -> bool:
    try:
        return await is_user_premium(client)
    except Exception as exc:
        logger.warning(f"[TTS] Premium check failed; using free text limit: {exc}")
        return False


async def _send_transcription(client, voice_message, text: str) -> None:
    limit = PREMIUM_MESSAGE_LIMIT if await _get_premium_status(client) else FREE_MESSAGE_LIMIT
    chunks = split_text_into_chunks(text, limit)
    for index, chunk in enumerate(chunks, 1):
        pagination = f" ({index}/{len(chunks)})" if len(chunks) > 1 else ""
        await asyncio.sleep(1.5 if index > 1 else 0)
        await voice_message.reply_text(
            f"<b>Transcription{pagination}:</b>\n<blockquote>{_esc(chunk)}</blockquote>",
            parse_mode=ParseMode.HTML,
        )


async def _show_error(client, chat_id: int, reply_id: int | None, error: str):
    await client.send_message(
        chat_id,
        f"❌ {_esc(error)}",
        reply_to_message_id=reply_id,
        parse_mode=ParseMode.HTML,
    )


@main.app.on_message(
    filters.me & filters.command(["tts", "say"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def tts_command(client, message: Message):
    raw = (message.text or "").split(maxsplit=1)
    raw_args = raw[1] if len(raw) > 1 else ""
    if not raw_args.strip():
        await message.edit(_format_help(), parse_mode=ParseMode.HTML)
        return

    reply = message.reply_to_message
    chat_id = message.chat.id
    command_id = message.id
    original_reply_id = reply.id if reply else None
    reply_text = (reply.text or reply.caption or "") if reply else ""
    try:
        await message.delete()
    except Exception as exc:
        logger.warning(f"[TTS] Could not delete command message: {exc}")

    try:
        multi, voice, speed, info, transcript, text = _parse_command(raw_args)
        if not text and reply_text:
            text = reply_text
        if not text:
            raise ValueError("Provide text or reply to a text message.")
        if multi:
            turns = _parse_dialogue(text)
        else:
            turns = [(voice, speed, text)]
    except ValueError as exc:
        await _show_error(client, chat_id, original_reply_id or command_id, str(exc))
        return

    stop_action = asyncio.Event()
    action_task = asyncio.create_task(_keep_recording_action(client, chat_id, stop_action))
    started = asyncio.get_running_loop().time()
    try:
        with tempfile.TemporaryDirectory(prefix="nekoub-tts-") as folder:
            output = await _synthesize_turns(turns, Path(folder))
            duration, waveform = _voice_metadata(output)
            elapsed = asyncio.get_running_loop().time() - started
            duration_text = f"{duration // 60}:{duration % 60:02d}"
            if multi:
                voices = []
                for turn_voice, _, _ in turns:
                    label = "Edge TTS" if turn_voice == EDGE_VOICE else VOICE_NAMES[turn_voice]
                    if label not in voices:
                        voices.append(label)
                voice_label = ", ".join(voices)
            else:
                voice_label = "Edge TTS" if voice == EDGE_VOICE else VOICE_NAMES[voice]
            metadata = (
                f"Kokoro 82M{' multi-voice' if multi else ''} · CPU · "
                f"{len(text)} chars · {voice_label} · generated in "
                f"{elapsed:.1f}s · speech {duration_text}"
                if voice != EDGE_VOICE
                else (
                    f"Edge TTS · {len(text)} chars · {voice_label} · "
                    f"generated in {elapsed:.1f}s · speech {duration_text}"
                )
            )
            caption = metadata if info else ""
            if info and transcript:
                caption_limit = 4090 if await _get_premium_status(client) else 1020
                if len(text) + len(metadata) + 2 <= caption_limit:
                    caption = f"{metadata}\n\n{_esc(text)}"
            voice_message = await client.send_voice(
                chat_id,
                str(output),
                caption=caption,
                parse_mode=ParseMode.HTML,
                duration=duration,
                waveform=waveform,
                reply_to_message_id=original_reply_id,
            )
            if transcript:
                await _send_transcription(client, voice_message, text)
    except Exception as exc:
        logger.error(f"[TTS] Synthesis or sending failed: {exc}", exc_info=True)
        await _show_error(client, chat_id, original_reply_id or command_id, f"TTS failed: {type(exc).__name__}: {exc}")
    finally:
        stop_action.set()
        action_task.cancel()
        await asyncio.gather(action_task, return_exceptions=True)
