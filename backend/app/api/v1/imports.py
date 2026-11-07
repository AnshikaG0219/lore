"""Import endpoints. Work is handed to the background worker via the import dispatcher."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.core.config import Settings, get_settings
from app.core.security import require_api_key
from app.db.enums import ImportStatus
from app.db.models import ImportBatch
from app.ingestion.importer import FINISHED_STATUSES, create_batch
from app.schemas.imports import ImportBatchDetail, ImportBatchOut
from app.workers.dispatch import DispatchError, ImportDispatcher

log = logging.getLogger(__name__)
router = APIRouter(prefix="/imports", tags=["imports"], dependencies=[Depends(require_api_key)])

_CHUNK = 1024 * 1024
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")


def get_dispatcher(request: Request) -> ImportDispatcher:
    return request.app.state.dispatcher


def _safe_filename(name: str | None) -> str:
    base = Path(name or "export.zip").name
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[:200] or "export.zip"


def _dispatch(dispatcher: ImportDispatcher, batch_id: uuid.UUID) -> None:
    try:
        dispatcher.enqueue_import(batch_id)
    except DispatchError as exc:
        # The batch is safely persisted as pending; the worker's periodic sweep will run it.
        log.warning("import_enqueue_failed", extra={"batch_id": str(batch_id), "error": str(exc)})


@router.post("", response_model=ImportBatchOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_import(
    file: UploadFile,
    session: Session = Depends(get_session),
    dispatcher: ImportDispatcher = Depends(get_dispatcher),
    settings: Settings = Depends(get_settings),
) -> ImportBatch:
    name = _safe_filename(file.filename)
    if not name.lower().endswith(".zip"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Upload a .zip export")
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.uploads_dir / f"{uuid.uuid4()}-{name}"

    written = 0
    head = b""
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(_CHUNK):
                if not head:
                    head = chunk[:4]
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise HTTPException(413, "Upload exceeds size limit")
                out.write(chunk)
        if not head.startswith(_ZIP_MAGIC):
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "File is not a ZIP archive")
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise

    batch = create_batch(session, dest, filename=name)
    _dispatch(dispatcher, batch.id)
    session.refresh(batch)
    return batch


@router.get("", response_model=list[ImportBatchOut])
def list_imports(
    limit: int = 50, offset: int = 0, session: Session = Depends(get_session)
) -> list[ImportBatch]:
    limit = max(1, min(limit, 200))
    return list(session.scalars(
        select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(limit).offset(offset)
    ))


@router.get("/{batch_id}", response_model=ImportBatchDetail)
def get_import(batch_id: uuid.UUID, session: Session = Depends(get_session)) -> ImportBatch:
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    return batch


@router.post("/{batch_id}/resume", response_model=ImportBatchOut,
             status_code=status.HTTP_202_ACCEPTED)
def resume_import(
    batch_id: uuid.UUID,
    session: Session = Depends(get_session),
    dispatcher: ImportDispatcher = Depends(get_dispatcher),
    settings: Settings = Depends(get_settings),
) -> ImportBatch:
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    if batch.status in FINISHED_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Import already finished")
    if batch.status == ImportStatus.RUNNING and batch.heartbeat_at is not None:
        hb = batch.heartbeat_at if batch.heartbeat_at.tzinfo else \
            batch.heartbeat_at.replace(tzinfo=UTC)
        if datetime.now(UTC) - hb < timedelta(seconds=settings.import_heartbeat_stale_seconds):
            raise HTTPException(status.HTTP_409_CONFLICT, "Import is currently running")
    _dispatch(dispatcher, batch.id)
    session.refresh(batch)
    return batch
