import json

from app.ingestion.instagram.archive import ExportArchive
from app.ingestion.instagram.inspector import format_report, inspect_export
from tests.conftest import SYNTHETIC_EXPORT


def _check(report):
    assert report.export.layout == "your_instagram_activity"
    assert report.export.root_prefix == "instagram-testuser-2026-11-01-abc123"
    assert report.saved_posts == 3
    assert report.saved_records_without_url == 1
    assert report.collections == {"Ladakh Trip": 2, "Cafés & Books": 1, "Empty Ideas": 0}
    d = report.to_dict()
    assert d["conversation_summary"] == {"total": 2, "group": 1, "direct": 1,
                                         "with_instagram_links": 2}
    weekend = next(c for c in report.conversations if c.title == "Weekend Plans")
    assert weekend.message_count == 5 and weekend.messages_with_links == 4  # across 2 files

    assert report.urls_by_kind == {"reel": 4, "post": 5, "tv": 1, "profile": 1, "share_link": 1}
    assert report.unique_content_items == 5
    assert report.duplicate_content_items == 3
    by_url = {d["canonical_url"]: d for d in report.duplicates}
    assert by_url["https://www.instagram.com/reel/C1aBcDeFgHi/"]["occurrences"] == 4
    assert by_url["https://www.instagram.com/reel/CaseSens01/"]["occurrences"] == 3
    assert report.unresolved_share_links == ["https://www.instagram.com/share/reel/BAbcdEfgh1/"]

    assert [p.rsplit("/", 1)[-1] for p in report.unsupported_files] == ["start_here.html"]
    assert any(p.endswith("liked_posts.json") for p in report.unrecognised_json_files)
    assert len(report.errors) == 1 and "Malformed JSON" in report.errors[0].message
    assert "broken_789" in report.errors[0].path
    assert any("no Instagram URL" in w.message for w in report.warnings)


def test_inspect_zip(synthetic_zip):
    with ExportArchive(synthetic_zip) as a:
        report = inspect_export(a)
    _check(report)
    text = format_report(report)
    assert "Saved posts identified: 3" in text
    assert "Duplicate content items (seen in >1 source): 3" in text
    json.dumps(report.to_dict(), default=str)  # serialisable


def test_inspect_directory_matches_zip():
    with ExportArchive(SYNTHETIC_EXPORT) as a:
        _check(inspect_export(a))


def test_private_message_text_not_in_report(synthetic_zip):
    with ExportArchive(synthetic_zip) as a:
        payload = json.dumps(inspect_export(a).to_dict(), default=str)
    assert "must never be stored" not in payload
