"""Safe, read-only access to an Instagram export (ZIP file or already-extracted directory).

Nothing is ever extracted to disk. Members are read into memory one at a time, bounded by
``max_member_bytes``. Unsafe members (path traversal, absolute paths, symlinks, encrypted
entries, suspicious compression ratios) are skipped and reported rather than read.
"""

from __future__ import annotations

import hashlib
import stat
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


class ArchiveError(Exception):
    """The archive as a whole cannot be processed."""


class MemberReadError(Exception):
    """A single member could not be read safely."""


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_member_bytes: int = 500 * 1024**2
    max_members: int = 200_000
    max_compression_ratio: int = 200
    # Ratio checks only apply above this size; tiny highly-compressible JSON is normal.
    ratio_check_min_bytes: int = 1024**2


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    path: str  # normalised POSIX path relative to the archive root
    size: int
    compressed_size: int | None = None


@dataclass(slots=True)
class RejectedMember:
    path: str
    reason: str


@dataclass(slots=True)
class ArchiveListing:
    members: list[ArchiveMember] = field(default_factory=list)
    rejected: list[RejectedMember] = field(default_factory=list)


def _normalise_member_name(name: str) -> str | None:
    """Return a safe relative POSIX path or None if the name is unsafe."""
    if not name or "\x00" in name:
        return None
    name = name.replace("\\", "/")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return None
    parts = PurePosixPath(name).parts
    if any(p == ".." for p in parts):
        return None
    clean = "/".join(p for p in parts if p not in ("", "."))
    return clean or None


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class ExportArchive:
    """Uniform read-only view over a ZIP export or an extracted export directory."""

    def __init__(self, path: Path | str, limits: ArchiveLimits | None = None) -> None:
        self.path = Path(path)
        self.limits = limits or ArchiveLimits()
        self._zip: zipfile.ZipFile | None = None
        self._zip_names: dict[str, str] = {}
        self._listing: ArchiveListing | None = None

        if not self.path.exists():
            raise ArchiveError(f"Export not found: {self.path}")
        if self.path.is_file():
            if not zipfile.is_zipfile(self.path):
                raise ArchiveError("File is not a valid ZIP archive")
            try:
                self._zip = zipfile.ZipFile(self.path)
            except (zipfile.BadZipFile, OSError) as exc:
                raise ArchiveError(f"Cannot open ZIP archive: {exc}") from exc
        elif not self.path.is_dir():
            raise ArchiveError(f"Unsupported export path: {self.path}")

    @property
    def is_zip(self) -> bool:
        return self._zip is not None

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self) -> ExportArchive:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def listing(self) -> ArchiveListing:
        if self._listing is None:
            self._listing = self._list_zip() if self._zip else self._list_dir()
        return self._listing

    def members(self) -> list[ArchiveMember]:
        return self.listing().members

    def _list_zip(self) -> ArchiveListing:
        assert self._zip is not None
        infos = self._zip.infolist()
        if len(infos) > self.limits.max_members:
            raise ArchiveError(
                f"Archive has {len(infos)} entries; limit is {self.limits.max_members}"
            )
        listing = ArchiveListing()
        for info in infos:
            if info.is_dir():
                continue
            safe = _normalise_member_name(info.filename)
            if safe is None:
                listing.rejected.append(RejectedMember(info.filename, "unsafe path"))
                continue
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                listing.rejected.append(RejectedMember(safe, "symbolic link"))
                continue
            if info.flag_bits & 0x1:
                listing.rejected.append(RejectedMember(safe, "encrypted entry"))
                continue
            if (
                info.file_size >= self.limits.ratio_check_min_bytes
                and info.compress_size > 0
                and info.file_size / info.compress_size > self.limits.max_compression_ratio
            ):
                listing.rejected.append(RejectedMember(safe, "suspicious compression ratio"))
                continue
            if safe in self._zip_names:
                listing.rejected.append(RejectedMember(safe, "duplicate entry name"))
                continue
            self._zip_names[safe] = info.filename
            listing.members.append(ArchiveMember(safe, info.file_size, info.compress_size))
        listing.members.sort(key=lambda m: m.path)
        return listing

    def _list_dir(self) -> ArchiveListing:
        listing = ArchiveListing()
        root = self.path.resolve()
        count = 0
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                listing.rejected.append(RejectedMember(rel, "symbolic link"))
                continue
            if not p.is_file():
                continue
            count += 1
            if count > self.limits.max_members:
                raise ArchiveError(f"Export has more than {self.limits.max_members} files")
            listing.members.append(ArchiveMember(rel, p.stat().st_size))
        return listing

    def read_bytes(self, member_path: str) -> bytes:
        limit = self.limits.max_member_bytes
        if self._zip is not None:
            real = self._zip_names.get(member_path)
            if real is None:
                self.listing()
                real = self._zip_names.get(member_path)
            if real is None:
                raise MemberReadError(f"Not a readable member: {member_path}")
            try:
                with self._zip.open(real) as fh:
                    data = fh.read(limit + 1)
            except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
                # CRC mismatches surface here as BadZipFile.
                raise MemberReadError(f"Corrupt member {member_path}: {exc}") from exc
        else:
            safe = _normalise_member_name(member_path)
            if safe is None:
                raise MemberReadError(f"Unsafe path: {member_path}")
            root = self.path.resolve()
            target = (root / safe).resolve()
            if not target.is_relative_to(root) or target.is_symlink() or not target.is_file():
                raise MemberReadError(f"Not a readable member: {member_path}")
            with target.open("rb") as fh:
                data = fh.read(limit + 1)
        if len(data) > limit:
            raise MemberReadError(f"Member {member_path} exceeds {limit} bytes")
        return data

    def iter_members(self, suffixes: tuple[str, ...] | None = None) -> Iterator[ArchiveMember]:
        for m in self.members():
            if suffixes is None or m.path.lower().endswith(suffixes):
                yield m
