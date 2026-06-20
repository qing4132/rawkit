from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import typer

from rawkit.aggregate import build_stats, _bytes_human, _hours_inline
from rawkit.exif import safe_batch_read
from rawkit.extract import ExtractError, extract_jpeg
from rawkit.query import QueryError, compile_where
from rawkit.render import RenderError, render as render_raw, suffix_for

app = typer.Typer(
    help="rawkit — RAW photography swiss-army CLI",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root() -> None:
    # Keeps typer in subcommand mode even when only one command is registered.
    pass


# Authoritative RAW extension set — intersection of libraw 0.21, dcraw, darktable
# 5.x and RawTherapee. Stills only; cinema/video RAW (R3D, BRAW, ARI) excluded
# because rawkit targets photo workflows. Update this set when a new still camera
# maker introduces a new extension.
RAW_EXTS: frozenset[str] = frozenset({
    ".3fr",   # Hasselblad
    ".arw",   # Sony
    ".bay",   # Casio
    ".cap",   # Phase One (legacy)
    ".cr2",   # Canon (2004–2018)
    ".cr3",   # Canon (2018+)
    ".crw",   # Canon (legacy, pre-2004)
    ".dcr",   # Kodak
    ".dcs",   # Kodak
    ".dng",   # Adobe / Leica / Pentax / Ricoh / Apple ProRAW / DJI / iPhone
    ".drf",   # Kodak
    ".eip",   # Phase One (enhanced)
    ".erf",   # Epson
    ".fff",   # Hasselblad / Imacon
    ".gpr",   # GoPro
    ".iiq",   # Phase One
    ".k25",   # Kodak (DC25)
    ".kdc",   # Kodak
    ".mdc",   # Minolta
    ".mef",   # Mamiya
    ".mos",   # Leaf / Mamiya
    ".mrw",   # Minolta
    ".nef",   # Nikon
    ".nrw",   # Nikon (compact)
    ".orf",   # Olympus / OM System
    ".ori",   # Olympus (legacy)
    ".pef",   # Pentax
    ".ptx",   # Pentax (legacy)
    ".pxn",   # Logitech
    ".raf",   # Fujifilm
    ".raw",   # Panasonic / Leica (legacy generic name)
    ".rw2",   # Panasonic
    ".rwl",   # Leica
    ".rwz",   # Rawzor (compressed wrapper)
    ".sr2",   # Sony (legacy, A100 era)
    ".srf",   # Sony (legacy, F828 / R1)
    ".srw",   # Samsung
    ".x3f",   # Sigma (Foveon)
})


# --- input resolution -------------------------------------------------------

def _collect_raws(inputs: Iterable[Path], recursive: bool) -> list[Path]:
    """Resolve a mix of files and directories to a sorted list of RAW paths.

    Behavior:
    - directory: scan for RAW files. By default only the top level (matching
      Unix `ls`); with `recursive=True` walks the whole subtree (unreadable
      subtrees skipped, symlinks not followed).
    - file with RAW suffix: included as-is
    - file with non-RAW suffix: skipped with a stderr warning
    - non-existent path: stderr error, abort the whole command (exit 1)

    Duplicates (same path reached via multiple args) are removed.
    """
    missing: list[Path] = []
    found: set[Path] = set()

    for inp in inputs:
        if not inp.exists():
            missing.append(inp)
            continue
        if inp.is_dir():
            if recursive:
                for dirpath, _dirnames, filenames in os.walk(
                    inp, onerror=lambda _e: None, followlinks=False
                ):
                    for name in filenames:
                        p = Path(dirpath) / name
                        if p.suffix.lower() in RAW_EXTS:
                            found.add(p)
            else:
                try:
                    for p in inp.iterdir():
                        if p.is_file() and p.suffix.lower() in RAW_EXTS:
                            found.add(p)
                except PermissionError:
                    typer.echo(
                        f"rawkit: {inp}: permission denied", err=True
                    )
        elif inp.is_file():
            if inp.suffix.lower() in RAW_EXTS:
                found.add(inp)
            else:
                typer.echo(
                    f"rawkit: skipping {inp} (not a RAW file)", err=True
                )
        # other (socket, fifo, broken symlink) silently ignored

    if missing:
        for p in missing:
            typer.echo(f"rawkit: {p}: no such file or directory", err=True)
        raise typer.Exit(code=1)

    return sorted(found)


def _output_relpath(raw: Path, inputs: Iterable[Path]) -> Path:
    """Map a discovered RAW back to the output-relative path that mirrors
    its source location.

    Rules:
    - If `raw` was found by walking a directory input `inp`, return
      `raw.relative_to(inp)` — so `samples/2024/foo.CR3` under input
      `samples/` becomes `2024/foo.CR3`, preserving the subdir.
    - If `raw` was passed as a direct file argument (no dir input
      contains it), return `Path(raw.name)` — just the basename.
    - If multiple dir inputs contain it (overlapping inputs), the
      FIRST match wins. This is rare; users don't normally pass
      overlapping dirs.

    This is what lets `extract -R` / `render -R` (and eventually
    `organize`) preserve folder structure in the output instead of
    silently flattening everything into one dir — where two RAWs with
    the same basename in different subdirs would collide.
    """
    for inp in inputs:
        if inp.is_dir():
            try:
                return raw.relative_to(inp)
            except ValueError:
                continue
    return Path(raw.name)


# Soft cap for the file column. A pathologically long name should not
# inflate every other row's padding — it just breaks alignment for that
# one row. 50 chars comfortably fits any in-camera + LrC renamed scheme.
_FILE_COL_SOFT_CAP = 50

# Soft cap for the lens column. Same rationale: one pathological lens
# name (e.g. Panasonic's "DC VARIO-SUMMILUX 1:1.7-2.8/10.9-34 ASPH.",
# 41 chars) shouldn't widen the column for every other row. Most
# real-world lens names — including verbose ones like
# "Apo-Summicron-M 1:2/50 ASPH." (28) or "RF24-105mm F2.8 L IS USM Z"
# (26) — fit under 32.
_LENS_COL_SOFT_CAP = 32


def _fmt_datetime(v: Any) -> str:
    """`2024-01-02 03:04:05` → `2024-01-02 03:04` (minute precision for table).

    Full datetime including seconds is preserved in the underlying record
    and exposed via --json and the `datetime` / `time` --where fields.
    """
    if not v:
        return "-"
    s = str(v).partition(".")[0]
    if len(s) >= 16 and s[10] == " ":
        return s[:16]
    return s


def _fmt_iso(v: Any) -> str:
    if v is None:
        return "-"
    try:
        return str(int(v))
    except (TypeError, ValueError):
        return str(v)


def _fmt_fnumber(v: Any) -> str:
    if v is None:
        return "-"
    try:
        return f"f/{float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_shutter(v: Any) -> str:
    """0.00625 → '1/160'; 2.0 → '2s'; 0.999 → '1s' (never '1/1')."""
    if v is None:
        return "-"
    try:
        s = float(v)
    except (TypeError, ValueError):
        return str(v)
    if s >= 1:
        return f"{s:g}s"
    denom = round(1 / s)
    if denom <= 1:
        return f"{s:g}s"
    return f"1/{denom}"


def _fmt_focal(v: Any) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):g}mm"
    except (TypeError, ValueError):
        return str(v)


def _fmt_bias(v: Any) -> str:
    """0 → '0'; +1 → '+1'; -2.41667 → '-2.42'; absent → '-'."""
    if v is None:
        return "-"
    try:
        b = round(float(v), 2)
    except (TypeError, ValueError):
        return str(v)
    if b == 0:
        return "0"
    return f"{b:+g}"  # the + format spec keeps signs on positive values too


_TABLE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # (header, normalized key, alignment)  alignment: 'l' = left, 'r' = right
    # Order: identity → framing (lens + focal) → exposure quartet.
    # ISO last on purpose: it has the widest range (100..102400) so its
    # right-aligned magnitude is easy to scan along the table's right edge.
    ("file",     "_filename", "l"),
    ("datetime", "datetime",  "l"),
    ("model",    "model",     "l"),
    ("lens",     "lens",      "l"),
    ("focal",    "focal",     "r"),
    ("aperture", "fnumber",   "r"),
    ("shutter",  "shutter",   "r"),
    ("bias",     "bias",      "r"),
    ("iso",      "iso",       "r"),
)

_FORMATTERS = {
    "datetime": _fmt_datetime,
    "iso":      _fmt_iso,
    "fnumber":  _fmt_fnumber,
    "shutter":  _fmt_shutter,
    "focal":    _fmt_focal,
    "bias":     _fmt_bias,
}

_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"


def _color_enabled() -> bool:
    """True iff we should emit ANSI color/style codes.

    Honors the no-color.org standard: any value of NO_COLOR (even empty)
    disables color, regardless of TTY. Also disables on non-TTY (pipes).
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    return sys.stdout.isatty()


# Maps a sort key to the visible header it should highlight. Time-precision
# variants (date, time) point at the `datetime` column — it's the closest
# visible representation of what the user sorted by.
_SORT_HEADER_MAP: dict[str, str] = {
    "file":     "file",
    "datetime": "datetime",
    "date":     "datetime",
    "time":     "datetime",
    "model":    "model",
    "lens":     "lens",
    "focal":    "focal",
    "aperture": "aperture",
    "fnumber":  "aperture",  # `--sort fnumber` highlights the same column
    "shutter":  "shutter",
    "bias":     "bias",
    "iso":      "iso",
}


def _render_table(
    records: Iterable[dict[str, Any]],
    sort_keys: list[SortKey],
    reverse: bool,
) -> None:
    """Render an aligned, content-width table on stdout.

    Column widths follow each column's natural max value, with two
    exceptions: the file and lens columns have a soft cap. Values that
    exceed the cap don't widen the column for every other row —
    instead they **wrap within their own column** and emit continuation
    lines whose other columns are left blank (only the wrapping cell
    has text). Normal rows still render as a single physical line.

    There is no overall line-width cap; we don't try to fit the table to
    the terminal. Use `| less -S` for horizontal scroll if needed.

    When stdout is a TTY (and NO_COLOR isn't set) the header row is bold,
    and the active sort column gets an ASC/DESC arrow suffix. We do NOT
    color any cells — not even the sort header — because color is too
    easily read as a value judgment, and the arrow already carries the
    'which column is sorted' information without ambiguity.
    """
    records = list(records)
    if not records:
        return

    rows: list[tuple[str, ...]] = []
    for r in records:
        row: list[str] = []
        for _header, key, _align in _TABLE_COLUMNS:
            if key == "_filename":
                row.append(Path(r.get("path", "")).name)
            elif key in _FORMATTERS:
                row.append(_FORMATTERS[key](r.get(key)))
            else:
                row.append(str(r.get(key) or "-"))
        rows.append(tuple(row))

    # Build headers — the PRIMARY sort key's header gets an arrow suffix
    # (secondary keys are not visually marked, to avoid header clutter).
    arrow = "\u2193" if reverse else "\u2191"  # ↓ desc / ↑ asc
    primary_key = sort_keys[0].value if sort_keys else "datetime"
    active_header_name = _SORT_HEADER_MAP[primary_key]
    headers: list[str] = []
    for h, _k, _a in _TABLE_COLUMNS:
        headers.append(h + arrow if h == active_header_name else h)

    widths = [max(len(s) for s in col) for col in zip(headers, *rows)]

    def _soft_capped_width(values: list[str], cap: int) -> int:
        """Width = widest 'normal' value; outliers (> cap) wrap inside
        the column instead of widening it for everyone else."""
        normal = [v for v in values if len(v) <= cap]
        return max(len(v) for v in normal) if normal else cap

    # File column (index 0) and lens column both get the soft-cap treatment.
    file_col_idx = 0
    lens_col_idx = next(i for i, (_h, k, _a) in enumerate(_TABLE_COLUMNS) if k == "lens")
    widths[file_col_idx] = _soft_capped_width(
        [headers[file_col_idx], *(row[file_col_idx] for row in rows)],
        _FILE_COL_SOFT_CAP,
    )
    widths[lens_col_idx] = _soft_capped_width(
        [headers[lens_col_idx], *(row[lens_col_idx] for row in rows)],
        _LENS_COL_SOFT_CAP,
    )

    # When stdout is a TTY, expand the lens column to fill the terminal
    # (if there's room). This way the user can widen the terminal and the
    # wrapped lens cell collapses back to one line. When piping, give it
    # the full natural width so the consumer sees the whole value.
    n_cols = len(_TABLE_COLUMNS)
    sep = "  "
    sep_total = len(sep) * (n_cols - 1)
    other_total = sum(w for i, w in enumerate(widths) if i != lens_col_idx)
    natural_lens = max(
        len(headers[lens_col_idx]),
        max((len(row[lens_col_idx]) for row in rows), default=0),
    )
    if sys.stdout.isatty():
        term_w = shutil.get_terminal_size((120, 24)).columns
        widths[lens_col_idx] = max(
            _LENS_COL_SOFT_CAP,
            min(natural_lens, term_w - other_total - sep_total),
        )
    else:
        widths[lens_col_idx] = natural_lens

    use_color = _color_enabled()
    aligns = [a for _h, _k, a in _TABLE_COLUMNS]

    def fmt_cell(text: str, width: int, align: str) -> str:
        return f"{text:>{width}}" if align == "r" else f"{text:<{width}}"

    def ansi(s: str, codes: str) -> str:
        # ANSI wrap a pre-padded cell. The codes don't change visible width;
        # terminals render them as zero-width. So padding-then-wrap is safe.
        return f"{codes}{s}{_RESET}" if use_color and codes else s

    def emit_row(cells: list[str], *, bold: bool = False) -> None:
        # Wrap each cell into physical lines that fit its column. Cells
        # whose content already fits stay as a single line, so normal
        # rows still print on one line. break_long_words=True so a single
        # huge token still fits; break_on_hyphens=False so 'RF24-105mm'
        # doesn't split at the hyphen.
        wrapped: list[list[str]] = [
            textwrap.wrap(
                cells[i],
                width=max(widths[i], 1),
                break_long_words=True,
                break_on_hyphens=False,
            ) or [""]
            for i in range(n_cols)
        ]
        height = max(len(w) for w in wrapped)
        for line_idx in range(height):
            line_parts: list[str] = []
            for col_idx in range(n_cols):
                text = wrapped[col_idx][line_idx] if line_idx < len(wrapped[col_idx]) else ""
                padded = fmt_cell(text, widths[col_idx], aligns[col_idx])
                if bold:
                    padded = ansi(padded, _BOLD)
                line_parts.append(padded)
            typer.echo(sep.join(line_parts).rstrip(), color=use_color)

    emit_row(list(headers), bold=True)
    for row in rows:
        emit_row(list(row))


def _emit_jsonl(records: Iterable[dict[str, Any]]) -> None:
    for r in records:
        typer.echo(json.dumps(r, ensure_ascii=False))


def _parse_by_dim(by: str) -> str | None:
    """Validate a single-dim --by spec. Returns the dim key, or None when
    by is empty. Multi-dim (comma-separated) is rejected as not yet built
    — future: hierarchical partition chain matching `organize --by`."""
    if not by:
        return None
    if "," in by:
        typer.echo(
            "rawkit: --by: multi-dim chain not yet supported "
            "(planned: hierarchical partition matching organize)",
            err=True,
        )
        raise typer.Exit(code=2)
    dim = by.strip().lower()
    if dim not in _INFO_BY_DIMS:
        typer.echo(
            f"rawkit: --by: unknown dimension {dim!r}; valid: "
            f"{', '.join(sorted(_INFO_BY_DIMS))}",
            err=True,
        )
        raise typer.Exit(code=2)
    return dim


# Maps --by dimension name → (display title, aggregate key from build_stats).
# info owns these labels; aggregate.py only owns the data shape.
_INFO_BY_DIMS: dict[str, tuple[str, str]] = {
    "model":       ("Camera",       "by_model"),
    "camera":      ("Camera",       "by_model"),  # alias
    "lens":        ("Lens",         "by_lens"),
    "maker":       ("Maker",        "by_maker"),
    "orientation": ("Orientation",  "by_orientation"),
    "iso":         ("ISO",          "by_iso_bucket"),
    "aperture":    ("Aperture",     "by_fnumber_bucket"),
    "fnumber":     ("Aperture",     "by_fnumber_bucket"),  # alias
    "focal":       ("Focal length", "by_focal_bucket"),
    "shutter":     ("Shutter",      "by_shutter_bucket"),
    "bias":        ("Bias",         "by_bias_bucket"),
    "rating":      ("Rating",       "by_rating_bucket"),
    "hour":        ("Hour",         "by_hour_bucket"),
    "year":        ("Year",         "by_year_bucket"),
    "month":       ("Month",        "by_month_bucket"),
    "day":         ("Day",          "by_day_bucket"),
}


def _render_info_by(stats: dict[str, Any], dim: str, *, top: int, where: str) -> str:
    """Single-dim partition view: title + indented bucket rows.

    No bars, no horizontal rule. Plain count and percent share, aligned.
    `top` truncates only the unbounded `lens` dimension; bounded dims
    (camera count, ISO/aperture/focal/hour/orientation buckets) ignore it.
    """
    title, stats_key = _INFO_BY_DIMS[dim]
    items = stats.get(stats_key, [])
    if not items:
        head = [title]
        if where:
            head.append(f"  filter: {where}")
        head.append("")
        head.append("  no data")
        return "\n".join(head)

    apply_top = dim == "lens"
    display = items
    hidden: list[dict[str, Any]] = []
    if apply_top and top > 0 and len(items) > top:
        display = items[:top]
        hidden = items[top:]

    rows: list[tuple[str, str, str]] = []
    for it in display:
        pct = round(it["share"] * 100)
        rows.append((str(it["key"]), str(it["count"]), f"{pct}%"))
    if hidden:
        others_count = sum(it["count"] for it in hidden)
        others_share = sum(it["share"] for it in hidden)
        rows.append((
            f"+{len(hidden)} others",
            str(others_count),
            f"{round(others_share * 100)}%",
        ))

    key_w = max(len(k) for k, _, _ in rows)
    count_w = max(len(c) for _, c, _ in rows)

    lines = [title]
    if where:
        lines.append(f"  filter: {where}")
    lines.append("")
    for k, c, p in rows:
        lines.append(f"  {k:<{key_w}}  {c:>{count_w}}  {p}")
    return "\n".join(lines)


def _build_info_file_record(raw: Path, record: dict[str, Any]) -> dict[str, Any]:
    st = raw.stat()
    out: dict[str, Any] = {
        "path": str(raw),
        "size_bytes": int(st.st_size),
        "size_human": _bytes_human(int(st.st_size)),
    }

    # Stable, human-first field order. Only present keys are added.
    for key in (
        "datetime", "date", "time",
        "maker", "model", "lens",
        "iso", "fnumber", "shutter", "focal", "bias", "rating",
        "orientation", "flash",
        "image_width", "image_height", "preview_width", "preview_height",
        "gps", "gps_lat", "gps_lon",
    ):
        if key in record:
            out[key] = record[key]

    if "datetime" not in out:
        d = out.get("date")
        t = out.get("time")
        if d and t:
            out["datetime"] = f"{d} {t}"
        elif d:
            out["datetime"] = str(d)
        elif t:
            out["datetime"] = str(t)

    preview_w = out.pop("preview_width", None)
    preview_h = out.pop("preview_height", None)

    lat = out.get("gps_lat")
    lon = out.get("gps_lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        out["gps_text"] = f"{float(lat):.6f}, {float(lon):.6f}"
    else:
        out["gps_text"] = "-"

    return out


def _inspect_embedded_jpegs(raw: Path) -> list[str]:
    try:
        r = extract_jpeg(raw)
    except ExtractError as e:
        return [f"unavailable ({e})"]
    return [f"JPEG {r.width}x{r.height} ({_bytes_human(len(r.data))})"]


def _format_extent(lo: Any, hi: Any, fmt) -> str:
    if lo is None:
        return "-"
    if lo == hi:
        return fmt(lo)
    return f"{fmt(lo)} \u2013 {fmt(hi)}"


def _format_enum_inline(items: list[dict[str, Any]], n_distinct: int, max_list: int = 3) -> str:
    """List names directly when count is small; otherwise show count + top-N + '+M others'."""
    if not items:
        return "-"
    if n_distinct <= max_list:
        return ", ".join(it["key"] for it in items[:n_distinct])
    head = ", ".join(it["key"] for it in items[:max_list])
    extra = n_distinct - max_list
    return f"{n_distinct} ({head}, +{extra} others)"


def _fit_enum_inline(items: list[dict[str, Any]], n_distinct: int,
                    max_width: int | None) -> str:
    """Largest `_format_enum_inline(max_list=k)` (k=3,2,1,0) that fits within
    `max_width`. Falls back to k=0 (just the count + top-1 if possible, else
    just the count) when no smaller form fits. `max_width=None` disables fitting."""
    candidates = []
    for k in (3, 2, 1, 0):
        candidates.append(_format_enum_inline(items, n_distinct, max_list=k))
    if max_width is None:
        return candidates[0]
    for s in candidates:
        if len(s) <= max_width:
            return s
    return candidates[-1]


def _format_count_pairs(items: list[dict[str, Any]]) -> str:
    """`22 (landscape), 3 (portrait)` style — matches stats's existing orientation row."""
    if not items:
        return "-"
    return ", ".join(f"{it['count']} ({it['key']})" for it in items)


def _format_bool_pairs(records: list[dict[str, Any]], field: str,
                      yes_label: str, no_label: str) -> str:
    """For booleans tagged on each record: e.g. flash on/off, gps yes/no.
    Records where the field is missing are bucketed under `no` — a missing
    GPS tag means 'not geotagged', a missing Flash tag means 'didn't fire'."""
    if not records:
        return "-"
    yes_n = sum(1 for r in records if r.get(field) is True)
    no_n = len(records) - yes_n
    return f"{yes_n} ({yes_label}), {no_n} ({no_label})"


def _render_info_dir(stats: dict[str, Any], records: list[dict[str, Any]],
                     path_display: str, where: str) -> str:
    """Vertical KV description of a folder, parallel to single-file info.

    Same shape as info FILE: 'this is what you're looking at', not a
    distribution / bar-chart analysis. For drill-down per dimension, use
    `rawkit stats --by ...`.
    """
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
        date_str = "-"

    files_line = (
        f"{total['count']} RAW{'s' if total['count'] != 1 else ''}"
        f" ({total.get('bytes_human', '-')})"
    )

    n_models = total.get("n_models", 0)
    n_lenses = total.get("n_lenses", 0)
    by_maker = stats.get("by_maker", [])

    # Width-aware: fit Maker / Camera / Lens lines to terminal so they never
    # wrap. Drop names progressively (3 → 2 → 1 → 0) until it fits.
    # Non-TTY (piped) output skips fitting — consumers want full info.
    longest_label = 12  # "Focal length" / "Orientation"
    label_col = longest_label + 2  # "  " separator after label
    if sys.stdout.isatty():
        term_w = shutil.get_terminal_size((120, 24)).columns
        value_max = max(20, term_w - label_col)
    else:
        value_max = None

    cameras_line = _fit_enum_inline(stats.get("by_model", []), n_models, value_max)
    lenses_line = _fit_enum_inline(stats.get("by_lens", []), n_lenses, value_max)
    makers_line = _fit_enum_inline(by_maker, len(by_maker), value_max)

    # Bias / rating extents — computed directly from records since stats
    # doesn't aggregate them today.
    biases = [r["bias"] for r in records if isinstance(r.get("bias"), (int, float))]
    if biases:
        bias_line = _format_extent(min(biases), max(biases),
                                   lambda v: f"{_fmt_bias(v)} EV")
    else:
        bias_line = "-"

    # Rating distribution: count per star rating (1..5) plus an 'unrated'
    # bucket. Rating=0 is treated as unrated (matches how LrC / Bridge /
    # Photo Mechanic display "no star" — 0 stars = not yet rated).
    # Order: unrated first, then ascending by rating value; empty buckets
    # are skipped (matches how ISO/aperture buckets are rendered).
    rating_counts: dict[int, int] = {}
    unrated = 0
    for r in records:
        rv = r.get("rating")
        if isinstance(rv, (int, float)) and int(rv) > 0:
            rating_counts[int(rv)] = rating_counts.get(int(rv), 0) + 1
        else:
            unrated += 1

    parts = []
    if unrated:
        parts.append(f"{unrated} (unrated)")
    for k in sorted(rating_counts):
        parts.append(f"{rating_counts[k]} ({k})")
    rating_line = ", ".join(parts) if parts else "-"

    orient_line = _format_count_pairs(stats.get("by_orientation", []))
    flash_line = _format_bool_pairs(records, "flash", "on", "off")
    gps_line = _format_bool_pairs(records, "gps", "yes", "no")

    rows: list[tuple[str, str]] = [("Path", path_display)]
    if where:
        rows.append(("Filter", where))
    rows.extend([
        ("File", files_line),
        ("Date range", date_str),
        ("Hour", _hours_inline(total.get("hours_present", []))),
        ("Maker", makers_line),
        ("Camera", cameras_line),
        ("Lens", lenses_line),
        ("ISO", _format_extent(total.get("iso_min"), total.get("iso_max"), _fmt_iso)),
        ("Aperture", _format_extent(total.get("fnumber_min"), total.get("fnumber_max"), _fmt_fnumber)),
        ("Shutter", _format_extent(total.get("shutter_min"), total.get("shutter_max"), _fmt_shutter)),
        ("Focal length", _format_extent(total.get("focal_min"), total.get("focal_max"), _fmt_focal)),
        ("Bias", bias_line),
        ("Rating", rating_line),
        ("Orientation", orient_line),
        ("Flash", flash_line),
        ("GPS", gps_line),
    ])

    width = max(len(k) for k, _ in rows)
    return "\n".join(f"{k:<{width}}  {v}" for k, v in rows)


def _render_info_file(record: dict[str, Any]) -> str:
    rows = [
        ("Path", record.get("path", "-")),
        ("Size", f"{record.get('size_human', '-')} ({record.get('size_bytes', '-')} B)"),
        ("DateTime", record.get("datetime", "-")),
        ("Maker", record.get("maker", "-")),
        ("Camera", record.get("model", "-")),
        ("Lens", record.get("lens", "-")),
        ("ISO", record.get("iso", "-")),
        ("Aperture", f"f/{record['fnumber']:g}" if isinstance(record.get("fnumber"), (int, float)) else record.get("fnumber", "-")),
        ("Shutter", _fmt_shutter(record.get("shutter"))),
        ("Focal length", _fmt_focal(record.get("focal"))),
        ("Bias", f"{_fmt_bias(record.get('bias'))} EV" if record.get("bias") is not None else "-"),
        ("Rating", record.get("rating", "-")),
        ("Orientation", record.get("orientation", "-")),
        ("Flash", record.get("flash", "-")),
        ("Image", f"{record.get('image_width', '-')}x{record.get('image_height', '-')}"),
        ("GPS", record.get("gps_text", "-")),
    ]
    if "embedded_jpegs" in record:
        rows.append(("Embedded", "; ".join(record.get("embedded_jpegs", [])) or "-"))
    width = max(len(k) for k, _ in rows)
    return "\n".join(f"{k:<{width}}  {v}" for k, v in rows)


# --- sorting ----------------------------------------------------------------

class SortKey(str, Enum):
    """All column headers from the default table are accepted as sort keys,
    plus the three time slices (datetime/date/time) so the user can pick
    precision. `fnumber` is a (positive-numeric, reversed-direction) alias
    of `aperture` for users who'd rather sort by EXIF FNumber value."""
    file     = "file"
    datetime = "datetime"
    date     = "date"
    time     = "time"
    model    = "model"
    lens     = "lens"
    focal    = "focal"
    aperture = "aperture"
    fnumber  = "fnumber"
    shutter  = "shutter"
    bias     = "bias"
    iso      = "iso"


# Per-sort-key extractor: returns the value to compare, or None if missing.
# Strings are lowercased so case differences don't reorder rows.
#
# aperture and fnumber sort the SAME WAY here (by fnumber numeric ascending,
# i.e. f/1.4 → f/22). The photographer-direction inversion of aperture lives
# only in --where (where 'aperture>=2.8' means 'wider than f/2.8'). Sort and
# stats --by use the same canonical fnumber order to avoid maintaining two
# directions for the same data.
_SORT_EXTRACTORS: dict[SortKey, Any] = {
    SortKey.file:     lambda r: Path(r["path"]).name.lower() if r.get("path") else None,
    SortKey.datetime: lambda r: r.get("datetime"),
    SortKey.date:     lambda r: r.get("date"),
    SortKey.time:     lambda r: r.get("time"),
    SortKey.model:    lambda r: r["model"].lower() if r.get("model") else None,
    SortKey.lens:     lambda r: r["lens"].lower() if r.get("lens") else None,
    SortKey.focal:    lambda r: r.get("focal"),
    SortKey.aperture: lambda r: r.get("fnumber"),
    SortKey.fnumber:  lambda r: r.get("fnumber"),
    SortKey.shutter:  lambda r: r.get("shutter"),
    SortKey.bias:     lambda r: r.get("bias"),
    SortKey.iso:      lambda r: r.get("iso"),
}


def _sort_records(
    records: list[dict[str, Any]],
    keys: list[SortKey],
    reverse: bool,
) -> list[dict[str, Any]]:
    """Sort by a sequence of keys (primary, secondary, ...).

    NULLS LAST semantics per key, applied hierarchically:
      - records with a missing primary key go after everything else
      - within a same-primary group, records with a missing secondary key
        sort after the rest of that group
      - ...and so on for further keys

    `reverse` flips the comparison direction; it applies to ALL keys
    (per-key direction is a possible future extension).
    """
    if not keys or not records:
        return list(records)

    primary, *rest = keys
    extract = _SORT_EXTRACTORS[primary]
    haves: list[tuple[Any, dict[str, Any]]] = []
    misses: list[dict[str, Any]] = []
    for r in records:
        v = extract(r)
        if v is None:
            misses.append(r)
        else:
            haves.append((v, r))
    haves.sort(key=lambda pair: pair[0], reverse=reverse)

    if not rest:
        sorted_have_records = [r for _, r in haves]
    else:
        # Tie-break on secondary keys within groups of equal primary.
        from itertools import groupby
        sorted_have_records = []
        for _, grp in groupby(haves, key=lambda pair: pair[0]):
            grp_records = [r for _, r in grp]
            if len(grp_records) > 1:
                sorted_have_records.extend(_sort_records(grp_records, rest, reverse))
            else:
                sorted_have_records.extend(grp_records)

    return sorted_have_records + misses


def _filter_paths_by_where(raws: list[Path], where_expr: str) -> list[Path]:
    """Filter `raws` to those whose EXIF satisfies the --where predicate.

    Shared by extract/render so they can accept the same DSL as `ls`. Reads
    EXIF for the candidate paths in ONE exiftool invocation, applies the
    compiled predicate, and returns the surviving paths in the original
    order. Returns `raws` unchanged when `where_expr` is empty.

    Exits with code 2 (usage error) on a malformed DSL expression, matching
    `ls --where`'s behaviour.
    """
    if not where_expr:
        return raws
    try:
        predicate = compile_where(where_expr)
    except QueryError as e:
        typer.echo(f"rawkit: --where: {e}", err=True)
        raise typer.Exit(code=2)
    records = safe_batch_read(raws)
    # exiftool keys records by absolute SourceFile; map by string for lookup.
    by_path = {r["path"]: r for r in records}
    return [r for r in raws if predicate(by_path.get(str(r), {}))]


def _parse_sort_keys(spec: str) -> list[SortKey]:
    """Parse a comma-separated --sort value into a list of SortKey enum members.

    'datetime'        -> [datetime]
    'model,datetime'  -> [model, datetime]

    Raises typer.BadParameter on invalid / empty / duplicate keys so the user
    gets the standard typer usage-error treatment.
    """
    raw = [s.strip().lower() for s in spec.split(",") if s.strip()]
    if not raw:
        raise typer.BadParameter("empty --sort spec")
    valid = {k.value for k in SortKey}
    keys: list[SortKey] = []
    seen: set[str] = set()
    for name in raw:
        if name not in valid:
            options = ", ".join(sorted(valid))
            raise typer.BadParameter(
                f"unknown sort key {name!r}. Valid keys: {options}"
            )
        if name in seen:
            raise typer.BadParameter(f"duplicate sort key {name!r}")
        seen.add(name)
        keys.append(SortKey(name))
    return keys


# --- ls command -------------------------------------------------------------

@app.command()
def ls(
    paths: list[Path] = typer.Argument(
        None,
        help="Files or directories to scan. Defaults to current directory. "
             "Directories are listed top-level only unless -R is given; "
             "files must have a RAW suffix.",
    ),
    where: str = typer.Option(
        "",
        "--where",
        "-w",
        metavar="EXPR",
        help="Filter rows by an EXIF predicate. "
             "Examples: 'iso>3200 and lens~\"50\"', 'date>=\"2024-01-01\"'.",
    ),
    sort: str = typer.Option(
        "datetime",
        "--sort",
        "-s",
        metavar="KEY[,KEY2,...]",
        help="Column(s) to sort by. Comma-separated for primary,secondary,... "
             "Missing values always sort to the end (NULLS LAST). "
             "Valid: file, datetime, date, time, model, lens, focal, aperture, "
             "shutter, bias, iso.",
    ),
    reverse: bool = typer.Option(
        False,
        "--reverse",
        "-r",
        help="Reverse the sort order.",
    ),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        "-R",
        help="Recurse into subdirectories (default is top-level only, matching `ls`).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit JSONL on stdout (one object per file) instead of an aligned table.",
    ),
) -> None:
    """List RAW files under the given paths with their key EXIF.

    Default output is an aligned, human-readable table. Use --json to pipe the
    output into jq or other tooling.
    """
    inputs = paths if paths else [Path(".")]
    raws = _collect_raws(inputs, recursive=recursive)
    if not raws:
        return

    sort_keys = _parse_sort_keys(sort)

    records = safe_batch_read(raws)

    if where:
        try:
            predicate = compile_where(where)
        except QueryError as e:
            typer.echo(f"rawkit: --where: {e}", err=True)
            raise typer.Exit(code=2)  # 2 = usage error (matches grep/find)
        records = [r for r in records if predicate(r)]
        if not records:
            return

    records = _sort_records(records, sort_keys, reverse)

    if as_json:
        _emit_jsonl(records)
    else:
        _render_table(records, sort_keys=sort_keys, reverse=reverse)


# --- extract command --------------------------------------------------------

@app.command()
def extract(
    paths: list[Path] = typer.Argument(
        None,
        help="Files or directories to extract embedded JPEGs from. Defaults to current "
             "directory. Directories are listed top-level only unless -R.",
    ),
    output: Path = typer.Option(
        Path("./jpegs"),
        "--output",
        "-o",
        metavar="DIR",
        help="Output directory. Created if missing. Each extracted JPEG is written as "
             "<DIR>/<basename>.jpg (basename = source stem).",
    ),
    long_edge: int = typer.Option(
        0,
        "--long",
        metavar="N",
        help="Downscale so the LONG edge is at most N pixels (LANCZOS). "
             "Triggers a JPEG decode+re-encode (slight quality loss). "
             "Mutually exclusive with --short / --mp.",
    ),
    short_edge: int = typer.Option(
        0,
        "--short",
        metavar="N",
        help="Downscale so the SHORT edge is at most N pixels. Useful for "
             "social-media sizing (Instagram = 1080 short). "
             "Mutually exclusive with --long / --mp.",
    ),
    megapixels: float = typer.Option(
        0.0,
        "--mp",
        metavar="N",
        help="Downscale so total pixels ≤ N million. e.g. --mp 12 ≈ 12-megapixel "
             "output. Mutually exclusive with --long / --short.",
    ),
    quality: int = typer.Option(
        90,
        "--quality",
        "-q",
        min=1,
        max=100,
        help="JPEG quality (1-100) for the re-encoded output. Only consulted "
             "when one of --long/--short/--mp is set.",
    ),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        "-R",
        help="Recurse into subdirectories (default is top-level only).",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        "-f",
        help="Overwrite existing output files. Default: skip with a warning.",
    ),
    where: str = typer.Option(
        "",
        "--where",
        "-w",
        metavar="EXPR",
        help="Filter inputs by an EXIF predicate (same DSL as `ls --where`). "
             "Examples: 'iso>3200 and lens~\"50\"', 'date>=\"2024-01-01\"'. "
             "Triggers one exiftool call to read EXIF for the candidate set.",
    ),
) -> None:
    """Extract each RAW's largest embedded SOOC JPEG.

    Always returns the camera's in-RAW JPEG (100% SOOC colour science).
    Uses libraw (via rawpy) to reach whatever the camera embedded —
    typically the full-resolution SOOC frame for Canon CR3 / Sony A1+ /
    Nikon Z…, the 3000-class reduced-resolution frame for Hasselblad 3FR,
    or the 1080-class JPEG for older Sony ARW.

    The 160x120-class navigation thumbnail is intentionally not used.

    \b
    Three mutually-exclusive resize options (when set, the embedded JPEG
    is decoded and LANCZOS-downscaled before re-encoding at --quality):
      --long N    — long edge ≤ N px
      --short N   — short edge ≤ N px (social-media sizing)
      --mp N      — total pixels ≤ N million

    Images already smaller than the target are written unchanged
    (no upscaling).

    Progress and per-file outcomes are reported on stderr; stdout is left
    empty so you can pipe `find … | xargs rawkit extract` without surprises.
    """
    # Validate the mutually-exclusive resize options upfront, before doing
    # any I/O — typer-level rejection so the user gets a usage error (exit 2).
    set_dims = [
        name for name, val in (("--long", long_edge), ("--short", short_edge), ("--mp", megapixels))
        if val
    ]
    if len(set_dims) > 1:
        raise typer.BadParameter(
            f"{' / '.join(set_dims)} are mutually exclusive — pick one"
        )

    inputs = paths if paths else [Path(".")]
    raws = _collect_raws(inputs, recursive=recursive)
    if not raws:
        return
    raws = _filter_paths_by_where(raws, where)
    if not raws:
        return

    output.mkdir(parents=True, exist_ok=True)

    # Precompute every output path and fail-fast on intra-run collisions
    # (two distinct RAWs in this invocation would write to the same jpg).
    # This is different from "file already exists on disk from a previous
    # run" — that case is handled per-file below with skip / --overwrite.
    # An intra-run collision is silent data loss either way, so we refuse
    # to extract anything until the user resolves it.
    #
    # Keys are case-folded paths: on macOS APFS (default case-insensitive)
    # and Windows, foo.jpg and Foo.jpg map to the SAME file on disk, so
    # the second write would silently overwrite the first. We treat them
    # as colliding even on case-sensitive filesystems (Linux ext4) — two
    # outputs differing only in case are too fragile to be intentional.
    out_paths: list[Path] = [
        (output / _output_relpath(r, inputs)).with_suffix(".jpg") for r in raws
    ]
    collisions: dict[str, list[tuple[Path, Path]]] = {}
    for raw, out_path in zip(raws, out_paths):
        key = str(out_path).casefold()
        collisions.setdefault(key, []).append((out_path, raw))
    duplicated = {k: pairs for k, pairs in collisions.items() if len(pairs) > 1}
    if duplicated:
        typer.echo(
            f"rawkit: refusing to extract — {len(duplicated)} output collision(s):",
            err=True,
        )
        for pairs in duplicated.values():
            unique_outs = sorted({op for op, _ in pairs}, key=str)
            head = str(unique_outs[0])
            if len(unique_outs) > 1:
                head += f"  (case variants: {', '.join(p.name for p in unique_outs[1:])})"
            typer.echo(f"  {head}", err=True)
            for _, src in pairs:
                typer.echo(f"    \u2190 {src}", err=True)
        typer.echo(
            "Hint: pass the conflicting RAWs via a common parent dir with -R "
            "so they land under distinct subdirs, or rename one source.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Convert "0 means unset" CLI defaults to the None the extractor expects.
    long_arg: int | None = long_edge if long_edge > 0 else None
    short_arg: int | None = short_edge if short_edge > 0 else None
    mp_arg: float | None = megapixels if megapixels > 0 else None

    n_ok = 0
    n_skipped = 0
    n_failed = 0
    for raw, out_path in zip(raws, out_paths):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists() and not overwrite:
            typer.echo(
                f"{raw.name}: skip (exists, use -f to overwrite)", err=True
            )
            n_skipped += 1
            continue
        try:
            result = extract_jpeg(
                raw,
                long_edge=long_arg,
                short_edge=short_arg,
                megapixels=mp_arg,
                quality=quality,
            )
        except ExtractError as e:
            typer.echo(f"{raw.name}: failed — {e}", err=True)
            n_failed += 1
            continue
        out_path.write_bytes(result.data)
        typer.echo(
            f"{raw.name}: {result.width}x{result.height} -> {out_path}",
            err=True,
        )
        n_ok += 1

    if n_failed or n_skipped:
        typer.echo(
            f"\n{n_ok} extracted, {n_skipped} skipped, {n_failed} failed",
            err=True,
        )
    if n_failed:
        raise typer.Exit(code=1)


# --- render command ---------------------------------------------------------

class RenderFormat(str, Enum):
    """Output formats render can produce. JPEG = small/lossy;
    TIFF/PNG = lossless (PNG is smaller for low-entropy images, TIFF for
    photographic content; both are appropriate for archival hand-off)."""
    jpeg = "jpeg"
    tiff = "tiff"
    png  = "png"


@app.command("render")
def cmd_render(
    paths: list[Path] = typer.Argument(
        None,
        help="Files or directories to render. Defaults to current directory. "
             "Directories are listed top-level only unless -R.",
    ),
    output: Path = typer.Option(
        Path("./renders"),
        "--output",
        "-o",
        metavar="DIR",
        help="Output directory. Created if missing. Each render is written as "
             "<DIR>/<basename>.<ext> (ext from --format).",
    ),
    output_format: RenderFormat = typer.Option(
        RenderFormat.jpeg,
        "--format",
        case_sensitive=False,
        help="Output container.",
    ),
    quality: int = typer.Option(
        90,
        "--quality",
        "-q",
        min=1,
        max=100,
        help="JPEG quality (1-100). Ignored for TIFF/PNG (lossless).",
    ),
    long_edge: int = typer.Option(
        0,
        "--long",
        metavar="N",
        help="Downscale so the LONG edge is at most N pixels (LANCZOS). "
             "Mutually exclusive with --short / --mp.",
    ),
    short_edge: int = typer.Option(
        0,
        "--short",
        metavar="N",
        help="Downscale so the SHORT edge is at most N pixels. Useful for "
             "social-media sizing (Instagram = 1080 short). "
             "Mutually exclusive with --long / --mp.",
    ),
    megapixels: float = typer.Option(
        0.0,
        "--mp",
        metavar="N",
        help="Downscale so total pixels ≤ N million. e.g. --mp 12 ≈ 12-megapixel "
             "output. Mutually exclusive with --long / --short.",
    ),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        "-R",
        help="Recurse into subdirectories (default is top-level only).",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        "-f",
        help="Overwrite existing output files. Default: skip with a warning.",
    ),
    where: str = typer.Option(
        "",
        "--where",
        "-w",
        metavar="EXPR",
        help="Filter inputs by an EXIF predicate (same DSL as `ls --where`). "
             "Examples: 'iso>3200', 'date>=\"2024-01-01\" and model~\"R5\"'. "
             "Triggers one exiftool call to read EXIF for the candidate set.",
    ),
) -> None:
    """Demosaic each RAW via libraw and encode as JPEG/TIFF/PNG.

    Opposite of `extract`: where extract hands back the camera's already-baked
    SOOC JPEG (fast, 100% SOOC), render decodes the raw Bayer pattern ourselves
    through libraw and encodes the result fresh.

    \b
    Colour science WILL drift from SOOC. libraw's defaults are a neutral
    sRGB pipeline, not Canon Picture Style / Fuji Film Simulation / etc.
    If you need SOOC colour, use `extract`. If you need fine-grained
    rendering control (WB, curves, sharpening), use Lightroom / Capture One.

    Render is the right tool when the camera didn't embed a big enough
    JPEG (e.g. Sony A7R IV only embeds 1616x1080) or when you want a
    full-sensor-resolution output that no embedded JPEG provides.

    Throughput: ~0.5-2 seconds per file (real demosaic work), vs
    extract's ~30ms per file. Don't render thousands when extract
    would do.
    """
    # Keep resize UX identical to `extract`.
    set_dims = [
        name
        for name, val in (("--long", long_edge), ("--short", short_edge), ("--mp", megapixels))
        if val
    ]
    if len(set_dims) > 1:
        raise typer.BadParameter(
            f"{' / '.join(set_dims)} are mutually exclusive — pick one"
        )

    inputs = paths if paths else [Path(".")]
    raws = _collect_raws(inputs, recursive=recursive)
    if not raws:
        return
    raws = _filter_paths_by_where(raws, where)
    if not raws:
        return

    output.mkdir(parents=True, exist_ok=True)
    suffix = suffix_for(output_format.value)
    out_paths: list[Path] = [
        (output / _output_relpath(r, inputs)).with_suffix(suffix) for r in raws
    ]
    collisions: dict[str, list[tuple[Path, Path]]] = {}
    for raw, out_path in zip(raws, out_paths):
        key = str(out_path).casefold()
        collisions.setdefault(key, []).append((out_path, raw))
    duplicated = {k: pairs for k, pairs in collisions.items() if len(pairs) > 1}
    if duplicated:
        typer.echo(
            f"rawkit: refusing to render — {len(duplicated)} output collision(s):",
            err=True,
        )
        for pairs in duplicated.values():
            unique_outs = sorted({op for op, _ in pairs}, key=str)
            head = str(unique_outs[0])
            if len(unique_outs) > 1:
                head += f"  (case variants: {', '.join(p.name for p in unique_outs[1:])})"
            typer.echo(f"  {head}", err=True)
            for _, src in pairs:
                typer.echo(f"    ← {src}", err=True)
        typer.echo(
            "Hint: pass the conflicting RAWs via a common parent dir with -R "
            "so they land under distinct subdirs, or rename one source.",
            err=True,
        )
        raise typer.Exit(code=1)

    long_arg: int | None = long_edge if long_edge > 0 else None
    short_arg: int | None = short_edge if short_edge > 0 else None
    mp_arg: float | None = megapixels if megapixels > 0 else None

    n_ok = 0
    n_skipped = 0
    n_failed = 0
    for raw, out_path in zip(raws, out_paths):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists() and not overwrite:
            typer.echo(
                f"{raw.name}: skip (exists, use -f to overwrite)", err=True
            )
            n_skipped += 1
            continue
        try:
            result = render_raw(
                raw,
                output_format=output_format.value,
                quality=quality,
                long_edge=long_arg,
                short_edge=short_arg,
                megapixels=mp_arg,
            )
        except RenderError as e:
            typer.echo(f"{raw.name}: failed — {e}", err=True)
            n_failed += 1
            continue
        out_path.write_bytes(result.data)
        typer.echo(
            f"{raw.name}: {result.width}x{result.height} "
            f"{output_format.value} -> {out_path}",
            err=True,
        )
        n_ok += 1

    if n_failed or n_skipped:
        typer.echo(
            f"\n{n_ok} rendered, {n_skipped} skipped, {n_failed} failed",
            err=True,
        )
    if n_failed:
        raise typer.Exit(code=1)


# --- organize command -------------------------------------------------------

# Bucket-extraction dispatch for organize. Each entry maps a --by dim name to
# (bucket_function, record_field). Bucket functions live in aggregate.py;
# `None` return becomes the `_unknown` directory.
def _organize_dim_dir(dim: str, record: dict[str, Any]) -> str:
    """Directory name for `record` along dimension `dim`. Falls back to
    '_unknown' when the record has no value for this dim (matches the
    documented missing-value behaviour)."""
    from rawkit.aggregate import (
        _aperture_bucket,
        _bias_bucket,
        _day_bucket,
        _focal_bucket,
        _hour_bucket,
        _iso_bucket,
        _month_bucket,
        _rating_bucket,
        _shutter_bucket,
        _year_bucket,
    )

    # String fields used verbatim.
    string_field = {
        "camera": "model",
        "model":  "model",
        "lens":   "lens",
        "maker":  "maker",
        "orientation": "orientation",
    }
    if dim in string_field:
        v = record.get(string_field[dim])
        return v if v else "_unknown"

    # Bucketed dims via aggregate.
    bucketed: dict[str, tuple[Any, str]] = {
        "iso":      (_iso_bucket,       "iso"),
        "aperture": (_aperture_bucket,  "fnumber"),
        "fnumber":  (_aperture_bucket,  "fnumber"),
        "focal":    (_focal_bucket,     "focal"),
        "shutter":  (_shutter_bucket,   "shutter"),
        "bias":     (_bias_bucket,      "bias"),
        "rating":   (_rating_bucket,    "rating"),
        "hour":     (_hour_bucket,      "time"),
        "month":    (_month_bucket,     "date"),
        "year":     (_year_bucket,      "date"),
        "day":      (_day_bucket,       "date"),
    }
    if dim in bucketed:
        fn, field = bucketed[dim]
        return fn(record.get(field)) or "_unknown"

    return "_unknown"


def _sanitize_dir_name(name: str) -> str:
    """Replace path-unfriendly characters in a directory name. Currently
    only the path separator '/' is replaced (with '_'); unicode characters
    like '≤', '–', '+' and spaces are preserved as-is so the bucket name
    stays recognisable."""
    return name.replace("/", "_")


# Same-stem file suffixes treated as RAW companions: LrC XMP sidecars
# (rating / develop adjustments live here) and the SOOC JPEG some cameras
# write next to the RAW. Lowercase-compared.
_SIDECAR_SUFFIXES: frozenset[str] = frozenset({".xmp", ".jpg", ".jpeg"})

# OS-generated cruft files that don't count as 'real' directory contents
# when deciding whether a dir is prune-eligible. We sweep these out before
# rmdir-ing the parent.
_PRUNE_JUNK_NAMES: frozenset[str] = frozenset({".DS_Store"})


def _find_sidecars(raw: Path) -> list[Path]:
    """Find same-stem sidecars (XMP / JPG) sitting next to the RAW.
    These describe or partner the RAW; organize moves them together so
    the RAW's LrC rating and develop edits aren't orphaned."""
    try:
        siblings = list(raw.parent.iterdir())
    except OSError:
        return []
    stem = raw.stem
    found: list[Path] = []
    for p in siblings:
        if p == raw or not p.is_file():
            continue
        if p.stem == stem and p.suffix.lower() in _SIDECAR_SUFFIXES:
            found.append(p)
    return found


def _prune_empty_subdirs(roots: list[Path], moves: list[tuple[Path, Path]], *,
                         simulated: bool = False) -> int:
    """rmdir subdirectories of `roots` that are (or would be) empty.

    Scope: walks every non-hidden subdirectory under each input root.
    'Empty' means no content other than OS junk (.DS_Store), which is
    swept before rmdir. Pre-existing empty dirs from previous runs are
    fair game — opportunistic cleanup is the whole point.

    Hard limits:
    - Source roots themselves are never removed.
    - Hidden directories (any path component starting with '.') are
      skipped entirely. This protects .git/, .venv/, .pytest_cache/
      and similar infrastructure from accidental deletion.

    `simulated=True` runs in dry-run mode: treats every src in `moves`
    as already absent when checking emptiness, prints planned rmdirs
    without touching the filesystem.
    """
    dir_roots: list[tuple[Path, Path]] = []
    for r in roots:
        try:
            if r.is_dir():
                dir_roots.append((r, r.resolve()))
        except OSError:
            continue
    if not dir_roots:
        return 0

    sim_moved: set[Path] = set()
    if simulated:
        for src, _tgt in moves:
            try:
                sim_moved.add(src.resolve())
            except OSError:
                pass

    pruned: set[Path] = set()
    for root, root_resolved in dir_roots:
        for dirpath, _, _ in os.walk(root, topdown=False, followlinks=False):
            d = Path(dirpath)
            try:
                d_resolved = d.resolve()
            except OSError:
                continue
            if d_resolved == root_resolved:
                continue
            # Skip hidden infrastructure (.git/, .venv/, .pytest_cache/, …).
            try:
                rel_parts = d.relative_to(root).parts
            except ValueError:
                rel_parts = d_resolved.relative_to(root_resolved).parts
            if any(p.startswith(".") for p in rel_parts):
                continue

            try:
                entries = list(d.iterdir())
            except OSError as e:
                typer.echo(f"prune {d}: failed \u2014 {e}", err=True)
                continue

            # Classify: real content blocks; .DS_Store is sweepable junk;
            # simulated-moved srcs and already-pruned subdirs count as gone.
            blocking = False
            junk: list[Path] = []
            for e in entries:
                if e.name in _PRUNE_JUNK_NAMES and e.is_file():
                    junk.append(e)
                    continue
                try:
                    er = e.resolve()
                except OSError:
                    blocking = True
                    break
                if er in pruned:
                    continue
                if simulated and er in sim_moved:
                    continue
                blocking = True
                break

            if blocking:
                continue

            if simulated:
                typer.echo(f"[dry-run] rmdir {d}", err=True)
                pruned.add(d_resolved)
            else:
                for j in junk:
                    try:
                        j.unlink()
                    except OSError:
                        pass  # rmdir below will report if it still fails
                try:
                    d.rmdir()
                except OSError as e:
                    typer.echo(f"rmdir {d}: failed \u2014 {e}", err=True)
                    continue
                typer.echo(f"rmdir: {d}", err=True)
                pruned.add(d_resolved)

    return len(pruned)


def _parse_by_chain(by: str) -> list[str]:
    """Validate a comma-separated --by chain. Returns ordered dim keys
    (duplicates rejected). Empty `by` returns an empty list — the
    caller treats this as 'no bucketing, dump files directly into DEST'."""
    if not by:
        return []
    raw_dims = [d.strip().lower() for d in by.split(",") if d.strip()]
    if not raw_dims:
        typer.echo("rawkit: --by: empty value", err=True)
        raise typer.Exit(code=2)
    seen: set[str] = set()
    dims: list[str] = []
    for d in raw_dims:
        if d not in _INFO_BY_DIMS:
            typer.echo(
                f"rawkit: --by: unknown dimension {d!r}; valid: "
                f"{', '.join(sorted(_INFO_BY_DIMS))}",
                err=True,
            )
            raise typer.Exit(code=2)
        if d in seen:
            typer.echo(f"rawkit: --by: duplicate dimension {d!r}", err=True)
            raise typer.Exit(code=2)
        seen.add(d)
        dims.append(d)
    return dims


def _organize_target_dir(dest: Path, dims: list[str], record: dict[str, Any]) -> Path:
    parts = [_sanitize_dir_name(_organize_dim_dir(d, record)) for d in dims]
    return dest.joinpath(*parts)


@app.command()
def organize(
    paths: list[Path] = typer.Argument(
        None,
        help="Files or directories to organize. Defaults to current "
             "directory. Directories are listed top-level only unless -R.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        metavar="DIR",
        help="Destination root. If omitted, defaults to the first input "
             "directory (in-place organize). Created if missing. "
             "Files land under DIR/<bucket1>/<bucket2>/.../<basename>.",
    ),
    by: str = typer.Option(
        "",
        "--by",
        metavar="DIM[,DIM,...]",
        help="Optional. One or more dimensions (comma-separated) used as "
             "nested directory layers. Omit to dump files flat into DEST "
             "(useful with --where to cherry-pick a subset). Same vocabulary "
             "as `info --by` and `ls --where`: camera/model, lens, maker, "
             "orientation, iso, aperture (alias: fnumber), focal, shutter, "
             "bias, rating, hour, year, month, day.",
    ),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        "-R",
        help="Recurse into subdirectories (default is top-level only).",
    ),
    where: str = typer.Option(
        "",
        "--where",
        "-w",
        metavar="EXPR",
        help="Filter inputs by an EXIF predicate (same DSL as `ls --where`).",
    ),
    copy: bool = typer.Option(
        False,
        "--copy",
        help="Copy files to the destination instead of moving them. "
             "Slower and uses 2x space; useful when DEST is on a "
             "different drive and you want to keep the originals.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Print the planned moves without touching the filesystem.",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        "-f",
        help="Overwrite existing files at the destination. Default: skip.",
    ),
    prune: bool = typer.Option(
        False,
        "--prune",
        help="After moving, rmdir source subdirectories that end up empty. "
             "Source roots themselves are never removed. Useful for repeated "
             "reorganizations that would otherwise leave behind empty "
             "folder skeletons.",
    ),
) -> None:
    """Move RAW files into a folder hierarchy keyed by EXIF dimensions.

    \b
    Examples:
      rawkit organize ~/dump -o ~/sorted --by month
      rawkit organize ~/dump -o ~/sorted --by year,month
      rawkit organize ~/dump -o ~/sorted --by camera,year -R
      rawkit organize ~/dump -o ~/keepers -R -w 'rating>=4'  # flat, no --by

    \b
    Behaviour:
      - Default action is MOVE; pass --copy to copy instead.
      - --by is OPTIONAL. Omit it to dump files flat into DEST (useful
        with --where to cherry-pick a subset by EXIF predicate).
      - Same-stem .xmp / .jpg sidecars move alongside the RAW so LrC
        ratings and develop adjustments aren't orphaned.
      - Files missing the relevant EXIF value land in '_unknown/'.
      - Target collisions (including case-insensitive on macOS/Windows)
        cause a fail-fast refusal before any file is touched.

    Progress and outcomes go to stderr; stdout is left empty so you can
    pipe `find … | xargs rawkit organize` without surprises.
    """
    import shutil as _shutil

    inputs = paths if paths else [Path(".")]
    dims = _parse_by_chain(by)

    # Default -o = first input dir (in-place organize), else cwd.
    if output is None:
        output = inputs[0] if inputs[0].is_dir() else Path(".")

    raws = _collect_raws(inputs, recursive=recursive)
    if not raws:
        typer.echo("no RAW files found", err=True)
        return

    # Compile --where once; reuse on each record.
    predicate = None
    if where:
        try:
            predicate = compile_where(where)
        except QueryError as e:
            typer.echo(f"rawkit: --where: {e}", err=True)
            raise typer.Exit(code=2)

    records = safe_batch_read(raws)
    by_path = {r.get("path"): r for r in records}

    # Build a flat plan of (source, target) moves — RAW + its sidecars.
    moves: list[tuple[Path, Path]] = []
    for raw in raws:
        rec = by_path.get(str(raw), {})
        if predicate is not None and not predicate(rec):
            continue
        target_dir = _organize_target_dir(output, dims, rec)
        moves.append((raw, target_dir / raw.name))
        for sidecar in _find_sidecars(raw):
            moves.append((sidecar, target_dir / sidecar.name))

    if not moves:
        if where:
            typer.echo("no records matched --where", err=True)
        else:
            typer.echo("nothing to organize", err=True)
        return

    # Collision preflight (intra-run, case-fold) — matches extract/render.
    collisions: dict[str, list[tuple[Path, Path]]] = {}
    for src, tgt in moves:
        key = str(tgt).casefold()
        collisions.setdefault(key, []).append((tgt, src))
    duplicated = {k: v for k, v in collisions.items() if len(v) > 1}
    if duplicated:
        typer.echo(
            f"rawkit: refusing to organize — {len(duplicated)} target collision(s):",
            err=True,
        )
        for pairs in duplicated.values():
            unique_targets = sorted({t for t, _ in pairs}, key=str)
            head = str(unique_targets[0])
            if len(unique_targets) > 1:
                head += f"  (case variants: {', '.join(p.name for p in unique_targets[1:])})"
            typer.echo(f"  {head}", err=True)
            for _, src in pairs:
                typer.echo(f"    \u2190 {src}", err=True)
        typer.echo(
            "Hint: add another dimension to --by so the conflicting RAWs "
            "land under distinct subdirs, or rename one source.",
            err=True,
        )
        raise typer.Exit(code=1)

    n_ok = 0
    n_skipped = 0
    n_failed = 0
    verb_past = "copied" if copy else "moved"
    for src, tgt in moves:
        # Source already at the target path (e.g., source==dest in-place
        # organize where this file is already in the right bucket).
        try:
            same = src.exists() and tgt.exists() and src.resolve() == tgt.resolve()
        except OSError:
            same = False
        if same:
            n_skipped += 1
            continue

        if tgt.exists() and not overwrite:
            typer.echo(
                f"{src.name}: skip (exists, use -f to overwrite)", err=True
            )
            n_skipped += 1
            continue

        if dry_run:
            typer.echo(f"[dry-run] {src} -> {tgt}", err=True)
            n_ok += 1
            continue

        try:
            tgt.parent.mkdir(parents=True, exist_ok=True)
            if copy:
                _shutil.copy2(src, tgt)
            else:
                _shutil.move(str(src), str(tgt))
        except OSError as e:
            typer.echo(f"{src.name}: failed — {e}", err=True)
            n_failed += 1
            continue

        typer.echo(f"{verb_past}: {src.name} -> {tgt}", err=True)
        n_ok += 1

    summary_verb = "planned" if dry_run else verb_past
    typer.echo(
        f"\n{n_ok} {summary_verb}, {n_skipped} skipped, {n_failed} failed",
        err=True,
    )

    if prune:
        if dry_run:
            n_pruned = _prune_empty_subdirs(inputs, moves, simulated=True)
            if n_pruned:
                typer.echo(f"{n_pruned} dir(s) would be pruned", err=True)
        else:
            n_pruned = _prune_empty_subdirs(inputs, moves)
            if n_pruned:
                typer.echo(f"{n_pruned} empty dir(s) removed", err=True)

    if n_failed:
        raise typer.Exit(code=1)


# --- info command -----------------------------------------------------------

@app.command()
def info(
    paths: list[Path] = typer.Argument(
        None,
        help="Single RAW file or directories. File input = full-field view; "
             "directory input = aggregated summary.",
    ),
    where: str = typer.Option(
        "",
        "--where",
        "-w",
        metavar="EXPR",
        help="Filter by an EXIF predicate (same DSL as `ls --where`).",
    ),
    by: str = typer.Option(
        "",
        "--by",
        metavar="DIM",
        help="Directory mode only: partition by one dimension instead of the "
             "default summary. Valid: camera/model, lens, maker, orientation, "
             "iso, aperture (alias: fnumber), focal, shutter, bias, rating, "
             "hour, year, month, day.",
    ),
    top: int = typer.Option(
        5,
        "--top",
        metavar="N",
        help="With --by lens: keep top N, collapse the rest into '+others'. "
             "Other dimensions ignore this (their buckets are bounded).",
    ),
    more: bool = typer.Option(
        False,
        "--more",
        help="With --by lens: show all lenses (overrides --top).",
    ),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        "-R",
        help="Recurse into subdirectories (default is top-level only).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit JSON. File mode = compact info object; directory mode = full structured aggregation.",
    ),
) -> None:
    """Describe RAW metadata.

    - `info FILE`: full-field key/value view for one RAW.
    - `info DIR`: vertical KV summary of the folder.
    - `info DIR --by DIM`: partition the folder along one dimension.
    """
    inputs = paths if paths else [Path(".")]

    # Single-file mode: explicit file input only.
    if len(inputs) == 1 and inputs[0].is_file():
        if by:
            typer.echo("rawkit: --by is only valid for directory inputs", err=True)
            raise typer.Exit(code=2)

        raws = _collect_raws(inputs, recursive=False)
        if not raws:
            typer.echo("no RAW files found", err=True)
            raise typer.Exit(code=1)

        raws_after_where = _filter_paths_by_where(raws, where)
        if not raws_after_where:
            typer.echo("no records matched --where", err=True)
            raise typer.Exit(code=1)

        raw = raws_after_where[0]
        records = safe_batch_read([raw])
        rec = records[0] if records else {}
        payload = _build_info_file_record(raw, rec)
        payload["embedded_jpegs"] = _inspect_embedded_jpegs(raw)
        if as_json:
            typer.echo(json.dumps(payload, ensure_ascii=False))
        else:
            typer.echo(_render_info_file(payload))
        return

    # Directory/multi-input mode.
    dim = _parse_by_dim(by)

    raws = _collect_raws(inputs, recursive=recursive)
    if not raws:
        typer.echo("no RAW files found", err=True)
        raise typer.Exit(code=1)

    raws_after_where = _filter_paths_by_where(raws, where)
    if not raws_after_where:
        typer.echo("no records matched --where", err=True)
        raise typer.Exit(code=1)

    records = safe_batch_read(raws_after_where)
    by_path = {r.get("path"): r for r in records}
    paired_records: list[dict[str, Any]] = []
    paired_paths: list[Path] = []
    for p in raws_after_where:
        rec = by_path.get(str(p))
        if rec is not None:
            paired_records.append(rec)
            paired_paths.append(p)

    stats_data = build_stats(paired_records, paired_paths)

    path_display = ", ".join(str(p) for p in inputs)

    if as_json:
        payload = {"path": path_display, **stats_data}
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return

    if dim is None:
        typer.echo(_render_info_dir(stats_data, paired_records, path_display=path_display, where=where))
    else:
        lens_top = 999_999 if more else top
        typer.echo(_render_info_by(stats_data, dim, top=lens_top, where=where))

