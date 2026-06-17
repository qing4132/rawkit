"""Thin wrapper around the `exiftool` CLI.

We shell out to exiftool because (a) it's the de facto truth for RAW maker
notes, and (b) re-implementing maker-note parsers is a project of its own.
Hard constraint #4 (Unix philosophy) — don't recreate what already works.

Performance note: ALWAYS pass every path in a single exiftool invocation. One
fork ≈ 80 ms; 1000 files at one-fork-each is a minute of latency for nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

import typer


# (exiftool tag, rawkit normalized key) pairs. The normalized keys are the
# same vocabulary used by the README's --where DSL grammar, so JSON output
# and future query expressions stay aligned. Some keys (`orientation`,
# `flash`, `gps`) are *derived* in _normalize() rather than directly mapped.
_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("SourceFile",         "path"),
    ("DateTimeOriginal",   "datetime"),  # full 'YYYY-MM-DD HH:MM:SS'; `date` and `time` are derived below
    ("Make",               "maker"),
    ("Model",              "model"),
    ("LensModel",          "lens"),
    ("ISO",                "iso"),
    ("FNumber",            "fnumber"),
    ("ExposureTime",       "shutter"),
    ("FocalLength",        "focal"),
    ("ExposureCompensation", "bias"),
    ("Rating",             "rating"),
    ("GPSLatitude",        "gps_lat"),
    ("GPSLongitude",       "gps_lon"),
    ("Orientation",        "_orientation_raw"),
    ("Flash",              "_flash_raw"),
)


class ExiftoolMissing(RuntimeError):
    """Raised when the `exiftool` binary is not on PATH."""


def require_exiftool() -> str:
    """Return the absolute path to exiftool, or raise ExiftoolMissing."""
    path = shutil.which("exiftool")
    if path is None:
        raise ExiftoolMissing(
            "rawkit needs `exiftool` but it isn't on PATH.\n"
            "Install it with:  brew install exiftool   (macOS)\n"
            "              or:  apt install libimage-exiftool-perl   (Debian/Ubuntu)"
        )
    return path


def batch_read(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Read EXIF for every path in ONE exiftool invocation.

    Returns a list of dicts using rawkit's normalized field vocabulary
    (`path`, `date`, `maker`, `model`, `lens`, `iso`, `fnumber`, `shutter`,
    `focal`). Missing fields are absent from the dict, not set to None — let
    the consumer decide how to render absence.
    """
    paths_list = [str(p) for p in paths]
    if not paths_list:
        return []

    require_exiftool()
    args = (
        ["exiftool", "-j", "-n"]
        + [f"-{tag}" for tag, _key in _FIELD_MAP if tag != "SourceFile"]
        + ["--"]
        + paths_list
    )
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    # exiftool exits 1 when it emits warnings about individual files but
    # still produces valid JSON for the rest. Treat that as success.
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            f"exiftool failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    raw_records: list[dict[str, Any]] = json.loads(proc.stdout or "[]")
    return [_normalize(r) for r in raw_records]


def _normalize(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tag, key in _FIELD_MAP:
        if tag in record and record[tag] not in (None, ""):
            out[key] = record[tag]

    # datetime / date / time three-field split.
    # exiftool returns 'YYYY:MM:DD HH:MM:SS' (legacy EXIF format with colons in
    # the date part). We expose three string fields so DSL queries can target
    # the precision the user actually means:
    #   datetime = 'YYYY-MM-DD HH:MM:SS'  (full, lexicographically sortable)
    #   date     = 'YYYY-MM-DD'           (calendar day)
    #   time     = 'HH:MM:SS'             (time of day)
    dt = out.get("datetime")
    if isinstance(dt, str) and len(dt) >= 19 and dt[4] == ":" and dt[7] == ":":
        normalized = dt[:4] + "-" + dt[5:7] + "-" + dt[8:]
        out["datetime"] = normalized
        out["date"] = normalized[:10]
        out["time"] = normalized[11:19]

    raw_o = out.pop("_orientation_raw", None)
    if raw_o is not None:
        try:
            o = int(raw_o)
            if o in (5, 6, 7, 8):
                out["orientation"] = "portrait"
            elif o in (1, 2, 3, 4):
                out["orientation"] = "landscape"
        except (TypeError, ValueError):
            pass

    raw_f = out.pop("_flash_raw", None)
    if raw_f is not None:
        try:
            out["flash"] = bool(int(raw_f) & 1)
        except (TypeError, ValueError):
            pass

    if "gps_lat" in out and "gps_lon" in out:
        out["gps"] = True

    return out


# --- typer-friendly error wrapper -------------------------------------------

def safe_batch_read(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Like `batch_read` but converts ExiftoolMissing into a typer.Exit
    with a human-readable stderr message. CLI commands should call this."""
    try:
        return batch_read(paths)
    except ExiftoolMissing as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
