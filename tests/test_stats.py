"""Tests for the rawkit.stats aggregator + the `rawkit stats` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rawkit.cli import app
from rawkit.stats import (
    build_stats,
    render_by,
    render_default,
    supported_dimensions,
)


runner = CliRunner()


# --- build_stats ----------------------------------------------------------

def _record(**kw) -> dict:
    """Helper: a record with sensible defaults that build_stats can ingest."""
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


def _fake_paths_with_size(tmp_path: Path, names: list[str], size: int = 1024) -> list[Path]:
    """Create N small files in tmp_path so build_stats can read st_size."""
    out: list[Path] = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"\0" * size)
        out.append(p)
    return out


def test_empty_records_returns_total_zero() -> None:
    s = build_stats([], [])
    assert s["total"]["count"] == 0


def test_total_counts_and_sums(tmp_path) -> None:
    records = [_record(path=str(tmp_path / "a.ARW"), iso=100),
               _record(path=str(tmp_path / "b.ARW"), iso=200)]
    paths = _fake_paths_with_size(tmp_path, ["a.ARW", "b.ARW"], size=2048)
    s = build_stats(records, paths)
    assert s["total"]["count"] == 2
    assert s["total"]["bytes"] == 4096
    assert s["total"]["bytes_human"].endswith("KiB")


def test_date_range_and_days_spanned() -> None:
    records = [
        _record(date="2024-01-01"),
        _record(date="2024-03-15"),
        _record(date="2024-02-01"),
    ]
    s = build_stats(records, [])
    assert s["total"]["date_range"] == ["2024-01-01", "2024-03-15"]
    # Jan 1 → Mar 15 inclusive (2024 is a leap year so Feb has 29 days):
    # 31 (Jan) + 29 (Feb) + 15 (Mar) = 75
    assert s["total"]["days_spanned"] == 75


def test_model_distribution_count_and_share() -> None:
    records = [
        _record(model="EOS R5"),
        _record(model="EOS R5"),
        _record(model="ILCE-7RM4A"),
        _record(model="X-E5"),
    ]
    s = build_stats(records, [])
    by_model = s["by_model"]
    # Order: count desc, then key asc
    assert [m["key"] for m in by_model] == ["EOS R5", "ILCE-7RM4A", "X-E5"]
    assert by_model[0]["count"] == 2
    assert by_model[0]["share"] == 0.5


def test_iso_buckets_skip_empty_ones() -> None:
    records = [_record(iso=100), _record(iso=200), _record(iso=3200)]
    s = build_stats(records, [])
    keys = [b["key"] for b in s["by_iso_bucket"]]
    # ≤100, 101–200, 1601–3200 — the others (which have count 0) are omitted
    assert keys == ["≤100", "101–200", "1601–3200"]


def test_aperture_bucket_snaps_to_standard() -> None:
    # 2.7 should snap to f/2.8 (within 6% tolerance)
    s = build_stats([_record(fnumber=2.7), _record(fnumber=4.0)], [])
    keys = [b["key"] for b in s["by_aperture_bucket"]]
    assert "f/2.8" in keys
    assert "f/4" in keys


def test_focal_buckets() -> None:
    records = [
        _record(focal=14),     # <20mm ultra-wide
        _record(focal=50),     # 35-70mm standard
        _record(focal=200),    # 200-600mm long (200 inclusive in next bucket)
    ]
    s = build_stats(records, [])
    keys = [b["key"] for b in s["by_focal_bucket"]]
    assert "<20mm ultra-wide" in keys
    assert "35-70mm standard" in keys
    assert "200-600mm long" in keys


def test_hour_buckets() -> None:
    records = [
        _record(time="08:30:00"),   # 06–08
        _record(time="16:00:00"),   # 15–17
        _record(time="16:45:00"),   # 15–17
    ]
    s = build_stats(records, [])
    by_hour = {b["key"]: b["count"] for b in s["by_hour_bucket"]}
    assert by_hour.get("06–08") == 1
    assert by_hour.get("15–17") == 2


def test_month_buckets_chronological() -> None:
    records = [
        _record(date="2024-03-15"),
        _record(date="2024-01-20"),
        _record(date="2024-03-01"),
        _record(date="2024-02-10"),
    ]
    s = build_stats(records, [])
    keys = [b["key"] for b in s["by_month_bucket"]]
    assert keys == ["2024-01", "2024-02", "2024-03"]


def test_lensless_count() -> None:
    # 1 lensless, 1 with lens
    s = build_stats(
        [_record(lens=None), _record(lens="RF50mm F1.8 STM")],
        [],
    )
    assert s["total"]["n_lensless_files"] == 1
    assert s["total"]["n_lenses"] == 1


# --- render -----------------------------------------------------------------

def test_render_default_contains_all_4_sections() -> None:
    records = [
        _record(model="EOS R5", iso=100, lens="RF24-105"),
        _record(model="EOS R5", iso=3200, lens="RF50"),
        _record(model="X-E5",   iso=400,  lens="XF33"),
    ]
    s = build_stats(records, [])
    out = render_default(s)
    assert "Summary" in out
    assert "By camera" in out
    assert "By ISO" in out
    assert "By lens" in out
    # Bars are present
    assert "█" in out


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
    # The earlier month should appear above the later one
    assert out.index("2024-01") < out.index("2024-03")


def test_render_by_caption_when_where() -> None:
    records = [_record(iso=400)]
    s = build_stats(records, [])
    out = render_by(s, "iso", where="iso>=400")
    assert "filter:" in out
    assert "n=1" in out


def test_supported_dimensions_includes_expected() -> None:
    dims = supported_dimensions()
    for d in ("model", "lens", "iso", "aperture", "focal", "hour", "month"):
        assert d in dims


# --- CLI surface ------------------------------------------------------------

@pytest.fixture
def fake_exif(monkeypatch):
    """Make safe_batch_read return a predictable EXIF set without exiftool."""

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


def test_cli_stats_default_4_sections(tmp_path, fake_exif) -> None:
    (tmp_path / "r5a.ARW").write_bytes(b"x" * 100)
    (tmp_path / "r5b.ARW").write_bytes(b"x" * 200)
    (tmp_path / "xe5.RAF").write_bytes(b"x" * 50)

    result = runner.invoke(app, ["stats", str(tmp_path)])
    assert result.exit_code == 0
    assert "Summary" in result.stdout
    assert "By camera" in result.stdout
    assert "EOS R5" in result.stdout
    assert "X-E5" in result.stdout


def test_cli_stats_by_dimension(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    result = runner.invoke(app, ["stats", str(tmp_path), "--by", "model"])
    assert result.exit_code == 0
    assert "By camera" in result.stdout
    # default 4 sections should NOT appear
    assert "Summary" not in result.stdout


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
