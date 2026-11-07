"""Content media endpoints (manual upload for posts whose media isn't otherwise available)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.core.config import Settings, get_settings
from app.core.security import require_api_key
from app.db.models import ContentItem, MediaAsset, OcrText, Transcript
from app.processing.media.engines import build_store
from app.processing.media.pipeline import attach_media, media_text_for
from app.storage.media import MediaRejectedError
from app.workers.dispatch import DispatchError, ImportDispatcher

log = logging.getLogger(__name__)
router = APIRouter(prefix="/content", tags=["content"], dependencies=[Depends(require_api_key)])

MAX_FILES_PER_REQUEST = 20


def get_dispatcher(request: Request) -> ImportDispatcher:
    return request.app.state.dispatcher


def _asset_out(a: MediaAsset) -> dict[str, Any]:
    return {"id": str(a.id), "asset_type": a.asset_type, "position": a.position,
            "mime_type": a.mime_type, "file_size": a.file_size,
            "original_filename": a.original_filename, "status": a.processing_status,
            "error": a.error_message,
            "processed_at": a.processed_at.isoformat() if a.processed_at else None}


@router.post("/{content_id}/media", status_code=status.HTTP_202_ACCEPTED)
def upload_media(
    content_id: uuid.UUID,
    files: list[UploadFile],
    session: Session = Depends(get_session),
    dispatcher: ImportDispatcher = Depends(get_dispatcher),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if session.get(ContentItem, content_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Content not found")
    if not files or len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(422, f"Upload 1–{MAX_FILES_PER_REQUEST} files")
    store = build_store(settings)
    results = []
    for f in files:
        try:
            asset, created = attach_media(session, store, content_id, f.file, f.filename)
        except MediaRejectedError as exc:
            results.append({"filename": f.filename, "error": str(exc)})
            continue
        if created or asset.processing_status == "failed":
            try:
                dispatcher.enqueue_media(asset.id)
            except DispatchError as exc:
                log.warning("media_enqueue_failed", extra={"asset_id": str(asset.id),
                                                           "error": str(exc)})
        session.refresh(asset)
        results.append({**_asset_out(asset), "filename": f.filename, "duplicate": not created})
    if not any(r.get("id") for r in results):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, {"files": results})
    return {"content_id": str(content_id), "files": results}


@router.get("/{content_id}/media")
def get_media(content_id: uuid.UUID, session: Session = Depends(get_session)) -> dict[str, Any]:
    if session.get(ContentItem, content_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Content not found")
    assets = session.scalars(select(MediaAsset).where(MediaAsset.content_id == content_id)
                             .order_by(MediaAsset.position)).all()
    text = media_text_for(session, content_id)
    transcripts = session.scalars(select(Transcript).where(Transcript.content_id == content_id))
    return {
        "content_id": str(content_id),
        "assets": [_asset_out(a) for a in assets],
        "on_screen_text": text.on_screen_text,
        "transcripts": [{"media_asset_id": str(t.media_asset_id), "language": t.language,
                         "text": t.transcript_text, "segments": t.timestamped_segments,
                         "provider": t.provider, "model": t.model} for t in transcripts],
        "ocr_frames": session.scalar(select(func.count()).where(
            OcrText.content_id == content_id)),
    }
