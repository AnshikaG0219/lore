import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def client(session_factory, tmp_path):
    app = create_app(session_factory)
    settings = Settings(api_key="test-key", data_dir=tmp_path / "data",
                        max_upload_bytes=50 * 1024**2)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c


H = {"X-API-Key": "test-key"}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json()["database"] == "ok"


def test_requires_api_key(client):
    assert client.get("/api/v1/imports").status_code == 401
    assert client.get("/api/v1/imports", headers={"X-API-Key": "wrong"}).status_code == 401


def test_upload_and_report(client, synthetic_zip):
    with synthetic_zip.open("rb") as fh:
        r = client.post("/api/v1/imports", headers=H,
                        files={"file": ("my export.zip", fh, "application/zip")})
    assert r.status_code == 202, r.text
    batch_id = r.json()["id"]
    # TestClient runs background tasks before returning.
    detail = client.get(f"/api/v1/imports/{batch_id}", headers=H).json()
    assert detail["status"] == "completed_with_errors"
    assert detail["filename"] == "my_export.zip"
    assert detail["report"]["references"]["content_items_created"] == 5
    assert [b["id"] for b in client.get("/api/v1/imports", headers=H).json()] == [batch_id]
    # resuming a batch that is not completed is accepted and is a no-op
    assert client.post(f"/api/v1/imports/{batch_id}/resume", headers=H).status_code == 202


def test_rejects_non_zip_upload(client):
    r = client.post("/api/v1/imports", headers=H,
                    files={"file": ("x.zip", b"hello", "application/zip")})
    assert r.status_code == 415
    r = client.post("/api/v1/imports", headers=H, files={"file": ("x.txt", b"PK", "text/plain")})
    assert r.status_code == 415


def test_upload_size_limit(session_factory, tmp_path, synthetic_zip):
    app = create_app(session_factory)
    app.dependency_overrides[get_settings] = lambda: Settings(
        api_key="test-key", data_dir=tmp_path / "d", max_upload_bytes=100)
    with TestClient(app) as c, synthetic_zip.open("rb") as fh:
        r = c.post("/api/v1/imports", headers=H, files={"file": ("e.zip", fh, "application/zip")})
    assert r.status_code == 413
    assert not any((tmp_path / "d" / "uploads").iterdir())


def test_unknown_batch(client):
    assert client.get("/api/v1/imports/00000000-0000-0000-0000-000000000000",
                      headers=H).status_code == 404
