"""Import endpoints. Imports run in a background task (Phase 1); see docs/DECISIONS.md."""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session, get_session_factory
from app.core.config import Settings, get_settings
from app.core.security import require_api_key
from app.db.enums import ImportStatus
from app.db.models import ImportBatch
from app.ingestion.importer import ImportInProgressError, create_batch, run_import
from app.ingestion.instagram.archive import ArchiveLimits
from app.schemas.imports import ImportBatchDetail, ImportBatchOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/imports", tags=["imports"], dependencies=[Depends(require_api_key)])

_CHUNK = 1024 * 1024
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")


def limits_from(settings: Settings) -> ArchiveLimits:
    return ArchiveLimits(
        max_member_bytes=settings.max_member_bytes,
        max_members=settings.max_archive_members,
        max_compression_ratio=settings.max_compression_ratio,
    )


def _safe_filename(name: str | None) -> str:
    base = Path(name or "export.zip").name
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[:200] or "export.zip"


def _run(factory: sessionmaker[Session], batch_id: uuid.UUID, limits: ArchiveLimits,
         takeover: bool = False) -> None:
    try:
        run_import(factory, batch_id, limits, allow_takeover=takeover)
    except ImportInProgressError:
        log.warning("import_already_running", extra={"batch_id": str(batch_id)})
    except Exception:
        log.exception("import_crashed", extra={"batch_id": str(batch_id)})
        with factory() as s:
            b = s.get(ImportBatch, batch_id)
            if b:
                b.status = ImportStatus.FAILED
                b.error_message = "Unexpected error; use resume to continue"
                s.commit()


@router.post("", response_model=ImportBatchOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_import(
    file: UploadFile,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    factory: sessionmaker[Session] = Depends(get_session_factory),
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
    background.add_task(_run, factory, batch.id, limits_from(settings))
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
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    factory: sessionmaker[Session] = Depends(get_session_factory),
    settings: Settings = Depends(get_settings),
) -> ImportBatch:
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
    if batch.status == ImportStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Import already completed")
    background.add_task(_run, factory, batch.id, limits_from(settings))
    return batch
