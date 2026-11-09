"""FETCHING stage: logged-out media download with pacing + circuit breaker (ADR-023)."""

import json
import uuid

import pytest
from sqlalchemy import select

from app.db.models import ContentItem, MediaAsset
from app.db.repositories.content import get_or_create_user
from app.processing import queue as q
from app.processing.fetch.instagram import (
    FetchedMedia,
    FetchError,
    LocalFetchGovernor,
    classify,
)
from app.processing.media.pipeline import MediaEngines
from app.processing.pipeline import PipelineRunner
from app.storage.media import LocalMediaStore
from tests.unit.test_caption_extraction import VALID, FakeProvider
from tests.unit.test_media_pipeline import JPEG, FakeOcr, FakeTranscriber

LONG_CAPTION = "Five short books under 200 pages: Siddhartha, Animal Farm, The Stranger…" * 2


class FakeFetcher:
    name = "fake-fetcher"

    def __init__(self, outcome="ok", caption=LONG_CAPTION):
        self.outcome, self.caption, self.calls = outcome, caption, []

    def fetch(self, url, dest):
        self.calls.append(url)
        if isinstance(self.outcome, FetchError):
            raise self.outcome
        dest.mkdir(parents=True, exist_ok=True)
        files = []
        for i in range(2):  # a 2-slide carousel
            p = dest / f"slide_{i}.img"
            p.write_bytes(JPEG + bytes([i]))
            files.append(p)
        return FetchedMedia(files=files, caption=self.caption, tool_version="test")


def _item(factory, caption="Look 😍", with_media=False, tmp_path=None):
    with factory() as s:
        user = get_or_create_user(s)
        code = uuid.uuid4().hex[:10]
        it = ContentItem(user_id=user.id, identity_key=f"instagram:shortcode:{code}",
                         canonical_url=f"https://www.instagram.com/p/{code}/",
                         canonical_url_hash="h", platform_content_id=code, content_type="post",
                         caption=caption, extracted_data={}, tags=[])
        s.add(it)
        s.commit()
        job = q.enqueue(s, it.id, reason="test")
        s.commit()
        return it.id, job.job_id


@pytest.fixture
def make_runner(session_factory, tmp_path):
    runners = []

    def make(fetcher, governor=None, responses=3):
        r = PipelineRunner(session_factory, lambda: MediaEngines(FakeOcr(), FakeTranscriber()),
                           LocalMediaStore(tmp_path / "media", 10**7),
                           lambda: FakeProvider(*[json.dumps(VALID)] * responses),
                           fetcher=fetcher, governor=governor or LocalFetchGovernor())
        runners.append(r)
        return r
    yield make
    for r in runners:
        r.close()


def _state(factory, cid):
    with factory() as s:
        it = s.get(ContentItem, cid)
        assets = s.scalars(select(MediaAsset).where(MediaAsset.content_id == cid)).all()
        return it, assets


@pytest.mark.parametrize(("msg", "reason"), [
    ("ERROR: [Instagram] X: Requested content is not available, rate-limit reached or login "
     "required", "blocked"),
    ("HTTP Error 429: Too Many Requests", "blocked"),
    ("ERROR: [Instagram] X: This content may be inappropriate… login required", "blocked"),
    ("ERROR: [Instagram] X: Post is private", "unavailable"),
    ("HTTP Error 404: Not Found", "unavailable"),
    ("Some unexpected parser failure", "error"),
])
def test_classify(msg, reason):
    assert classify(msg) == reason


def test_fetch_attaches_media_upgrades_caption_and_completes(session_factory, make_runner):
    cid, job = _item(session_factory)
    fetcher = FakeFetcher()
    out = make_runner(fetcher).run(job)
    assert out["status"] == "completed" and out["media"]["processed"] == 2
    assert out["media"]["fetch"]["status"] == "fetched" and out["media"]["fetch"]["files"] == 2
    item, assets = _state(session_factory, cid)
    assert item.caption == LONG_CAPTION  # chat preview replaced by the post's own caption
    assert {a.source for a in assets} == {"fake-fetcher"} and len(assets) == 2
    assert item.extracted_data["media_fetch"]["status"] == "fetched"


def test_shorter_fetched_caption_does_not_replace(session_factory, make_runner):
    cid, job = _item(session_factory, caption="A longer caption already stored from the export")
    make_runner(FakeFetcher(caption="short")).run(job)
    assert _state(session_factory, cid)[0].caption.startswith("A longer caption")


def test_block_pauses_all_fetching(session_factory, make_runner):
    gov = LocalFetchGovernor()
    blocked = FakeFetcher(FetchError("blocked", "login required"))
    cid1, job1 = _item(session_factory)
    out = make_runner(blocked, gov).run(job1)
    assert out["media"]["fetch"]["status"] == "blocked"

    later = FakeFetcher()  # a new fetcher must not even be called while paused
    cid2, job2 = _item(session_factory)
    out2 = make_runner(later, gov).run(job2)
    assert later.calls == [] and out2["media"]["fetch"]["status"] == "deferred"
    assert _state(session_factory, cid2)[0].extracted_data["media_fetch"]["attempts"] == 0


@pytest.mark.parametrize("reason", ["unavailable", "no_media"])
def test_final_outcomes_are_not_retried(session_factory, make_runner, reason):
    fetcher = FakeFetcher(FetchError(reason, "gone"))
    cid, job = _item(session_factory)
    make_runner(fetcher).run(job)
    with session_factory() as s:
        q.enqueue(s, cid, reason="manual", force=True)
        s.commit()
    make_runner(fetcher).run(job)
    assert len(fetcher.calls) == 1


def test_errors_retry_up_to_cap(session_factory, make_runner):
    fetcher = FakeFetcher(FetchError("error", "parser broke"))
    cid, job = _item(session_factory)
    for _ in range(5):
        with session_factory() as s:
            q.enqueue(s, cid, reason="manual", force=True)
            s.commit()
        make_runner(fetcher).run(job)
    assert len(fetcher.calls) == 3  # max_fetch_errors
    assert _state(session_factory, cid)[0].extracted_data["media_fetch"]["attempts"] == 3


def test_items_with_media_are_not_fetched(session_factory, make_runner, tmp_path):
    import io

    from app.processing.media.pipeline import attach_media

    cid, job = _item(session_factory)
    with session_factory() as s:
        attach_media(s, LocalMediaStore(tmp_path / "media", 10**7), cid, io.BytesIO(JPEG), "a.jpg")
    fetcher = FakeFetcher()
    assert make_runner(fetcher).run(job)["status"] == "completed"
    assert fetcher.calls == []


def test_fetching_is_opt_in():
    from app.core.config import Settings
    from app.processing.factory import build_fetcher

    assert build_fetcher(Settings()) == (None, None)
