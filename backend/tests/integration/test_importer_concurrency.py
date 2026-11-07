"""Concurrency and failure semantics of the importer (fencing token, transient DB errors)."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db.enums import ImportFileStatus, ImportStatus
from app.db.models import ContentSource, ImportBatch, ImportFile
from app.ingestion import importer
from app.ingestion.importer import ClaimLostError, _claim, create_batch, run_import


def _batch(factory, path):
    with factory() as s:
        return create_batch(s, path).id


def test_superseded_runner_stops_and_counts_stay_correct(session_factory, synthetic_zip):
    batch_id = _batch(session_factory, synthetic_zip)
    seen = []

    def steal_after_first(path):
        seen.append(path)
        if len(seen) == 1:  # another runner takes over while this one is mid-import
            with session_factory() as s:
                _claim(s, batch_id, allow_takeover=True, stale_after=timedelta(minutes=5))

    with pytest.raises(ClaimLostError):
        run_import(session_factory, batch_id, on_file_committed=steal_after_first)
    assert len(seen) == 1  # stopped before writing anything else

    report = run_import(session_factory, batch_id, allow_takeover=True)
    assert report["references"]["imported"] == 11
    with session_factory() as s:
        b = s.get(ImportBatch, batch_id)
        assert b.records_imported == 11 and b.runner_id is None
        assert s.scalar(select(func.count()).select_from(ContentSource)) == 11


def test_transient_db_error_leaves_file_pending(session_factory, synthetic_zip, monkeypatch):
    batch_id = _batch(session_factory, synthetic_zip)
    real = importer._persist_file
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))
        return real(*args, **kwargs)

    monkeypatch.setattr(importer, "_persist_file", flaky)
    with pytest.raises(OperationalError):
        run_import(session_factory, batch_id)

    with session_factory() as s:
        b = s.get(ImportBatch, batch_id)
        assert b.status == ImportStatus.RUNNING and b.failures == 0  # not a permanent failure
        assert s.scalar(select(func.count()).where(
            ImportFile.batch_id == batch_id, ImportFile.status == ImportFileStatus.FAILED)) == 0

    monkeypatch.setattr(importer, "_persist_file", real)
    report = run_import(session_factory, batch_id, allow_takeover=True)
    assert report["references"]["imported"] == 11 and report["failures"] == 1  # malformed file


def test_archive_path_is_stored_absolute(session_factory, synthetic_zip, monkeypatch):
    monkeypatch.chdir(synthetic_zip.parent)
    from pathlib import Path

    batch_id = _batch(session_factory, Path(synthetic_zip.name))
    with session_factory() as s:
        assert Path(s.get(ImportBatch, batch_id).archive_path).is_absolute()
