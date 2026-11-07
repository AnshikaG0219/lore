import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.models import ContentItem, ContentSource, MediaAsset, OcrText, Transcript
from app.db.repositories.content import get_or_create_user
from app.main import create_app
from app.processing.extraction.caption import build_input
from app.processing.media import ffmpeg
from app.processing.media import pipeline as pl
from app.processing.ocr.engine import OcrResult, parse_tsv
from app.processing.transcription.engine import TranscriptionError, TranscriptResult
from app.storage.media import LocalMediaStore, MediaRejectedError, sniff
from app.workers.dispatch import InlineImportDispatcher

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


class FakeOcr:
    name = "fake-ocr"

    def __init__(self, texts=None):
        self.calls, self.texts = 0, texts or ["5 BOOKS\n1. Siddhartha"]

    def read(self, path):
        self.calls += 1
        return OcrResult(self.texts[(self.calls - 1) % len(self.texts)], 0.9)


class FakeTranscriber:
    provider, model = "fake-asr", "v1"

    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def transcribe(self, path):
        self.calls += 1
        if self.fail:
            raise TranscriptionError("model crashed")
        return TranscriptResult("visit pangong lake", "en", 0.99, 5.0,
                                [{"start": 0.0, "end": 2.0, "text": "visit pangong lake"}])


@pytest.fixture
def fake_video_tools(monkeypatch, tmp_path):
    monkeypatch.setattr(ffmpeg, "probe", lambda p: {"duration": 6.0, "has_audio": True,
                                                   "has_video": True, "width": 1, "height": 1})
    monkeypatch.setattr(ffmpeg, "extract_audio", lambda src, dest: dest)

    def frames(src, out, every, maxf, dur):
        return [(float(t), tmp_path / f"f{t}.png") for t in (0, 2, 4)]

    monkeypatch.setattr(ffmpeg, "sample_frames", frames)


@pytest.fixture
def item_id(session_factory):
    with session_factory() as s:
        user = get_or_create_user(s)
        it = ContentItem(user_id=user.id, identity_key="instagram:shortcode:VID001",
                         canonical_url="https://www.instagram.com/reel/VID001/",
                         canonical_url_hash="h", content_type="reel", caption=None,
                         extracted_data={}, tags=[])
        s.add(it)
        s.flush()
        s.add(ContentSource(content_id=it.id, source_key="k", source_type="group_message",
                            source_name="Private Chat", sender="Friend", meta={}))
        s.commit()
        return it.id


@pytest.mark.parametrize(("head", "kind"), [
    (JPEG, "image"), (b"\x89PNG\r\n\x1a\n" + b"0" * 8, "image"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image"), (MP4, "video"),
    (b"\x00\x00\x00\x18ftypqt  ", "video"), (b"\x00\x00\x00\x18ftypM4A ", "audio"),
    (b"ID3\x04" + b"0" * 8, "audio"),
])
def test_sniff_by_magic_bytes(head, kind):
    assert sniff(head).asset_type == kind


@pytest.mark.parametrize("data", [b"#!/bin/sh\nrm -rf /", b"<html>", b"PK\x03\x04zip", b""])
def test_store_rejects_unsupported(tmp_path, data):
    store = LocalMediaStore(tmp_path, 1024)
    with pytest.raises(MediaRejectedError):
        store.save(uuid.uuid4(), io.BytesIO(data))
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]  # temp file cleaned up


def test_store_size_limit_and_content_addressing(tmp_path):
    store = LocalMediaStore(tmp_path, 100)
    with pytest.raises(MediaRejectedError, match="exceeds"):
        store.save(uuid.uuid4(), io.BytesIO(JPEG * 10))
    cid = uuid.uuid4()
    a = store.save(cid, io.BytesIO(JPEG))
    assert a.path.name == f"{a.checksum}.jpg" and a.path.parent.name == str(cid)
    with pytest.raises(MediaRejectedError):
        store.resolve("/etc/passwd")


def test_parse_tsv_drops_low_confidence_noise():
    tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight"
           "\tconf\ttext\n"
           "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t95\tPangong\n"
           "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t91\tLake\n"
           "5\t1\t1\t1\t1\t3\t0\t0\t1\t1\t12\t~@#\n"
           "5\t1\t1\t1\t2\t1\t0\t0\t1\t1\t88\tLeh\n")
    r = parse_tsv(tsv, 40)
    assert r.text == "Pangong Lake\nLeh" and r.confidence == pytest.approx(0.913, abs=0.001)


def _attach(factory, store, item_id, data, name):
    with factory() as s:
        return pl.attach_media(s, store, item_id, io.BytesIO(data), name)


def test_attach_dedupes_and_orders(session_factory, item_id, tmp_path):
    store = LocalMediaStore(tmp_path, 10**6)
    a, created = _attach(session_factory, store, item_id, JPEG, "1.jpg")
    b, created2 = _attach(session_factory, store, item_id, JPEG, "copy.jpg")
    c, _ = _attach(session_factory, store, item_id, MP4, "reel.mp4")
    assert created and not created2 and a.id == b.id
    assert (a.position, c.position) == (0, 1)
    with pytest.raises(LookupError):
        _attach(session_factory, store, uuid.uuid4(), JPEG, "x.jpg")


def test_video_pipeline_dedupes_frames_and_is_idempotent(session_factory, item_id, tmp_path,
                                                         fake_video_tools):
    store = LocalMediaStore(tmp_path, 10**6)
    asset, _ = _attach(session_factory, store, item_id, MP4, "reel.mp4")
    ocr = FakeOcr(["LEH FOOD GUIDE", "Leh food guide!\nLehvenda Cafe", "LEHVENDA CAFE"])
    asr = FakeTranscriber()
    engines = pl.MediaEngines(ocr, asr)
    with session_factory() as s:
        assert pl.process_asset(s, asset.id, engines, store)["status"] == "completed"
        assert pl.process_asset(s, asset.id, engines, store, force=True)["status"] == "completed"
        assert (ocr.calls, asr.calls) == (3, 1)  # forced rerun reused stored results
        text = pl.media_text_for(s, item_id)
    assert text.on_screen_text == "LEH FOOD GUIDE\nLehvenda Cafe"
    assert text.transcript == "visit pangong lake" and text.transcript_language == "en"


def test_partial_results_kept_when_a_step_fails(session_factory, item_id, tmp_path,
                                                fake_video_tools):
    store = LocalMediaStore(tmp_path, 10**6)
    asset, _ = _attach(session_factory, store, item_id, MP4, "reel.mp4")
    with session_factory() as s:
        r = pl.process_asset(s, asset.id, pl.MediaEngines(FakeOcr(), FakeTranscriber(True)),
                             store)
        assert r["status"] == "failed" and "model crashed" in r["error"]
        assert s.get(MediaAsset, asset.id).processing_status == "failed"
        # retry with a working transcriber completes, without redoing anything already stored
        ocr = FakeOcr()
        r = pl.process_asset(s, asset.id, pl.MediaEngines(ocr, FakeTranscriber()), store)
        assert r["status"] == "completed"
        assert s.scalar(select(Transcript.transcript_text)) == "visit pangong lake"
        assert s.query(OcrText).count() == 3


def test_extraction_input_uses_media_text_but_no_chat_data(session_factory, item_id, tmp_path,
                                                           fake_video_tools):
    store = LocalMediaStore(tmp_path, 10**6)
    asset, _ = _attach(session_factory, store, item_id, MP4, "reel.mp4")
    with session_factory() as s:
        assert build_input(s, s.get(ContentItem, item_id)) is None  # no caption, no media text
        pl.process_asset(s, asset.id, pl.MediaEngines(FakeOcr(), FakeTranscriber()), store)
        inp = build_input(s, s.get(ContentItem, item_id))
    payload = json.loads(inp.prompt().split("\n", 1)[1])
    assert payload["on_screen_text"].startswith("5 BOOKS")
    assert payload["spoken_transcript"] == "visit pangong lake"
    assert inp.sources == ["on_screen_text", "spoken_transcript"]
    assert "Private Chat" not in inp.prompt() and "Friend" not in inp.prompt()


def test_upload_media_api(session_factory, item_id, tmp_path):
    from app.processing.pipeline import PipelineRunner
    from tests.unit.test_caption_extraction import VALID, FakeProvider

    settings = Settings(api_key="k", data_dir=tmp_path / "data")
    runner = PipelineRunner(session_factory, lambda: pl.MediaEngines(FakeOcr(), FakeTranscriber()),
                            LocalMediaStore(settings.media_dir, 10**6),
                            lambda: FakeProvider(json.dumps(VALID)))
    app = create_app(session_factory, InlineImportDispatcher(session_factory, settings, runner))
    app.dependency_overrides[get_settings] = lambda: settings
    h = {"X-API-Key": "k"}
    with TestClient(app) as c:
        r = c.post(f"/api/v1/content/{item_id}/media", headers=h,
                   files=[("files", ("slide.jpg", JPEG, "image/jpeg")),
                          ("files", ("evil.sh", b"#!/bin/sh", "image/jpeg"))])
        assert r.status_code == 202, r.text
        files = r.json()["files"]
        assert files[0]["status"] == "completed" and files[1]["error"].startswith("Unsupported")
        job = c.get(f"/api/v1/content/{item_id}/job", headers=h).json()
        assert job["status"] == "completed" and job["result"]["sources"] == ["on_screen_text"]
        got = c.get(f"/api/v1/content/{item_id}/media", headers=h).json()
        assert got["on_screen_text"].startswith("5 BOOKS") and len(got["assets"]) == 1
        assert c.post(f"/api/v1/content/{item_id}/media", headers=h,
                      files=[("files", ("x.sh", b"#!", "text/plain"))]).status_code == 415
        assert c.get(f"/api/v1/content/{uuid.uuid4()}/media", headers=h).status_code == 404
        assert c.get(f"/api/v1/content/{item_id}/media").status_code == 401
