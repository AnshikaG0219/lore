"""Lore MCP tools: read-only access + Notion sync bookkeeping (ADR-026)."""

import uuid
from datetime import UTC, datetime

import pytest

from app import mcp_server as m
from app.db.models import ContentItem
from app.db.repositories.content import get_or_create_user
from app.processing.classification.facets import replace_facets

CAFE = {
    "title": "Kunzum",
    "gist": "Kunzum Travel Café, Hauz Khas: pay what you like",
    "post_type": "eat_drink",
    "topic": "food_drinks",
    "information_sufficient": True,
    "eat_drink": {
        "venues": [
            {"name": "Kunzum", "venue_type": "cafe", "location": {"area": "Hauz Khas Village"}}
        ]
    },
    "_meta": {"prompt_version": "classification.v5"},
}


@pytest.fixture
def items(session_factory, monkeypatch):
    monkeypatch.setattr(m, "_session", lambda: session_factory())
    ids = []
    with session_factory() as s:
        user = get_or_create_user(s)
        for i, (data, hidden) in enumerate([(CAFE, False), ({**CAFE, "post_type": "fun"}, True)]):
            it = ContentItem(
                user_id=user.id,
                identity_key=f"k{i}",
                canonical_url=f"https://www.instagram.com/p/C{i}/",
                canonical_url_hash="h",
                platform_content_id=f"C{i}",
                content_type="post",
                title=data["title"],
                caption="secret caption",
                extracted_data={"caption_extraction": data},
                tags=[],
                last_processed_at=datetime.now(UTC),
                archived_at=datetime.now(UTC) if hidden else None,
                archive_reason="fun" if hidden else None,
            )
            s.add(it)
            s.flush()
            replace_facets(s, it.id, data)
            ids.append(it.id)
        s.commit()
    return ids


def test_search_filters_and_hides(items):
    r = m.search_posts(type="eat_drink", zone="South Delhi")
    assert r["total"] == 1 and r["posts"][0]["gist"].startswith("Kunzum")
    assert r["posts"][0]["areas"] == ["Hauz Khas Village"]
    assert m.search_posts(area="hauz khas village")["total"] == 1
    assert m.search_posts(zone="North Delhi")["total"] == 0
    assert m.search_posts(include_hidden=True)["total"] == 2
    assert m.search_posts(limit=10_000)["posts"]  # limit is clamped, not an error


def test_get_post_hides_source_unless_asked(items):
    p = m.get_post(str(items[0]))
    assert "source" not in p and p["details"]["eat_drink"]["venues"][0]["name"] == "Kunzum"
    assert m.get_post(str(items[0]), include_source=True)["source"]["caption"] == "secret caption"
    assert m.get_post(str(uuid.uuid4())) == {"error": "not found"}


def test_sync_bookkeeping(items, session_factory):
    work = m.posts_to_sync()
    assert [p["id"] for p in work["to_upsert"]] == [str(items[0])] and work["to_archive"] == []
    m.mark_synced(str(items[0]), "notion-page-1")
    assert m.posts_to_sync()["remaining_to_upsert"] == 0  # marking doesn't count as a change
    m.mark_synced(str(items[0]), "notion-page-1")  # idempotent
    with session_factory() as s:  # the post changes → needs syncing again
        s.get(ContentItem, items[0]).title = "Kunzum Travel Café"
        s.commit()
    assert m.posts_to_sync()["to_upsert"][0]["notion_page_id"] == "notion-page-1"
    with session_factory() as s:  # hidden after being synced → archive its page
        it = s.get(ContentItem, items[0])
        it.archived_at, it.archive_reason = datetime.now(UTC), "manual"
        s.commit()
    assert m.posts_to_sync()["to_archive"][0]["notion_page_id"] == "notion-page-1"
    m.mark_synced(str(items[0]), "notion-page-1", archived=True)
    assert m.posts_to_sync()["to_archive"] == []


def test_facet_values(items):
    r = m.facet_values(["zone", "venue_type"], type="eat_drink")
    assert r == {
        "zone": [{"value": "South Delhi", "count": 1}],
        "venue_type": [{"value": "cafe", "count": 1}],
    }


def test_http_requires_key(monkeypatch):
    from starlette.testclient import TestClient

    from app.core.config import Settings

    monkeypatch.setattr(m, "get_settings", lambda: Settings(mcp_key="k" * 32))
    with TestClient(m.build_app()) as c:
        assert c.post("/mcp", json={}).status_code == 401
        assert c.post("/mcp", json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_overview_lists_categories_with_breakdown(items):
    o = m.overview()
    assert o["useful_posts"] == 1 and o["hidden_posts"] == 1
    cat = o["categories"][0]
    assert cat["type"] == "eat_drink" and cat["label"] == "Eat & drink" and cat["posts"] == 1
    assert cat["breakdown"]["zone"] == [{"value": "South Delhi", "count": 1}]


def test_list_category_pages_through_everything(items, session_factory):
    with session_factory() as s:  # 5 more cafés → 6 visible eat_drink posts
        user = get_or_create_user(s)
        for i in range(5):
            it = ContentItem(
                user_id=user.id,
                identity_key=f"x{i}",
                canonical_url=f"https://www.instagram.com/p/X{i}/",
                canonical_url_hash="h",
                platform_content_id=f"X{i}",
                content_type="post",
                title=f"Cafe {i}",
                extracted_data={"caption_extraction": CAFE},
                tags=[],
                last_processed_at=datetime.now(UTC),
            )
            s.add(it)
            s.flush()
            replace_facets(s, it.id, CAFE)
        s.commit()
    seen, page = [], 1
    while page:
        r = m.list_category("eat_drink", page=page, page_size=4)
        seen += [p["id"] for p in r["posts"]]
        page = r["next_page"]
    assert len(seen) == len(set(seen)) == 6 and r["pages"] == 2
    line = m.list_category("eat_drink", page_size=1)["posts"][0]["line"]
    assert (
        "where: Hauz Khas Village" in line
        and "venue_type: cafe" in line
        and "instagram.com" in line
    )
    full = m.list_category("eat_drink", format="full", page_size=500)
    assert len(full["posts"]) == 6 and "details" in full["posts"][0]  # full is capped at 25/page
    assert m.list_category("eat_drink", zone="North Delhi")["total"] == 0


def test_category_document(items):
    doc = m.category_document("eat_drink")
    assert doc.startswith("# Eat & drink (1 posts)") and "\n- Kunzum" in doc
    assert "secret caption" not in doc
