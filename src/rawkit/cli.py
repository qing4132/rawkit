from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

import typer

from rawkit.exif import safe_batch_read
from rawkit.query import QueryError, compile_where

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

def _collect_raws(inputs: Iterable[Path]) -> list[Path]:
    """Resolve a mix of files and directories to a sorted list of RAW paths.

    Behavior:
    - directory: recursive scan (unreadable subtrees skipped, symlinks not
      followed) for files whose suffix is in RAW_EXTS
    - file with RAW suffix: included as-is
    - file with non-RAW suffix: skipped with a stderr warning
    - non-existent path: stderr error, abort the whole command (exit 1)

    Duplicates (same realpath reached via multiple args) are removed.
    """
    missing: list[Path] = []
    found: set[Path] = set()

    for inp in inputs:
        if not inp.exists():
            missing.append(inp)
            continue
        if inp.is_dir():
            for dirpath, _dirnames, filenames in os.walk(
                inp, onerror=lambda _e: None, followlinks=False
            ):
                for name in filenames:
                    p = Path(dirpath) / name
                    if p.suffix.lower() in RAW_EXTS:
                        found.add(p)
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


def _fmt_date(v: Any) -> str:
    """`2023:10:27 17:09:43` → `2023-10-27 17:09` (minute precision)."""
    if not v:
        return "-"
    head = str(v).partition(".")[0]
    try:
        d, t = head.split(" ", 1)
        d = d.replace(":", "-")
        t = ":".join(t.split(":")[:2])
        return f"{d} {t}"
    except ValueError:
        return str(v)


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
    ("date",     "date",      "l"),
    ("model",    "model",     "l"),
    ("lens",     "lens",      "l"),
    ("focal",    "focal",     "r"),
    ("aperture", "fnumber",   "r"),
    ("shutter",  "shutter",   "r"),
    ("bias",     "bias",      "r"),
    ("iso",      "iso",       "r"),
)

_FORMATTERS = {
    "date":    _fmt_date,
    "iso":     _fmt_iso,
    "fnumber": _fmt_fnumber,
    "shutter": _fmt_shutter,
    "focal":   _fmt_focal,
    "bias":    _fmt_bias,
}

_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"


def _render_table(records: Iterable[dict[str, Any]]) -> None:
    """Render an aligned, content-width table on stdout.

    We intentionally do NOT fit-to-terminal: columns are sized to the widest
    value so no data is ever truncated. Output may exceed the terminal width;
    in that case `| less -S` (horizontal scroll) is the standard escape hatch.

    When stdout is a TTY we bold the header so the eye has an anchor; data
    rows stay plain. No color or zebra striping — those tried and abandoned.
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

    headers = tuple(h for h, _k, _a in _TABLE_COLUMNS)
    widths = [max(len(s) for s in col) for col in zip(headers, *rows)]
    file_names = [headers[0], *(row[0] for row in rows)]
    normal_names = [n for n in file_names if len(n) <= _FILE_COL_SOFT_CAP]
    if normal_names:
        widths[0] = max(len(n) for n in normal_names)
    else:
        widths[0] = _FILE_COL_SOFT_CAP

    is_tty = sys.stdout.isatty()

    def fmt_cell(text: str, width: int, align: str) -> str:
        return f"{text:>{width}}" if align == "r" else f"{text:<{width}}"

    header_line = "  ".join(
        fmt_cell(h, widths[i], _TABLE_COLUMNS[i][2]) for i, h in enumerate(headers)
    )
    if is_tty:
        header_line = f"{_BOLD}{header_line}{_RESET}"
    typer.echo(header_line)

    for row in rows:
        typer.echo("  ".join(
            fmt_cell(row[i], widths[i], _TABLE_COLUMNS[i][2]) for i in range(len(row))
        ))


def _emit_jsonl(records: Iterable[dict[str, Any]]) -> None:
    for r in records:
        typer.echo(json.dumps(r, ensure_ascii=False))


# --- ls command -------------------------------------------------------------

@app.command()
def ls(
    paths: list[Path] = typer.Argument(
        None,
        help="Files or directories to scan. Defaults to current directory. "
             "Directories are walked recursively; files must have a RAW suffix.",
    ),
    where: str = typer.Option(
        "",
        "--where",
        "-w",
        metavar="EXPR",
        help="Filter rows by an EXIF predicate. "
             "Examples: 'iso>3200 and lens~\"50\"', 'date>=\"2024-01-01\"'.",
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
    raws = _collect_raws(inputs)
    if not raws:
        return

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

    if as_json:
        _emit_jsonl(records)
    else:
        _render_table(records)
