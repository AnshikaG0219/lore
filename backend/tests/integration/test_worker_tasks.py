"""Celery task behaviour, run eagerly (no broker): retry policy, idempotency, recovery sweep."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.db.enums import ImportStatus
from app.db.models import ImportBatch
from app.ingestion.importer import create_batch
from app.workers import tasks
from app.workers.recovery import find_recoverable_batches


@pytest.fixture
def worker_env(session_factory, monkeypatch):
    settings = Settings(import_task_max_retries=2, import_heartbeat_stale_seconds=300,
                        import_pending_grace_seconds=120)
    monkeypatch.setattr(tasks, "_factory", lambda: session_factory)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    return session_factory


def _batch(factory, path):
    with factory() as s:
        return create_batch(s, path).id


def _set(factory, batch_id, **fields):
    with factory() as s:
        b = s.get(ImportBatch, batch_id)
        for k, v in fields.items():
            setattr(b, k, v)
        s.commit()


def test_backoff_is_exponential_and_capped():
    assert [tasks.backoff_seconds(n) for n in range(4)] == [5, 10, 20, 40]
    assert tasks.backoff_seconds(20) == tasks.MAX_BACKOFF_SECONDS


def test_task_runs_import(worker_env, synthetic_zip):
    batch_id = _batch(worker_env, synthetic_zip)
    result = tasks.run_import_task.apply(args=[str(batch_id)])
    assert result.successful()
    assert result.result["status"] == ImportStatus.COMPLETED_WITH_ERRORS


def test_duplicate_delivery_is_a_noop(worker_env, synthetic_zip, monkeypatch):
    batch_id = _batch(worker_env, synthetic_zip)
    tasks.run_import_task.apply(args=[str(batch_id)])
    monkeypatch.setattr(tasks, "run_import", lambda *a, **k: pytest.fail("must not rerun"))
    result = tasks.run_import_task.apply(args=[str(batch_id)])
    assert result.result["skipped"] is True


def test_unknown_batch(worker_env):
    result = tasks.run_import_task.apply(args=["00000000-0000-0000-0000-000000000000"])
    assert result.result["status"] == "missing"


def test_transient_db_error_is_retried(worker_env, synthetic_zip, monkeypatch):
    batch_id = _batch(worker_env, synthetic_zip)
    real = tasks.run_import
    attempts = []

    def flaky(*args, **kwargs):
        attempts.append(kwargs.get("allow_takeover"))
        if len(attempts) == 1:
            raise OperationalError("x", {}, Exception("connection refused"))
        return real(*args, **kwargs)

    monkeypatch.setattr(tasks, "run_import", flaky)
    result = tasks.run_import_task.apply(args=[str(batch_id)])  # eager: retries run inline
    assert result.successful() and len(attempts) == 2


def test_live_runner_causes_bounded_retries(worker_env, synthetic_zip):
    batch_id = _batch(worker_env, synthetic_zip)
    _set(worker_env, batch_id, status=ImportStatus.RUNNING, heartbeat_at=datetime.now(UTC))
    result = tasks.run_import_task.apply(args=[str(batch_id)])
    assert result.failed()  # gave up after max retries; the sweep remains the safety net
    with worker_env() as s:
        assert s.get(ImportBatch, batch_id).status == ImportStatus.RUNNING  # untouched


def test_unexpected_error_marks_batch_failed(worker_env, synthetic_zip, monkeypatch):
    batch_id = _batch(worker_env, synthetic_zip)

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(tasks, "run_import", boom)
    assert tasks.run_import_task.apply(args=[str(batch_id)]).failed()
    with worker_env() as s:
        b = s.get(ImportBatch, batch_id)
        assert b.status == ImportStatus.FAILED and "bug" in b.error_message


def test_find_recoverable_batches(session_factory, synthetic_zip):
    now = datetime.now(UTC)
    ids = {name: _batch(session_factory, synthetic_zip) for name in
           ("stale", "live", "never_beat", "old_pending", "new_pending", "done")}
    _set(session_factory, ids["stale"], status=ImportStatus.RUNNING,
         heartbeat_at=now - timedelta(minutes=10))
    _set(session_factory, ids["live"], status=ImportStatus.RUNNING,
         heartbeat_at=now - timedelta(seconds=30))
    _set(session_factory, ids["never_beat"], status=ImportStatus.RUNNING, heartbeat_at=None)
    _set(session_factory, ids["old_pending"], created_at=now - timedelta(minutes=10))
    _set(session_factory, ids["done"], status=ImportStatus.COMPLETED,
         created_at=now - timedelta(days=1))
    with session_factory() as s:
        due = find_recoverable_batches(s, now=now, stale_after=timedelta(minutes=5),
                                       pending_grace=timedelta(minutes=2))
    assert set(due) == {ids["stale"], ids["never_beat"], ids["old_pending"]}


def test_sweep_enqueues_recoverable(worker_env, synthetic_zip, monkeypatch):
    stale = _batch(worker_env, synthetic_zip)
    _set(worker_env, stale, status=ImportStatus.RUNNING,
         heartbeat_at=datetime.now(UTC) - timedelta(hours=1))
    sent = []
    monkeypatch.setattr(tasks.run_import_task, "apply_async",
                        lambda args, **kw: sent.append(args[0]))
    assert tasks.sweep_imports_task.apply().result == [str(stale)]
    assert sent == [str(stale)]


def test_crashed_import_is_recovered_by_sweep(worker_env, synthetic_zip, monkeypatch):
    """End-to-end recovery: runner dies mid-import → heartbeat goes stale → sweep resumes it."""
    from app.ingestion.importer import run_import

    batch_id = _batch(worker_env, synthetic_zip)

    class Crash(Exception):
        pass

    def die(path):
        raise Crash

    with pytest.raises(Crash):
        run_import(worker_env, batch_id, on_file_committed=die)
    _set(worker_env, batch_id, heartbeat_at=datetime.now(UTC) - timedelta(hours=1))

    monkeypatch.setattr(tasks.run_import_task, "apply_async",
                        lambda args, **kw: tasks.run_import_task.apply(args=args))
    tasks.sweep_imports_task.apply()
    with worker_env() as s:
        b = s.get(ImportBatch, batch_id)
        assert b.status == ImportStatus.COMPLETED_WITH_ERRORS and b.records_imported == 11
