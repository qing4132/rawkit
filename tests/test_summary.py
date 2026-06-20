"""Tests for `rawkit summary` — scalar KV summary and --by bucket breakdown."""

from __future__ import annotations

import json
import os
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
    # No Path row — its presence falsely implied "this dir contains
    # exactly these RAWs", which is wrong under --where or a pipe.
    assert not out.startswith("Path")
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


def test_summary_dir_json_includes_paths(tmp_path, fake_exif) -> None:
    """JSON top-level `paths` lists every absolute path summary covered —
    the honest answer to 'what did you include?'. Machine consumers can
    derive any scope/parent/coverage they want from this list."""
    (tmp_path / "a.ARW").write_bytes(b"x")
    (tmp_path / "b.CR3").write_bytes(b"x")

    result = runner.invoke(app, ["summary", str(tmp_path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "path" not in payload  # old singular key is gone
    assert isinstance(payload["paths"], list)
    assert len(payload["paths"]) == 2
    for p in payload["paths"]:
        assert os.path.isabs(p)
        assert p.endswith((".ARW", ".CR3"))
    assert payload["total"]["count"] == 2


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


# --- no Path row -----------------------------------------------------------

def test_summary_has_no_path_row_under_any_input(tmp_path, fake_exif) -> None:
    """The Path row used to falsely imply 'this dir contains exactly these
    RAWs'. After --where / pipe filtering that's almost never true, so the
    row was removed. It must not come back under any input style."""
    a = tmp_path / "a.ARW"
    b = tmp_path / "b.CR3"
    a.write_bytes(b"x")
    b.write_bytes(b"x")

    for cmd, kwargs in [
        (["summary", str(tmp_path)], {}),
        (["summary", str(a)], {}),
        (["summary", str(a), str(b)], {}),
        (["summary", "-"], {"input": f"{a}\n{b}\n"}),
    ]:
        result = runner.invoke(app, cmd, **kwargs)
        assert result.exit_code == 0, cmd
        for line in result.stdout.splitlines():
            assert not line.startswith("Path"), f"unexpected Path row from {cmd}"


# --- JSON key naming (user-facing, matches --by vocabulary) -----------------

def test_summary_json_keys_match_by_vocabulary(tmp_path, fake_exif) -> None:
    """summary --json must use the same dim names the user types after --by,
    not the internal aggregate keys (no _bucket suffix, no fnumber/model)."""
    (tmp_path / "a.ARW").write_bytes(b"x")
    (tmp_path / "b.CR3").write_bytes(b"x")

    result = runner.invoke(app, ["summary", str(tmp_path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)

    # Each --by dim the user can type maps 1:1 to a JSON `by_<dim>` key.
    for dim in ("camera", "lens", "maker", "orientation", "iso", "aperture",
                "focal", "shutter", "bias", "rating", "hour", "month",
                "year", "day"):
        assert f"by_{dim}" in payload, f"missing by_{dim}"

    # Internal names that leaked before must be gone.
    for old in ("by_model", "by_fnumber_bucket", "by_iso_bucket",
                "by_focal_bucket", "by_shutter_bucket", "by_bias_bucket",
                "by_rating_bucket", "by_hour_bucket", "by_month_bucket",
                "by_year_bucket", "by_day_bucket"):
        assert old not in payload, f"leaked internal key {old}"

    # total{} also gets the user-facing aliases.
    total = payload["total"]
    assert "n_cameras" in total
    assert "aperture_min" in total and "aperture_max" in total
    assert "n_models" not in total
    assert "fnumber_min" not in total and "fnumber_max" not in total
