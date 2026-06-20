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
    # Same resolve+trailing-slash rule as the human view.
    assert payload["path"].rstrip("/") == str(tmp_path).rstrip("/")
    assert payload["path"].endswith("/")
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
    assert "EOS R5" in out
    assert "100%" in out
    # --by suppresses the default KV view.
    assert "Date range" not in out
    # Bare rows: no title, no caption, no leading indent, no chart chrome.
    assert "█" not in out
    assert "──" not in out
    assert "By camera" not in out
    assert not out.startswith("Camera")
    assert not out.startswith("  ")


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


def test_summary_by_pipe_and_local_where_match(tmp_path, fake_exif) -> None:
    """ls -w | summary --by  and  summary --by -w  must produce identical
    output. The --by view shows the data, not the provenance."""
    (tmp_path / "a.ARW").write_bytes(b"x")
    (tmp_path / "b.CR3").write_bytes(b"x")

    local = runner.invoke(
        app, ["summary", str(tmp_path), "--by", "camera", "--where", "iso>=50"]
    )
    piped = runner.invoke(
        app, ["summary", "-", "--by", "camera"],
        input="\n".join(str(p) for p in (tmp_path / "a.ARW", tmp_path / "b.CR3")) + "\n",
    )
    assert local.exit_code == 0 and piped.exit_code == 0
    assert local.stdout == piped.stdout
    # No filter caption sneaks back in.
    assert "filter:" not in local.stdout


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


# --- path row truncation ----------------------------------------------------

def test_summary_pipe_path_row_uses_common_parent(tmp_path, fake_exif) -> None:
    """Piping N file paths must not dump all basenames into the Path row;
    it should collapse to the common parent directory, with a trailing /."""
    for name in ("a.ARW", "b.CR3", "c.NEF", "d.RAF", "e.DNG"):
        (tmp_path / name).write_bytes(b"x")
    stdin = "\n".join(str(tmp_path / n) for n in
                     ("a.ARW", "b.CR3", "c.NEF", "d.RAF", "e.DNG")) + "\n"

    result = runner.invoke(app, ["summary", "-"], input=stdin)
    assert result.exit_code == 0
    path_rows = [ln for ln in result.stdout.splitlines() if ln.startswith("Path")]
    assert len(path_rows) == 1
    row = path_rows[0]
    # Single common-parent directory, trailing slash. Count is NOT here
    # (it's in the File row); no basenames leak through.
    assert row.rstrip().endswith("/")
    assert "a.ARW" not in row
    assert "b.CR3" not in row
    assert "paths" not in row
    assert "RAW" not in row.replace("RAWs", "")  # belongs to File row


def test_summary_path_row_unified_across_inputs(tmp_path, fake_exif) -> None:
    """All four input styles (1 file, 1 dir, N piped files, mixed) collapse
    to the SAME Path row when they refer to the same scope."""
    a = tmp_path / "a.ARW"
    b = tmp_path / "b.CR3"
    a.write_bytes(b"x")
    b.write_bytes(b"x")

    def path_row(result):
        rows = [ln for ln in result.stdout.splitlines() if ln.startswith("Path")]
        assert len(rows) == 1
        return rows[0]

    r_dir = runner.invoke(app, ["summary", str(tmp_path)])
    r_one_file = runner.invoke(app, ["summary", str(a)])
    r_two_files = runner.invoke(app, ["summary", str(a), str(b)])
    r_piped = runner.invoke(app, ["summary", "-"], input=f"{a}\n{b}\n")

    rows = {path_row(r_dir), path_row(r_one_file),
            path_row(r_two_files), path_row(r_piped)}
    assert len(rows) == 1, f"Path rows differ across input styles: {rows}"
