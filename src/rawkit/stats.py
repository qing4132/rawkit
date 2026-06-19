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
    (0.0,     20.0,    "<20mm 超广"),
    (20.0,    35.0,    "20–35mm 广角"),
    (35.0,    70.0,    "35–70mm 标准"),
    (70.0,    200.0,   "70–200mm 中长"),
    (200.0,   600.0,   "200–600mm 长焦"),
    (600.0,   10000.0, ">600mm 超长"),
)

# 3-hour blocks; photographers think in golden-hour / blue-hour / midday
# bands, not single hours.
_HOUR_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (0,  3,  "00–02"),
    (3,  6,  "03–05"),
    (6,  9,  "06–08"),
    (9,  12, "09–11"),
    (12, 15, "12–14"),
    (15, 18, "15–17"),
    (18, 21, "18–20"),
    (21, 24, "21–23"),
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
            "n_models": len(set(models)),
            "n_lenses": len(set(lenses)),
            "n_lensless_files": lensless_count,
        },
        "by_model": _ranked(models),
        "by_iso_bucket": _bucketed(list(_ISO_BUCKETS), lambda r: _iso_bucket(r.get("iso"))),
        "by_lens": _ranked(lenses),
        # Optional dimensions, populated for --by foo views.
        "by_aperture_bucket": _bucketed(
            [(a, f"f/{a:g}") for a in _STD_APERTURES],
            lambda r: _aperture_bucket(r.get("fnumber")),
        ),
        "by_focal_bucket": _bucketed(list(_FOCAL_BUCKETS), lambda r: _focal_bucket(r.get("focal"))),
        "by_hour_bucket": _bucketed(list(_HOUR_BUCKETS), lambda r: _hour_bucket(r.get("time"))),
        "by_month_bucket": _ranked_chrono(
            [_month_bucket(r.get("date")) for r in records],
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
    Rows are (key, count_str, bar_with_share)."""
    if not rows:
        return ""
    key_w = max(len(r[0]) for r in rows)
    count_w = max(len(r[1]) for r in rows)
    lines = [title, _HRULE]
    for key, count, bar in rows:
        lines.append(f"{key:<{key_w}}  {count:>{count_w}}  {bar}")
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


def render_default(
    stats: dict[str, Any],
    *,
    lens_top: int = 5,
    where: str = "",
) -> str:
    """Render the 4-section default view: 总览 / by model / by ISO / by lens (top N).

    `where` (if given) is shown as a small caption beneath '总览' so a
    subset stats run doesn't look like the whole library.
    """
    total = stats.get("total", {})
    if total.get("count", 0) == 0:
        return "no records"

    # Section 1: total summary as a plain key/value table.
    dr = total.get("date_range", [None, None])
    days = total.get("days_spanned", 0)
    date_str = f"{dr[0]} → {dr[1]}  ({days} 天)" if dr[0] else "-"
    lensless = total.get("n_lensless_files", 0)
    lens_extra = f"  (含 {lensless} 张定焦机)" if lensless else ""

    summary_rows = [
        ("张数",         f"{total['count']}"),
        ("文件总大小",   total.get("bytes_human", "-")),
        ("时间跨度",     date_str),
        ("机型种类",     f"{total.get('n_models', 0)}"),
        ("镜头种类",     f"{total.get('n_lenses', 0)}{lens_extra}"),
    ]
    if where:
        summary_rows.insert(0, ("筛选",     where))
    key_w = max(len(k) for k, _ in summary_rows)
    summary_lines = ["总览", _HRULE]
    for k, v in summary_rows:
        summary_lines.append(f"{k:<{key_w}}  {v}")
    sections = ["\n".join(summary_lines)]

    by_model = stats.get("by_model", [])
    if by_model:
        sections.append(_section("按机型", _build_dist_rows(by_model)))

    by_iso = stats.get("by_iso_bucket", [])
    if by_iso:
        sections.append(_section("按 ISO(对数分桶)", _build_dist_rows(by_iso)))

    by_lens = stats.get("by_lens", [])
    if by_lens:
        title = f"按镜头(top {lens_top})" if len(by_lens) > lens_top else "按镜头"
        rows = _build_dist_rows(by_lens, top=lens_top)
        sec = _section(title, rows)
        if len(by_lens) > lens_top:
            sec += f"\n... 还有 {len(by_lens) - lens_top} 种镜头未显示 (--more / --top N / --by lens 看全)"
        sections.append(sec)

    return "\n\n".join(sections)


_DIMENSIONS = {
    "model":    ("按机型",              "by_model"),
    "lens":     ("按镜头",              "by_lens"),
    "iso":      ("按 ISO",              "by_iso_bucket"),
    "aperture": ("按光圈",              "by_aperture_bucket"),
    "focal":    ("按焦段",              "by_focal_bucket"),
    "hour":     ("按拍摄时段(EXIF 时间,小时)", "by_hour_bucket"),
    "month":    ("按月份",              "by_month_bucket"),
    "day":      ("按月份",              "by_month_bucket"),  # alias
}


def supported_dimensions() -> list[str]:
    return sorted(_DIMENSIONS)


def render_by(
    stats: dict[str, Any],
    dimension: str,
    *,
    top: int | None = None,
    where: str = "",
) -> str:
    """Single-dimension deep view: no top truncation by default, full bar chart.

    `where` (if given) is appended to the section title so subset stats
    don't look like the whole library.
    """
    if dimension not in _DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}; valid: {supported_dimensions()}")
    title, key = _DIMENSIONS[dimension]
    items = stats.get(key, [])
    if not items:
        return f"no data for dimension {dimension!r}"
    n = stats.get("total", {}).get("count", 0)
    if where:
        title = f"{title}  ·  筛: {where}  ·  {n} 张"
    return _section(title, _build_dist_rows(items, top=top))
