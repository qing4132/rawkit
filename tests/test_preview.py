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
    calls: list[dict] = []

    def fake(path, **kwargs):
        calls.append({"path": path, **kwargs})
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

    def fail(path, **_):
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


# --- resize (--long / --short / --mp) --------------------------------------

def _make_real_jpeg(w: int, h: int) -> bytes:
    """Build an actual valid JPEG of the requested size (solid grey).
    Needed because the resize path calls Image.open(), which won't accept
    our handcrafted SOF-only fixtures."""
    import io as _io
    from PIL import Image
    img = Image.new("RGB", (w, h), (128, 128, 128))
    buf = _io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_extract_long_edge_downscales(monkeypatch) -> None:
    jpeg = _make_real_jpeg(4000, 3000)
    _patch_rawpy(monkeypatch, _FakeThumb(jpeg, "JPEG"))
    r = extract_preview(Path("fake.cr3"), long_edge=1000)
    assert (r.width, r.height) == (1000, 750)
    assert r.data[:3] == b"\xff\xd8\xff"


def test_extract_short_edge_downscales(monkeypatch) -> None:
    jpeg = _make_real_jpeg(3000, 4000)  # portrait
    _patch_rawpy(monkeypatch, _FakeThumb(jpeg, "JPEG"))
    r = extract_preview(Path("fake.cr3"), short_edge=1080)
    # short side 3000 → 1080, ratio 0.36 → 1080 x 1440
    assert (r.width, r.height) == (1080, 1440)


def test_extract_megapixels_downscales(monkeypatch) -> None:
    jpeg = _make_real_jpeg(4000, 3000)  # 12 MP
    _patch_rawpy(monkeypatch, _FakeThumb(jpeg, "JPEG"))
    r = extract_preview(Path("fake.cr3"), megapixels=3.0)
    # 3 MP target → ratio = sqrt(3/12) = 0.5 → 2000x1500 = 3 MP
    assert (r.width, r.height) == (2000, 1500)


def test_extract_skips_resize_when_already_small(monkeypatch) -> None:
    jpeg = _make_real_jpeg(800, 600)
    _patch_rawpy(monkeypatch, _FakeThumb(jpeg, "JPEG"))
    r = extract_preview(Path("fake.cr3"), long_edge=2000)
    # No upscale; dimensions preserved. The resize path always re-encodes
    # (the embedded JPEG had to be decoded for orientation handling), so
    # the bytes aren't bitwise identical to the input — but the size is.
    assert (r.width, r.height) == (800, 600)
    assert r.data.startswith(b"\xff\xd8")


def test_extract_rejects_multiple_resize_dimensions(monkeypatch) -> None:
    jpeg = _make_real_jpeg(1000, 800)
    _patch_rawpy(monkeypatch, _FakeThumb(jpeg, "JPEG"))
    with pytest.raises(PreviewExtractError, match="at most one"):
        extract_preview(Path("fake.cr3"), long_edge=500, short_edge=400)


def test_extract_no_resize_returns_original_bytes(monkeypatch) -> None:
    """Fast path: when no resize is set, hand back the embedded bytes verbatim
    (no decode, no re-encode — preserves the camera's original JPEG)."""
    jpeg = _make_real_jpeg(1616, 1080)
    _patch_rawpy(monkeypatch, _FakeThumb(jpeg, "JPEG"))
    r = extract_preview(Path("fake.arw"))
    assert r.data is jpeg or r.data == jpeg


def _make_jpeg_with_orientation(w: int, h: int, orientation: int) -> bytes:
    """Build a real JPEG whose EXIF says it should be displayed with the
    given Orientation (1 = normal, 6 = rotate 90° CW, 8 = rotate 90° CCW)."""
    import io as _io
    from PIL import Image
    img = Image.new("RGB", (w, h), (128, 128, 128))
    exif = img.getexif()
    exif[0x0112] = orientation  # Orientation tag
    buf = _io.BytesIO()
    img.save(buf, format="JPEG", quality=85, exif=exif)
    return buf.getvalue()


def test_extract_bakes_exif_orientation_into_pixels(monkeypatch) -> None:
    """Real-world bug: portrait photos lose their EXIF Orientation tag when
    we re-encode for resize, leaving viewers that don't honour Orientation
    showing portraits as sideways landscapes. Fix: bake the rotation into
    pixels via exif_transpose before re-encoding.

    Sony A7R IV portrait shot looks like: physical JPEG 1616x1080 + Orientation=8
    (rotate 90° CCW), should display as 1080x1616. After resize the pixels must
    physically be portrait and the Orientation tag must be absent / 1."""
    import io as _io
    from PIL import Image
    jpeg = _make_jpeg_with_orientation(1616, 1080, orientation=8)
    _patch_rawpy(monkeypatch, _FakeThumb(jpeg, "JPEG"))

    # Resize with a long edge target larger than the long display dimension
    # — still triggers the decode/re-encode path because of transpose.
    r = extract_preview(Path("fake.arw"), long_edge=3000)

    # Physical pixels are now portrait (rotated)
    assert (r.width, r.height) == (1080, 1616)

    # Verify the re-encoded JPEG itself has portrait pixels AND no orientation tag
    out = Image.open(_io.BytesIO(r.data))
    out.load()
    assert out.size == (1080, 1616)
    assert out.getexif().get(0x0112) in (None, 1)


def test_extract_orientation_applied_with_actual_downscale(monkeypatch) -> None:
    """Combine orientation bake + actual downscale. Source: 4000x3000 portrait
    (i.e. 4000 is sensor width, displays as 3000x4000). Resize long=2000 →
    should produce physically portrait 1500x2000."""
    import io as _io
    from PIL import Image
    jpeg = _make_jpeg_with_orientation(4000, 3000, orientation=6)
    _patch_rawpy(monkeypatch, _FakeThumb(jpeg, "JPEG"))

    r = extract_preview(Path("fake.cr3"), long_edge=2000)
    # Source displays as 3000x4000 (long=4000). long_edge=2000 → ratio 0.5
    # → 1500x2000 physical pixels.
    assert (r.width, r.height) == (1500, 2000)
    out = Image.open(_io.BytesIO(r.data))
    out.load()
    assert out.size == (1500, 2000)
    assert out.getexif().get(0x0112) in (None, 1)


def test_cli_preview_long_flag(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, ["preview", str(tmp_path), "-o", str(out), "--long", "2000"])
    assert result.exit_code == 0
    assert fake_extract[0]["long_edge"] == 2000
    assert fake_extract[0]["short_edge"] is None
    assert fake_extract[0]["megapixels"] is None


def test_cli_preview_short_flag(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, ["preview", str(tmp_path), "-o", str(out), "--short", "1080"])
    assert result.exit_code == 0
    assert fake_extract[0]["short_edge"] == 1080


def test_cli_preview_mp_flag(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, ["preview", str(tmp_path), "-o", str(out), "--mp", "6"])
    assert result.exit_code == 0
    assert fake_extract[0]["megapixels"] == 6.0


def test_cli_preview_rejects_multiple_resize_flags(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, [
        "preview", str(tmp_path), "-o", str(out),
        "--long", "2000", "--short", "1080",
    ])
    assert result.exit_code == 2  # usage error
    assert "mutually exclusive" in result.stderr
    assert not fake_extract


def test_cli_preview_default_no_resize(tmp_path, fake_extract) -> None:
    """No flags → all resize args are None (fast embedded-bytes path)."""
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, ["preview", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert fake_extract[0]["long_edge"] is None
    assert fake_extract[0]["short_edge"] is None
    assert fake_extract[0]["megapixels"] is None
    assert fake_extract[0]["quality"] == 90
