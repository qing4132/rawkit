"""Tests for the OPTIONAL `rawkit stats` command and its bar-chart renderer.

This file is the addon's test surface. ls / extract / render / info are
tested elsewhere and do NOT depend on rawkit.stats. Deleting this file
along with `src/rawkit/stats.py` and the stats block in `cli.py` leaves
the rest of the suite working untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rawkit.aggregate import build_stats
from rawkit.cli import app
from rawkit.stats import (
    render,
    render_by,
    render_default,
    supported_dimensions,
)

runner = CliRunner()


def _record(**kw) -> dict:
    base = {
        "path": kw.pop("path", "fake.RAW"),
        "datetime": "2024-06-15 12:00:00",
        "date": "2024-06-15",
        "time": "12:00:00",
        "model": "EOS R5",
        "lens": "RF50mm F1.8 STM",
        "iso": 400,
        "fnumber": 2.8,
        "shutter": 1 / 200,
        "focal": 50.0,
    }
    base.update(kw)
    return base


# --- render API ------------------------------------------------------------

def test_render_default_with_where_shows_caption() -> None:
    records = [_record(model="EOS R5", iso=400)]
    s = build_stats(records, [])
    out = render_default(s, where="iso>=400")
    assert "Filter" in out
    assert "iso>=400" in out


def test_render_by_invalid_dimension_raises() -> None:
    with pytest.raises(ValueError, match="unknown dimension"):
        render_by({}, "color")


def test_render_by_month_has_chrono_order() -> None:
    records = [
        _record(date="2024-03-15"),
        _record(date="2024-01-20"),
    ]
    s = build_stats(records, [])
    out = render_by(s, "month")
    assert out.index("2024-01") < out.index("2024-03")


def test_render_by_caption_when_where() -> None:
    records = [_record(iso=400)]
    s = build_stats(records, [])
    out = render_by(s, "iso", where="iso>=400")
    assert "filter:" in out
    assert "n=1" in out


def test_supported_dimensions_includes_expected() -> None:
    dims = supported_dimensions()
    for d in ("model", "lens", "maker", "orientation",
              "iso", "fnumber", "aperture", "focal",
              "hour", "year", "month", "day"):
        assert d in dims


def test_render_by_fnumber_works() -> None:
    records = [_record(fnumber=2.8), _record(fnumber=4.0), _record(fnumber=2.8)]
    s = build_stats(records, [])
    out = render_by(s, "fnumber")
    assert "f/2.8" in out
    assert "f/4" in out
    assert "By f-number" in out


def test_render_by_aperture_matches_fnumber() -> None:
    records = [_record(fnumber=2.8), _record(fnumber=4.0), _record(fnumber=11.0)]
    s = build_stats(records, [])
    assert render_by(s, "aperture") == render_by(s, "fnumber").replace(
        "By f-number", "By aperture"
    )


# --- summary inline rendering ---------------------------------------------

def test_render_default_is_one_line_distribution() -> None:
    records = [
        _record(model="EOS R5", iso=100, lens="RF24-105", date="2024-01-15"),
        _record(model="EOS R5", iso=3200, lens="RF50",   date="2024-02-10"),
        _record(model="X-E5",   iso=400,  lens="XF33",   date="2024-02-20"),
    ]
    s = build_stats(records, [])
    out = render_default(s)
    assert "Summary" not in out
    assert "──────" not in out
    assert "Distribution" not in out
    assert "By camera" not in out
    assert "█" not in out
    assert "%" not in out
    assert "year" in out
    assert "month" in out
    assert "day" in out
    assert "ISO" in out
    assert "100 – 3200" in out


def test_render_with_explicit_dim_uses_bars() -> None:
    records = [
        _record(model="EOS R5", iso=100),
        _record(model="EOS R5", iso=400),
        _record(model="X-E5",   iso=3200),
    ]
    s = build_stats(records, [])
    out = render(s, dims=["camera"])
    assert "By camera" in out
    assert "█" in out
    assert "%" in out


def test_summary_includes_year_month_day_counts() -> None:
    records = [
        _record(date="2024-01-15"),
        _record(date="2024-02-10"),
        _record(date="2024-02-20"),
        _record(date="2025-08-01"),
    ]
    s = build_stats(records, [])
    out = render_default(s)
    assert "1 year," in out
    assert "6 months," in out
    assert "17 days" in out


def test_summary_iso_range_is_real_min_max() -> None:
    records = [_record(iso=64), _record(iso=12800), _record(iso=400)]
    s = build_stats(records, [])
    out = render_default(s)
    assert "ISO" in out
    assert "64 – 12800" in out


def test_summary_aperture_range_uses_f_notation() -> None:
    records = [_record(fnumber=1.4), _record(fnumber=11.0), _record(fnumber=2.8)]
    s = build_stats(records, [])
    out = render_default(s)
    assert "Aperture" in out
    assert "f/1.4 – f/11" in out


def test_summary_shutter_range_uses_photographer_format() -> None:
    records = [_record(shutter=0.001), _record(shutter=2.0)]
    s = build_stats(records, [])
    out = render_default(s)
    assert "Shutter" in out
    assert "1/1000" in out
    assert "2s" in out


def test_summary_camera_top_3_plus_others() -> None:
    records = [
        _record(model="EOS R5"),
        _record(model="EOS R5"),
        _record(model="EOS R5"),
        _record(model="X-E5"),
        _record(model="Z5_2"),
        _record(model="X2D 100C"),
        _record(model="GR III"),
    ]
    s = build_stats(records, [])
    out = render_default(s)
    cam_row = next(ln for ln in out.splitlines() if ln.startswith("Cameras"))
    assert cam_row.split()[-1] == "5"
    assert "EOS R5" not in out
    assert "others" not in out


def test_summary_orientation_lists_both_when_only_two() -> None:
    records = [
        _record(model="X", lens="L1"),
        _record(model="X", lens="L2"),
    ]
    records[0]["orientation"] = "landscape"
    records[1]["orientation"] = "portrait"
    s = build_stats(records, [])
    out = render_default(s)
    assert "1 (landscape), 1 (portrait)" in out
    assert "+0 others" not in out


# --- CLI surface ----------------------------------------------------------

@pytest.fixture
def fake_exif(monkeypatch):
    def fake(paths):
        return [
            {
                "path": str(p),
                "datetime": "2024-01-01 12:00:00",
                "date": "2024-01-01",
                "time": "12:00:00",
                "maker": "Canon",
                "model": "EOS R5" if "r5" in Path(p).name.lower() else "X-E5",
                "lens": "RF50",
                "iso": 100,
                "fnumber": 2.8,
                "shutter": 0.005,
                "focal": 50.0,
            }
            for p in paths
        ]

    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)
    return fake


def test_cli_stats_by_dimension(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    result = runner.invoke(app, ["stats", str(tmp_path), "--by", "model"])
    assert result.exit_code == 0
    assert "Photos" not in result.stdout
    assert "By camera" in result.stdout
    assert "By month" not in result.stdout


def test_cli_stats_by_multiple_dimensions(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    result = runner.invoke(
        app, ["stats", str(tmp_path), "--by", "model,lens,year"]
    )
    assert result.exit_code == 0
    assert "By camera" in result.stdout
    assert "By lens" in result.stdout
    assert "By year" in result.stdout


def test_cli_stats_by_rejects_duplicate(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    result = runner.invoke(
        app, ["stats", str(tmp_path), "--by", "model,model"]
    )
    assert result.exit_code == 2
    assert "duplicate" in result.stderr


def test_cli_stats_unknown_dimension_exits_2(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    result = runner.invoke(app, ["stats", str(tmp_path), "--by", "color"])
    assert result.exit_code == 2
    assert "unknown dimension" in result.stderr


def test_cli_stats_json(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x" * 1024)
    result = runner.invoke(app, ["stats", str(tmp_path), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["total"]["count"] == 1
    assert "by_model" in data
    assert "by_iso_bucket" in data


def test_cli_stats_no_raws(tmp_path) -> None:
    result = runner.invoke(app, ["stats", str(tmp_path)])
    assert result.exit_code == 1
    assert "no RAW files" in result.stderr


def test_cli_stats_where_zero_matches(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    result = runner.invoke(app, ["stats", str(tmp_path), "--where", "iso>100000"])
    assert result.exit_code == 1
    assert "no records matched --where" in result.stderr


def test_cli_stats_where_caption(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    result = runner.invoke(app, ["stats", str(tmp_path), "--where", "iso>=50"])
    assert result.exit_code == 0
    assert "Filter" in result.stdout
    assert "iso>=50" in result.stdout
