"""Find imports that need a (re)run: crashed runners and never-dispatched batches."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import ImportStatus
from app.db.models import ImportBatch


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def find_recoverable_batches(
    session: Session,
    *,
    now: datetime,
    stale_after: timedelta,
    pending_grace: timedelta,
) -> list[uuid.UUID]:
    """Batches to re-enqueue.

    - RUNNING with a heartbeat older than ``stale_after`` (the runner died).
    - PENDING for longer than ``pending_grace`` (enqueue failed, e.g. Redis was down).
    Filtering is done in Python so timezone handling is identical on SQLite and PostgreSQL.
    """
    rows = session.execute(
        select(ImportBatch.id, ImportBatch.status, ImportBatch.heartbeat_at,
               ImportBatch.created_at)
        .where(ImportBatch.status.in_([ImportStatus.RUNNING, ImportStatus.PENDING]))
        .order_by(ImportBatch.created_at)
    ).all()
    due: list[uuid.UUID] = []
    for batch_id, status, heartbeat_at, created_at in rows:
        if status == ImportStatus.RUNNING:
            hb = _aware(heartbeat_at)
            if hb is None or now - hb >= stale_after:
                due.append(batch_id)
        elif now - _aware(created_at) >= pending_grace:
            due.append(batch_id)
    return due
