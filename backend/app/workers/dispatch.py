"""How the API/CLI hand an import to the background system.

``CeleryImportDispatcher`` is the production path. ``InlineImportDispatcher`` runs the import
synchronously in the caller; it exists for tests and for local use without Redis.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.ingestion.importer import ImportInProgressError, limits_from_settings, run_import

log = logging.getLogger(__name__)


class DispatchError(Exception):
    pass


class ImportDispatcher(Protocol):
    def enqueue_import(self, batch_id: uuid.UUID) -> None: ...

    def is_healthy(self) -> bool: ...


class CeleryImportDispatcher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def enqueue_import(self, batch_id: uuid.UUID) -> None:
        from app.workers.tasks import run_import_task

        try:
            run_import_task.apply_async(args=[str(batch_id)], retry=False)
        except Exception as exc:  # broker unreachable; the periodic sweep will pick it up
            raise DispatchError(str(exc)) from exc

    def is_healthy(self) -> bool:
        import redis

        try:
            client = redis.Redis.from_url(self.settings.redis_url, socket_timeout=1,
                                          socket_connect_timeout=1)
            return bool(client.ping())
        except Exception:
            return False


class InlineImportDispatcher:
    def __init__(self, session_factory: sessionmaker[Session], settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings

    def enqueue_import(self, batch_id: uuid.UUID) -> None:
        try:
            run_import(self.session_factory, batch_id, limits_from_settings(self.settings),
                       stale_after=timedelta(seconds=self.settings.import_heartbeat_stale_seconds))
        except ImportInProgressError:
            log.warning("import_already_running", extra={"batch_id": str(batch_id)})

    def is_healthy(self) -> bool:
        return True


def build_dispatcher(
    settings: Settings, session_factory: sessionmaker[Session]
) -> ImportDispatcher:
    if settings.task_backend == "inline":
        return InlineImportDispatcher(session_factory, settings)
    return CeleryImportDispatcher(settings)
