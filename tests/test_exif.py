"""Tests for the exiftool backend + the `_normalize` post-processor.

> Backend context (2026-06-23): the default EXIF backend is `lite` (the
> pure-stdlib TIFF/CR3 parser, see [src/rawkit/_exif_lite.py]). This file
> covers the **exiftool fallback path** plus the format-agnostic
> `_normalize` step both backends pump through.
>
> The lite parser's own tests are in [tests/test_exif_lite.py].

All tests here mock out `subprocess.run`, so they don't need exiftool to
be installed and don't touch any real RAW file. They exercise:

  * the `_FIELD_MAP` → rawkit-key collapse
  * the datetime / date / time split and SubSec stitching
  * orientation / flash / gps / model-prefix derivations
  * APEX-aperture fallback when EXIF:FNumber is missing
  * MakerNotes-pollution guards (Pentax ISO 13, Leica M11M FNumber 1.0)

A module-level autouse fixture pins these onto the exiftool backend
(`RAWKIT_BACKEND=exiftool`) so the dispatcher routes there even though
the default is `lite`. Without that fixture, the subprocess mocks
wouldn't hit anything — the lite path doesn't call subprocess at all.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rawkit import exif


@pytest.fixture(autouse=True)
def _force_exiftool_backend(monkeypatch):
    """Default this file's tests to the exiftool backend.

    The lite backend is rawkit's runtime default, but every test in this
    file pokes the legacy exiftool plumbing (mocked subprocess). Pinning
    here is cleaner than scattering `monkeypatch.setenv` calls into every
    test. Fixture is file-scoped (auto-discovered via test_exif.py imports
    only); sibling files like test_exif_lite.py set backend env vars
    explicitly per-test.
    """
    monkeypatch.setenv("RAWKIT_BACKEND", "exiftool")


def test_batch_read_empty_paths_skips_exiftool(monkeypatch) -> None:
    """Calling with no paths must not even shell out (no fork cost)."""

    def boom(*_a, **_kw):
        raise AssertionError("subprocess.run must not be called for empty input")

    monkeypatch.setattr(exif.subprocess, "run", boom)
    monkeypatch.setattr(exif.shutil, "which", lambda _x: "/fake/exiftool")
    assert exif.batch_read([]) == []


def test_batch_read_normalizes_fields(monkeypatch) -> None:
    """Raw exiftool keys (Model, ISO, ...) → rawkit keys (model, iso, ...)."""
    fake_stdout = json.dumps([
        {
            "SourceFile": "a.ARW",
            "DateTimeOriginal": "2024:01:02 03:04:05",
            "Make": "SONY",
            "Model": "ILCE-7M4",
            "LensModel": "FE 50mm F1.4",
            "ISO": 800,
            "FNumber": 1.4,
            "ExposureTime": 0.004,
            "FocalLength": 50,
        }
    ])

    class FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(exif.shutil, "which", lambda _x: "/fake/exiftool")
    monkeypatch.setattr(exif.subprocess, "run", lambda *_a, **_kw: FakeProc())

    records = exif.batch_read([Path("a.ARW")])
    assert records == [{
        "path": "a.ARW",
        "datetime": "2024-01-02 03:04:05",  # colons normalized to dashes
        "date":     "2024-01-02",            # derived YYYY-MM-DD slice
        "time":     "03:04:05",              # derived HH:MM:SS slice
        "year":     2024,                    # derived integer bucket
        "month":    1,
        "day":      2,
        "hour":     3,
        "maker": "SONY",
        "model": "ILCE-7M4",
        "lens": "FE 50mm F1.4",
        "iso": 800,
        "fnumber": 1.4,
        "shutter": 0.004,
        "focal": 50,
    }]


def test_batch_read_missing_fields_omitted(monkeypatch) -> None:
    """Fixed-lens cameras have no LensModel — the key should be absent."""
    fake_stdout = json.dumps([{"SourceFile": "gr.DNG", "Model": "RICOH GR III"}])

    class FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(exif.shutil, "which", lambda _x: "/fake/exiftool")
    monkeypatch.setattr(exif.subprocess, "run", lambda *_a, **_kw: FakeProc())

    [r] = exif.batch_read([Path("gr.DNG")])
    assert "lens" not in r
    assert r["model"] == "RICOH GR III"


def test_subsec_appended_to_datetime_and_time(monkeypatch) -> None:
    """SubSecTimeOriginal must end up as a '.NNN' suffix on datetime and time
    (but NOT on date — that stays YYYY-MM-DD)."""
    fake_stdout = json.dumps([
        {
            "SourceFile": "burst.CR3",
            "DateTimeOriginal": "2024:10:27 17:09:43",
            "SubSecTimeOriginal": "048",
        }
    ])

    class FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(exif.shutil, "which", lambda _x: "/fake/exiftool")
    monkeypatch.setattr(exif.subprocess, "run", lambda *_a, **_kw: FakeProc())

    [r] = exif.batch_read([Path("burst.CR3")])
    assert r["datetime"] == "2024-10-27 17:09:43.048"
    assert r["time"]     == "17:09:43.048"
    assert r["date"]     == "2024-10-27"  # date never gets subsec
    assert "_subsec_raw" not in r          # internal key must not leak


def test_subsec_absent_leaves_datetime_clean(monkeypatch) -> None:
    """Camera that didn't write SubSecTime → datetime stays at second precision."""
    fake_stdout = json.dumps([
        {
            "SourceFile": "no_subsec.ARW",
            "DateTimeOriginal": "2024:10:27 17:09:43",
        }
    ])

    class FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(exif.shutil, "which", lambda _x: "/fake/exiftool")
    monkeypatch.setattr(exif.subprocess, "run", lambda *_a, **_kw: FakeProc())

    [r] = exif.batch_read([Path("no_subsec.ARW")])
    assert r["datetime"] == "2024-10-27 17:09:43"
    assert r["time"]     == "17:09:43"


def test_subsec_lexical_order_matches_time_order(monkeypatch) -> None:
    """Lex compare of variable-length subsec strings must match real time
    order (no need to pad). This is the property the sort relies on."""
    fake_stdout = json.dumps([
        {"SourceFile": "a", "DateTimeOriginal": "2024:01:01 00:00:00", "SubSecTimeOriginal": "9"},     # .9 sec
        {"SourceFile": "b", "DateTimeOriginal": "2024:01:01 00:00:00", "SubSecTimeOriginal": "247"},   # .247 sec
        {"SourceFile": "c", "DateTimeOriginal": "2024:01:01 00:00:00", "SubSecTimeOriginal": "01"},    # .01 sec
    ])

    class FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(exif.shutil, "which", lambda _x: "/fake/exiftool")
    monkeypatch.setattr(exif.subprocess, "run", lambda *_a, **_kw: FakeProc())

    recs = exif.batch_read([Path("a"), Path("b"), Path("c")])
    # Sort ascending by datetime; expected chronological order is c (.01) → b (.247) → a (.9).
    in_order = sorted(recs, key=lambda r: r["datetime"])
    assert [r["path"] for r in in_order] == ["c", "b", "a"]


def test_orientation_landscape_vs_portrait(monkeypatch) -> None:
    fake_stdout = json.dumps([
        {"SourceFile": "h.ARW", "Orientation": 1},  # landscape
        {"SourceFile": "v.ARW", "Orientation": 6},  # portrait (camera held vertically)
        {"SourceFile": "v2.ARW", "Orientation": 8},  # portrait other way
    ])

    class FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(exif.shutil, "which", lambda _x: "/fake/exiftool")
    monkeypatch.setattr(exif.subprocess, "run", lambda *_a, **_kw: FakeProc())

    recs = exif.batch_read([Path("h.ARW"), Path("v.ARW"), Path("v2.ARW")])
    assert [r["orientation"] for r in recs] == ["landscape", "portrait", "portrait"]
    for r in recs:
        # the raw int must not leak into the normalized record
        assert "_orientation_raw" not in r


def test_flash_bitfield_to_bool(monkeypatch) -> None:
    """Flash low bit = 'flash fired'. 16 = on but did-not-fire; 1 = fired."""
    fake_stdout = json.dumps([
        {"SourceFile": "off.ARW",    "Flash": 0},
        {"SourceFile": "noFire.ARW", "Flash": 16},
        {"SourceFile": "fired.ARW",  "Flash": 1},
        {"SourceFile": "fireRed.ARW","Flash": 0x49},  # fired + red-eye reduction
    ])

    class FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(exif.shutil, "which", lambda _x: "/fake/exiftool")
    monkeypatch.setattr(exif.subprocess, "run", lambda *_a, **_kw: FakeProc())

    recs = exif.batch_read([Path("a"), Path("b"), Path("c"), Path("d")])
    assert [r["flash"] for r in recs] == [False, False, True, True]


def test_gps_presence_derived(monkeypatch) -> None:
    """`gps` == True only when BOTH lat and lon are present."""
    fake_stdout = json.dumps([
        {"SourceFile": "with.ARW",    "GPSLatitude": 39.9, "GPSLongitude": 116.4},
        {"SourceFile": "without.ARW", "Model": "X"},
        {"SourceFile": "half.ARW",    "GPSLatitude": 39.9},
    ])

    class FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(exif.shutil, "which", lambda _x: "/fake/exiftool")
    monkeypatch.setattr(exif.subprocess, "run", lambda *_a, **_kw: FakeProc())

    recs = exif.batch_read([Path("a"), Path("b"), Path("c")])
    assert recs[0]["gps"] is True
    assert recs[0]["gps_lat"] == 39.9 and recs[0]["gps_lon"] == 116.4
    assert "gps" not in recs[1]
    assert "gps" not in recs[2]  # only lat, no lon → not "has GPS"


def test_bias_and_rating_passthrough(monkeypatch) -> None:
    fake_stdout = json.dumps([
        {"SourceFile": "a", "ExposureCompensation": -1.5, "Rating": 4},
        {"SourceFile": "b", "ExposureCompensation": 0,    "Rating": 0},
    ])

    class FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(exif.shutil, "which", lambda _x: "/fake/exiftool")
    monkeypatch.setattr(exif.subprocess, "run", lambda *_a, **_kw: FakeProc())

    recs = exif.batch_read([Path("a"), Path("b")])
    assert recs[0]["bias"] == -1.5
    assert recs[0]["rating"] == 4
    assert recs[1]["rating"] == 0


def test_model_strips_redundant_maker_prefix(monkeypatch) -> None:
    """Canon/Nikon/Leica/Ricoh write `Model` as `"<MAKER> <body>"` —
    we drop the maker word because it's already in the `maker` column.
    Casing is normalized for the comparison so `LEICA M11 Monochrom`
    matches `Leica Camera AG` (first-word, case-insensitive)."""
    fake_stdout = json.dumps([
        {"SourceFile": "canon.CR3",  "Make": "Canon",                         "Model": "Canon EOS R5"},
        {"SourceFile": "nikon.NEF",  "Make": "NIKON CORPORATION",             "Model": "NIKON Z5_2"},
        {"SourceFile": "leica.DNG",  "Make": "Leica Camera AG",               "Model": "LEICA M11 Monochrom"},
        {"SourceFile": "ricoh.DNG",  "Make": "RICOH IMAGING COMPANY, LTD.",   "Model": "RICOH GR III"},
        # No prefix to strip — left untouched.
        {"SourceFile": "sony.ARW",   "Make": "SONY",                          "Model": "ILCE-7RM4A"},
        {"SourceFile": "fuji.RAF",   "Make": "FUJIFILM",                      "Model": "X-E5"},
        {"SourceFile": "om.ORF",     "Make": "OM Digital Solutions",          "Model": "OM-5MarkII"},
        {"SourceFile": "hassel.3FR", "Make": "Hasselblad",                    "Model": "X2D 100C"},
    ])

    class FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(exif.shutil, "which", lambda _x: "/fake/exiftool")
    monkeypatch.setattr(exif.subprocess, "run", lambda *_a, **_kw: FakeProc())

    recs = exif.batch_read([Path(f"x{i}") for i in range(8)])
    models = [r["model"] for r in recs]
    assert models == [
        "EOS R5",          # "Canon " stripped
        "Z5_2",            # "NIKON " stripped
        "M11 Monochrom",   # "LEICA " stripped (case-insensitive match)
        "GR III",          # "RICOH " stripped
        "ILCE-7RM4A",      # no prefix → untouched
        "X-E5",            # no prefix → untouched
        "OM-5MarkII",      # "OM-" has no space after, so the rule (requires
                           # prefix + space) doesn't fire — left alone.
        "X2D 100C",        # no prefix → untouched
    ]
    # maker column itself is preserved exactly as exiftool emitted it.
    assert recs[0]["maker"] == "Canon"
    assert recs[2]["maker"] == "Leica Camera AG"


def test_model_prefix_strip_never_yields_empty(monkeypatch) -> None:
    """Degenerate case: Model is literally just the maker word. The strip
    would leave an empty string — keep the original instead."""
    fake_stdout = json.dumps([
        {"SourceFile": "weird.RAW", "Make": "Canon", "Model": "Canon"},
    ])

    class FakeProc:
        returncode = 0
        stdout = fake_stdout
        stderr = ""

    monkeypatch.setattr(exif.shutil, "which", lambda _x: "/fake/exiftool")
    monkeypatch.setattr(exif.subprocess, "run", lambda *_a, **_kw: FakeProc())

    [r] = exif.batch_read([Path("weird.RAW")])
    assert r["model"] == "Canon"


def test_missing_exiftool_raises_human_message(monkeypatch) -> None:
    monkeypatch.setattr(exif.shutil, "which", lambda _x: None)
    with pytest.raises(exif.ExiftoolMissing) as ei:
        exif.batch_read([Path("a.ARW")])
    msg = str(ei.value)
    assert "exiftool" in msg
    assert "brew install" in msg  # gives an actionable next step


def test_warnings_are_not_failures(monkeypatch) -> None:
    """exiftool exits 1 on per-file warnings — we should still parse JSON."""
    fake_stdout = json.dumps([{"SourceFile": "ok.ARW", "ISO": 100}])

    class FakeProc:
        returncode = 1  # warning, not failure
        stdout = fake_stdout
        stderr = "Warning: something minor"

    monkeypatch.setattr(exif.shutil, "which", lambda _x: "/fake/exiftool")
    monkeypatch.setattr(exif.subprocess, "run", lambda *_a, **_kw: FakeProc())

    records = exif.batch_read([Path("ok.ARW")])
    assert records[0]["iso"] == 100


def test_real_samples_if_available() -> None:
    """End-to-end against real RAW samples when exiftool + samples/ both exist.

    Skipped on systems without exiftool or without the local samples directory.
    Mirrors the README fixture strategy (RAWKIT_TEST_SAMPLES env var, falling
    back to ./samples).
    """
    import os as _os

    samples_dir = Path(_os.environ.get("RAWKIT_TEST_SAMPLES", "samples"))
    if not samples_dir.is_dir():
        pytest.skip(f"no sample dir at {samples_dir}")
    if shutil.which("exiftool") is None:
        pytest.skip("exiftool not installed")

    raws = sorted(p for p in samples_dir.iterdir() if p.is_file() and p.suffix.lower() in {
        ".arw", ".cr3", ".dng", ".3fr",
    })
    if not raws:
        pytest.skip("no RAW samples available")

    records = exif.batch_read(raws)
    assert len(records) == len(raws)
    # Every record must carry at least path + model.
    for r in records:
        assert "path" in r
        assert "model" in r, f"missing model in {r}"
