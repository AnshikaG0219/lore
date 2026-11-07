"""Tests against the anonymised fixture mirroring the verified real export structure (2026-11)."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import ContentItem, ContentSource
from app.ingestion.importer import create_batch, run_import
from app.ingestion.instagram.archive import ArchiveMember, ExportArchive
from app.ingestion.instagram.inspector import inspect_export
from app.ingestion.instagram.models import SourceType
from app.ingestion.instagram.reader import parse_member
from tests.conftest import FIXTURES, zip_directory

REAL_SHAPE = FIXTURES / "real_shape_2026_11"
SAVED = "your_instagram_activity/saved/"


def _parse(path):
    with ExportArchive(REAL_SHAPE) as a:
        return parse_member(a, ArchiveMember(path, 0))


def test_saved_posts_label_values_format():
    r = _parse(SAVED + "saved_posts.json")
    assert r.ok and not r.issues
    first, second = r.references
    assert first.url.canonical_url == "https://www.instagram.com/reel/AAAreel0001/"
    assert first.creator_username == "creator.one"
    assert first.captured_at == datetime.fromtimestamp(1792783662, UTC)
    assert first.caption == "6 cafés in Leh ❤️\n\n#ladakh"  # mojibake repaired
    assert first.metadata == {"caption": first.caption, "hashtags": ["ladakh", "travel"],
                              "creator_name": "Creator One"}
    # variant seen in real data: duplicated Caption/Title, no Owner section
    assert second.creator_username is None and second.caption == "Five books for autumn"


def test_collections_nested_media_format():
    r = _parse(SAVED + "saved_collections.json")
    assert r.ok and not r.issues
    assert r.collections == {"ladakh": 2, "Caption ideas": 0}
    assert [x.url.shortcode for x in r.references] == ["AAAreel0001", "CCCreel0003"]
    ref = r.references[0]
    assert ref.source_type is SourceType.SAVED_COLLECTION and ref.source_name == "ladakh"
    assert ref.captured_at is None  # the export has no per-item time; never invented
    assert ref.creator_username == "creator.one"
    assert ref.metadata["collection_id"] == "1000000000000001"
    assert ref.metadata["collection_updated_at"].startswith("2026-08-27")
    assert ref.metadata["collection_privacy"] == "Collaborative"


def test_messages_share_caption_story_and_profile():
    r = _parse("your_instagram_activity/messages/inbox/bookclub_111/message_1.json")
    assert r.conversation.is_group
    kinds = [x.url.kind.value for x in r.references]
    assert kinds == ["reel", "story", "profile"]
    reel = r.references[0]
    assert reel.caption == "6 cafés in Leh" and reel.creator_username == "creator.one"
    assert r.references[2].url.username == "creator_three"  # /_u/<username>


def test_inspect_real_shape():
    with ExportArchive(REAL_SHAPE) as a:
        report = inspect_export(a)
    assert not report.errors and not report.warnings
    assert report.saved_posts == 2
    assert report.urls_by_kind == {"reel": 4, "post": 2, "story": 1, "profile": 1}
    assert report.unique_content_items == 3
    assert report.duplicate_content_items == 2
    s = report.to_dict()["conversation_summary"]
    assert s == {"total": 2, "group": 1, "direct": 1, "with_instagram_links": 2}


def test_import_real_shape(session_factory, tmp_path):
    archive = zip_directory(REAL_SHAPE, tmp_path / "real_shape.zip")
    with session_factory() as s:
        batch_id = create_batch(s, archive).id
    report = run_import(session_factory, batch_id)
    assert report["status"] == "completed" and report["failures"] == 0
    refs = report["references"]
    assert refs["content_items_created"] == 3 and refs["imported"] == 6
    assert refs["non_content_urls"] == {"story": 1, "profile": 1}

    with session_factory() as s:
        items = {i.platform_content_id: i for i in s.scalars(select(ContentItem))}
        assert items["AAAreel0001"].caption == "6 cafés in Leh ❤️\n\n#ladakh"
        assert items["BBBpost0002"].creator_username == "bookish"  # filled from DM share
        sources = s.scalars(select(ContentSource).where(
            ContentSource.content_id == items["AAAreel0001"].id)).all()
        assert sorted(x.source_type for x in sources) == [
            "group_message", "saved_collection", "saved_post"]
        coll = next(x for x in sources if x.source_type == "saved_collection")
        assert coll.meta["hashtags"] == ["ladakh"]
        assert all("must never be stored" not in str(x.meta) for x in sources)
