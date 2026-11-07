"""Celery tasks for the content processing queue. State lives in ``processing_jobs``."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from celery import signals

from app.core.config import get_settings
from app.processing import queue as q
from app.processing.factory import build_runner
from app.processing.pipeline import PipelineRunner
from app.workers.celery_app import celery_app
from app.workers.tasks import _factory

log = logging.getLogger(__name__)
_runner: PipelineRunner | None = None


def _get_runner() -> PipelineRunner:
    global _runner
    if _runner is None:  # one per worker process: Whisper model + AI client are reused
        _runner = build_runner(get_settings(), _factory())
    return _runner


@signals.worker_process_shutdown.connect
def _close_runner(**_: object) -> None:
    if _runner is not None:
        _runner.close()


@celery_app.task(name="instabrain.pipeline.run")
def run_pipeline_task(job_id: str) -> dict[str, Any]:
    return _get_runner().run(uuid.UUID(job_id))


def dispatch(job_ids: list[uuid.UUID]) -> int:
    """Send run messages and record dispatch time. Lost messages are re-sent by the sweep."""
    sent = []
    for job_id in job_ids:
        try:
            run_pipeline_task.apply_async(args=[str(job_id)], retry=False)
            sent.append(job_id)
        except Exception as exc:
            log.warning("pipeline_dispatch_failed", extra={"job_id": str(job_id),
                                                           "error": str(exc)})
            break
    if sent:
        with _factory()() as session:
            q.mark_dispatched(session, sent)
    return len(sent)


@celery_app.task(name="instabrain.pipeline.sweep")
def sweep_pipeline_task() -> int:
    settings = get_settings()
    with _factory()() as session:
        due = q.due_jobs(session,
                         stale_after=timedelta(seconds=settings.pipeline_stale_seconds),
                         redispatch_after=timedelta(seconds=settings.pipeline_redispatch_seconds),
                         limit=settings.pipeline_sweep_batch)
    return dispatch(due) if due else 0
