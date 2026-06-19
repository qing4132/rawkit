"""Tests for the extract command + rawpy-based extraction.

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
from rawkit.extract import (
    ExtractError,
    ExtractResult,
    _read_jpeg_size,
    extract_jpeg,
)

runner = CliRunner()


# --- extract_jpeg behaviour ---------------------------------------------

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
    r = extract_jpeg(Path("fake.arw"))
    assert (r.width, r.height) == (1616, 1080)
    assert r.data.startswith(b"\xff\xd8")


def test_extract_raises_when_libraw_fails(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, None, raises=OSError("Unsupported file format"))
    with pytest.raises(ExtractError, match="libraw failed"):
        extract_jpeg(Path("fake.unknown"))


def test_extract_raises_on_non_jpeg_preview(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, _FakeThumb(b"raw bitmap bytes", "BITMAP"))
    with pytest.raises(ExtractError, match="not JPEG"):
        extract_jpeg(Path("fake.x3f"))


def test_extract_raises_on_unparseable_jpeg(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, _FakeThumb(b"not a real jpeg", "JPEG"))
    with pytest.raises(ExtractError, match="parse JPEG dimensions"):
        extract_jpeg(Path("fake.arw"))


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
    """Stub extract_jpeg so the CLI test doesn't touch real RAW files."""
    calls: list[dict] = []

    def fake(path, **kwargs):
        calls.append({"path": path, **kwargs})
        return ExtractResult(b"\xff\xd8FAKE", 1616, 1080)

    monkeypatch.setattr("rawkit.cli.extract_jpeg", fake)
    return calls


def test_extract_writes_one_jpg_per_raw(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    (tmp_path / "b.CR3").write_bytes(b"")
    out = tmp_path / "out"

    result = runner.invoke(app, ["extract", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert (out / "a.jpg").read_bytes().startswith(b"\xff\xd8")
    assert (out / "b.jpg").read_bytes().startswith(b"\xff\xd8")
    assert "a.ARW" in result.stderr
    assert "1616x1080" in result.stderr


def test_extract_skips_existing_by_default(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.jpg").write_bytes(b"EXISTING")

    result = runner.invoke(app, ["extract", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert (out / "a.jpg").read_bytes() == b"EXISTING"
    assert "skip" in result.stderr
    assert not fake_extract  # extract was never called for the skipped file


def test_extract_overwrites_with_f(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.jpg").write_bytes(b"EXISTING")

    result = runner.invoke(app, ["extract", str(tmp_path), "-o", str(out), "-f"])
    assert result.exit_code == 0
    assert (out / "a.jpg").read_bytes().startswith(b"\xff\xd8")


def test_extract_reports_failure_and_exits_nonzero(tmp_path, monkeypatch) -> None:
    (tmp_path / "broken.ARW").write_bytes(b"")

    def fail(path, **_):
        raise ExtractError("libraw failed: bogus header")

    monkeypatch.setattr("rawkit.cli.extract_jpeg", fail)

    out = tmp_path / "out"
    result = runner.invoke(app, ["extract", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 1
    assert "failed" in result.stderr
    assert "libraw failed" in result.stderr


def test_extract_empty_directory(tmp_path, fake_extract) -> None:
    out = tmp_path / "out"
    result = runner.invoke(app, ["extract", str(tmp_path), "-o", str(out)])
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
    r = extract_jpeg(Path("fake.cr3"), long_edge=1000)
    assert (r.width, r.height) == (1000, 750)
    assert r.data[:3] == b"\xff\xd8\xff"


def test_extract_short_edge_downscales(monkeypatch) -> None:
    jpeg = _make_real_jpeg(3000, 4000)  # portrait
    _patch_rawpy(monkeypatch, _FakeThumb(jpeg, "JPEG"))
    r = extract_jpeg(Path("fake.cr3"), short_edge=1080)
    # short side 3000 → 1080, ratio 0.36 → 1080 x 1440
    assert (r.width, r.height) == (1080, 1440)


def test_extract_megapixels_downscales(monkeypatch) -> None:
    jpeg = _make_real_jpeg(4000, 3000)  # 12 MP
    _patch_rawpy(monkeypatch, _FakeThumb(jpeg, "JPEG"))
    r = extract_jpeg(Path("fake.cr3"), megapixels=3.0)
    # 3 MP target → ratio = sqrt(3/12) = 0.5 → 2000x1500 = 3 MP
    assert (r.width, r.height) == (2000, 1500)


def test_extract_skips_resize_when_already_small(monkeypatch) -> None:
    jpeg = _make_real_jpeg(800, 600)
    _patch_rawpy(monkeypatch, _FakeThumb(jpeg, "JPEG"))
    r = extract_jpeg(Path("fake.cr3"), long_edge=2000)
    # No upscale; dimensions preserved. The resize path always re-encodes
    # (the embedded JPEG had to be decoded for orientation handling), so
    # the bytes aren't bitwise identical to the input — but the size is.
    assert (r.width, r.height) == (800, 600)
    assert r.data.startswith(b"\xff\xd8")


def test_extract_rejects_multiple_resize_dimensions(monkeypatch) -> None:
    jpeg = _make_real_jpeg(1000, 800)
    _patch_rawpy(monkeypatch, _FakeThumb(jpeg, "JPEG"))
    with pytest.raises(ExtractError, match="at most one"):
        extract_jpeg(Path("fake.cr3"), long_edge=500, short_edge=400)


def test_extract_no_resize_returns_original_bytes(monkeypatch) -> None:
    """Fast path: when no resize is set, hand back the embedded bytes verbatim
    (no decode, no re-encode — preserves the camera's original JPEG)."""
    jpeg = _make_real_jpeg(1616, 1080)
    _patch_rawpy(monkeypatch, _FakeThumb(jpeg, "JPEG"))
    r = extract_jpeg(Path("fake.arw"))
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
    r = extract_jpeg(Path("fake.arw"), long_edge=3000)

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

    r = extract_jpeg(Path("fake.cr3"), long_edge=2000)
    # Source displays as 3000x4000 (long=4000). long_edge=2000 → ratio 0.5
    # → 1500x2000 physical pixels.
    assert (r.width, r.height) == (1500, 2000)
    out = Image.open(_io.BytesIO(r.data))
    out.load()
    assert out.size == (1500, 2000)
    assert out.getexif().get(0x0112) in (None, 1)


def test_cli_extract_long_flag(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, ["extract", str(tmp_path), "-o", str(out), "--long", "2000"])
    assert result.exit_code == 0
    assert fake_extract[0]["long_edge"] == 2000
    assert fake_extract[0]["short_edge"] is None
    assert fake_extract[0]["megapixels"] is None


def test_cli_extract_short_flag(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, ["extract", str(tmp_path), "-o", str(out), "--short", "1080"])
    assert result.exit_code == 0
    assert fake_extract[0]["short_edge"] == 1080


def test_cli_extract_mp_flag(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, ["extract", str(tmp_path), "-o", str(out), "--mp", "6"])
    assert result.exit_code == 0
    assert fake_extract[0]["megapixels"] == 6.0


def test_cli_extract_rejects_multiple_resize_flags(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, [
        "extract", str(tmp_path), "-o", str(out),
        "--long", "2000", "--short", "1080",
    ])
    assert result.exit_code == 2  # usage error
    assert "mutually exclusive" in result.stderr
    assert not fake_extract


def test_cli_extract_default_no_resize(tmp_path, fake_extract) -> None:
    """No flags → all resize args are None (fast embedded-bytes path)."""
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, ["extract", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert fake_extract[0]["long_edge"] is None
    assert fake_extract[0]["short_edge"] is None
    assert fake_extract[0]["megapixels"] is None
    assert fake_extract[0]["quality"] == 90


# --- --where filter ---------------------------------------------------------

@pytest.fixture
def fake_exif_for_where(monkeypatch):
    """Stub safe_batch_read so --where uses synthetic EXIF without exiftool."""

    def fake(paths):
        out = []
        for p in paths:
            name = Path(p).name
            # Encode ISO in the test filename so per-file tests can target rows.
            iso = 100 if "low" in name else 6400
            out.append({"path": str(p), "iso": iso, "model": "EOS R5", "maker": "Canon"})
        return out

    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)
    return fake


def test_extract_where_filters_to_matching(tmp_path, fake_extract, fake_exif_for_where) -> None:
    (tmp_path / "low.ARW").write_bytes(b"")     # ISO 100 → won't match
    (tmp_path / "high.ARW").write_bytes(b"")    # ISO 6400 → matches
    out = tmp_path / "out"

    result = runner.invoke(app, [
        "extract", str(tmp_path), "-o", str(out),
        "--where", "iso>3200",
    ])
    assert result.exit_code == 0
    extracted_names = {Path(c["path"]).name for c in fake_extract}
    assert extracted_names == {"high.ARW"}
    assert not (out / "low.jpg").exists()
    assert (out / "high.jpg").exists()


def test_extract_where_zero_matches_is_clean_exit(tmp_path, fake_extract, fake_exif_for_where) -> None:
    (tmp_path / "low.ARW").write_bytes(b"")
    out = tmp_path / "out"

    result = runner.invoke(app, [
        "extract", str(tmp_path), "-o", str(out),
        "--where", "iso>50000",
    ])
    assert result.exit_code == 0
    assert not fake_extract  # no extractions attempted
    assert not out.exists()  # output dir not even created


def test_extract_where_bad_syntax_exits_2(tmp_path, fake_extract) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, [
        "extract", str(tmp_path), "-o", str(out),
        "--where", "iso >>>> not valid",
    ])
    assert result.exit_code == 2
    assert "--where" in result.stderr
    assert not fake_extract


def test_extract_without_where_skips_exiftool(tmp_path, fake_extract, monkeypatch) -> None:
    """No --where → safe_batch_read must NOT be called (preview stays
    exiftool-free for the fast SOOC path)."""
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"

    def explode(_paths):
        raise AssertionError("safe_batch_read called without --where")

    monkeypatch.setattr("rawkit.cli.safe_batch_read", explode)

    result = runner.invoke(app, ["extract", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert len(fake_extract) == 1


# --- output path mirrors source hierarchy ----------------------------------
# Critical when --recursive scoops up multiple subdirs that contain
# same-basename RAWs (common pattern: Canon shutter-count wraparound
# producing IMG_0001.CR3 in different year/month folders).

def test_extract_recursive_mirrors_subdir_structure(tmp_path, fake_extract) -> None:
    """`-R` extracts must preserve the source subdir path under the output dir,
    not flatten everything to one level (which would collide on dup basenames)."""
    (tmp_path / "2024").mkdir()
    (tmp_path / "2025").mkdir()
    (tmp_path / "2024" / "IMG_0001.CR3").write_bytes(b"")
    (tmp_path / "2025" / "IMG_0001.CR3").write_bytes(b"")
    out = tmp_path / "out"

    result = runner.invoke(app, ["extract", str(tmp_path), "-R", "-o", str(out)])
    assert result.exit_code == 0
    assert (out / "2024" / "IMG_0001.jpg").exists()
    assert (out / "2025" / "IMG_0001.jpg").exists()


def test_extract_direct_file_arg_uses_basename(tmp_path, fake_extract) -> None:
    """A RAW passed as a direct file arg (not via a parent dir input)
    lands as just its basename under -o — no leading absolute path."""
    raw = tmp_path / "weird_subdir" / "foo.ARW"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"")
    out = tmp_path / "out"

    result = runner.invoke(app, ["extract", str(raw), "-o", str(out)])
    assert result.exit_code == 0
    assert (out / "foo.jpg").exists()
    # Must NOT have written under any subdir path that mirrors source.
    assert not (out / "weird_subdir" / "foo.jpg").exists()


def test_extract_mixed_file_and_dir_input(tmp_path, fake_extract) -> None:
    """A direct file gets basename; a dir input gets mirrored subtree.
    Both modes coexist in one invocation."""
    direct = tmp_path / "loose" / "loose.ARW"
    direct.parent.mkdir()
    direct.write_bytes(b"")
    (tmp_path / "scan" / "sub").mkdir(parents=True)
    (tmp_path / "scan" / "sub" / "nested.ARW").write_bytes(b"")
    out = tmp_path / "out"

    result = runner.invoke(
        app, ["extract", str(direct), str(tmp_path / "scan"), "-R", "-o", str(out)]
    )
    assert result.exit_code == 0
    # direct file → basename
    assert (out / "loose.jpg").exists()
    # dir input → mirrored subtree, rooted at the input dir
    assert (out / "sub" / "nested.jpg").exists()


def test_extract_rejects_intra_run_basename_collision(tmp_path, fake_extract) -> None:
    """Two file args with the same basename in different dirs would both
    write to out/foo.jpg → silent data loss. Refuse fast (exit 1) and
    show which sources collide."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "foo.CR3").write_bytes(b"")
    (tmp_path / "b" / "foo.CR3").write_bytes(b"")
    out = tmp_path / "out"

    result = runner.invoke(app, [
        "extract", str(tmp_path / "a" / "foo.CR3"), str(tmp_path / "b" / "foo.CR3"),
        "-o", str(out),
    ])
    assert result.exit_code == 1
    assert "collision" in result.stderr
    # Both source paths must be mentioned so the user knows what to fix.
    assert "/a/foo.CR3" in result.stderr
    assert "/b/foo.CR3" in result.stderr
    # And nothing was extracted (fail-fast, not partial).
    assert not fake_extract


def test_extract_rejects_intra_run_dir_collision(tmp_path, fake_extract) -> None:
    """Two -R dir inputs whose subtrees both have foo.CR3 at the same
    relative path also collide. Must fail-fast."""
    (tmp_path / "trip1" / "day").mkdir(parents=True)
    (tmp_path / "trip2" / "day").mkdir(parents=True)
    (tmp_path / "trip1" / "day" / "x.CR3").write_bytes(b"")
    (tmp_path / "trip2" / "day" / "x.CR3").write_bytes(b"")
    out = tmp_path / "out"

    result = runner.invoke(app, [
        "extract", str(tmp_path / "trip1"), str(tmp_path / "trip2"),
        "-R", "-o", str(out),
    ])
    assert result.exit_code == 1
    assert "collision" in result.stderr
    assert not fake_extract


def test_extract_skip_existing_is_not_a_collision(tmp_path, fake_extract) -> None:
    """Pre-existing files on disk from a previous run are NOT collisions —
    they get the per-file skip-or-overwrite treatment as before. Only
    THIS run's RAWs vying for the same output path counts."""
    (tmp_path / "a.CR3").write_bytes(b"")
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.jpg").write_bytes(b"leftover from a previous run")

    result = runner.invoke(app, ["extract", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert "skip" in result.stderr
    assert "collision" not in result.stderr


def test_extract_case_insensitive_collision_detected(tmp_path, fake_extract) -> None:
    """On macOS APFS (default case-insensitive), foo.jpg and Foo.jpg are
    the same file → second one overwrites first. Detect that pre-write,
    even though Python's str compare sees the paths as different."""
    (tmp_path / "a" / "foo.CR3").parent.mkdir()
    (tmp_path / "b" / "Foo.CR3").parent.mkdir()
    (tmp_path / "a" / "foo.CR3").write_bytes(b"")
    (tmp_path / "b" / "Foo.CR3").write_bytes(b"")
    out = tmp_path / "out"

    result = runner.invoke(app, [
        "extract", str(tmp_path / "a" / "foo.CR3"), str(tmp_path / "b" / "Foo.CR3"),
        "-o", str(out),
    ])
    assert result.exit_code == 1
    assert "collision" in result.stderr
    # Both case variants must be visible somewhere in the report so the
    # user knows it was a case issue, not a name issue.
    assert "foo" in result.stderr.lower()
    assert "case variants" in result.stderr.lower()
    assert not fake_extract
