"""Real-engine test: Tesseract OCR, ffmpeg frame sampling and faster-whisper transcription.

Skipped when the tools aren't installed. Downloads the Whisper "base" model on first run.
"""

import pytest

from app.db.models import ContentItem, MediaAsset, OcrText, Transcript
from app.db.repositories.content import get_or_create_user
from app.processing.media.pipeline import MediaEngines, attach_media, media_text_for, process_asset
from app.processing.ocr.engine import TesseractOcr
from app.processing.transcription.engine import FasterWhisperTranscriber
from app.storage.media import LocalMediaStore
from tests.media_fixtures import media_tools_available, reel, text_slide

pytestmark = pytest.mark.skipif(not media_tools_available(), reason="ffmpeg/tesseract/say missing")

SPEECH = ("If you are in Leh, visit Lehvenda Cafe for apricot pie, and drive to "
          "Pangong Lake early in the morning.")


@pytest.fixture(scope="module")
def engines():
    return MediaEngines(ocr=TesseractOcr("eng"),
                        transcriber=FasterWhisperTranscriber("base", "cpu", "int8"),
                        frame_interval=1.0, max_frames=20)


@pytest.fixture
def item_id(session_factory):
    with session_factory() as s:
        user = get_or_create_user(s)
        it = ContentItem(user_id=user.id, identity_key="instagram:shortcode:MEDIA1",
                         canonical_url="https://www.instagram.com/reel/MEDIA1/",
                         canonical_url_hash="h", content_type="reel", extracted_data={}, tags=[])
        s.add(it)
        s.commit()
        return it.id


def test_image_ocr_and_video_transcription(session_factory, engines, item_id, tmp_path):
    store = LocalMediaStore(tmp_path / "media", 50 * 1024**2)
    slide = text_slide(tmp_path / "books.jpg", ["5 short books", "1. Siddhartha",
                                                "2. Animal Farm", "3. The Alchemist"])
    video = reel(tmp_path / "reel.mp4", [(["LEH FOOD GUIDE"], 3), (["Lehvenda Cafe"], 3)],
                 SPEECH)

    with session_factory() as s:
        ids = []
        for f in (slide, video):
            with f.open("rb") as fh:
                ids.append(attach_media(s, store, item_id, fh, f.name)[0].id)
        for a in ids:
            assert process_asset(s, a, engines, store)["status"] == "completed"
        assert process_asset(s, ids[0], engines, store)["status"] == "skipped"  # idempotent

        text = media_text_for(s, item_id)
        ocr = text.on_screen_text.lower()
        for expected in ("siddhartha", "animal farm", "alchemist", "leh food guide",
                         "lehvenda cafe"):
            assert expected in ocr, (expected, text.on_screen_text)
        assert ocr.count("lehvenda cafe") == 1  # repeated overlay de-duplicated across frames

        spoken = text.transcript.lower()
        assert text.transcript_language == "en"
        # Whisper mis-spells rare proper nouns ("Pangond", "Leven the Cafe"); this is why the
        # extraction prompt prefers on-screen text for names. Assert the stable phrases.
        assert "lake early in the morning" in spoken and "cafe" in spoken, text.transcript

        tr = s.query(Transcript).one()
        assert tr.timestamped_segments and tr.timestamped_segments[0]["start"] >= 0
        assert s.query(OcrText).filter(OcrText.frame_time >= 0).count() >= 5
        video_asset = s.get(MediaAsset, ids[1])
        assert video_asset.meta["probe"]["has_audio"] is True
