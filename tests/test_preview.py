"""Tests for the preview command + rawpy-based extraction.

Real rawpy is exercised by hand against samples/. These tests pin the
single-engine extraction surface and the CLI behaviour (skip / overwrite /
exit codes / stderr layout).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rawkit.cli import app
from rawkit.preview import (
    PreviewExtractError,
    PreviewResult,
    _read_jpeg_size,
    extract_preview,
)

runner = CliRunner()


# --- extract_preview behaviour ---------------------------------------------

class _FakeFormat:
    """Stand-in for rawpy.ThumbFormat enum member (only `.name` is read)."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeThumb:
    def __init__(self, data: bytes, fmt: str) -> None:
        self.data = data
        self.format = _FakeFormat(fmt)


class _FakeRaw:
    def __init__(self, thumb: _FakeThumb) -> None:
        self._thumb = thumb

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def extract_thumb(self) -> _FakeThumb:
        return self._thumb


def _patch_rawpy(
    monkeypatch,
    thumb: _FakeThumb | None,
    raises: Exception | None = None,
) -> None:
    """Replace `rawpy.imread` with a fake that returns `thumb` or raises."""
    fake_module = types.SimpleNamespace()

    def fake_imread(_path: str):
        if raises is not None:
            raise raises
        return _FakeRaw(thumb)

    fake_module.imread = fake_imread
    monkeypatch.setitem(sys.modules, "rawpy", fake_module)


# A minimal valid JPEG: SOI + SOF0 with width 1616, height 1080.
_FAKE_JPEG_1616x1080 = bytes([
    0xFF, 0xD8,
    0xFF, 0xC0, 0x00, 0x0B,  # SOF0, segment length 11
    0x08,                     # precision
    0x04, 0x38,               # height 0x0438 = 1080
    0x06, 0x50,               # width  0x0650 = 1616
    0x03, 0x01, 0x22, 0x00,
])


def test_extract_returns_jpeg_with_parsed_dimensions(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, _FakeThumb(_FAKE_JPEG_1616x1080, "JPEG"))
    r = extract_preview(Path("fake.arw"))
    assert (r.width, r.height) == (1616, 1080)
    assert r.data.startswith(b"\xff\xd8")


def test_extract_raises_when_libraw_fails(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, None, raises=OSError("Unsupported file format"))
    with pytest.raises(PreviewExtractError, match="libraw failed"):
        extract_preview(Path("fake.unknown"))


def test_extract_raises_on_non_jpeg_preview(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, _FakeThumb(b"raw bitmap bytes", "BITMAP"))
    with pytest.raises(PreviewExtractError, match="not JPEG"):
        extract_preview(Path("fake.x3f"))


def test_extract_raises_on_unparseable_jpeg(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, _FakeThumb(b"not a real jpeg", "JPEG"))
    with pytest.raises(PreviewExtractError, match="parse JPEG dimensions"):
        extract_preview(Path("fake.arw"))


# --- JPEG SOF parser --------------------------------------------------------

def test_read_jpeg_size_parses_sof0() -> None:
    w, h = _read_jpeg_size(_FAKE_JPEG_1616x1080)
    assert (w, h) == (1616, 1080)


def test_read_jpeg_size_rejects_non_jpeg() -> None:
    assert _read_jpeg_size(b"not a jpeg") == (0, 0)
    assert _read_jpeg_size(b"") == (0, 0)


# --- CLI surface ------------------------------------------------------------

@pytest.fixture
def fake_extract(monkeypatch):
    """Stub extract_preview so the CLI test doesn't touch real RAW files."""
    calls: list[Path] = []

    def fake(path):
        calls.append(path)
        return PreviewResult(b"\xff\xd8FAKE", 1616, 1080)

    monkeypatch.setattr("rawkit.cli.extract_preview", fake)
    return calls


def test_preview_writes_one_jpg_per_raw(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    (tmp_path / "b.CR3").write_bytes(b"")
    out = tmp_path / "out"

    result = runner.invoke(app, ["preview", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert (out / "a.jpg").read_bytes().startswith(b"\xff\xd8")
    assert (out / "b.jpg").read_bytes().startswith(b"\xff\xd8")
    assert "a.ARW" in result.stderr
    assert "1616x1080" in result.stderr


def test_preview_skips_existing_by_default(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.jpg").write_bytes(b"EXISTING")

    result = runner.invoke(app, ["preview", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert (out / "a.jpg").read_bytes() == b"EXISTING"
    assert "skip" in result.stderr
    assert not fake_extract  # extract was never called for the skipped file


def test_preview_overwrites_with_f(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.jpg").write_bytes(b"EXISTING")

    result = runner.invoke(app, ["preview", str(tmp_path), "-o", str(out), "-f"])
    assert result.exit_code == 0
    assert (out / "a.jpg").read_bytes().startswith(b"\xff\xd8")


def test_preview_reports_failure_and_exits_nonzero(tmp_path, monkeypatch) -> None:
    (tmp_path / "broken.ARW").write_bytes(b"")

    def fail(path):
        raise PreviewExtractError("libraw failed: bogus header")

    monkeypatch.setattr("rawkit.cli.extract_preview", fail)

    out = tmp_path / "out"
    result = runner.invoke(app, ["preview", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 1
    assert "failed" in result.stderr
    assert "libraw failed" in result.stderr


def test_preview_empty_directory(tmp_path, fake_extract) -> None:
    out = tmp_path / "out"
    result = runner.invoke(app, ["preview", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    # Output directory is NOT created when there's nothing to extract.
    assert not out.exists()
