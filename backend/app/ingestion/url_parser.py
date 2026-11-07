"""Instagram URL detection and canonicalisation.

Identity rule: an Instagram media item is identified by its shortcode. Shortcodes are
case-sensitive and are shared across /p/, /reel/ and /tv/ paths for the same media, so
the shortcode, not the path, is the deduplication key.
"""

from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

INSTAGRAM_HOSTS = frozenset(
    {"instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am", "www.instagr.am"}
)

# Candidate URLs in free text. Deliberately broad; each match is validated by parse_url().
_URL_IN_TEXT = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?(?:instagram\.com|instagr\.am)/[^\s<>\"'\])}]+",
    re.IGNORECASE,
)
_SHORTCODE = re.compile(r"^[A-Za-z0-9_-]{5,64}$")
_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")

_MEDIA_KINDS = {"p": "post", "reel": "reel", "reels": "reel", "tv": "tv"}
_RESERVED_ROOTS = {
    "explore", "accounts", "direct", "about", "legal", "developer", "stories", "s",
    "share", "web", "challenge", "api", "graphql", "emails", "session", "privacy",
}


class UrlKind(enum.StrEnum):
    POST = "post"
    REEL = "reel"
    TV = "tv"
    SHARE_LINK = "share_link"  # needs network resolution to obtain a shortcode
    STORY = "story"
    PROFILE = "profile"
    OTHER = "other"


MEDIA_KINDS = frozenset({UrlKind.POST, UrlKind.REEL, UrlKind.TV})


@dataclass(frozen=True, slots=True)
class ParsedInstagramUrl:
    raw: str
    kind: UrlKind
    canonical_url: str
    shortcode: str | None = None
    username: str | None = None

    @property
    def is_media(self) -> bool:
        return self.kind in MEDIA_KINDS

    @property
    def identity_key(self) -> str:
        """Stable deduplication key. Media → shortcode; everything else → canonical URL."""
        if self.shortcode:
            return f"instagram:shortcode:{self.shortcode}"
        return f"instagram:url:{self.canonical_url}"

    @property
    def url_hash(self) -> str:
        return hashlib.sha256(self.canonical_url.encode()).hexdigest()


def _strip_trailing_punctuation(url: str) -> str:
    return url.rstrip(".,;:!?…")


def parse_url(raw: str) -> ParsedInstagramUrl | None:
    """Parse a single URL. Returns None when it is not an Instagram URL."""
    if not raw or not isinstance(raw, str):
        return None
    candidate = _strip_trailing_punctuation(raw.strip())
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host not in INSTAGRAM_HOSTS:
        return None

    segments = [unquote(s) for s in parts.path.split("/") if s]
    if not segments:
        return None

    # /{kind}/{code}/ or /{username}/{kind}/{code}/ (never /share/reel/{token}/ etc.)
    for offset in (0, 1):
        if offset == 1 and segments[0].lower() in _RESERVED_ROOTS:
            break
        if len(segments) >= offset + 2 and segments[offset].lower() in _MEDIA_KINDS:
            code = segments[offset + 1]
            if not _SHORTCODE.match(code):
                return None
            kind = UrlKind(_MEDIA_KINDS[segments[offset].lower()])
            username = segments[0] if offset == 1 and _USERNAME.match(segments[0]) else None
            path_kind = "reel" if kind is UrlKind.REEL else segments[offset].lower()
            return ParsedInstagramUrl(
                raw=raw,
                kind=kind,
                canonical_url=f"https://www.instagram.com/{path_kind}/{code}/",
                shortcode=code,
                username=username,
            )

    root = segments[0].lower()
    if root == "share" or root == "s":
        tail = "/".join(segments)
        return ParsedInstagramUrl(
            raw=raw, kind=UrlKind.SHARE_LINK, canonical_url=f"https://www.instagram.com/{tail}/"
        )
    if root == "stories" and len(segments) >= 2:
        tail = "/".join(segments[:3])
        return ParsedInstagramUrl(
            raw=raw,
            kind=UrlKind.STORY,
            canonical_url=f"https://www.instagram.com/{tail}/",
            username=segments[1] if segments[1] != "highlights" else None,
        )
    if len(segments) == 1 and root not in _RESERVED_ROOTS and _USERNAME.match(segments[0]):
        return ParsedInstagramUrl(
            raw=raw,
            kind=UrlKind.PROFILE,
            canonical_url=f"https://www.instagram.com/{segments[0].lower()}/",
            username=segments[0].lower(),
        )
    return ParsedInstagramUrl(
        raw=raw,
        kind=UrlKind.OTHER,
        canonical_url=f"https://www.instagram.com/{'/'.join(segments)}/",
    )


def extract_urls(text: str) -> list[ParsedInstagramUrl]:
    """Find and parse all Instagram URLs in free text, preserving order, without duplicates."""
    if not text:
        return []
    found: dict[str, ParsedInstagramUrl] = {}
    for match in _URL_IN_TEXT.finditer(text):
        parsed = parse_url(match.group(0))
        if parsed and parsed.identity_key not in found:
            found[parsed.identity_key] = parsed
    return list(found.values())
