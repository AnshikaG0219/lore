"""Process-wide media engines (Whisper model is loaded once per worker process)."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings
from app.processing.media.pipeline import MediaEngines
from app.processing.ocr.engine import TesseractOcr
from app.processing.transcription.engine import FasterWhisperTranscriber
from app.storage.media import LocalMediaStore


@lru_cache(maxsize=4)
def build_engines(settings: Settings) -> MediaEngines:
    return MediaEngines(
        ocr=TesseractOcr(settings.ocr_languages, settings.ocr_min_word_confidence),
        transcriber=FasterWhisperTranscriber(settings.whisper_model, settings.whisper_device,
                                             settings.whisper_compute_type),
        frame_interval=settings.frame_interval_seconds,
        max_frames=settings.max_frames_per_video,
    )


def build_store(settings: Settings) -> LocalMediaStore:
    return LocalMediaStore(settings.media_dir, settings.max_media_bytes)
