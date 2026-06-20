"""Tests for `rawkit summary` — scalar KV summary and --by bucket breakdown."""

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
    return fake


# --- default scalar summary -------------------------------------------------

def test_summary_dir_kv_view(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    (tmp_path / "b.CR3").write_bytes(b"x")

    result = runner.invoke(app, ["summary", str(tmp_path)])
    assert result.exit_code == 0
    out = result.stdout
    assert "Path" in out
    assert "File" in out
    assert "RAW" in out
    assert "Date range" in out
    assert "Maker" in out
    assert "Camera" in out
    assert "Lens" in out
    assert "ISO" in out
    assert "Aperture" in out
    assert "Shutter" in out
    assert "Focal length" in out
    assert "By month" not in out
    assert "Distribution" not in out


def test_summary_dir_json_includes_path(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")

    result = runner.invoke(app, ["summary", str(tmp_path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["path"] == str(tmp_path)
    assert payload["total"]["count"] == 1


def test_summary_filter_row_shown_when_where(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")

    result = runner.invoke(app, ["summary", str(tmp_path), "--where", "iso>=100"])
    assert result.exit_code == 0
    assert "Filter" in result.stdout
    assert "iso>=100" in result.stdout


# --- --by bucket breakdown --------------------------------------------------

def test_summary_by_camera_renders_section(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    (tmp_path / "b.CR3").write_bytes(b"x")

    result = runner.invoke(app, ["summary", str(tmp_path), "--by", "camera"])
    assert result.exit_code == 0
    out = result.stdout
    assert "Camera" in out
    assert "EOS R5" in out
    assert "100%" in out
    # --by suppresses the default KV view.
    assert "Date range" not in out
    assert "█" not in out
    assert "──" not in out
    assert "By camera" not in out


def test_summary_by_unknown_dim_exits_2(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    result = runner.invoke(app, ["summary", str(tmp_path), "--by", "color"])
    assert result.exit_code == 2
    assert "unknown dimension" in result.stderr


def test_summary_by_multidim_not_yet_supported(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    result = runner.invoke(app, ["summary", str(tmp_path), "--by", "camera,lens"])
    assert result.exit_code == 2
    assert "multi-dim" in result.stderr


def test_summary_by_filter_caption(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    result = runner.invoke(
        app, ["summary", str(tmp_path), "--by", "camera", "--where", "iso>=50"]
    )
    assert result.exit_code == 0
    assert "filter: iso>=50" in result.stdout


# --- pipe input (the new capability) ----------------------------------------

def test_summary_reads_paths_from_stdin(tmp_path, fake_exif) -> None:
    a = tmp_path / "a.ARW"
    b = tmp_path / "b.CR3"
    a.write_bytes(b"x")
    b.write_bytes(b"x")

    result = runner.invoke(app, ["summary", "-"], input=f"{a}\n{b}\n")
    assert result.exit_code == 0
    assert "RAW" in result.stdout
    assert "Canon" in result.stdout


def test_summary_pipe_with_by(tmp_path, fake_exif) -> None:
    """The killer use case: ls | summary --by  for a curated subset."""
    a = tmp_path / "a.ARW"
    a.write_bytes(b"x")

    result = runner.invoke(app, ["summary", "-", "--by", "camera"], input=f"{a}\n")
    assert result.exit_code == 0
    assert "EOS R5" in result.stdout
    assert "100%" in result.stdout
