from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

import typer
from rich.console import Console
from rich.table import Table

from rawkit.exif import safe_batch_read

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


def _walk_raws(directory: Path) -> list[Path]:
    """Sorted RAW files under `directory`.

    os.walk so a single permission-denied subtree doesn't abort the scan, and
    symlinks are not followed (avoids cycles in real photo libraries).
    """
    found: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(
        directory, onerror=lambda _e: None, followlinks=False
    ):
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() in RAW_EXTS:
                found.append(p)
    return sorted(found)


# --- display formatters -----------------------------------------------------

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


def _render_table(records: Iterable[dict[str, Any]]) -> None:
    # no_wrap on every column = one row per file, even in narrow terminals.
    # Long values get an ellipsis instead of vertically exploding the row.
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("file", style="cyan", no_wrap=True, overflow="ellipsis")
    table.add_column("date", no_wrap=True, overflow="ellipsis")
    table.add_column("model", no_wrap=True, overflow="ellipsis")
    table.add_column("lens", no_wrap=True, overflow="ellipsis")
    table.add_column("iso", justify="right", no_wrap=True)
    table.add_column("aperture", justify="right", no_wrap=True)
    table.add_column("shutter", justify="right", no_wrap=True)
    table.add_column("focal", justify="right", no_wrap=True)

    for r in records:
        table.add_row(
            Path(r.get("path", "")).name,
            _fmt_date(r.get("date")),
            str(r.get("model") or "-"),
            str(r.get("lens") or "-"),
            _fmt_iso(r.get("iso")),
            _fmt_fnumber(r.get("fnumber")),
            _fmt_shutter(r.get("shutter")),
            _fmt_focal(r.get("focal")),
        )

    Console(file=sys.stdout).print(table)


def _emit_jsonl(records: Iterable[dict[str, Any]]) -> None:
    for r in records:
        typer.echo(json.dumps(r, ensure_ascii=False))


# --- ls command -------------------------------------------------------------

@app.command()
def ls(
    directory: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Directory to scan (recursive).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit JSONL on stdout (one object per file) instead of an aligned table.",
    ),
) -> None:
    """List RAW files under DIRECTORY with their key EXIF.

    Default output is an aligned, human-readable table. Use --json to pipe the
    output into jq or other tooling.
    """
    paths = _walk_raws(directory)
    if not paths:
        return

    records = safe_batch_read(paths)
    if as_json:
        _emit_jsonl(records)
    else:
        _render_table(records)
