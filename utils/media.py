import asyncio
import shutil
from pathlib import Path

from PIL import Image, ImageOps

import config

STICKER_SIDE = 512
VIDEO_MAX_SECONDS = 3
VIDEO_FPS = 30
VIDEO_BITRATE = "256k"


class ConversionError(Exception):
    pass


def _ffmpeg() -> str:
    override = getattr(config, "FFMPEG_BINARY", "")
    if override:
        found = shutil.which(override)
        if found:
            return found
        configured = Path(override).expanduser()
        if configured.is_file() and configured.stat().st_mode & 0o111:
            return str(configured)
        raise ConversionError(
            f"Configured ffmpeg binary is unavailable: {override}"
        )

    found = shutil.which("ffmpeg")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "ffmpeg"
    if local.is_file() and local.stat().st_mode & 0o111:
        return str(local)
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if Path(bundled).is_file():
            return bundled
    except (ImportError, RuntimeError) as exc:
        logger_message = str(exc)
    else:
        logger_message = ""
    raise ConversionError(
        "ffmpeg is required to convert GIF/video media. Install ffmpeg or set "
        f"FFMPEG_BINARY.{f' {logger_message}' if logger_message else ''}"
    )


async def _run_ffmpeg(args: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        lines = stderr.decode(errors="ignore").strip().splitlines()
        raise ConversionError(" ".join(lines[-3:]) or "ffmpeg failed")


def _sticker_size(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGBA")
    width, height = image.size
    scale = STICKER_SIDE / max(width, height)
    size = (round(width * scale), round(height * scale))
    return image.resize(size, Image.Resampling.LANCZOS)


async def image_to_sticker(src: str, dst: str) -> None:
    """Convert an image to a 512x512 WEBP static sticker."""

    def convert() -> None:
        with Image.open(src) as image:
            sticker = _sticker_size(image)
            for quality in (90, 80, 70, 60, 50, 40):
                sticker.save(dst, "WEBP", quality=quality, method=6)
                if Path(dst).stat().st_size <= 512 * 1024:
                    return
            raise ConversionError("Converted WEBP exceeds Telegram's 512 KB sticker limit")

    await asyncio.to_thread(convert)


async def video_to_sticker(src: str, dst: str) -> None:
    """Convert a GIF/video to a 512x512 VP9 WEBM video sticker."""
    filters = (
        f"scale={STICKER_SIDE}:{STICKER_SIDE}:force_original_aspect_ratio=decrease,"
        f"pad={STICKER_SIDE}:{STICKER_SIDE}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        f"fps={VIDEO_FPS}"
    )
    await _run_ffmpeg(
        [
            _ffmpeg(),
            "-y",
            "-i",
            src,
            "-t",
            str(VIDEO_MAX_SECONDS),
            "-vf",
            filters,
            "-c:v",
            "libvpx-vp9",
            "-pix_fmt",
            "yuva420p",
            "-b:v",
            VIDEO_BITRATE,
            "-auto-alt-ref",
            "0",
            "-an",
            dst,
        ]
    )
