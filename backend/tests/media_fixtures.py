"""Generate realistic test media: Instagram-style text slides and a short Reel with voice-over."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def media_tools_available() -> bool:
    return all(shutil.which(t) for t in ("ffmpeg", "ffprobe", "tesseract", "say")) and \
        Path(FONT).exists()


def text_slide(path: Path, lines: list[str], size=(1080, 1350), bg=(250, 238, 220),
               fg=(40, 30, 20)) -> Path:
    img = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, 64)
    y = 180
    for line in lines:
        draw.text((90, y), line, font=font, fill=fg)
        y += 110
    img.save(path, "JPEG", quality=90)
    return path


def reel(path: Path, slides: list[tuple[list[str], float]], speech: str,
         voice: str = "Rishi") -> Path:
    """Vertical video: each slide shown for N seconds, with synthesized speech as audio."""
    work = path.parent / f"{path.stem}_work"
    work.mkdir(exist_ok=True)
    concat = []
    for i, (lines, seconds) in enumerate(slides):
        frame = text_slide(work / f"s{i}.jpg", lines, size=(720, 1280), bg=(20, 60, 90),
                           fg=(255, 255, 255))
        concat += [f"file '{frame}'", f"duration {seconds}"]
    concat.append(f"file '{work / f's{len(slides) - 1}.jpg'}'")
    (work / "list.txt").write_text("\n".join(concat))
    subprocess.run(["say", "-v", voice, "-o", str(work / "speech.aiff"), speech], check=True)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(work / "list.txt"), "-i", str(work / "speech.aiff"),
                    "-vf", "fps=25,format=yuv420p", "-c:v", "libx264", "-c:a", "aac",
                    "-shortest", str(path)], check=True)
    return path
