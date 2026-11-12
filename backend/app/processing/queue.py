"""The content processing queue: one durable job per content item (PRD §12).

PostgreSQL is the source of truth; Celery only delivers "run job X" messages. Deliveries can be
lost or duplicated without harm: the periodic sweep re-dispatches due jobs, and ``claim`` lets
only one runner own a job at a time (fencing token, as for imports — ADR-017).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.enums import ProcessingStatus
from app.db.models import AuditLog, ContentItem, ProcessingJob

PIPELINE_JOB = "content_pipeline"


class JobStatus:
    QUEUED = "queued"          # waiting to run
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"  # transient failure; runs again at next_attempt_at
    COMPLETED = "completed"
    NEEDS_MANUAL_INPUT = "needs_manual_input"
    FAILED = "failed"          # gave up; manual reprocess re-arms it


ACTIVE = frozenset({JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRY_WAIT})
TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.NEEDS_MANUAL_INPUT, JobStatus.FAILED})


class JobClaimLostError(Exception):
    pass


def now() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=UTC))


@dataclass(slots=True)
class EnqueueResult:
    job_id: uuid.UUID
    created: bool
    rearmed: bool

    @property
    def should_dispatch(self) -> bool:
        return self.created or self.rearmed


def enqueue(session: Session, content_id: uuid.UUID, *, reason: str, force: bool = False,
            priority: int = 100, max_attempts: int = 5) -> EnqueueResult:
    """Ensure the content item has a job waiting to run. Idempotent.

    - no job yet → create it
    - job active (queued/running/retry_wait) → no-op (that run will pick up the latest state)
    - job finished → re-arm only when ``force`` (manual reprocess, new media…)
    The caller commits.
    """
    job = session.scalar(select(ProcessingJob).where(
        ProcessingJob.content_id == content_id, ProcessingJob.job_type == PIPELINE_JOB))
    if job is None:
        job = ProcessingJob(content_id=content_id, job_type=PIPELINE_JOB,
                            status=JobStatus.QUEUED, stage=ProcessingStatus.QUEUED,
                            priority=priority, reason=reason, max_attempts=max_attempts,
                            retry_count=0, result={}, queued_at=now())
        try:
            with session.begin_nested():
                session.add(job)
        except IntegrityError:  # concurrent enqueue created it first
            return enqueue(session, content_id, reason=reason, force=force,
                           priority=priority, max_attempts=max_attempts)
        _set_content_status(session, content_id, ProcessingStatus.QUEUED)
        return EnqueueResult(job.id, created=True, rearmed=False)

    if job.status in ACTIVE:
        if job.status == JobStatus.RETRY_WAIT and force:
            job.next_attempt_at = now()
            job.status = JobStatus.QUEUED
            return EnqueueResult(job.id, created=False, rearmed=True)
        return EnqueueResult(job.id, created=False, rearmed=False)

    if not force:
        return EnqueueResult(job.id, created=False, rearmed=False)
    job.status, job.stage = JobStatus.QUEUED, ProcessingStatus.QUEUED
    job.reason, job.priority = reason, priority
    job.retry_count, job.error_message, job.next_attempt_at = 0, None, None
    job.runner_id, job.completed_at, job.queued_at = None, None, now()
    _set_content_status(session, content_id, ProcessingStatus.QUEUED)
    return EnqueueResult(job.id, created=False, rearmed=True)


def _set_content_status(session: Session, content_id: uuid.UUID, status: str) -> None:
    item = session.get(ContentItem, content_id)
    if item is not None:
        item.processing_status = status


def claim(session: Session, job_id: uuid.UUID, stale_after: timedelta) -> str | None:
    """Take ownership of a runnable job. Returns a fencing token, or None if not runnable."""
    job = session.get(ProcessingJob, job_id, with_for_update=True, populate_existing=True)
    if job is None or job.status in TERMINAL:
        return None
    if job.status == JobStatus.RUNNING:
        hb = aware(job.heartbeat_at)
        if hb and now() - hb < stale_after:
            return None  # another runner is alive
    if job.status == JobStatus.RETRY_WAIT and aware(job.next_attempt_at) and \
            aware(job.next_attempt_at) > now():
        return None  # not due yet
    token = str(uuid.uuid4())
    job.status, job.runner_id = JobStatus.RUNNING, token
    job.heartbeat_at = now()
    job.started_at = job.started_at if job.retry_count else now()
    session.commit()
    return token


def owned(session: Session, job_id: uuid.UUID, token: str) -> ProcessingJob:
    job = session.get(ProcessingJob, job_id, with_for_update=True, populate_existing=True)
    if job is None or job.runner_id != token:
        raise JobClaimLostError(str(job_id))
    return job


def set_stage(session: Session, job_id: uuid.UUID, token: str, stage: str) -> None:
    job = owned(session, job_id, token)
    job.stage, job.heartbeat_at = stage, now()
    _set_content_status(session, job.content_id, stage)
    session.commit()


def finish(session: Session, job_id: uuid.UUID, token: str, status: str, *,
           content_status: str, result: dict | None = None, error: str | None = None) -> None:
    job = owned(session, job_id, token)
    job.status, job.stage, job.runner_id = status, content_status, None
    job.completed_at, job.heartbeat_at = now(), now()
    job.error_message = error
    if result is not None:
        job.result = result
    _set_content_status(session, job.content_id, content_status)
    session.add(AuditLog(entity_type="content_item", entity_id=job.content_id,
                         action=f"pipeline.{status}",
                         details={"job_id": str(job.id), "attempt": job.retry_count + 1,
                                  "error": error, **(result or {})}))
    session.commit()


def retry_later(session: Session, job_id: uuid.UUID, token: str, error: str,
                backoff: timedelta) -> bool:
    """Schedule a retry, or fail when attempts are exhausted. Returns True if retrying."""
    job = owned(session, job_id, token)
    job.retry_count += 1
    if job.retry_count >= job.max_attempts:
        session.commit()
        finish(session, job_id, token, JobStatus.FAILED, content_status=ProcessingStatus.FAILED,
               error=f"Gave up after {job.retry_count} attempts: {error}")
        return False
    job.status, job.runner_id, job.error_message = JobStatus.RETRY_WAIT, None, error[:2000]
    job.next_attempt_at = now() + backoff
    session.add(AuditLog(entity_type="content_item", entity_id=job.content_id,
                         action="pipeline.retry_scheduled",
                         details={"job_id": str(job.id), "attempt": job.retry_count,
                                  "error": error[:500], "retry_in_s": backoff.total_seconds()}))
    session.commit()
    return True


def due_jobs(session: Session, *, stale_after: timedelta, redispatch_after: timedelta,
             limit: int = 500, broker_backlog: int = 0) -> list[uuid.UUID]:
    """Jobs the sweep should (re)dispatch, highest priority first.

    - queued and never dispatched, or dispatched long ago (message lost)
    - retry_wait whose next_attempt_at has passed
    - running with a stale heartbeat (worker died)
    """
    t = now()
    rows = session.execute(
        select(ProcessingJob.id, ProcessingJob.status, ProcessingJob.dispatched_at,
               ProcessingJob.next_attempt_at, ProcessingJob.heartbeat_at)
        .where(ProcessingJob.status.in_(list(ACTIVE)))
        .order_by(ProcessingJob.priority, ProcessingJob.queued_at)
    ).all()
    due = []
    for job_id, status, dispatched_at, next_at, hb in rows:
        if status == JobStatus.QUEUED:
            d = aware(dispatched_at)
            # Re-send a waiting job only if its message can actually have been lost: if the
            # broker still holds messages, a long backlog is just slow, not lost (re-sending
            # it flooded the queue with duplicates during the 2,500-post run).
            ok = d is None or (t - d >= redispatch_after and broker_backlog == 0)
        elif status == JobStatus.RETRY_WAIT:
            ok = aware(next_at) is None or aware(next_at) <= t
        else:
            ok = aware(hb) is None or t - aware(hb) >= stale_after
        if ok:
            due.append(job_id)
            if len(due) >= limit:
                break
    return due


def mark_dispatched(session: Session, job_ids: list[uuid.UUID]) -> None:
    for job in session.scalars(select(ProcessingJob).where(ProcessingJob.id.in_(job_ids))):
        job.dispatched_at = now()
    session.commit()
