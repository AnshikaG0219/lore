import os
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import models  # noqa: F401
from app.db.base import Base

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC_EXPORT = FIXTURES / "synthetic_export_v1"
# Drop real (anonymised) exports here; tests marked `real_export` pick them up automatically.
REAL_EXPORTS = FIXTURES / "real"


def zip_directory(src: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(src).as_posix())
    return dest


@pytest.fixture
def synthetic_zip(tmp_path: Path) -> Path:
    return zip_directory(SYNTHETIC_EXPORT, tmp_path / "instagram-export.zip")


def _sqlite_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture(params=["sqlite", "postgres"])
def session_factory(request):
    """Run DB tests on SQLite always, and on PostgreSQL when INSTABRAIN_TEST_DATABASE_URL is set."""
    if request.param == "sqlite":
        yield _sqlite_factory()
        return
    url = os.environ.get("INSTABRAIN_TEST_DATABASE_URL")
    if not url:
        pytest.skip("INSTABRAIN_TEST_DATABASE_URL not set")
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.drop_all(engine)
    engine.dispose()
