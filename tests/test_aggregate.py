"""Tests for the aggregation core in `rawkit.aggregate`.

This module owns build_stats and the bucket helpers used by `info` DIR
mode. It does NOT depend on `rawkit.stats` (the optional bar-chart addon).
"""

from __future__ import annotations

from pathlib import Path

from rawkit.aggregate import build_stats


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
    assert [m["key"] for m in by_model] == ["EOS R5", "ILCE-7RM4A", "X-E5"]
    assert by_model[0]["count"] == 2
    assert by_model[0]["share"] == 0.5


def test_iso_buckets_skip_empty_ones() -> None:
    records = [_record(iso=100), _record(iso=200), _record(iso=3200)]
    s = build_stats(records, [])
    keys = [b["key"] for b in s["by_iso_bucket"]]
    assert keys == ["≤100", "101–200", "1601–3200"]


def test_aperture_bucket_snaps_to_standard() -> None:
    s = build_stats([_record(fnumber=2.7), _record(fnumber=4.0)], [])
    keys = [b["key"] for b in s["by_fnumber_bucket"]]
    assert "f/2.8" in keys
    assert "f/4" in keys


def test_focal_buckets() -> None:
    records = [
        _record(focal=14),
        _record(focal=50),
        _record(focal=200),
    ]
    s = build_stats(records, [])
    keys = [b["key"] for b in s["by_focal_bucket"]]
    assert "<20mm ultra-wide" in keys
    assert "35-70mm standard" in keys
    assert "200-600mm long" in keys


def test_hour_buckets() -> None:
    records = [
        _record(time="08:30:00"),
        _record(time="16:00:00"),
        _record(time="16:45:00"),
    ]
    s = build_stats(records, [])
    by_hour = {b["key"]: b["count"] for b in s["by_hour_bucket"]}
    assert by_hour.get("08") == 1
    assert by_hour.get("16") == 2


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


def test_year_and_day_buckets_chronological() -> None:
    records = [
        _record(date="2024-03-15"),
        _record(date="2023-12-31"),
        _record(date="2024-03-15"),
        _record(date="2024-03-16"),
    ]
    s = build_stats(records, [])
    years = [b["key"] for b in s["by_year_bucket"]]
    assert years == ["2023", "2024"]
    days = [(b["key"], b["count"]) for b in s["by_day_bucket"]]
    assert days == [("2023-12-31", 1), ("2024-03-15", 2), ("2024-03-16", 1)]


def test_lensless_count() -> None:
    s = build_stats(
        [_record(lens=None), _record(lens="RF50mm F1.8 STM")],
        [],
    )
    assert s["total"]["n_lensless_files"] == 1
    assert s["total"]["n_lenses"] == 1


def test_build_stats_includes_by_maker_and_orientation() -> None:
    records = [
        _record(model="EOS R5", maker="Canon"),
        _record(model="EOS R5", maker="Canon"),
        _record(model="ILCE-7RM4A", maker="SONY"),
    ]
    records[0]["orientation"] = "landscape"
    records[1]["orientation"] = "portrait"
    records[2]["orientation"] = "landscape"
    s = build_stats(records, [])

    makers = {m["key"]: m["count"] for m in s["by_maker"]}
    assert makers == {"Canon": 2, "SONY": 1}

    orients = {o["key"]: o["count"] for o in s["by_orientation"]}
    assert orients == {"landscape": 2, "portrait": 1}
