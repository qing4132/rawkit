"""rawkit stats — aggregate EXIF + filesize across a set of RAW records.

Two public entry points:

  build_stats(records, paths)            -> dict (structured aggregation)
  render_default(stats)                  -> str  (4-section human table)
  render_by(stats, dimension)            -> str  (single-dimension deep view)

`records` is the list of EXIF dicts produced by `rawkit.exif.batch_read`
(same shape used by `ls --json` and the --where DSL).
`paths` is the parallel list of Path objects, used only for st_size — we
don't redo any EXIF work here.

Design notes:
  - ALL aggregation lives in build_stats(); render_* are pure formatters.
    Easier to test the numbers without parsing tables, and lets `--json`
    consume the same dict.
  - Counters are sorted by count desc, then key asc, so output is stable
    across runs.
  - Buckets are deliberately coarse photographer-friendly ranges, not
    auto-binned — "ISO 800" is a meaningful unit, not arbitrary slicing.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from calendar import monthrange
from pathlib import Path
from typing import Any, Iterable


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

# 24 hourly buckets, one per clock hour. We previously had 3-hour bands
# (00–02, 03–05, ...) but the ranges were unclear (does '00–02' mean
# inclusive? does 03 belong to 03–05 or 00–02?). One-hour buckets are
# self-explanatory; large outputs are the user's choice to make.
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
    # Pick the closest standard aperture within a relative tolerance.
    best = min(_STD_APERTURES, key=lambda s: abs(s - f))
    if abs(best - f) / best < 0.06:
        return f"f/{best:g}"
    # Fallback: just print the raw value
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
    return date_str[:7]  # 'YYYY-MM'


def _year_bucket(date_str: str | None) -> str | None:
    if not isinstance(date_str, str) or len(date_str) < 4:
        return None
    return date_str[:4]  # 'YYYY'


def _day_bucket(date_str: str | None) -> str | None:
    if not isinstance(date_str, str) or len(date_str) < 10:
        return None
    return date_str[:10]  # 'YYYY-MM-DD'


# --- main aggregation ------------------------------------------------------

def build_stats(
    records: list[dict[str, Any]],
    paths: list[Path],
) -> dict[str, Any]:
    """Aggregate EXIF + filesize. Returns a JSON-friendly dict."""
    n = len(records)
    if n == 0:
        return {"total": {"count": 0}}

    # File sizes — best-effort. Missing file = 0 (don't fail aggregation).
    total_bytes = 0
    for p in paths:
        try:
            total_bytes += p.stat().st_size
        except OSError:
            pass

    # Date range from the `date` field (YYYY-MM-DD). Records without
    # datetime get skipped here but still counted everywhere else.
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
                # borrow from the month preceding d1
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

    # Numeric extents (real values, not bucket names).
    def _extent(field: str) -> tuple[Any, Any]:
        vals = [r[field] for r in records if r.get(field) is not None]
        if not vals:
            return (None, None)
        return (min(vals), max(vals))

    iso_min, iso_max         = _extent("iso")
    fnumber_min, fnumber_max = _extent("fnumber")
    focal_min, focal_max     = _extent("focal")
    shutter_min, shutter_max = _extent("shutter")

    # Hours extracted from the `time` field (HH:MM[:SS]). Stored as a
    # sorted list of distinct hours so the renderer can collapse runs
    # of consecutive hours into ranges (e.g. 02–04, 22–23) instead of
    # silently swallowing midday gaps with a single min–max.
    hours = [int(r["time"][:2]) for r in records if isinstance(r.get("time"), str) and len(r["time"]) >= 2]
    hours_present = sorted(set(hours))

    models = [r["model"] for r in records if r.get("model")]
    lenses = [r["lens"] for r in records if r.get("lens")]
    lensless_count = sum(1 for r in records if not r.get("lens"))

    def _ranked(items: list[str]) -> list[dict[str, Any]]:
        c = Counter(items)
        # sort: count desc, then key asc for stable tie-breaking
        ordered = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
        return [
            {"key": k, "count": v, "share": v / n}
            for k, v in ordered
        ]

    def _bucketed(buckets: list[tuple[Any, ...]], extractor) -> list[dict[str, Any]]:
        """Apply `extractor(record)` to every record, bucket by the returned
        string, return ordered list keyed in the canonical bucket order
        (skipping empty buckets to keep output compact)."""
        c: Counter[str] = Counter()
        for r in records:
            b = extractor(r)
            if b is not None:
                c[b] += 1
        # preserve the canonical bucket order from the definition
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
        # Optional dimensions, populated for --by foo views.
        # Note: this is keyed 'by_fnumber_bucket' (NOT 'by_aperture_bucket')
        # to match the --where DSL field name. Photographers know 'aperture'
        # but f/1.4 is a 'larger aperture' than f/4 — naming the field after
        # the cultural term would invert the natural <,>= semantics in the
        # DSL. We use 'fnumber' (the EXIF tag) so 'fnumber>=2.8' means what
        # you'd expect numerically. 'aperture' is still accepted as an alias
        # in the CLI for convenience.
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
    """Like _ranked but sorted chronologically (lex order is fine for YYYY-MM)."""
    c = Counter(x for x in items_with_none if x is not None)
    return [
        {"key": k, "count": c[k], "share": c[k] / total}
        for k in sorted(c)
    ]


def _bytes_human(n: int) -> str:
    """1024-based with photographer-friendly units. 1.36 GiB / 220 MiB / 4.2 KiB."""
    if n < 1024:
        return f"{n} B"
    units = ["KiB", "MiB", "GiB", "TiB"]
    f = float(n)
    for u in units:
        f /= 1024.0
        if f < 1024.0:
            return f"{f:.2f} {u}" if f < 10 else f"{f:.1f} {u}"
    return f"{f:.1f} {units[-1]}"


# --- text rendering --------------------------------------------------------

_BAR_CHAR = "█"
_BAR_WIDTH = 30  # cells reserved for the bar


def _bar(share: float) -> str:
    """30-cell ascii bar, minimum 1 cell so 'X%' is never invisible."""
    cells = max(1, round(share * _BAR_WIDTH))
    return _BAR_CHAR * cells


def _fmt_share(share: float) -> str:
    """3-cell-wide percentage, no decimals: '52%', ' 4%', '100%'."""
    pct = round(share * 100)
    return f"{pct:>3}%"


_HRULE = "─" * 56  # matches default 4-section block width


def _section(title: str, rows: list[tuple[str, str, str]]) -> str:
    """Render one section: bold title (if TTY upstream), hrule, key/count/share rows.
    All bar/share formatting is precomputed by the caller; we just align.
    Rows are (key, count_str, bar_with_share). When the third column is
    empty (compact mode), trailing whitespace is stripped."""
    if not rows:
        return ""
    key_w = max(len(r[0]) for r in rows)
    count_w = max(len(r[1]) for r in rows)
    lines = [title, _HRULE]
    for key, count, bar in rows:
        lines.append(f"{key:<{key_w}}  {count:>{count_w}}  {bar}".rstrip())
    return "\n".join(lines)


def _build_dist_rows(items: list[dict[str, Any]], top: int | None = None) -> list[tuple[str, str, str]]:
    """Turn a 'by_*' list into the (key, count, 'bar  XX%') tuples that
    _section consumes. The bar column is padded to _BAR_WIDTH so the
    trailing percentages line up vertically."""
    rows: list[tuple[str, str, str]] = []
    display = items if top is None else items[:top]
    for it in display:
        bar = _bar(it["share"])
        # Pad the bar to fixed width (in *cells*, not bytes; '█' is single-cell).
        bar_padded = bar + " " * (_BAR_WIDTH - len(bar))
        rows.append((str(it["key"]), str(it["count"]), f"{bar_padded}  {_fmt_share(it['share'])}"))
    return rows


def _build_compact_rows(items: list[dict[str, Any]], top: int | None = None) -> list[tuple[str, str, str]]:
    """Like _build_dist_rows but with NO bar and NO percentage — just
    `key  count` for compact multi-dimension overview. The third tuple
    field is empty so _section's alignment logic still works."""
    rows: list[tuple[str, str, str]] = []
    display = items if top is None else items[:top]
    for it in display:
        rows.append((str(it["key"]), str(it["count"]), ""))
    return rows


def _fmt_shutter(s: float | None) -> str:
    """Format a shutter speed (in seconds) the way photographers read it.
    0.004 → '1/250'; 2.0 → '2s'; 0.999 → '1s' (never '1/1')."""
    if s is None:
        return "—"
    s = float(s)
    if s >= 1:
        return f"{s:g}s"
    denom = round(1 / s)
    if denom <= 1:
        return f"{s:g}s"
    return f"1/{denom}"


def _fmt_aperture(f: float | None) -> str:
    if f is None:
        return "—"
    return f"f/{float(f):g}"


def _fmt_focal(mm: float | None) -> str:
    if mm is None:
        return "—"
    return f"{float(mm):g}mm"


def _fmt_iso(i: int | float | None) -> str:
    if i is None:
        return "—"
    return f"{int(i)}"


def _fmt_hour(h: int | None) -> str:
    if h is None:
        return "—"
    return f"{int(h):02d}"


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
        _fmt_hour(a) if a == b else f"{_fmt_hour(a)}–{_fmt_hour(b)}"
        for a, b in segments
    )


def _enum_inline(items: list[dict[str, Any]], n_distinct: int) -> str:
    """`{count} ({key})` for each top-3 item, plus '+N others' when there
    are more. e.g. '22 (landscape), 3 (portrait)'. `n_distinct` is the
    dimension's total distinct count (drives the +others tail)."""
    if not items:
        return "\u2014"
    top_n = 3
    parts = [f"{it['count']} ({it['key']})" for it in items[:top_n]]
    extra = n_distinct - min(top_n, len(items))
    if extra > 0:
        parts.append(f"+{extra} others")
    return ", ".join(parts)


def _render_summary(stats: dict[str, Any], where: str) -> str:
    """Single consolidated overview block — totals, date range with
    independent year/month/day distinct counts, count-only for camera/
    lens, top-3 enums, and min\u2013max ranges for numeric dims. No header,
    no horizontal rule; just the rows."""
    total = stats.get("total", {})
    if total.get("count", 0) == 0:
        return "no records"

    dr = total.get("date_range", [None, None])
    sy = total.get("span_years",  0)
    sm = total.get("span_months", 0)
    sd = total.get("span_days",   0)
    if dr[0]:
        date_str = (
            f"{dr[0]} \u2192 {dr[1]}  "
            f"({sy} year{'s' if sy != 1 else ''}, "
            f"{sm} month{'s' if sm != 1 else ''}, "
            f"{sd} day{'s' if sd != 1 else ''})"
        )
    else:
        date_str = "\u2014"

    by_orientation = stats.get("by_orientation", [])
    orient_line = _enum_inline(by_orientation, len(by_orientation)) if by_orientation else "\u2014"

    def _range(lo, hi, fmt) -> str:
        if lo is None:
            return "\u2014"
        if lo == hi:
            return fmt(lo)
        return f"{fmt(lo)} \u2013 {fmt(hi)}"

    iso_line     = _range(total.get("iso_min"),     total.get("iso_max"),     _fmt_iso)
    aperture_line= _range(total.get("fnumber_min"), total.get("fnumber_max"), _fmt_aperture)
    shutter_line = _range(total.get("shutter_min"), total.get("shutter_max"), _fmt_shutter)
    focal_line   = _range(total.get("focal_min"),   total.get("focal_max"),   _fmt_focal)
    hour_line    = _hours_inline(total.get("hours_present", []))

    rows: list[tuple[str, str]] = []
    if where:
        rows.append(("Filter",      where))
    rows.append(("Photos",       f"{total['count']}"))
    rows.append(("Total size",   total.get("bytes_human", "-")))
    rows.append(("Date range",   date_str))
    rows.append(("Hour",         hour_line))
    rows.append(("Cameras",      f"{total.get('n_models', 0)}"))
    rows.append(("Lenses",       f"{total.get('n_lenses', 0)}"))
    rows.append(("Orientation",  orient_line))
    rows.append(("ISO",          iso_line))
    rows.append(("Aperture",     aperture_line))
    rows.append(("Shutter",      shutter_line))
    rows.append(("Focal length", focal_line))

    key_w = max(len(k) for k, _ in rows)
    return "\n".join(f"{k:<{key_w}}  {v}" for k, v in rows)


def _render_one_dim(
    stats: dict[str, Any],
    dimension: str,
    *,
    top: int | None,
    compact: bool = False,
) -> str:
    """Render one dimension as a section. `top` only takes effect on
    'lens' (the only dimension where the count of distinct keys can blow
    up); other dimensions ignore it because their buckets are bounded.

    `compact=True` skips the bar chart and percentage column — just
    'key  count' rows. Currently unused by the default view (which uses
    `_render_overview` instead), kept available for API completeness."""
    if dimension not in _DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}; valid: {supported_dimensions()}")
    title, key = _DIMENSIONS[dimension]
    items = stats.get(key, [])
    if not items:
        return ""

    # Truncation only matters for unbounded dimensions (lens / day /
    # custom strings). For bounded ones (camera / iso / aperture / focal
    # / hour / year / month / orientation / maker) `top` is irrelevant —
    # show all buckets.
    apply_top = dimension in {"lens"}
    effective_top = top if apply_top else None

    builder = _build_compact_rows if compact else _build_dist_rows
    rows = builder(items, top=effective_top)
    if apply_top and top is not None and len(items) > top:
        sec = _section(f"{title} (top {top})", rows)
        plural = {"lens": "lenses"}.get(dimension, dimension + "s")
        sec += (
            f"\n... {len(items) - top} more {plural} hidden "
            f"(--more or --top N to see all)"
        )
        return sec
    return _section(title, rows)


def render(
    stats: dict[str, Any],
    *,
    dims: list[str] | None = None,
    lens_top: int = 5,
    where: str = "",
) -> str:
    """Render either the overview block OR per-dimension bar charts.

    When `dims` is None (default), output is just the overview block —
    one line per dimension, totals + ranges + top-3 enums.

    When `dims` is given, produce ONLY the DETAILED bar-chart sections
    for the chosen dimensions. The overview is suppressed; --by is the
    "drill in, skip the front matter" mode.

      render(stats)                            # default: overview only
      render(stats, dims=["month"])            # detailed by month, no overview
      render(stats, dims=["camera", "lens"])   # two detailed sections
    """
    total = stats.get("total", {})
    if total.get("count", 0) == 0:
        return "no records"

    if dims is None:
        return _render_summary(stats, where)

    sections: list[str] = []
    for dim in dims:
        sec = _render_one_dim(stats, dim, top=lens_top, compact=False)
        if sec:
            sections.append(sec)
    return "\n\n".join(sections)


# Backwards-compatible thin wrappers for callers / tests that referred to
# the previous API directly. These are pure re-routes — no new behaviour.

def render_default(stats: dict[str, Any], *, lens_top: int = 5, where: str = "") -> str:
    """Compat shim: same as render() with default dims=['month']."""
    return render(stats, dims=None, lens_top=lens_top, where=where)


def render_by(
    stats: dict[str, Any],
    dimension: str,
    *,
    top: int | None = None,
    where: str = "",
) -> str:
    """Single-dimension deep view (no Summary header). Used by the CLI's
    older single-dim path; multi-dim users go through render()."""
    if dimension not in _DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}; valid: {supported_dimensions()}")
    title, key = _DIMENSIONS[dimension]
    items = stats.get(key, [])
    if not items:
        return f"no data for dimension {dimension!r}"
    n = stats.get("total", {}).get("count", 0)
    if where:
        title = f"{title}  ·  filter: {where}  ·  n={n}"
    return _section(title, _build_dist_rows(items, top=top))


_DIMENSIONS = {
    # Field names match the --where DSL. 'aperture' and 'fnumber' show
    # identical bucket order here (small-fnumber/large-aperture first) —
    # the photographer-direction inversion lives only in --where where it
    # earns its keep (so 'aperture>=2.8' reads naturally as 'wider than
    # or equal to f/2.8'). In display / sort, having TWO directions for
    # the same data is confusing without payoff, so we settle on one.
    "model":       ("By camera",                            "by_model"),
    "camera":      ("By camera",                            "by_model"),  # alias of model (display word)
    "lens":        ("By lens",                              "by_lens"),
    "maker":       ("By maker",                             "by_maker"),
    "orientation": ("By orientation",                       "by_orientation"),
    "iso":         ("By ISO",                               "by_iso_bucket"),
    "aperture":    ("By aperture",                          "by_fnumber_bucket"),
    "fnumber":     ("By f-number",                          "by_fnumber_bucket"),  # alias of aperture, same direction
    "focal":       ("By focal length",                      "by_focal_bucket"),
    "hour":        ("By hour of day",                       "by_hour_bucket"),
    "year":        ("By year",                              "by_year_bucket"),
    "month":       ("By month",                             "by_month_bucket"),
    "day":         ("By day",                               "by_day_bucket"),
}


# Set of dimensions whose bucket list should be reversed before render.
# Currently empty: aperture / fnumber share the same display order to
# avoid "two ways to look at the same number" cognitive load. Inversion
# survives only in --where where it carries its weight.
_REVERSE_FOR_DISPLAY: frozenset[str] = frozenset()


def supported_dimensions() -> list[str]:
    return sorted(_DIMENSIONS)
