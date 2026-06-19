"""Tests for the `rawkit info` command (single-file and directory modes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rawkit.cli import app

runner = CliRunner()


@pytest.fixture
def fake_exif(monkeypatch):
    def fake(paths):
        return [
            {
                "path": str(p),
                "datetime": "2024-01-02 03:04:05",
                "date": "2024-01-02",
                "time": "03:04:05",
                "maker": "Canon",
                "model": "EOS R5",
                "lens": "RF50mm F1.8 STM",
                "iso": 800,
                "fnumber": 1.8,
                "shutter": 0.004,
                "focal": 50.0,
                "bias": -1.0,
                "orientation": "landscape",
                "flash": False,
                "image_width": 8192,
                "image_height": 5464,
                "preview_width": 1616,
                "preview_height": 1080,
                "gps": True,
                "gps_lat": 31.2,
                "gps_lon": 121.5,
            }
            for p in paths
        ]

    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)

    from rawkit.extract import ExtractResult

    def fake_extract(_path, **_kwargs):
        return ExtractResult(b"x" * 123456, 1616, 1080)

    monkeypatch.setattr("rawkit.cli.extract_jpeg", fake_extract)
    return fake


def test_info_file_human_kv_output(tmp_path, fake_exif) -> None:
    raw = tmp_path / "a.ARW"
    raw.write_bytes(b"x" * 2048)

    result = runner.invoke(app, ["info", str(raw)])

    assert result.exit_code == 0
    out = result.stdout
    assert "Path" in out
    assert "Size" in out
    assert "DateTime" in out
    assert "Canon" in out
    assert "EOS R5" in out
    assert "f/1.8" in out
    assert "1/250" in out
    assert "Image" in out
    assert "Preview" in out
    assert "GPS" in out
    assert "31.200000, 121.500000" in out
    assert "Embedded" in out
    assert "JPEG 1616x1080" in out


def test_info_file_json_output(tmp_path, fake_exif) -> None:
    raw = tmp_path / "a.ARW"
    raw.write_bytes(b"x" * 1024)

    result = runner.invoke(app, ["info", str(raw), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path"].endswith("a.ARW")
    assert payload["size_bytes"] == 1024
    assert payload["maker"] == "Canon"
    assert payload["image_width"] == 8192
    assert payload["preview"] == "1616x1080"
    assert payload["gps_text"] == "31.200000, 121.500000"
    assert payload["embedded_jpegs"] == ["JPEG 1616x1080 (120.6 KiB)"]


def test_info_file_rejects_by(tmp_path, fake_exif) -> None:
    raw = tmp_path / "a.ARW"
    raw.write_bytes(b"x")

    result = runner.invoke(app, ["info", str(raw), "--by", "month"])

    assert result.exit_code == 2
    assert "--by is only valid" in result.stderr


def test_info_directory_mode_matches_stats_shape(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    (tmp_path / "b.CR3").write_bytes(b"x")

    result = runner.invoke(app, ["info", str(tmp_path)])

    assert result.exit_code == 0
    out = result.stdout
    assert "Photos" in out
    assert "Date range" in out
    assert "Cameras" in out


def test_info_directory_by_month_works(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    result = runner.invoke(app, ["info", str(tmp_path), "--by", "month"])
    assert result.exit_code == 0
    assert "By month" in result.stdout
