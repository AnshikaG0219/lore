import io
import stat
import zipfile
from pathlib import Path

import pytest

from app.ingestion.instagram.archive import (
    ArchiveError,
    ArchiveLimits,
    ExportArchive,
    MemberReadError,
)


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_rejects_non_zip(tmp_path):
    p = tmp_path / "x.zip"
    p.write_bytes(b"not a zip at all")
    with pytest.raises(ArchiveError):
        ExportArchive(p)


def test_missing_path(tmp_path):
    with pytest.raises(ArchiveError):
        ExportArchive(tmp_path / "nope.zip")


def test_path_traversal_and_absolute_entries_are_rejected(tmp_path):
    p = _zip(tmp_path / "evil.zip", {
        "../../etc/evil.json": b"{}",
        "/abs/evil.json": b"{}",
        "ok/..\\..\\win.json": b"{}",
        "C:/windows/evil.json": b"{}",
        "fine/saved_posts.json": b"{}",
    })
    with ExportArchive(p) as a:
        listing = a.listing()
        assert [m.path for m in listing.members] == ["fine/saved_posts.json"]
        assert {r.reason for r in listing.rejected} == {"unsafe path"}
        assert len(listing.rejected) == 4
        with pytest.raises(MemberReadError):
            a.read_bytes("../../etc/evil.json")


def test_nothing_is_written_to_disk(tmp_path):
    p = _zip(tmp_path / "a.zip", {"x/saved_posts.json": b"{}"})
    before = set(tmp_path.rglob("*"))
    with ExportArchive(p) as a:
        a.read_bytes("x/saved_posts.json")
    assert set(tmp_path.rglob("*")) == before


def test_symlink_entries_are_rejected(tmp_path):
    p = tmp_path / "link.zip"
    with zipfile.ZipFile(p, "w") as zf:
        info = zipfile.ZipInfo("export/link.json")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with ExportArchive(p) as a:
        assert a.members() == []
        assert a.listing().rejected[0].reason == "symbolic link"


def test_compression_bomb_is_rejected(tmp_path):
    p = _zip(tmp_path / "bomb.zip", {"x/messages/inbox/a/message_1.json": b"0" * (5 * 1024**2)})
    with ExportArchive(p, ArchiveLimits(max_compression_ratio=50)) as a:
        assert a.members() == []
        assert a.listing().rejected[0].reason == "suspicious compression ratio"


def test_member_size_limit(tmp_path):
    p = _zip(tmp_path / "big.zip", {"x/saved_posts.json": b"x" * 2048})
    with ExportArchive(p, ArchiveLimits(max_member_bytes=1024)) as a, \
            pytest.raises(MemberReadError, match="exceeds"):
        a.read_bytes("x/saved_posts.json")


def test_member_count_limit(tmp_path):
    p = _zip(tmp_path / "many.zip", {f"f{i}.json": b"{}" for i in range(5)})
    with ExportArchive(p, ArchiveLimits(max_members=3)) as a, pytest.raises(ArchiveError):
        a.members()


def test_corrupt_member_crc_reported(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("x/saved_posts.json", b'{"a": "hello world"}')
    data = bytearray(buf.getvalue())
    idx = data.find(b"hello")
    data[idx] = ord("J")
    p = tmp_path / "corrupt.zip"
    p.write_bytes(bytes(data))
    with ExportArchive(p) as a, pytest.raises(MemberReadError, match="Corrupt"):
        a.read_bytes("x/saved_posts.json")


def test_directory_export_rejects_escaping_symlink(tmp_path):
    root = tmp_path / "export"
    root.mkdir()
    (root / "saved_posts.json").write_text("{}")
    outside = tmp_path / "secret.json"
    outside.write_text("{}")
    (root / "link.json").symlink_to(outside)
    with ExportArchive(root) as a:
        assert [m.path for m in a.members()] == ["saved_posts.json"]
        with pytest.raises(MemberReadError):
            a.read_bytes("link.json")
        with pytest.raises(MemberReadError):
            a.read_bytes("../secret.json")
