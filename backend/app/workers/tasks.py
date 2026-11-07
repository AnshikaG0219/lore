"""Background tasks. Business logic lives in ``app.ingestion.importer``; tasks add retry policy."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from celery import Task
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.models import ImportBatch
from app.db.session import get_sessionmaker
from app.ingestion.importer import (
    FINISHED_STATUSES,
    ClaimLostError,
    ImportInProgressError,
    is_transient_db_error,
    limits_from_settings,
    run_import,
)
from app.workers.celery_app import celery_app
from app.workers.recovery import find_recoverable_batches

log = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 600


def _factory() -> sessionmaker[Session]:
    return get_sessionmaker()


def backoff_seconds(retries: int, base: int = 5) -> int:
    """Exponential backoff: 5, 10, 20, 40 … capped at 10 minutes."""
    return min(base * 2**retries, MAX_BACKOFF_SECONDS)


@celery_app.task(bind=True, name="instabrain.imports.run", max_retries=None)
def run_import_task(self: Task, batch_id: str, takeover: bool = False) -> dict[str, Any]:
    settings = get_settings()
    factory = _factory()
    bid = uuid.UUID(batch_id)
    stale = timedelta(seconds=settings.import_heartbeat_stale_seconds)

    with factory() as session:
        batch = session.get(ImportBatch, bid)
        if batch is None:
            log.warning("import_task_unknown_batch", extra={"batch_id": batch_id})
            return {"batch_id": batch_id, "status": "missing"}
        if batch.status in FINISHED_STATUSES:
            # Duplicate delivery (sweep + broker redelivery) or an already-finished resume.
            return {"batch_id": batch_id, "status": batch.status, "skipped": True}

    try:
        report = run_import(factory, bid, limits_from_settings(settings),
                            allow_takeover=takeover, stale_after=stale)
    except ImportInProgressError as exc:
        # Another runner is alive, or a crashed one's heartbeat is not stale yet.
        raise self.retry(exc=exc, countdown=settings.import_heartbeat_stale_seconds,
                         max_retries=settings.import_task_max_retries) from exc
    except ClaimLostError:
        log.info("import_task_superseded", extra={"batch_id": batch_id})
        return {"batch_id": batch_id, "status": "superseded"}
    except Exception as exc:
        if is_transient_db_error(exc):
            log.warning("import_task_transient_error",
                        extra={"batch_id": batch_id, "retry": self.request.retries})
            raise self.retry(exc=exc, countdown=backoff_seconds(self.request.retries),
                             max_retries=settings.import_task_max_retries) from exc
        log.exception("import_task_failed", extra={"batch_id": batch_id})
        _mark_failed(factory, bid, f"Unexpected error: {exc!r}. Use resume to continue.")
        raise
    return {"batch_id": batch_id, "status": report["status"]}


def _mark_failed(factory: sessionmaker[Session], batch_id: uuid.UUID, message: str) -> None:
    from app.db.enums import ImportStatus

    try:
        with factory() as session:
            batch = session.get(ImportBatch, batch_id)
            if batch is not None and batch.status not in FINISHED_STATUSES:
                batch.status = ImportStatus.FAILED
                batch.error_message = message
                batch.runner_id = None
                session.commit()
    except Exception:
        log.exception("import_mark_failed_error", extra={"batch_id": str(batch_id)})


@celery_app.task(name="instabrain.imports.sweep")
def sweep_imports_task() -> list[str]:
    settings = get_settings()
    with _factory()() as session:
        due = find_recoverable_batches(
            session,
            now=datetime.now(UTC),
            stale_after=timedelta(seconds=settings.import_heartbeat_stale_seconds),
            pending_grace=timedelta(seconds=settings.import_pending_grace_seconds),
        )
    for batch_id in due:
        log.info("import_recovered", extra={"batch_id": str(batch_id)})
        run_import_task.apply_async(args=[str(batch_id)])
    return [str(b) for b in due]
