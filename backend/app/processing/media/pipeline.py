"""Attach media to content items and turn it into text (OCR + transcription).

Every step is idempotent and commits on its own, so partial results survive failures and
re-processing never repeats finished work (PRD §12, §15 cost: no repeated OCR/transcription).
"""

from __future__ import annotations

import logging
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ContentItem, MediaAsset, OcrText, Transcript
from app.processing.media import ffmpeg
from app.processing.ocr.engine import OcrEngine, OcrError
from app.processing.transcription.engine import Transcriber, TranscriptionError
from app.storage.media import LocalMediaStore

log = logging.getLogger(__name__)


@dataclass(slots=True)
class MediaEngines:
    ocr: OcrEngine
    transcriber: Transcriber
    frame_interval: float = 2.0
    max_frames: int = 45


def attach_media(session: Session, store: LocalMediaStore, content_id: uuid.UUID,
                 stream: BinaryIO, filename: str | None) -> tuple[MediaAsset, bool]:
    """Store a file for a content item. Returns (asset, created); duplicates are reused."""
    if session.get(ContentItem, content_id) is None:
        raise LookupError(f"Content item {content_id} not found")
    stored = store.save(content_id, stream)
    existing = session.scalar(select(MediaAsset).where(
        MediaAsset.content_id == content_id, MediaAsset.checksum == stored.checksum))
    if existing:
        return existing, False
    position = session.scalar(select(func.count()).where(MediaAsset.content_id == content_id))
    asset = MediaAsset(
        content_id=content_id, asset_type=stored.kind.asset_type, position=position or 0,
        storage_path=str(stored.path), original_filename=(filename or "")[:255] or None,
        mime_type=stored.kind.mime_type, file_size=stored.size, checksum=stored.checksum,
        processing_status="pending", meta={},
    )
    session.add(asset)
    session.commit()
    return asset, True


def _has_ocr(session: Session, asset_id: uuid.UUID, frame_time: float, engine: str) -> bool:
    return session.scalar(select(OcrText.id).where(
        OcrText.media_asset_id == asset_id, OcrText.frame_time == frame_time,
        OcrText.engine == engine)) is not None


def _ocr(session: Session, asset: MediaAsset, image: Path, frame_time: float,
         engine: OcrEngine) -> None:
    if _has_ocr(session, asset.id, frame_time, engine.name):
        return
    result = engine.read(image)
    session.add(OcrText(content_id=asset.content_id, media_asset_id=asset.id,
                        frame_time=frame_time, text=result.text,
                        confidence=result.confidence, engine=engine.name))
    session.commit()


def _transcribe(session: Session, asset: MediaAsset, audio: Path,
                transcriber: Transcriber) -> None:
    done = session.scalar(select(Transcript.id).where(
        Transcript.media_asset_id == asset.id, Transcript.provider == transcriber.provider,
        Transcript.model == transcriber.model))
    if done:
        return
    r = transcriber.transcribe(audio)
    session.add(Transcript(content_id=asset.content_id, media_asset_id=asset.id,
                           language=r.language, language_probability=r.language_probability,
                           transcript_text=r.text, timestamped_segments=r.segments,
                           duration_seconds=r.duration, provider=transcriber.provider,
                           model=transcriber.model))
    session.commit()


def process_asset(session: Session, asset_id: uuid.UUID, engines: MediaEngines,
                  store: LocalMediaStore, *, force: bool = False) -> dict:
    asset = session.get(MediaAsset, asset_id)
    if asset is None:
        return {"asset_id": str(asset_id), "status": "missing"}
    if asset.processing_status == "completed" and not force:
        return {"asset_id": str(asset_id), "status": "skipped"}
    asset.processing_status, asset.error_message = "processing", None
    session.commit()
    try:
        path = store.resolve(asset.storage_path)
        if asset.asset_type == "image":
            _ocr(session, asset, path, -1.0, engines.ocr)
        else:
            info = ffmpeg.probe(path)
            asset.meta = {**(asset.meta or {}), "probe": info}
            session.commit()
            with tempfile.TemporaryDirectory() as tmp:
                if info["has_audio"]:
                    audio = ffmpeg.extract_audio(path, Path(tmp) / "audio.wav")
                    _transcribe(session, asset, audio, engines.transcriber)
                if info["has_video"]:
                    frames = ffmpeg.sample_frames(path, Path(tmp) / "frames",
                                                  engines.frame_interval, engines.max_frames,
                                                  info["duration"])
                    for t, frame in frames:
                        _ocr(session, asset, frame, t, engines.ocr)
    except (OSError, ffmpeg.MediaToolError, OcrError, TranscriptionError) as exc:
        session.rollback()
        asset = session.get(MediaAsset, asset_id)
        asset.processing_status, asset.error_message = "failed", str(exc)[:2000]
        session.commit()
        log.warning("media_processing_failed", extra={"asset_id": str(asset_id)})
        return {"asset_id": str(asset_id), "status": "failed", "error": str(exc)[:300]}
    asset.processing_status, asset.processed_at = "completed", datetime.now(UTC)
    session.commit()
    return {"asset_id": str(asset_id), "status": "completed"}


_NORMALISE = re.compile(r"[^\w]+", re.UNICODE)


def _key(line: str) -> str:
    return _NORMALISE.sub(" ", line.lower()).strip()


@dataclass(slots=True)
class MediaText:
    on_screen_text: str = ""
    transcript: str = ""
    transcript_language: str | None = None
    assets: int = 0
    sources: list[str] = field(default_factory=list)


def media_text_for(session: Session, content_id: uuid.UUID, max_chars: int = 8000) -> MediaText:
    """Combined OCR and transcript text for a content item, de-duplicated across frames.

    Reels repeat the same overlay text for many seconds; lines are kept once, in order.
    """
    assets = session.scalars(select(MediaAsset).where(
        MediaAsset.content_id == content_id).order_by(MediaAsset.position)).all()
    seen: set[str] = set()
    lines: list[str] = []
    transcripts: list[str] = []
    language = None
    for asset in assets:
        for ocr in session.scalars(select(OcrText).where(OcrText.media_asset_id == asset.id)
                                   .order_by(OcrText.frame_time)):
            for line in ocr.text.splitlines():
                k = _key(line)
                if len(k.replace(" ", "")) >= 3 and k not in seen:
                    seen.add(k)
                    lines.append(line.strip())
        for tr in session.scalars(select(Transcript).where(Transcript.media_asset_id == asset.id)):
            if tr.transcript_text.strip():
                transcripts.append(tr.transcript_text.strip())
                language = language or tr.language
    return MediaText(
        on_screen_text="\n".join(lines)[:max_chars],
        transcript="\n".join(transcripts)[:max_chars],
        transcript_language=language,
        assets=len(assets),
        sources=[a.asset_type for a in assets],
    )
