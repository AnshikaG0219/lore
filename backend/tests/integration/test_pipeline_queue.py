"""The content processing queue: one job per item, stages, retries, recovery."""

import io
import json
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.db.models import AuditLog, ContentItem, ProcessingJob
from app.db.repositories.content import get_or_create_user
from app.main import create_app
from app.processing import queue as q
from app.processing.ai.provider import ProviderError
from app.processing.media.pipeline import MediaEngines, attach_media
from app.processing.pipeline import PipelineRunner, ProviderNotConfiguredError
from app.storage.media import LocalMediaStore
from app.workers.dispatch import InlineImportDispatcher
from tests.unit.test_caption_extraction import VALID, FakeProvider
from tests.unit.test_media_pipeline import JPEG, FakeOcr, FakeTranscriber

THIN = {**VALID, "information_sufficient": False, "confidence": 0.4}


def _item(factory, caption="6 cafés in Leh", code=None):
    with factory() as s:
        user = get_or_create_user(s)
        code = code or uuid.uuid4().hex[:10]
        it = ContentItem(user_id=user.id, identity_key=f"instagram:shortcode:{code}",
                         canonical_url=f"https://www.instagram.com/reel/{code}/",
                         canonical_url_hash="h", platform_content_id=code, content_type="reel",
                         caption=caption, extracted_data={}, tags=[])
        s.add(it)
        s.commit()
        return it.id


def _enqueue(factory, cid, **kw):
    with factory() as s:
        r = q.enqueue(s, cid, reason=kw.pop("reason", "test"), **kw)
        s.commit()
        return r


def _job(factory, job_id):
    with factory() as s:
        return s.get(ProcessingJob, job_id)


class Providers:
    """Provider factory that records how often it was used."""

    def __init__(self, *responses):
        self.provider = FakeProvider(*responses)

    def __call__(self):
        return self.provider


@pytest.fixture
def runner_for(session_factory, tmp_path):
    made = []

    def make(provider, ocr=None):
        r = PipelineRunner(session_factory,
                           lambda: MediaEngines(ocr or FakeOcr(), FakeTranscriber()),
                           LocalMediaStore(tmp_path / "media", 10**6), provider)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def test_one_job_per_content_item(session_factory):
    cid = _item(session_factory)
    a = _enqueue(session_factory, cid)
    b = _enqueue(session_factory, cid)
    assert a.created and not b.should_dispatch and a.job_id == b.job_id
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(ProcessingJob)) == 1
        assert s.get(ContentItem, cid).processing_status == "QUEUED"


def test_happy_path_completes_and_records_stages(session_factory, runner_for):
    cid = _item(session_factory)
    job = _enqueue(session_factory, cid).job_id
    out = runner_for(Providers(json.dumps(VALID))).run(job)
    assert out["status"] == "completed" and out["category"] == "trip"
    j = _job(session_factory, job)
    assert (j.status, j.stage, j.runner_id) == ("completed", "COMPLETED", None)
    assert j.result["sources"] == ["caption"] and j.result["information_sufficient"] is True
    with session_factory() as s:
        assert s.get(ContentItem, cid).processing_status == "COMPLETED"
        assert "pipeline.completed" in s.scalars(select(AuditLog.action)).all()
    # finished job: plain enqueue is a no-op, forced enqueue re-arms the same row
    assert not _enqueue(session_factory, cid).should_dispatch
    again = _enqueue(session_factory, cid, force=True)
    assert again.rearmed and again.job_id == job
    providers = Providers()  # no responses: must not be called (input unchanged → cached)
    assert runner_for(providers).run(job)["status"] == "completed"
    assert providers.provider.prompts == []


def test_thin_caption_without_media_needs_manual_input(session_factory, runner_for):
    cid = _item(session_factory, caption="wait for it 😍")
    job = _enqueue(session_factory, cid).job_id
    assert runner_for(Providers(json.dumps(THIN))).run(job)["status"] == "needs_manual_input"
    with session_factory() as s:
        assert s.get(ContentItem, cid).processing_status == "NEEDS_MANUAL_INPUT"


def test_nothing_to_process_needs_manual_input_without_ai_call(session_factory, runner_for):
    job = _enqueue(session_factory, _item(session_factory, caption=None)).job_id
    providers = Providers()
    assert runner_for(providers).run(job)["status"] == "needs_manual_input"
    assert providers.provider.prompts == []


def test_media_fills_the_gap_and_completes(session_factory, runner_for, tmp_path):
    cid = _item(session_factory, caption="wait for it 😍")
    store = LocalMediaStore(tmp_path / "media", 10**6)
    with session_factory() as s:
        attach_media(s, store, cid, io.BytesIO(JPEG), "slide.jpg")
    job = _enqueue(session_factory, cid).job_id
    providers = Providers(json.dumps(THIN))
    out = runner_for(providers).run(job)
    # thin overall, but media exists → completed (the media text is the best we can get)
    assert out["status"] == "completed" and out["media"]["processed"] == 1
    assert "5 BOOKS" in providers.provider.prompts[0]


def test_transient_ai_errors_retry_with_backoff_then_fail(session_factory, runner_for):
    cid = _item(session_factory)
    job = _enqueue(session_factory, cid, max_attempts=2).job_id
    runner = runner_for(Providers(ProviderError("rate limited"), ProviderError("rate limited")))
    assert runner.run(job)["status"] == "retry_wait"
    j = _job(session_factory, job)
    assert j.status == "retry_wait" and j.retry_count == 1 and j.next_attempt_at is not None
    assert runner.run(job)["status"] == "skipped"  # not due yet
    with session_factory() as s:
        s.get(ProcessingJob, job).next_attempt_at = q.now() - timedelta(seconds=1)
        s.commit()
    assert runner.run(job)["status"] == "failed"  # second failure exhausts max_attempts
    j = _job(session_factory, job)
    assert j.status == "failed" and "Gave up after 2 attempts" in j.error_message
    with session_factory() as s:
        assert s.get(ContentItem, cid).processing_status == "FAILED"


def test_retry_then_success(session_factory, runner_for):
    job = _enqueue(session_factory, _item(session_factory)).job_id
    runner = runner_for(Providers(ProviderError("timeout"), json.dumps(VALID)))
    runner.run(job)
    with session_factory() as s:
        s.get(ProcessingJob, job).next_attempt_at = q.now() - timedelta(seconds=1)
        s.commit()
    assert runner.run(job)["status"] == "completed"


def test_missing_provider_is_permanent(session_factory, runner_for):
    job = _enqueue(session_factory, _item(session_factory)).job_id

    def no_provider():
        raise ProviderNotConfiguredError("AI provider not configured")

    assert runner_for(no_provider).run(job)["status"] == "failed"
    assert _job(session_factory, job).error_message == "AI provider not configured"


def test_fencing_and_stale_takeover(session_factory):
    job = _enqueue(session_factory, _item(session_factory)).job_id
    stale = timedelta(minutes=10)
    with session_factory() as s:
        t1 = q.claim(s, job, stale)
    with session_factory() as s:
        assert q.claim(s, job, stale) is None  # live runner
        s.get(ProcessingJob, job).heartbeat_at = q.now() - timedelta(hours=1)
        s.commit()
    with session_factory() as s:
        t2 = q.claim(s, job, stale)
    assert t2 and t2 != t1
    with session_factory() as s, pytest.raises(q.JobClaimLostError):
        q.set_stage(s, job, t1, "STRUCTURING")  # the dead runner can't write any more


def test_due_jobs(session_factory):
    ids = {n: _enqueue(session_factory, _item(session_factory)).job_id
           for n in ("fresh", "dispatched", "lost", "retry_due", "retry_later", "stale", "live")}
    t = q.now()
    with session_factory() as s:
        def upd(name, **kw):
            j = s.get(ProcessingJob, ids[name])
            for k, v in kw.items():
                setattr(j, k, v)
        upd("dispatched", dispatched_at=t)
        upd("lost", dispatched_at=t - timedelta(days=1))
        upd("retry_due", status="retry_wait", next_attempt_at=t - timedelta(seconds=1))
        upd("retry_later", status="retry_wait", next_attempt_at=t + timedelta(hours=1))
        upd("stale", status="running", heartbeat_at=t - timedelta(hours=1))
        upd("live", status="running", heartbeat_at=t)
        s.commit()
        due = q.due_jobs(s, stale_after=timedelta(minutes=10),
                         redispatch_after=timedelta(hours=6))
    assert set(due) == {ids[n] for n in ("fresh", "lost", "retry_due", "stale")}


def test_sweep_dispatches_and_marks(session_factory, monkeypatch):
    from app.workers import pipeline_tasks, tasks

    monkeypatch.setattr(tasks, "_factory", lambda: session_factory)
    monkeypatch.setattr(pipeline_tasks, "_factory", lambda: session_factory)
    sent = []
    monkeypatch.setattr(pipeline_tasks.run_pipeline_task, "apply_async",
                        lambda args, **kw: sent.append(args[0]))
    job = _enqueue(session_factory, _item(session_factory)).job_id
    assert pipeline_tasks.sweep_pipeline_task.apply().result == 1
    assert sent == [str(job)] and _job(session_factory, job).dispatched_at is not None
    assert pipeline_tasks.sweep_pipeline_task.apply().result == 0  # not re-sent immediately


def test_api_enqueue_reprocess_stats(session_factory, runner_for, tmp_path):
    for name in ("a", "b", "c"):
        _item(session_factory, code=f"API{name}0001")
    settings = Settings(api_key="k", data_dir=tmp_path / "d")
    runner = runner_for(Providers(*[json.dumps(VALID)] * 5))
    app = create_app(session_factory, InlineImportDispatcher(session_factory, settings, runner))
    app.dependency_overrides[get_settings] = lambda: settings
    h = {"X-API-Key": "k"}
    with TestClient(app) as c:
        r = c.post("/api/v1/processing/enqueue", headers=h, json={"limit": 2})
        assert r.json() == {"selected": 2, "queued": 2}
        assert c.post("/api/v1/processing/enqueue", headers=h,
                      json={"limit": 10}).json()["selected"] == 1  # only items without a job
        stats = c.get("/api/v1/stats", headers=h).json()
        assert stats["jobs_by_status"] == {"completed": 3} and stats["categories"]["trip"] == 3
        cid = c.get("/api/v1/processing/jobs", headers=h).json()[0]["content_id"]
        rp = c.post(f"/api/v1/content/{cid}/reprocess", headers=h).json()
        assert rp["job"]["status"] == "completed" and rp["already_active"] is False
        assert c.post(f"/api/v1/content/{uuid.uuid4()}/reprocess", headers=h).status_code == 404
        assert c.get("/api/v1/stats").status_code == 401
