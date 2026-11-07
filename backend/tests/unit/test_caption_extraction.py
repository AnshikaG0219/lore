import asyncio
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.models import ContentItem, ContentSource, ProcessingJob
from app.db.repositories.content import get_or_create_user
from app.processing.ai.provider import Completion, ProviderError
from app.processing.extraction.caption import (
    JOB_TYPE,
    build_input,
    parse_response,
    process_item,
)

VALID = {
    "title": "Six cafés in Leh", "summary": "Lists six cafés.", "primary_category": "travel",
    "secondary_category": "food_and_recipes", "subcategory": "cafes", "key_takeaways": [],
    "entities": [], "extracted_information": {"places": [
        {"name": "Café A", "place_type": "cafe", "location": "Leh", "notes": None,
         "evidence": "explicit"}]},
    "tags": ["Ladakh", "ladakh", "Cafes"], "language": "en", "information_sufficient": True,
    "confidence": 0.8,
}


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, *responses):
        self.responses, self.prompts = list(responses), []

    async def complete(self, system, prompt):
        self.prompts.append(prompt)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return Completion(text=r, provider=self.name, model=self.model)

    async def close(self):
        pass


def test_parse_handles_fences_and_dedupes_tags():
    ex = parse_response("```json\n" + json.dumps(VALID) + "\n```")
    assert ex.tags == ["Ladakh", "Cafes"]
    assert ex.extracted_information.places[0].evidence == "explicit"


@pytest.mark.parametrize("bad", [
    "no json here",
    json.dumps({**VALID, "primary_category": "astrology"}),
    json.dumps({**VALID, "confidence": 3}),
    json.dumps({**VALID, "invented_field": 1}),
    json.dumps({**VALID, "extracted_information": {"places": [{"name": "X"}]}}),  # no evidence
])
def test_parse_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_response(bad)


@pytest.fixture
def item(session_factory):
    with session_factory() as s:
        user = get_or_create_user(s)
        it = ContentItem(user_id=user.id, identity_key="instagram:shortcode:AAAA11",
                         canonical_url="https://www.instagram.com/reel/AAAA11/",
                         canonical_url_hash="h", content_type="reel", caption="6 cafés in Leh",
                         creator_username="creator", extracted_data={}, tags=[])
        s.add(it)
        s.flush()
        for st, name, meta in [("saved_collection", "ladakh", {"hashtags": ["ladakh"]}),
                               ("group_message", "Secret Group Chat", {}),
                               ("direct_message", "Private Person", {})]:
            s.add(ContentSource(content_id=it.id, source_key=st, source_type=st,
                                source_name=name, sender="Someone Private", meta=meta,
                                captured_at=datetime.now(UTC)))
        s.commit()
        return it.id


def _run(session_factory, item_id, provider, **kw):
    with session_factory() as s:
        return asyncio.run(process_item(s, s.get(ContentItem, item_id), provider, **kw))


def test_prompt_contains_post_content_but_no_chat_data(session_factory, item):
    with session_factory() as s:
        prompt = build_input(s, s.get(ContentItem, item)).prompt()
    assert "6 cafés in Leh" in prompt and "ladakh" in prompt
    for private in ("Secret Group Chat", "Private Person", "Someone Private"):
        assert private not in prompt


def test_success_persists_and_caches(session_factory, item):
    provider = FakeProvider(json.dumps(VALID))
    assert _run(session_factory, item, provider)["status"] == "completed"
    with session_factory() as s:
        it = s.get(ContentItem, item)
        assert it.primary_category == "travel" and it.extraction_confidence == 0.8
        assert it.extracted_data[JOB_TYPE]["_meta"]["model"] == "fake-1"
        assert s.scalar(select(ProcessingJob.status)) == "completed"
    # unchanged input → skipped without calling the provider
    assert _run(session_factory, item, FakeProvider())["reason"] == "up to date"


def test_invalid_then_valid_retries_with_feedback(session_factory, item):
    provider = FakeProvider("garbage", json.dumps(VALID))
    r = _run(session_factory, item, provider)
    assert r["status"] == "completed" and r["attempts"] == 2
    assert "previous answer was rejected" in provider.prompts[1]


def test_failures_are_recorded_not_raised(session_factory, item):
    assert _run(session_factory, item, FakeProvider("x", "y"))["status"] == "failed"
    assert _run(session_factory, item, FakeProvider(ProviderError("quota")))["status"] == "failed"
    with session_factory() as s:
        jobs = s.scalars(select(ProcessingJob)).all()
        assert [j.status for j in jobs] == ["failed", "failed"]
        assert s.get(ContentItem, item).primary_category is None  # nothing half-written
