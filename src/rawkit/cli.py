from __future__ import annotations

import json
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import typer

from rawkit.exif import safe_batch_read
from rawkit.preview import PreviewExtractError, extract_preview
from rawkit.query import QueryError, compile_where
from rawkit.render import RenderError, render, suffix_for

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


# Soft cap for the file column. A pathologically long name should not
# inflate every other row's padding — it just breaks alignment for that
# one row. 50 chars comfortably fits any in-camera + LrC renamed scheme.
_FILE_COL_SOFT_CAP = 50


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

    We intentionally do NOT fit-to-terminal: columns are sized to the widest
    value so no data is ever truncated. Output may exceed the terminal width;
    in that case `| less -S` (horizontal scroll) is the standard escape hatch.

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
    file_names = [headers[0], *(row[0] for row in rows)]
    normal_names = [n for n in file_names if len(n) <= _FILE_COL_SOFT_CAP]
    if normal_names:
        widths[0] = max(len(n) for n in normal_names)
    else:
        widths[0] = _FILE_COL_SOFT_CAP

    use_color = _color_enabled()

    def fmt_cell(text: str, width: int, align: str) -> str:
        return f"{text:>{width}}" if align == "r" else f"{text:<{width}}"

    def wrap(s: str, codes: str) -> str:
        # ANSI wrap a pre-padded cell. The codes don't change visible width;
        # terminals render them as zero-width. So padding-then-wrap is safe.
        return f"{codes}{s}{_RESET}" if use_color and codes else s

    # Header line: whole row bold; active sort column carries the arrow only.
    header_cells: list[str] = []
    for i, h in enumerate(headers):
        padded = fmt_cell(h, widths[i], _TABLE_COLUMNS[i][2])
        header_cells.append(wrap(padded, _BOLD))
    typer.echo("  ".join(header_cells), color=use_color)

    # Data rows: plain text, no cell-level coloring.
    for row in rows:
        cells = [
            fmt_cell(row[i], widths[i], _TABLE_COLUMNS[i][2])
            for i in range(len(row))
        ]
        typer.echo("  ".join(cells), color=use_color)


def _emit_jsonl(records: Iterable[dict[str, Any]]) -> None:
    for r in records:
        typer.echo(json.dumps(r, ensure_ascii=False))


# --- sorting ----------------------------------------------------------------

class SortKey(str, Enum):
    """All column headers from the default table are accepted as sort keys,
    plus the three time slices (datetime/date/time) so the user can pick
    precision."""
    file     = "file"
    datetime = "datetime"
    date     = "date"
    time     = "time"
    model    = "model"
    lens     = "lens"
    focal    = "focal"
    aperture = "aperture"
    shutter  = "shutter"
    bias     = "bias"
    iso      = "iso"


# Per-sort-key extractor: returns the value to compare, or None if missing.
# Strings are lowercased so case differences don't reorder rows.
_SORT_EXTRACTORS: dict[SortKey, Any] = {
    SortKey.file:     lambda r: Path(r["path"]).name.lower() if r.get("path") else None,
    SortKey.datetime: lambda r: r.get("datetime"),
    SortKey.date:     lambda r: r.get("date"),
    SortKey.time:     lambda r: r.get("time"),
    SortKey.model:    lambda r: r["model"].lower() if r.get("model") else None,
    SortKey.lens:     lambda r: r["lens"].lower() if r.get("lens") else None,
    SortKey.focal:    lambda r: r.get("focal"),
    SortKey.aperture: lambda r: r.get("fnumber"),
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


# --- preview command --------------------------------------------------------

@app.command()
def preview(
    paths: list[Path] = typer.Argument(
        None,
        help="Files or directories to extract previews from. Defaults to current "
             "directory. Directories are listed top-level only unless -R.",
    ),
    output: Path = typer.Option(
        Path("./previews"),
        "--output",
        "-o",
        metavar="DIR",
        help="Output directory. Created if missing. Each preview is written as "
             "<DIR>/<basename>.jpg (basename = source stem).",
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
) -> None:
    """Extract each RAW's largest embedded SOOC JPEG preview.

    Always returns the camera's in-RAW JPEG (100% SOOC colour science).
    Uses libraw (via rawpy) to reach whatever the camera embedded —
    typically the full-resolution SOOC frame for Canon CR3 / Sony A1+ /
    Nikon Z…, the 3000-class reduced-resolution frame for Hasselblad 3FR,
    or the 1080-class preview for older Sony ARW.

    The 160x120-class navigation thumbnail is intentionally not used.

    Progress and per-file outcomes are reported on stderr; stdout is left
    empty so you can pipe `find … | xargs rawkit preview` without surprises.
    """
    inputs = paths if paths else [Path(".")]
    raws = _collect_raws(inputs, recursive=recursive)
    if not raws:
        return

    output.mkdir(parents=True, exist_ok=True)

    n_ok = 0
    n_skipped = 0
    n_failed = 0
    for raw in raws:
        out_path = output / f"{raw.stem}.jpg"
        if out_path.exists() and not overwrite:
            typer.echo(
                f"{raw.name}: skip (exists, use -f to overwrite)", err=True
            )
            n_skipped += 1
            continue
        try:
            result = extract_preview(raw)
        except PreviewExtractError as e:
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
    max_side: int = typer.Option(
        0,
        "--max-side",
        metavar="N",
        help="Downscale so the long edge is at most N pixels (LANCZOS). "
             "0 = keep native sensor resolution.",
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
) -> None:
    """Demosaic each RAW via libraw and encode as JPEG/TIFF/PNG.

    Opposite of `preview`: where preview hands back the camera's already-baked
    SOOC JPEG (fast, 100% SOOC), render decodes the raw Bayer pattern ourselves
    through libraw and encodes the result fresh.

    \b
    Colour science WILL drift from SOOC. libraw's defaults are a neutral
    sRGB pipeline, not Canon Picture Style / Fuji Film Simulation / etc.
    If you need SOOC colour, use `preview`. If you need fine-grained
    rendering control (WB, curves, sharpening), use Lightroom / Capture One.

    Render is the right tool when the camera didn't embed a big enough
    preview (e.g. Sony A7R IV only embeds 1616x1080) or when you want a
    full-sensor-resolution output that no embedded JPEG provides.

    Throughput: ~0.5-2 seconds per file (real demosaic work), vs
    preview's ~30ms per file. Don't render thousands when preview
    would do.
    """
    inputs = paths if paths else [Path(".")]
    raws = _collect_raws(inputs, recursive=recursive)
    if not raws:
        return

    output.mkdir(parents=True, exist_ok=True)
    suffix = suffix_for(output_format.value)
    max_side_arg: int | None = max_side if max_side > 0 else None

    n_ok = 0
    n_skipped = 0
    n_failed = 0
    for raw in raws:
        out_path = output / f"{raw.stem}{suffix}"
        if out_path.exists() and not overwrite:
            typer.echo(
                f"{raw.name}: skip (exists, use -f to overwrite)", err=True
            )
            n_skipped += 1
            continue
        try:
            result = render(
                raw,
                output_format=output_format.value,
                quality=quality,
                max_side=max_side_arg,
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
