"""Celery application.

Reliability settings (see docs/DECISIONS.md, ADR-016):
- ``task_acks_late`` + ``task_reject_on_worker_lost``: a task is acknowledged only after it
  finishes, so a worker crash leaves it in the queue for redelivery.
- ``worker_prefetch_multiplier=1``: a worker never holds back tasks it isn't running.
- A periodic sweep (``instabrain.imports.sweep``) re-enqueues imports whose runner died, so
  recovery does not depend on broker redelivery alone.
"""

from __future__ import annotations

from celery import Celery, signals

from app.core.config import get_settings
from app.core.logging import configure_logging

IMPORT_QUEUE = "instabrain.imports"


def create_celery() -> Celery:
    settings = get_settings()
    app = Celery("instabrain", broker=settings.redis_url, include=["app.workers.tasks"])
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_ignore_result=True,
        task_serializer="json",
        accept_content=["json"],
        task_default_queue=IMPORT_QUEUE,
        broker_connection_retry_on_startup=True,
        broker_transport_options={
            "visibility_timeout": settings.broker_visibility_timeout_seconds
        },
        worker_hijack_root_logger=False,
        timezone="UTC",
        beat_schedule={
            "sweep-imports": {
                "task": "instabrain.imports.sweep",
                "schedule": float(settings.import_sweep_interval_seconds),
            }
        },
    )
    return app


celery_app = create_celery()


@signals.setup_logging.connect
def _setup_logging(**_: object) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)


@signals.worker_process_init.connect
def _reset_db_pool(**_: object) -> None:
    # Never share pooled DB connections across forked worker processes.
    from app.db.session import get_engine

    if get_engine.cache_info().currsize:
        get_engine().dispose(close=False)


@signals.worker_ready.connect
def _sweep_on_start(sender=None, **_: object) -> None:
    # Pick up interrupted work as soon as a worker comes back, not only on the next beat tick.
    celery_app.send_task("instabrain.imports.sweep")
