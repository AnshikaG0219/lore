"""Smoke-tests every export placed under tests/fixtures/real/ (ZIP or extracted folder)."""

import pytest

from app.ingestion.instagram.archive import ExportArchive
from app.ingestion.instagram.inspector import inspect_export
from tests.conftest import REAL_EXPORTS

EXPORTS = sorted(p for p in REAL_EXPORTS.glob("*") if p.suffix == ".zip" or p.is_dir()) \
    if REAL_EXPORTS.exists() else []


@pytest.mark.skipif(not EXPORTS, reason="no real exports in tests/fixtures/real/")
@pytest.mark.parametrize("export", EXPORTS, ids=lambda p: p.name)
def test_real_export_parses(export):
    with ExportArchive(export) as archive:
        report = inspect_export(archive)
    assert report.parsed_files, "no saved/message files recognised"
    assert not report.errors, [f"{e.path}: {e.message}" for e in report.errors]
    assert report.url_occurrences > 0
