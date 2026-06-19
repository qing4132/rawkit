"""Tests for `rawkit render` + the libraw-based demosaic path.

Real rawpy + Pillow are exercised by hand against samples/. These tests
pin the CLI behaviour (formats, quality, max-side, skip/overwrite, exit
codes) using mocked extractors so they're fast and deterministic.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rawkit.cli import app
from rawkit.render import (
    RenderError,
    RenderResult,
    render,
    suffix_for,
)

runner = CliRunner()


# --- core render() behaviour ------------------------------------------------

def _patch_rawpy(monkeypatch, rgb_returner=None, raises=None) -> None:
    """Install a fake rawpy.imread returning `rgb_returner()` (an ndarray)."""
    fake_module = types.SimpleNamespace()

    class _FakeRaw:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def postprocess(self):
            if raises is not None:
                raise raises
            return rgb_returner()

    fake_module.imread = lambda _p: _FakeRaw()
    monkeypatch.setitem(sys.modules, "rawpy", fake_module)


def _rgb_array(w: int, h: int):
    """A solid 50%-grey RGB array of the requested size."""
    import numpy as np
    return np.full((h, w, 3), 128, dtype="uint8")


def test_render_jpeg_default(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, lambda: _rgb_array(640, 480))
    r = render(Path("fake.raw"))
    assert r.format == "jpeg"
    assert (r.width, r.height) == (640, 480)
    # JPEG magic header
    assert r.data[:3] == b"\xff\xd8\xff"


def test_render_tiff_is_lossless(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, lambda: _rgb_array(100, 80))
    r = render(Path("fake.raw"), output_format="tiff")
    assert r.format == "tiff"
    # TIFF magic: II*\0 (little-endian) or MM\0* (big-endian)
    assert r.data[:4] in (b"II*\x00", b"MM\x00*")


def test_render_png_works(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, lambda: _rgb_array(50, 40))
    r = render(Path("fake.raw"), output_format="png")
    assert r.format == "png"
    assert r.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_unknown_format_raises(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, lambda: _rgb_array(10, 10))
    with pytest.raises(RenderError, match="unknown format"):
        render(Path("fake.raw"), output_format="webp")


def test_render_libraw_failure_propagates(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, raises=OSError("Unsupported file format"))
    with pytest.raises(RenderError, match="libraw failed"):
        render(Path("broken.raw"))


def test_render_max_side_downscales_long_edge(monkeypatch) -> None:
    # Landscape 4000x3000 → max_side=1000 → 1000x750 (long edge wins)
    _patch_rawpy(monkeypatch, lambda: _rgb_array(4000, 3000))
    r = render(Path("fake.raw"), long_edge=1000)
    assert (r.width, r.height) == (1000, 750)


def test_render_max_side_skipped_when_already_smaller(monkeypatch) -> None:
    # 800x600 with max_side=2000 → no resize
    _patch_rawpy(monkeypatch, lambda: _rgb_array(800, 600))
    r = render(Path("fake.raw"), long_edge=2000)
    assert (r.width, r.height) == (800, 600)


def test_render_handles_monochrome_raw(monkeypatch) -> None:
    """Leica M Monochrom / Phase One Achromatic give libraw a single-channel
    array (H, W, 1) instead of the usual (H, W, 3). Pillow's `fromarray`
    rejects the trailing 1-axis with a cryptic 'Cannot handle this data type'
    — we squeeze it down to 2D so Pillow reads it as 'L' grayscale."""
    import numpy as np
    mono = np.full((600, 800, 1), 100, dtype="uint8")
    _patch_rawpy(monkeypatch, lambda: mono)

    r = render(Path("leica_m11m.dng"))
    assert (r.width, r.height) == (800, 600)
    assert r.data[:3] == b"\xff\xd8\xff"


def test_render_portrait_max_side(monkeypatch) -> None:
    # Portrait 3000x4000 → max_side=1000 → 750x1000
    _patch_rawpy(monkeypatch, lambda: _rgb_array(3000, 4000))
    r = render(Path("fake.raw"), long_edge=1000)
    assert (r.width, r.height) == (750, 1000)


def test_render_short_edge_downscales(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, lambda: _rgb_array(3000, 4000))
    r = render(Path("fake.raw"), short_edge=1080)
    assert (r.width, r.height) == (1080, 1440)


def test_render_megapixels_downscales(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, lambda: _rgb_array(4000, 3000))
    r = render(Path("fake.raw"), megapixels=3.0)
    assert (r.width, r.height) == (2000, 1500)


def test_render_rejects_multiple_resize_dimensions(monkeypatch) -> None:
    _patch_rawpy(monkeypatch, lambda: _rgb_array(1000, 800))
    with pytest.raises(RenderError, match="at most one"):
        render(Path("fake.raw"), long_edge=500, short_edge=400)


def test_render_quality_affects_jpeg_size(monkeypatch) -> None:
    """Higher quality → bigger file. Sanity check that --quality actually
    reaches Pillow. We use a low-frequency gradient image (real-photo-like
    entropy) rather than pure random noise — libjpeg's optimize/progressive
    pass can choke on maximum-entropy data in pathological ways, but
    actual photographs (and reasonable test fixtures) are fine."""
    import numpy as np

    h, w = 400, 600
    # Smooth horizontal+vertical gradient — JPEG compresses this well and
    # quality differences are clearly visible in the output size.
    xs = np.linspace(0, 255, w, dtype="uint8")
    ys = np.linspace(0, 255, h, dtype="uint8")
    rgb = np.stack(np.broadcast_arrays(xs, ys[:, None]), axis=-1)
    rgb = np.concatenate([rgb, rgb[..., :1]], axis=-1)  # → (h, w, 3)
    _patch_rawpy(monkeypatch, lambda: rgb.astype("uint8"))

    r_low = render(Path("a.raw"), output_format="jpeg", quality=10)
    r_high = render(Path("a.raw"), output_format="jpeg", quality=95)
    assert len(r_high.data) > len(r_low.data)


def test_suffix_for() -> None:
    assert suffix_for("jpeg") == ".jpg"
    assert suffix_for("tiff") == ".tiff"
    assert suffix_for("png") == ".png"


# --- CLI surface ------------------------------------------------------------

@pytest.fixture
def fake_render(monkeypatch):
    """Stub render() so CLI tests don't touch real RAW files."""
    calls: list[dict] = []

    def fake(path, *, output_format="jpeg", quality=90, long_edge=None, short_edge=None, megapixels=None):
        calls.append({
            "path": path,
            "format": output_format,
            "quality": quality,
            "long_edge": long_edge,
            "short_edge": short_edge,
            "megapixels": megapixels,
        })
        return RenderResult(b"\xff\xd8FAKE", 4000, 3000, output_format)

    monkeypatch.setattr("rawkit.cli.render_raw", fake)
    return calls


def test_cli_render_default_jpeg(tmp_path, fake_render) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, ["render", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert (out / "a.jpg").read_bytes().startswith(b"\xff\xd8")
    assert fake_render[0]["format"] == "jpeg"
    assert fake_render[0]["quality"] == 90
    assert fake_render[0]["long_edge"] is None
    assert fake_render[0]["short_edge"] is None
    assert fake_render[0]["megapixels"] is None
    assert "4000x3000 jpeg" in result.stderr


def test_cli_render_tiff_extension(tmp_path, fake_render) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(
        app, ["render", str(tmp_path), "-o", str(out), "--format", "tiff"]
    )
    assert result.exit_code == 0
    assert (out / "a.tiff").exists()
    assert fake_render[0]["format"] == "tiff"


def test_cli_render_passes_quality_and_long_edge(tmp_path, fake_render) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, [
        "render", str(tmp_path), "-o", str(out),
        "-q", "75", "--long", "1024",
    ])
    assert result.exit_code == 0
    assert fake_render[0]["quality"] == 75
    assert fake_render[0]["long_edge"] == 1024


def test_cli_render_default_no_resize(tmp_path, fake_render) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, ["render", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert fake_render[0]["long_edge"] is None
    assert fake_render[0]["short_edge"] is None
    assert fake_render[0]["megapixels"] is None


def test_cli_render_short_flag(tmp_path, fake_render) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, ["render", str(tmp_path), "-o", str(out), "--short", "1080"])
    assert result.exit_code == 0
    assert fake_render[0]["short_edge"] == 1080


def test_cli_render_mp_flag(tmp_path, fake_render) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, ["render", str(tmp_path), "-o", str(out), "--mp", "6"])
    assert result.exit_code == 0
    assert fake_render[0]["megapixels"] == 6.0


def test_cli_render_rejects_multiple_resize_flags(tmp_path, fake_render) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, [
        "render", str(tmp_path), "-o", str(out),
        "--long", "2000", "--short", "1080",
    ])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.stderr
    assert not fake_render


def test_cli_render_skips_existing(tmp_path, fake_render) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.jpg").write_bytes(b"EXISTING")

    result = runner.invoke(app, ["render", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert (out / "a.jpg").read_bytes() == b"EXISTING"
    assert "skip" in result.stderr
    assert not fake_render  # never called


def test_cli_render_overwrites_with_f(tmp_path, fake_render) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.jpg").write_bytes(b"EXISTING")

    result = runner.invoke(app, ["render", str(tmp_path), "-o", str(out), "-f"])
    assert result.exit_code == 0
    assert (out / "a.jpg").read_bytes().startswith(b"\xff\xd8")


def test_cli_render_failure_reports_and_exits_nonzero(tmp_path, monkeypatch) -> None:
    (tmp_path / "broken.ARW").write_bytes(b"")

    def fail(path, **_):
        raise RenderError("libraw failed: bogus")

    monkeypatch.setattr("rawkit.cli.render_raw", fail)

    result = runner.invoke(app, ["render", str(tmp_path), "-o", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "failed" in result.stderr
    assert "libraw failed" in result.stderr


def test_cli_render_empty_directory(tmp_path, fake_render) -> None:
    out = tmp_path / "out"
    result = runner.invoke(app, ["render", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert not out.exists()


# --- --where filter ---------------------------------------------------------

@pytest.fixture
def fake_exif_for_where(monkeypatch):
    def fake(paths):
        return [
            {
                "path": str(p),
                "iso": 100 if "low" in Path(p).name else 6400,
                "model": "EOS R5",
                "maker": "Canon",
            }
            for p in paths
        ]

    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)
    return fake


def test_render_where_filters_to_matching(tmp_path, fake_render, fake_exif_for_where) -> None:
    (tmp_path / "low.ARW").write_bytes(b"")
    (tmp_path / "high.ARW").write_bytes(b"")
    out = tmp_path / "out"

    result = runner.invoke(app, [
        "render", str(tmp_path), "-o", str(out),
        "--where", "iso>3200",
    ])
    assert result.exit_code == 0
    rendered = {Path(c["path"]).name for c in fake_render}
    assert rendered == {"high.ARW"}
    assert (out / "high.jpg").exists()
    assert not (out / "low.jpg").exists()


def test_render_where_bad_syntax_exits_2(tmp_path, fake_render) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"
    result = runner.invoke(app, [
        "render", str(tmp_path), "-o", str(out),
        "--where", "iso === bogus",
    ])
    assert result.exit_code == 2
    assert "--where" in result.stderr
    assert not fake_render


def test_render_without_where_skips_exiftool(tmp_path, fake_render, monkeypatch) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    out = tmp_path / "out"

    def explode(_paths):
        raise AssertionError("safe_batch_read called without --where")

    monkeypatch.setattr("rawkit.cli.safe_batch_read", explode)

    result = runner.invoke(app, ["render", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert len(fake_render) == 1


def test_render_recursive_preserves_subtree_under_output(tmp_path, fake_render) -> None:
    src = tmp_path / "src"
    (src / "a" / "b").mkdir(parents=True)
    (src / "a" / "b" / "x.ARW").write_bytes(b"")
    (src / "a" / "b" / "y.CR3").write_bytes(b"")
    out = tmp_path / "out"

    result = runner.invoke(app, [
        "render", str(src), "-R", "-o", str(out),
    ])

    assert result.exit_code == 0
    assert (out / "a" / "b" / "x.jpg").exists()
    assert (out / "a" / "b" / "y.jpg").exists()


def test_render_intra_run_collision_detected(tmp_path, fake_render) -> None:
    a = tmp_path / "A"
    b = tmp_path / "B"
    a.mkdir()
    b.mkdir()
    (a / "same.ARW").write_bytes(b"")
    (b / "same.CR3").write_bytes(b"")
    out = tmp_path / "out"

    result = runner.invoke(app, [
        "render", str(a / "same.ARW"), str(b / "same.CR3"),
        "-o", str(out),
    ])

    assert result.exit_code == 1
    assert "output collision" in result.stderr
    assert not fake_render


def test_render_case_insensitive_collision_detected(tmp_path, fake_render) -> None:
    a = tmp_path / "a.ARW"
    b = tmp_path / "A.CR3"
    a.write_bytes(b"")
    b.write_bytes(b"")
    out = tmp_path / "out"

    result = runner.invoke(app, ["render", str(a), str(b), "-o", str(out)])

    assert result.exit_code == 1
    assert "output collision" in result.stderr
    assert "case variants" in result.stderr
    assert not fake_render
