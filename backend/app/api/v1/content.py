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
from app.db.models import ContentItem, MediaAsset, OcrText, ProcessingJob, Transcript
from app.processing import queue
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
    results, new_media = [], False
    for f in files:
        try:
            asset, created = attach_media(session, store, content_id, f.file, f.filename)
        except MediaRejectedError as exc:
            results.append({"filename": f.filename, "error": str(exc)})
            continue
        new_media = new_media or created or asset.processing_status == "failed"
        results.append({"id": str(asset.id), "filename": f.filename, "duplicate": not created})
    if not any(r.get("id") for r in results):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, {"files": results})
    job = None
    if new_media:  # new media changes what we know → re-run this item's single job
        enq = queue.enqueue(session, content_id, reason="media_added", force=True, priority=10)
        session.commit()
        job = enq.job_id
        _dispatch(dispatcher, [enq.job_id] if enq.should_dispatch else [])
    by_id = {a.id: a for a in session.scalars(
        select(MediaAsset).where(MediaAsset.content_id == content_id))}
    for r in results:
        if r.get("id"):
            session.refresh(by_id[uuid.UUID(r["id"])])
            r.update(_asset_out(by_id[uuid.UUID(r["id"])]))
    return {"content_id": str(content_id), "job_id": str(job) if job else None, "files": results}


def _dispatch(dispatcher: ImportDispatcher, job_ids: list[uuid.UUID]) -> None:
    try:
        dispatcher.dispatch_pipeline(job_ids)
    except DispatchError as exc:
        log.warning("pipeline_dispatch_failed", extra={"error": str(exc)})


def _job_out(job: ProcessingJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {"id": str(job.id), "content_id": str(job.content_id), "status": job.status,
            "stage": job.stage, "reason": job.reason, "attempts": job.retry_count,
            "max_attempts": job.max_attempts, "error": job.error_message, "result": job.result,
            "next_attempt_at": job.next_attempt_at.isoformat() if job.next_attempt_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None}


@router.post("/{content_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
def reprocess(content_id: uuid.UUID, session: Session = Depends(get_session),
              dispatcher: ImportDispatcher = Depends(get_dispatcher)) -> dict[str, Any]:
    if session.get(ContentItem, content_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Content not found")
    enq = queue.enqueue(session, content_id, reason="manual", force=True, priority=10)
    session.commit()
    _dispatch(dispatcher, [enq.job_id] if enq.should_dispatch else [])
    return {"job": _job_out(session.get(ProcessingJob, enq.job_id)),
            "already_active": not enq.should_dispatch}


@router.get("/{content_id}/job")
def get_job(content_id: uuid.UUID, session: Session = Depends(get_session)) -> dict[str, Any]:
    job = session.scalar(select(ProcessingJob).where(
        ProcessingJob.content_id == content_id, ProcessingJob.job_type == queue.PIPELINE_JOB))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No processing job for this content")
    return _job_out(job)


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
