"""Thin, safe wrappers around ffmpeg/ffprobe (argument lists, never a shell)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class MediaToolError(Exception):
    pass


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    if shutil.which(args[0]) is None:
        raise MediaToolError(f"{args[0]} is not installed")
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise MediaToolError(f"{args[0]} timed out") from exc
    if result.returncode != 0:
        raise MediaToolError(
            f"{args[0]} failed: {result.stderr.decode(errors='replace')[-500:]}"
        )
    return result


def probe(path: Path) -> dict:
    """Duration and stream types of a media file."""
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                "-show_streams", str(path)], timeout=60).stdout
    data = json.loads(out or b"{}")
    streams = data.get("streams", [])
    duration = data.get("format", {}).get("duration")
    return {
        "duration": float(duration) if duration else None,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "has_video": any(s.get("codec_type") == "video" for s in streams),
        "width": next((s.get("width") for s in streams if s.get("codec_type") == "video"), None),
        "height": next((s.get("height") for s in streams if s.get("codec_type") == "video"),
                       None),
    }


def extract_audio(src: Path, dest: Path) -> Path:
    """16 kHz mono WAV, the input format speech models expect."""
    _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "1",
          "-ar", "16000", "-f", "wav", str(dest)], timeout=900)
    return dest


def sample_frames(src: Path, out_dir: Path, every_seconds: float, max_frames: int,
                  duration: float | None) -> list[tuple[float, Path]]:
    """One frame every ``every_seconds`` (spread out to fit ``max_frames``)."""
    interval = every_seconds
    if duration and duration / interval > max_frames:
        interval = duration / max_frames
    out_dir.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src),
          "-vf", f"fps=1/{interval:.3f}", "-frames:v", str(max_frames),
          str(out_dir / "frame_%04d.png")], timeout=900)
    frames = sorted(out_dir.glob("frame_*.png"))
    return [(round(i * interval, 2), p) for i, p in enumerate(frames)]
