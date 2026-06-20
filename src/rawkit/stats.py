"""rawkit stats — OPTIONAL bar-chart drill-down over RAW EXIF.

This module is the addon. `rawkit info` does its own KV summary by
calling `rawkit.aggregate.build_stats` directly; it does NOT import
anything from here. Deleting this file (and the `stats` command
registration in `cli.py`) leaves `info` and the rest of the CLI
working untouched.

What lives here:
  - `render(stats, dims=..., lens_top=..., where=...)` — bar-chart
    rendering for one or more dimensions
  - `render_default` / `render_by` — thin compatibility wrappers
  - `_DIMENSIONS` + `supported_dimensions()` — the dimension catalogue
    used by `rawkit stats --by` argument validation
"""

from __future__ import annotations

from typing import Any

from rawkit.aggregate import _hours_inline


# --- bar chart primitives --------------------------------------------------

_BAR_CHAR = "█"
_BAR_WIDTH = 30  # cells reserved for the bar


def _bar(share: float) -> str:
    cells = max(1, round(share * _BAR_WIDTH))
    return _BAR_CHAR * cells


def _fmt_share(share: float) -> str:
    pct = round(share * 100)
    return f"{pct:>3}%"


_HRULE = "─" * 56


def _section(title: str, rows: list[tuple[str, str, str]]) -> str:
    if not rows:
        return ""
    key_w = max(len(r[0]) for r in rows)
    count_w = max(len(r[1]) for r in rows)
    lines = [title, _HRULE]
    for key, count, bar in rows:
        lines.append(f"{key:<{key_w}}  {count:>{count_w}}  {bar}".rstrip())
    return "\n".join(lines)


def _build_dist_rows(items: list[dict[str, Any]], top: int | None = None) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    display = items if top is None else items[:top]
    for it in display:
        bar = _bar(it["share"])
        bar_padded = bar + " " * (_BAR_WIDTH - len(bar))
        rows.append((str(it["key"]), str(it["count"]), f"{bar_padded}  {_fmt_share(it['share'])}"))
    return rows


def _build_compact_rows(items: list[dict[str, Any]], top: int | None = None) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    display = items if top is None else items[:top]
    for it in display:
        rows.append((str(it["key"]), str(it["count"]), ""))
    return rows


# --- value formatters (private to stats's summary) -------------------------

def _fmt_shutter(s: float | None) -> str:
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


def _enum_inline(items: list[dict[str, Any]], n_distinct: int) -> str:
    if not items:
        return "\u2014"
    top_n = 3
    parts = [f"{it['count']} ({it['key']})" for it in items[:top_n]]
    extra = n_distinct - min(top_n, len(items))
    if extra > 0:
        parts.append(f"+{extra} others")
    return ", ".join(parts)


# --- renderers -------------------------------------------------------------

def _render_summary(stats: dict[str, Any], where: str) -> str:
    """Single consolidated overview block — totals, date range,
    count-only for camera/lens, top-3 enums, and min–max ranges for
    numeric dims. The shape stats's default view emits; `info` has its
    own (parallel but distinct) KV view."""
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
    if dimension not in _DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}; valid: {supported_dimensions()}")
    title, key = _DIMENSIONS[dimension]
    items = stats.get(key, [])
    if not items:
        return ""

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

    `dims=None` → overview block. `dims=[...]` → suppressed overview,
    stacked bar-chart sections.
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


def render_default(stats: dict[str, Any], *, lens_top: int = 5, where: str = "") -> str:
    return render(stats, dims=None, lens_top=lens_top, where=where)


def render_by(
    stats: dict[str, Any],
    dimension: str,
    *,
    top: int | None = None,
    where: str = "",
) -> str:
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
    "model":       ("By camera",                            "by_model"),
    "camera":      ("By camera",                            "by_model"),
    "lens":        ("By lens",                              "by_lens"),
    "maker":       ("By maker",                             "by_maker"),
    "orientation": ("By orientation",                       "by_orientation"),
    "iso":         ("By ISO",                               "by_iso_bucket"),
    "aperture":    ("By aperture",                          "by_fnumber_bucket"),
    "fnumber":     ("By f-number",                          "by_fnumber_bucket"),
    "focal":       ("By focal length",                      "by_focal_bucket"),
    "hour":        ("By hour of day",                       "by_hour_bucket"),
    "year":        ("By year",                              "by_year_bucket"),
    "month":       ("By month",                             "by_month_bucket"),
    "day":         ("By day",                               "by_day_bucket"),
}


_REVERSE_FOR_DISPLAY: frozenset[str] = frozenset()


def supported_dimensions() -> list[str]:
    return sorted(_DIMENSIONS)
