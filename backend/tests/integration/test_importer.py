import json
import shutil

import pytest
from sqlalchemy import func, select

from app.db.enums import ImportFileStatus, ImportStatus
from app.db.models import ContentItem, ContentSource, ImportBatch, ImportFile, RawImportRecord
from app.ingestion.importer import ImportInProgressError, create_batch, run_import
from tests.conftest import SYNTHETIC_EXPORT, zip_directory


def _new_batch(factory, path):
    with factory() as s:
        return create_batch(s, path).id


def _items(factory):
    with factory() as s:
        return {i.platform_content_id or i.canonical_url: i
                for i in s.scalars(select(ContentItem))}


def test_full_import(session_factory, synthetic_zip):
    batch_id = _new_batch(session_factory, synthetic_zip)
    report = run_import(session_factory, batch_id)

    assert report["status"] == ImportStatus.COMPLETED_WITH_ERRORS  # the malformed file
    assert report["failures"] == 1
    refs = report["references"]
    assert refs["found"] == 11  # 12 URLs minus 1 profile URL
    assert refs["content_items_created"] == 5
    assert refs["imported"] == 11
    assert refs["duplicates_consolidated"] == 6
    assert refs["non_content_urls"] == {"profile": 1}
    assert refs["records_without_url"] == 1
    assert report["saved_collections"] == {"Ladakh Trip": 2, "Cafés & Books": 1, "Empty Ideas": 0}
    assert report["conversations"]["group"] == 1

    items = _items(session_factory)
    assert set(items) == {"C1aBcDeFgHi", "C2xYz_12345", "CaseSens01", "casesens01",
                          "https://www.instagram.com/share/reel/BAbcdEfgh1/"}
    reel = items["C1aBcDeFgHi"]
    assert reel.content_type == "reel"
    assert reel.canonical_url == "https://www.instagram.com/reel/C1aBcDeFgHi/"
    assert items["CaseSens01"].content_type == "reel"  # seen as /p/, /reel/ and /tv/
    assert items["C2xYz_12345"].creator_username == "café_hunter"
    assert items["https://www.instagram.com/share/reel/BAbcdEfgh1/"].extracted_data == {
        "needs_url_resolution": True}

    with session_factory() as s:
        sources = s.scalars(select(ContentSource).where(ContentSource.content_id == reel.id)).all()
        kinds = sorted((x.source_type, x.source_name) for x in sources)
        assert kinds == [("group_message", "Weekend Plans"), ("saved_collection", "Ladakh Trip"),
                         ("saved_post", "Saved"), ("saved_post", "Saved")]
        group = next(x for x in sources if x.source_type == "group_message")
        assert group.sender == "Priya"
        assert group.captured_at is not None
        assert group.meta["conversation_id"] == "inbox/weekendplans_123"
        assert "utm_source" in group.original_url  # original preserved

        raws = s.scalars(select(RawImportRecord)).all()
        assert raws and all("must never be stored" not in json.dumps(r.record) for r in raws)
        assert sum(1 for r in raws if not r.resolved) == 1

        statuses = dict(s.execute(select(ImportFile.status, func.count())
                                  .group_by(ImportFile.status)).all())
        assert statuses[ImportFileStatus.FAILED] == 1
        assert statuses[ImportFileStatus.SKIPPED] == 2  # html + jpg are never opened


def test_reimport_is_idempotent(session_factory, synthetic_zip, tmp_path):
    run_import(session_factory, _new_batch(session_factory, synthetic_zip))
    copy = shutil.copy(synthetic_zip, tmp_path / "again.zip")
    report = run_import(session_factory, _new_batch(session_factory, copy))
    assert report["references"]["imported"] == 0
    assert report["references"]["already_imported"] == 11
    assert report["references"]["content_items_created"] == 0
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(ContentItem)) == 5
        assert s.scalar(select(func.count()).select_from(ContentSource)) == 11


class Crash(Exception):
    pass


def test_resume_after_crash(session_factory, synthetic_zip):
    batch_id = _new_batch(session_factory, synthetic_zip)
    processed = []

    def crash_after_two(path):
        processed.append(path)
        if len(processed) == 2:
            raise Crash

    with pytest.raises(Crash):
        run_import(session_factory, batch_id, on_file_committed=crash_after_two)

    with session_factory() as s:
        b = s.get(ImportBatch, batch_id)
        assert b.status == ImportStatus.RUNNING and b.processed_files == 2

    with pytest.raises(ImportInProgressError):  # heartbeat is fresh
        run_import(session_factory, batch_id)

    resumed = []
    report = run_import(session_factory, batch_id, allow_takeover=True,
                        on_file_committed=resumed.append)
    assert not set(resumed) & set(processed)  # completed files are not reprocessed
    assert report["references"]["imported"] == 11
    assert report["references"]["content_items_created"] == 5
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(ContentSource)) == 11
        assert s.get(ImportBatch, batch_id).processed_files == len(processed) + len(resumed)


def test_invalid_archive_marks_batch_failed(session_factory, tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"garbage")
    report = run_import(session_factory, _new_batch(session_factory, bad))
    assert report["status"] == ImportStatus.FAILED
    assert "not a valid ZIP" in report["error_message"]


def test_directory_import(session_factory, tmp_path):
    report = run_import(session_factory, _new_batch(session_factory, SYNTHETIC_EXPORT))
    assert report["references"]["content_items_created"] == 5


def test_zip_with_rejected_member_is_reported(session_factory, tmp_path):
    import zipfile
    p = zip_directory(SYNTHETIC_EXPORT, tmp_path / "e.zip")
    with zipfile.ZipFile(p, "a") as zf:
        zf.writestr("../escape.json", "{}")
    report = run_import(session_factory, _new_batch(session_factory, p))
    assert any("Rejected: unsafe path" in e["message"] for e in report["errors"])
    assert report["failures"] == 2
