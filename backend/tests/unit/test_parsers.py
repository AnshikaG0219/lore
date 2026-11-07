from datetime import UTC, datetime

from app.ingestion.instagram.detect import classify_json_shape, classify_path
from app.ingestion.instagram.models import FileCategory, SourceType
from app.ingestion.instagram.parsers import (
    parse_messages,
    parse_saved_collections,
    parse_saved_posts,
)


def test_classify_path_is_prefix_independent():
    for prefix in ("", "instagram-x-2026/", "instagram-x/your_instagram_activity/"):
        assert classify_path(prefix + "saved/saved_posts.json") is FileCategory.SAVED_POSTS
        assert classify_path(prefix + "saved/saved_collections.json") \
            is FileCategory.SAVED_COLLECTIONS
        assert classify_path(prefix + "messages/inbox/abc_1/message_3.json") \
            is FileCategory.MESSAGES
    assert classify_path("x/messages/inbox/a/photos/1.jpg") is FileCategory.MEDIA
    assert classify_path("x/start_here.html") is FileCategory.HTML
    assert classify_path("x/something_new.json") is None


def test_classify_by_shape_when_path_unknown():
    assert classify_json_shape({"participants": [], "messages": []}) is FileCategory.MESSAGES
    assert classify_json_shape({"saved_saved_media": []}) is FileCategory.SAVED_POSTS
    assert classify_json_shape({"saved_saved_collections": []}) is FileCategory.SAVED_COLLECTIONS
    assert classify_json_shape({"foo": []}) is FileCategory.OTHER_JSON


def test_saved_posts_basic():
    data = {"saved_saved_media": [
        {"title": "creator", "string_map_data": {"Saved on": {
            "href": "https://www.instagram.com/reel/C1aBcDeFgHi/", "timestamp": 1717000000}}},
        {"title": "gone", "string_map_data": {"Saved on": {"timestamp": 1717000001}}},
    ]}
    r = parse_saved_posts("s.json", data)
    assert r.records_seen == 2
    assert len(r.references) == 1
    ref = r.references[0]
    assert ref.source_type is SourceType.SAVED_POST
    assert ref.creator_username == "creator"
    assert ref.captured_at == datetime(2024, 5, 29, 16, 26, 40, tzinfo=UTC)
    assert r.unresolved_records == [data["saved_saved_media"][1]]
    assert r.ok  # missing URL is a warning, not an error


def test_saved_posts_tolerates_label_values_shape():
    data = {"saved_media_v2": [{"label_values": [
        {"label": "URL", "href": "https://www.instagram.com/p/C2xYz_12345/"},
        {"label": "Saved", "timestamp_value": 1, "timestamp": 1717000000}]}]}
    r = parse_saved_posts("s.json", data)
    assert [x.url.shortcode for x in r.references] == ["C2xYz_12345"]


def test_saved_posts_unrecognised_structure_warns():
    r = parse_saved_posts("s.json", {"weird": "value"})
    assert r.references == [] and r.issues[0].severity == "warning"


def test_collections_group_items_under_headers():
    data = {"saved_saved_collections": [
        {"title": "Collection", "string_map_data": {"Name": {"value": "A"}}},
        {"string_map_data": {"Name": {"href": "https://instagram.com/p/AAAAA1/", "value": "u1"},
                             "Added Time": {"timestamp": 1717000000}}},
        {"title": "Collection", "string_map_data": {"Name": {"value": "B"}}},
        {"string_map_data": {"Name": {"href": "https://instagram.com/p/BBBBB1/", "value": "u2"}}},
        {"string_map_data": {"Name": {"href": "https://instagram.com/reel/AAAAA1/"}}},
        {"title": "Collection", "string_map_data": {"Name": {"value": "Empty"}}},
    ]}
    r = parse_saved_collections("c.json", data)
    assert r.collections == {"A": 1, "B": 2, "Empty": 0}
    assert [(x.source_name, x.url.shortcode) for x in r.references] == [
        ("A", "AAAAA1"), ("B", "BBBBB1"), ("B", "AAAAA1")]
    assert r.references[0].creator_username == "u1"
    assert r.references[0].captured_at is not None


def test_collections_item_before_header_is_kept_with_warning():
    data = {"x": [{"string_map_data": {"Name": {"href": "https://instagram.com/p/AAAAA1/"}}}]}
    r = parse_saved_collections("c.json", data)
    assert len(r.references) == 1 and r.references[0].source_name is None
    assert any("before any collection" in i.message for i in r.issues)


def _thread(participants, messages, **extra):
    return {"participants": [{"name": n} for n in participants], "messages": messages, **extra}


def test_group_messages_extract_links_and_metadata():
    data = _thread(["Me", "A", "B"], [
        {"sender_name": "A", "timestamp_ms": 1717400000000,
         "share": {"link": "https://www.instagram.com/reel/C1aBcDeFgHi/",
                   "original_content_owner": "owner1"}},
        {"sender_name": "B", "timestamp_ms": 1717400000001, "content": "private words"},
        {"sender_name": "B", "timestamp_ms": 1717400000002,
         "content": "two: instagram.com/p/C2xYz_12345 https://www.instagram.com/p/C2xYz_12345/"},
    ], title="Friends", thread_path="inbox/friends_1")
    r = parse_messages("x/messages/inbox/friends_1/message_1.json", data)
    assert r.conversation.is_group and r.conversation.title == "Friends"
    assert r.conversation.message_count == 3 and r.conversation.messages_with_links == 2
    assert [x.url.shortcode for x in r.references] == ["C1aBcDeFgHi", "C2xYz_12345"]
    first = r.references[0]
    assert first.source_type is SourceType.GROUP_MESSAGE
    assert first.sender == "A" and first.creator_username == "owner1"
    assert first.captured_at == datetime(2024, 6, 3, 7, 33, 20, tzinfo=UTC)
    assert all("private words" not in str(x.raw) for x in r.references)


def test_direct_message_and_fallback_conversation_id():
    data = _thread(["Me", "A"], [
        {"sender_name": "A", "timestamp_ms": 1717400000000,
         "share": {"link": "https://www.instagram.com/p/C2xYz_12345/"}}])
    r = parse_messages("root/messages/inbox/a_99/message_1.json", data)
    assert r.references[0].source_type is SourceType.DIRECT_MESSAGE
    assert r.conversation.conversation_id == "inbox/a_99"
    assert r.conversation.title == "Me, A"


def test_messages_tolerate_missing_fields():
    r = parse_messages("m.json", _thread([], [
        {"content": "https://www.instagram.com/p/C2xYz_12345/"}, "garbage", {}]))
    assert len(r.references) == 1
    assert r.references[0].sender is None and r.references[0].captured_at is None
    assert any(i.severity == "warning" for i in r.issues)


def test_messages_unrecognised_structure():
    assert parse_messages("m.json", ["nope"]).issues[0].severity == "warning"
