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
        return override
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "ffmpeg"
    if local.exists():
        return str(local)
    return "ffmpeg"


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


def _square(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGBA")
    image.thumbnail((STICKER_SIDE, STICKER_SIDE), Image.LANCZOS)
    canvas = Image.new("RGBA", (STICKER_SIDE, STICKER_SIDE), (0, 0, 0, 0))
    canvas.paste(
        image,
        (
            (STICKER_SIDE - image.width) // 2,
            (STICKER_SIDE - image.height) // 2,
        ),
        image,
    )
    return canvas


async def image_to_sticker(src: str, dst: str) -> None:
    """Convert an image to a 512x512 WEBP static sticker."""

    def convert() -> None:
        with Image.open(src) as image:
            _square(image).save(dst, "WEBP", quality=90, method=6)

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
