"""Tests for rawkit.exif (the exiftool wrapper)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rawkit import exif


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
        "date": "2024-01-02 03:04:05",  # colons normalized to dashes
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
