"""Tests for `rawkit info` — always per-file detail, accepts pipe input."""

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


# --- single file ----------------------------------------------------------

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
    assert "GPS" in out
    assert "31.200000, 121.500000" in out
    assert "Embedded" in out
    assert "JPEG 1616x1080" in out


def test_info_file_json_output(tmp_path, fake_exif) -> None:
    raw = tmp_path / "a.ARW"
    raw.write_bytes(b"x" * 1024)

    result = runner.invoke(app, ["info", str(raw), "--json"])

    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["path"].endswith("a.ARW")
    assert payload["size_bytes"] == 1024
    assert payload["maker"] == "Canon"
    assert payload["image_width"] == 8192
    assert "preview" not in payload
    assert payload["gps_text"] == "31.200000, 121.500000"
    assert payload["embedded_jpegs"] == ["JPEG 1616x1080 (120.6 KiB)"]


def test_info_rejects_by_flag(tmp_path, fake_exif) -> None:
    """--by belongs to `summary`, not `info`. typer should reject it."""
    raw = tmp_path / "a.ARW"
    raw.write_bytes(b"x")
    result = runner.invoke(app, ["info", str(raw), "--by", "month"])
    assert result.exit_code == 2


# --- multi-file / dir / pipe ------------------------------------------------

def test_info_multiple_files_emits_one_block_each(tmp_path, fake_exif) -> None:
    a = tmp_path / "a.ARW"
    b = tmp_path / "b.CR3"
    a.write_bytes(b"x")
    b.write_bytes(b"x")

    result = runner.invoke(app, ["info", str(a), str(b)])
    assert result.exit_code == 0
    path_lines = [ln for ln in result.stdout.splitlines() if ln.startswith("Path")]
    assert len(path_lines) == 2


def test_info_directory_walks_files(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    (tmp_path / "b.CR3").write_bytes(b"x")

    result = runner.invoke(app, ["info", str(tmp_path)])
    assert result.exit_code == 0
    # No "Date range" / aggregate labels — those belong to summary now.
    assert "Date range" not in result.stdout
    path_lines = [ln for ln in result.stdout.splitlines() if ln.startswith("Path")]
    assert len(path_lines) == 2


def test_info_reads_paths_from_stdin(tmp_path, fake_exif) -> None:
    a = tmp_path / "a.ARW"
    a.write_bytes(b"x")

    result = runner.invoke(app, ["info", "-"], input=f"{a}\n")
    assert result.exit_code == 0
    assert "a.ARW" in result.stdout
    assert "Canon" in result.stdout


def test_info_json_emits_jsonl_for_multi(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    (tmp_path / "b.CR3").write_bytes(b"x")

    result = runner.invoke(app, ["info", str(tmp_path), "--json"])
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2
    for ln in lines:
        obj = json.loads(ln)
        assert "path" in obj
        assert obj["maker"] == "Canon"


def test_info_where_filters_before_render(tmp_path, monkeypatch) -> None:
    def fake(paths):
        return [
            {"path": str(p), "iso": 100 if "low" in Path(p).name else 6400,
             "maker": "Canon", "model": "EOS R5", "lens": "RF50"}
            for p in paths
        ]
    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)

    (tmp_path / "low.ARW").write_bytes(b"x")
    (tmp_path / "high.ARW").write_bytes(b"x")

    result = runner.invoke(app, ["info", str(tmp_path), "-w", "iso>=3200"])
    assert result.exit_code == 0
    assert "high.ARW" in result.stdout
    assert "low.ARW" not in result.stdout
