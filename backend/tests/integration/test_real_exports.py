"""Smoke-tests real exports without committing them.

Picks up exports from tests/fixtures/real/ (ZIP or folder) and from the
LORE_REAL_EXPORT environment variable (os.pathsep-separated paths).
"""

import os
from pathlib import Path

import pytest

from app.ingestion.instagram.archive import ExportArchive
from app.ingestion.instagram.inspector import inspect_export
from tests.conftest import REAL_EXPORTS


def _exports() -> list[Path]:
    found = []
    if REAL_EXPORTS.exists():
        found += [p for p in REAL_EXPORTS.glob("*") if p.suffix == ".zip" or p.is_dir()]
    env = os.environ.get("LORE_REAL_EXPORT")
    if env:
        found += [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    return sorted(found)


EXPORTS = _exports()


@pytest.mark.skipif(not EXPORTS, reason="no real exports (tests/fixtures/real/ or "
                                        "LORE_REAL_EXPORT)")
@pytest.mark.parametrize("export", EXPORTS, ids=lambda p: p.name)
def test_real_export_parses(export):
    with ExportArchive(export) as archive:
        report = inspect_export(archive)
    assert report.parsed_files, "no saved/message files recognised"
    assert not report.errors, [f"{e.path}: {e.message}" for e in report.errors]
    assert not report.warnings, [f"{w.locator}: {w.message}" for w in report.warnings[:20]]
    assert report.url_occurrences > 0
    assert "" not in report.collections, "collection without a name"
