"""OCR engines. Tesseract runs locally; nothing leaves the machine."""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps


class OcrError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class OcrResult:
    text: str
    confidence: float | None  # mean word confidence 0-1, None if no words


class OcrEngine(Protocol):
    name: str

    def read(self, image_path: Path) -> OcrResult: ...


BRIGHT_TEXT_THRESHOLD = 200


def _prepare(src: Path, out_dir: Path) -> list[Path]:
    """Two renderings: plain greyscale (dark text on light), and a "bright text" mask.

    Instagram overlays are very often white text/subtitles over photos or video, which
    Tesseract misses on the plain image. Thresholding bright pixels to black-on-white
    recovers them. Small images are upscaled; Tesseract is much better on ≥1000px text.
    """
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img).convert("L")
        if img.width < 1000:
            scale = 1000 / img.width
            img = img.resize((1000, int(img.height * scale)), Image.Resampling.LANCZOS)
        plain, bright = out_dir / "plain.png", out_dir / "bright.png"
        img.save(plain, "PNG")
        img.point(lambda v: 0 if v > BRIGHT_TEXT_THRESHOLD else 255).save(bright, "PNG")
    return [plain, bright]


class TesseractOcr:
    def __init__(self, languages: str = "eng", min_word_confidence: float = 40.0) -> None:
        if shutil.which("tesseract") is None:
            raise OcrError("tesseract is not installed")
        self.languages = languages
        self.min_conf = min_word_confidence
        self.name = f"tesseract-{languages}-v2"  # v2: + bright-text pass

    def read(self, image_path: Path) -> OcrResult:
        """OCR every rendering and keep the one with the most confident text."""
        best = OcrResult("", None)
        with tempfile.TemporaryDirectory() as tmp:
            for prepared in _prepare(image_path, Path(tmp)):
                proc = subprocess.run(
                    ["tesseract", str(prepared), "stdout", "-l", self.languages, "--psm", "3",
                     "tsv"],
                    capture_output=True, timeout=120, check=False,
                )
                if proc.returncode != 0:
                    raise OcrError(proc.stderr.decode(errors="replace")[-300:])
                result = parse_tsv(proc.stdout.decode("utf-8", errors="replace"), self.min_conf)
                if _score(result) > _score(best):
                    best = result
        return best


def _score(r: OcrResult) -> float:
    return len(r.text.replace("\n", "").replace(" ", "")) * (r.confidence or 0)


def parse_tsv(tsv: str, min_conf: float) -> OcrResult:
    """Rebuild lines from Tesseract TSV, dropping low-confidence words (usually noise)."""
    lines: dict[tuple, list[str]] = {}
    confs: list[float] = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        word = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            continue
        if not word or conf < min_conf:
            continue
        key = (row["page_num"], row["block_num"], row["par_num"], row["line_num"])
        lines.setdefault(key, []).append(word)
        confs.append(conf)
    text = "\n".join(" ".join(words) for words in lines.values())
    return OcrResult(text=text, confidence=round(sum(confs) / len(confs) / 100, 3)
                     if confs else None)
