"""Record-set aggregation primitives used by `info` (and any other consumer).

Pure data → data: takes the same `records` dicts produced by
`rawkit.exif.batch_read` and returns a JSON-friendly aggregation dict.
No rendering, no CLI awareness.

This module is the home of:
  - bucket definitions (ISO / aperture / focal / hour ranges photographers
    actually think in)
  - `build_stats(records, paths)` — the full aggregation
  - `_hours_inline` / `_bytes_human` — tiny formatting helpers needed by
    callers that present `build_stats` output

The optional `rawkit.stats` module (bar-chart drill-downs) depends on
this module, not the other way around. Deleting `stats.py` leaves
everything in here, and `info`, intact.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from calendar import monthrange
from pathlib import Path
from typing import Any


# --- buckets ---------------------------------------------------------------

# ISO is conventionally log2-spaced; one bucket per stop.
_ISO_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (0,    100,    "≤100"),
    (101,  200,    "101–200"),
    (201,  400,    "201–400"),
    (401,  800,    "401–800"),
    (801,  1600,   "801–1600"),
    (1601, 3200,   "1601–3200"),
    (3201, 6400,   "3201–6400"),
    (6401, 10**9,  ">6400"),
)

# Standard photographic apertures. We bucket each shot to the nearest
# standard stop (within ±5% to handle f/2.7 → 2.8, f/3.5 → 3.5 specials).
_STD_APERTURES: tuple[float, ...] = (
    1.0, 1.2, 1.4, 1.8, 2.0, 2.5, 2.8, 3.5, 4.0, 5.6, 8.0, 11.0, 16.0, 22.0, 32.0,
)

# Focal-length classes (mm, 35mm-equivalent assumed; we don't try to
# normalise for crop because EXIF rarely carries equivalent focal).
_FOCAL_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (0.0,     20.0,    "<20mm ultra-wide"),
    (20.0,    35.0,    "20-35mm wide"),
    (35.0,    70.0,    "35-70mm standard"),
    (70.0,    200.0,   "70-200mm tele"),
    (200.0,   600.0,   "200-600mm long"),
    (600.0,   10000.0, ">600mm super-tele"),
)

# 24 hourly buckets, one per clock hour.
_HOUR_BUCKETS: tuple[tuple[int, int, str], ...] = tuple(
    (h, h + 1, f"{h:02d}") for h in range(24)
)


def _iso_bucket(iso: float | int | None) -> str | None:
    if iso is None:
        return None
    try:
        n = int(iso)
    except (TypeError, ValueError):
        return None
    for lo, hi, name in _ISO_BUCKETS:
        if lo <= n <= hi:
            return name
    return None


def _aperture_bucket(fnum: float | int | None) -> str | None:
    if fnum is None:
        return None
    try:
        f = float(fnum)
    except (TypeError, ValueError):
        return None
    best = min(_STD_APERTURES, key=lambda s: abs(s - f))
    if abs(best - f) / best < 0.06:
        return f"f/{best:g}"
    return f"f/{f:g}"


def _focal_bucket(focal: float | int | None) -> str | None:
    if focal is None:
        return None
    try:
        mm = float(focal)
    except (TypeError, ValueError):
        return None
    for lo, hi, name in _FOCAL_BUCKETS:
        if lo <= mm < hi:
            return name
    return None


def _hour_bucket(time_str: str | None) -> str | None:
    if not isinstance(time_str, str) or len(time_str) < 2:
        return None
    try:
        h = int(time_str[:2])
    except ValueError:
        return None
    for lo, hi, name in _HOUR_BUCKETS:
        if lo <= h < hi:
            return name
    return None


def _month_bucket(date_str: str | None) -> str | None:
    if not isinstance(date_str, str) or len(date_str) < 7:
        return None
    return date_str[:7]


def _year_bucket(date_str: str | None) -> str | None:
    if not isinstance(date_str, str) or len(date_str) < 4:
        return None
    return date_str[:4]


def _day_bucket(date_str: str | None) -> str | None:
    if not isinstance(date_str, str) or len(date_str) < 10:
        return None
    return date_str[:10]


# --- main aggregation ------------------------------------------------------

def build_stats(
    records: list[dict[str, Any]],
    paths: list[Path],
) -> dict[str, Any]:
    """Aggregate EXIF + filesize. Returns a JSON-friendly dict."""
    n = len(records)
    if n == 0:
        return {"total": {"count": 0}}

    total_bytes = 0
    for p in paths:
        try:
            total_bytes += p.stat().st_size
        except OSError:
            pass

    dates = sorted(r["date"] for r in records if isinstance(r.get("date"), str))
    date_range = [dates[0], dates[-1]] if dates else [None, None]
    days_spanned = 0
    if dates:
        try:
            d0 = datetime.strptime(dates[0], "%Y-%m-%d")
            d1 = datetime.strptime(dates[-1], "%Y-%m-%d")
            days_spanned = (d1 - d0).days + 1
        except ValueError:
            pass

    # Calendar breakdown of the date range (years + months + days that
    # together reconstruct the span). 2022-04-23 → 2025-08-09 = 3y 3m 17d.
    span_years = span_months = span_days = 0
    if dates:
        try:
            d0 = datetime.strptime(dates[0], "%Y-%m-%d").date()
            d1 = datetime.strptime(dates[-1], "%Y-%m-%d").date()
            y = d1.year - d0.year
            m = d1.month - d0.month
            d = d1.day - d0.day
            if d < 0:
                pm = d1.month - 1 if d1.month > 1 else 12
                py = d1.year if d1.month > 1 else d1.year - 1
                d += monthrange(py, pm)[1]
                m -= 1
            if m < 0:
                m += 12
                y -= 1
            span_years, span_months, span_days = y, m, d
        except ValueError:
            pass

    def _extent(field: str) -> tuple[Any, Any]:
        vals = [r[field] for r in records if r.get(field) is not None]
        if not vals:
            return (None, None)
        return (min(vals), max(vals))

    iso_min, iso_max         = _extent("iso")
    fnumber_min, fnumber_max = _extent("fnumber")
    focal_min, focal_max     = _extent("focal")
    shutter_min, shutter_max = _extent("shutter")

    hours_present = sorted({r["hour"] for r in records if isinstance(r.get("hour"), int)})

    models = [r["model"] for r in records if r.get("model")]
    lenses = [r["lens"] for r in records if r.get("lens")]
    lensless_count = sum(1 for r in records if not r.get("lens"))

    def _ranked(items: list[str]) -> list[dict[str, Any]]:
        c = Counter(items)
        ordered = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
        return [
            {"key": k, "count": v, "share": v / n}
            for k, v in ordered
        ]

    def _bucketed(buckets: list[tuple[Any, ...]], extractor) -> list[dict[str, Any]]:
        c: Counter[str] = Counter()
        for r in records:
            b = extractor(r)
            if b is not None:
                c[b] += 1
        names_in_order = [name for *_, name in buckets]
        return [
            {"key": name, "count": c[name], "share": c[name] / n}
            for name in names_in_order
            if c[name] > 0
        ]

    return {
        "total": {
            "count": n,
            "bytes": total_bytes,
            "bytes_human": _bytes_human(total_bytes),
            "date_range": date_range,
            "days_spanned": days_spanned,
            "span_years":  span_years,
            "span_months": span_months,
            "span_days":   span_days,
            "n_models": len(set(models)),
            "n_lenses": len(set(lenses)),
            "n_makers": len({r["maker"] for r in records if r.get("maker")}),
            "n_lensless_files": lensless_count,
            "iso_min": iso_min, "iso_max": iso_max,
            "fnumber_min": fnumber_min, "fnumber_max": fnumber_max,
            "focal_min": focal_min, "focal_max": focal_max,
            "shutter_min": shutter_min, "shutter_max": shutter_max,
            "hours_present": hours_present,
        },
        "by_model": _ranked(models),
        "by_maker": _ranked([r["maker"] for r in records if r.get("maker")]),
        "by_iso_bucket": _bucketed(list(_ISO_BUCKETS), lambda r: _iso_bucket(r.get("iso"))),
        "by_lens": _ranked(lenses),
        "by_orientation": _ranked([r["orientation"] for r in records if r.get("orientation")]),
        "by_fnumber_bucket": _bucketed(
            [(a, f"f/{a:g}") for a in _STD_APERTURES],
            lambda r: _aperture_bucket(r.get("fnumber")),
        ),
        "by_focal_bucket": _bucketed(list(_FOCAL_BUCKETS), lambda r: _focal_bucket(r.get("focal"))),
        "by_hour_bucket": _bucketed(list(_HOUR_BUCKETS), lambda r: _hour_bucket(r.get("time"))),
        "by_month_bucket": _ranked_chrono(
            [_month_bucket(r.get("date")) for r in records],
            n,
        ),
        "by_year_bucket": _ranked_chrono(
            [_year_bucket(r.get("date")) for r in records],
            n,
        ),
        "by_day_bucket": _ranked_chrono(
            [_day_bucket(r.get("date")) for r in records],
            n,
        ),
    }


def _ranked_chrono(items_with_none: list[str | None], total: int) -> list[dict[str, Any]]:
    """Like the inline _ranked helper but sorted chronologically (lex order is fine for YYYY-MM)."""
    c = Counter(x for x in items_with_none if x is not None)
    return [
        {"key": k, "count": c[k], "share": c[k] / total}
        for k in sorted(c)
    ]


# --- presentation helpers (used by callers of build_stats) -----------------

def _bytes_human(n: int) -> str:
    """1024-based with photographer-friendly units. 1.36 GiB / 220 MiB / 4.2 KiB."""
    if n < 1024:
        return f"{n} B"
    x = float(n)
    units = ["KiB", "MiB", "GiB", "TiB"]
    for u in units:
        x /= 1024.0
        if x < 1024.0 or u == units[-1]:
            return f"{x:.2f} {u}" if x < 10 else f"{x:.1f} {u}"
    return f"{n} B"


def _hours_inline(hours: list[int]) -> str:
    """Collapse a sorted list of distinct hours into segment notation.
    Consecutive hours fold into 'lo–hi'; isolated hours stay single.
    [2,3,4,22,23] → '02–04, 22–23'.  [10] → '10'.  [] → '—'."""
    if not hours:
        return "—"
    segments: list[tuple[int, int]] = []
    lo = prev = hours[0]
    for h in hours[1:]:
        if h == prev + 1:
            prev = h
        else:
            segments.append((lo, prev))
            lo = prev = h
    segments.append((lo, prev))
    return ", ".join(
        f"{a:02d}" if a == b else f"{a:02d}–{b:02d}"
        for a, b in segments
    )
