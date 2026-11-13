"""Build pipeline dependencies from settings (one place, used by workers, API and CLI)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.processing.ai.provider import LLMProvider
from app.processing.media.engines import build_engines, build_store
from app.processing.pipeline import PipelineRunner, ProviderNotConfiguredError


def provider_factory(settings: Settings):
    cache: dict[str, LLMProvider] = {}

    def get() -> LLMProvider:
        if "p" not in cache:
            if not settings.copilot_github_token:
                raise ProviderNotConfiguredError(
                    "AI provider not configured (set LORE_COPILOT_GITHUB_TOKEN); "
                    "reprocess the item once configured")
            from app.processing.ai.copilot_sdk import CopilotSdkProvider

            cache["p"] = CopilotSdkProvider(settings.copilot_github_token.get_secret_value(),
                                            settings.copilot_model)
        return cache["p"]

    return get


def build_fetcher(settings: Settings):
    if not settings.media_fetch_enabled:
        return None, None
    from app.processing.fetch.instagram import RedisFetchGovernor, YtDlpInstagramFetcher

    fetcher = YtDlpInstagramFetcher(max_bytes=settings.media_fetch_max_bytes,
                                    video_format=settings.media_fetch_video_format)
    governor = RedisFetchGovernor(settings.redis_url, settings.media_fetch_min_interval_seconds,
                                  settings.media_fetch_pause_seconds)
    return fetcher, governor


def build_runner(settings: Settings, session_factory: sessionmaker[Session],
                 provider=None) -> PipelineRunner:
    fetcher, governor = build_fetcher(settings)
    return PipelineRunner(
        session_factory,
        engines=lambda: build_engines(settings),
        store=build_store(settings),
        provider=provider or provider_factory(settings),
        stale_after=timedelta(seconds=settings.pipeline_stale_seconds),
        fetcher=fetcher,
        governor=governor,
    )
