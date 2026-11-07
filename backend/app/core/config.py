from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INSTABRAIN_", env_file=".env", extra="ignore",
                                      frozen=True)

    env: str = "development"
    database_url: str = "postgresql+psycopg://instabrain:instabrain@localhost:5432/instabrain"
    redis_url: str = "redis://localhost:6379/0"
    api_key: SecretStr | None = None
    data_dir: Path = Path("./data")

    max_upload_bytes: int = Field(default=10 * 1024**3, gt=0)
    max_member_bytes: int = Field(default=500 * 1024**2, gt=0)
    max_archive_members: int = Field(default=200_000, gt=0)
    max_compression_ratio: int = Field(default=200, gt=0)

    # "celery": imports run on the worker (production). "inline": run in the calling
    # process (tests, or local use without Redis).
    task_backend: Literal["celery", "inline"] = "celery"
    import_task_max_retries: int = Field(default=8, ge=0)
    import_heartbeat_stale_seconds: int = Field(default=300, gt=0)
    import_sweep_interval_seconds: int = Field(default=60, gt=0)
    import_pending_grace_seconds: int = Field(default=120, ge=0)
    # Unacked tasks are redelivered after this; must exceed the longest import.
    broker_visibility_timeout_seconds: int = Field(default=6 * 3600, gt=0)

    # Media processing (Phase 2)
    max_media_bytes: int = Field(default=500 * 1024**2, gt=0)
    ocr_languages: str = "eng+hin"
    ocr_min_word_confidence: float = Field(default=40.0, ge=0, le=100)
    frame_interval_seconds: float = Field(default=2.0, gt=0)
    max_frames_per_video: int = Field(default=45, gt=0)
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    whisper_device: str = "cpu"
    copilot_model: str = "claude-haiku-5.5"
    # GitHub token with Copilot access, used by the extraction task. Never logged.
    copilot_github_token: SecretStr | None = None

    log_level: str = "INFO"
    log_json: bool = False

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"


@lru_cache
def get_settings() -> Settings:
    return Settings()
