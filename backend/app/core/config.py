from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INSTABRAIN_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://instabrain:instabrain@localhost:5432/instabrain"
    redis_url: str = "redis://localhost:6379/0"
    api_key: SecretStr | None = None
    data_dir: Path = Path("./data")

    max_upload_bytes: int = Field(default=10 * 1024**3, gt=0)
    max_member_bytes: int = Field(default=500 * 1024**2, gt=0)
    max_archive_members: int = Field(default=200_000, gt=0)
    max_compression_ratio: int = Field(default=200, gt=0)

    log_level: str = "INFO"
    log_json: bool = False

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


@lru_cache
def get_settings() -> Settings:
    return Settings()
