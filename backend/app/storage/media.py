"""Local media storage (object storage can implement the same interface later).

Files are content-addressed: ``<media_dir>/<content_id>/<sha256>.<ext>``. Type detection uses
magic bytes, never the filename or client-supplied MIME type. Files are never executed.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


class MediaRejectedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class MediaKind:
    asset_type: str  # image | video | audio
    mime_type: str
    extension: str


def sniff(head: bytes) -> MediaKind | None:
    """Identify supported media from the first bytes of a file."""
    if head.startswith(b"\xff\xd8\xff"):
        return MediaKind("image", "image/jpeg", "jpg")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return MediaKind("image", "image/png", "png")
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return MediaKind("image", "image/webp", "webp")
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return MediaKind("audio", "audio/wav", "wav")
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1"):
            return MediaKind("image", "image/heic", "heic")
        if brand in (b"M4A ", b"M4B "):
            return MediaKind("audio", "audio/mp4", "m4a")
        if brand == b"qt  ":
            return MediaKind("video", "video/quicktime", "mov")
        return MediaKind("video", "video/mp4", "mp4")
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return MediaKind("audio", "audio/mpeg", "mp3")
    return None


@dataclass(frozen=True, slots=True)
class StoredMedia:
    path: Path
    kind: MediaKind
    size: int
    checksum: str


class LocalMediaStore:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root.expanduser().resolve()
        self.max_bytes = max_bytes

    def save(self, content_id: uuid.UUID, stream: BinaryIO) -> StoredMedia:
        """Stream to a temp file (bounded), verify type, then move into place atomically."""
        target_dir = self.root / str(content_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        head = b""
        fd, tmp_name = tempfile.mkstemp(dir=target_dir, prefix=".upload-")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := stream.read(1024 * 1024):
                    if len(head) < 32:
                        head += chunk[: 32 - len(head)]
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise MediaRejectedError(f"File exceeds {self.max_bytes} bytes")
                    digest.update(chunk)
                    out.write(chunk)
            if size == 0:
                raise MediaRejectedError("Empty file")
            kind = sniff(head)
            if kind is None:
                raise MediaRejectedError("Unsupported or unrecognised media type")
            checksum = digest.hexdigest()
            final = target_dir / f"{checksum}.{kind.extension}"
            tmp.replace(final)
            return StoredMedia(final, kind, size, checksum)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def resolve(self, storage_path: str) -> Path:
        path = Path(storage_path).resolve()
        if not path.is_relative_to(self.root):
            raise MediaRejectedError("Media path outside media store")
        return path
