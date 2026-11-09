"""Fetch public Instagram media with yt-dlp, logged out (ADR-023).

Deliberate, user-approved exception to the original no-scraping rule:
- public posts only, never with cookies, passwords or browser sessions;
- slow (global minimum interval between requests) and self-limiting (stops on any block);
- media is stored locally and only derived text is sent to the LLM.

Reels/videos are downloaded as MP4. For photo and carousel posts yt-dlp has no video format, so
we read its metadata (``ignore_no_formats_error``) and download each slide's largest image.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)


class FetchError(Exception):
    """``reason`` ∈ blocked | unavailable | no_media | error. Only ``blocked`` pauses fetching."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class FetchPausedError(Exception):
    """Fetching is paused after a block; try this item again later."""


_BLOCKED = re.compile(r"rate.?limit|429|too many requests|please wait|login required|"
                      r"log in|checkpoint|challenge|not logged in|requires authentication",
                      re.IGNORECASE)
_UNAVAILABLE = re.compile(r"not available|404|private|been removed|does not exist|"
                          r"unavailable|restricted", re.IGNORECASE)


def classify(message: str) -> str:
    if _BLOCKED.search(message):
        return "blocked"
    if _UNAVAILABLE.search(message):
        return "unavailable"
    return "error"


@dataclass(slots=True)
class FetchedMedia:
    files: list[Path] = field(default_factory=list)
    caption: str | None = None
    uploader: str | None = None
    published_at: datetime | None = None
    tool_version: str | None = None


class MediaFetcher(Protocol):
    name: str

    def fetch(self, url: str, dest: Path) -> FetchedMedia: ...


class FetchGovernor(Protocol):
    def acquire(self) -> None: ...  # waits for a slot; raises FetchPausedError when paused

    def report_block(self) -> None: ...


class YtDlpInstagramFetcher:
    name = "yt-dlp"

    def __init__(self, *, max_bytes: int = 150 * 1024**2, max_items: int = 20,
                 video_format: str = "best[height<=720]/best", timeout: float = 30) -> None:
        self.max_bytes = max_bytes
        self.max_items = max_items
        self.video_format = video_format
        self.timeout = timeout

    def _opts(self, dest: Path) -> dict[str, Any]:
        return {
            "quiet": True, "no_warnings": True, "noprogress": True,
            "outtmpl": str(dest / "%(id)s.%(ext)s"),
            "format": self.video_format,
            "ignore_no_formats_error": True,
            "max_filesize": self.max_bytes,
            "socket_timeout": self.timeout,
            "retries": 1, "extractor_retries": 0,
            # Never authenticate: no cookie file, no browser cookies, no netrc.
            "cookiefile": None, "cookiesfrombrowser": None, "usenetrc": False,
        }

    def fetch(self, url: str, dest: Path) -> FetchedMedia:
        import yt_dlp

        dest.mkdir(parents=True, exist_ok=True)
        try:
            with yt_dlp.YoutubeDL(self._opts(dest)) as ydl:
                info = ydl.extract_info(url, download=False)
                entries = [e for e in (info.get("entries") or [info]) if e][: self.max_items]
                result = FetchedMedia(
                    caption=(info.get("description") or "").strip() or None,
                    uploader=info.get("channel") or info.get("uploader_id"),
                    published_at=datetime.fromtimestamp(info["timestamp"], UTC)
                    if info.get("timestamp") else None,
                    tool_version=yt_dlp.version.__version__,
                )
                for i, entry in enumerate(entries):
                    if entry.get("formats"):  # video
                        done = ydl.process_ie_result(entry, download=True)
                        path = (done.get("requested_downloads") or [{}])[0].get("filepath")
                        if path and Path(path).exists():
                            result.files.append(Path(path))
                    elif entry.get("thumbnails"):  # photo / carousel slide
                        result.files.append(self._download_image(entry["thumbnails"][-1]["url"],
                                                                 dest / f"slide_{i:02d}"))
        except FetchError:
            raise
        except Exception as exc:  # yt-dlp raises DownloadError/ExtractorError with text only
            message = str(exc)
            raise FetchError(classify(message), message[:500]) from exc
        if not result.files:
            raise FetchError("no_media", "Post has no downloadable media")
        return result

    def _download_image(self, url: str, stem: Path) -> Path:
        import httpx

        with httpx.stream("GET", url, timeout=self.timeout, follow_redirects=True) as r:
            if r.status_code in (403, 429):
                raise FetchError("blocked", f"Image CDN returned {r.status_code}")
            if r.status_code != 200:
                raise FetchError("error", f"Image CDN returned {r.status_code}")
            path = stem.with_suffix(".img")
            size = 0
            with path.open("wb") as fh:
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise FetchError("error", "Image exceeds size limit")
                    fh.write(chunk)
        return path


class RedisFetchGovernor:
    """Cross-process pacing + circuit breaker, shared by every pipeline worker."""

    SLOT, PAUSED = "lore:fetch:slot", "lore:fetch:paused"

    def __init__(self, redis_url: str, min_interval: float, pause_seconds: int,
                 max_wait: float = 120) -> None:
        import redis

        self.r = redis.Redis.from_url(redis_url)
        self.min_interval_ms = int(min_interval * 1000)
        self.pause_seconds = pause_seconds
        self.max_wait = max_wait

    def acquire(self) -> None:
        deadline = time.monotonic() + self.max_wait
        while True:
            if self.r.exists(self.PAUSED):
                raise FetchPausedError("Instagram fetching paused after a block")
            if self.r.set(self.SLOT, b"1", nx=True, px=self.min_interval_ms):
                return
            if time.monotonic() > deadline:
                raise FetchPausedError("No fetch slot available (busy)")
            time.sleep(max(0.2, (self.r.pttl(self.SLOT) or 200) / 1000))

    def report_block(self) -> None:
        self.r.set(self.PAUSED, b"1", ex=self.pause_seconds)
        log.warning("instagram_fetch_paused", extra={"seconds": self.pause_seconds})


class LocalFetchGovernor:
    """In-process equivalent (tests, inline mode)."""

    def __init__(self, min_interval: float = 0, pause_seconds: int = 1800) -> None:
        self.min_interval, self.pause_seconds = min_interval, pause_seconds
        self._next = 0.0
        self._paused_until = 0.0

    def acquire(self) -> None:
        now = time.monotonic()
        if now < self._paused_until:
            raise FetchPausedError("paused")
        if now < self._next:
            time.sleep(self._next - now)
        self._next = time.monotonic() + self.min_interval

    def report_block(self) -> None:
        self._paused_until = time.monotonic() + self.pause_seconds
