"""rawkit EXIF reader.

Two backends, same normalized record shape:

  * `lite` (default): rawpy (LibRaw) for the fields LibRaw exposes, plus a
    tiny in-process TIFF/CR3 IFD parser (`_exif_lite`) for the standard
    EXIF tags rawpy/LibRaw don't surface (Make, Model, GPS, Flash, Rating,
    SubSecTime, ExposureCompensation). No external process, no Perl. About
    50× faster than the exiftool path on the cold-cache external-SSD case
    that motivates the rewrite.

  * `exiftool` (opt-in via `RAWKIT_BACKEND=exiftool`): the original path —
    one exiftool process for the whole batch, paths streamed via `-@ -`
    to dodge ARG_MAX. Kept as a reference/fallback so anyone diagnosing
    a field discrepancy can flip a switch and compare.

Both backends pass through the same `_normalize()` so downstream code
(CLI rendering, JSON output, DSL `--where`, aggregations) sees identical
field semantics regardless of which path produced the data.

Concurrency: the lite backend uses a ThreadPoolExecutor (rawpy/LibRaw
releases the GIL during file I/O and most of the metadata parsing).
Threads are cheap to fan out; for batches over 500 files on external SSDs
the speedup is roughly linear up to ~8 workers.

Progress: when stderr is a TTY and the batch is large enough that the
wait is humanly perceptible, a rich.progress bar is shown on stderr.
This is purely informational — it never touches stdout, so piped /
redirected commands behave identically to before.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import typer

from rawkit import _exif_lite


# (exiftool tag, rawkit normalized key) pairs. The normalized keys are the
# same vocabulary used by the README's --where DSL grammar, so JSON output
# and future query expressions stay aligned. Some keys (`orientation`,
# `flash`, `gps`) are *derived* in _normalize() rather than directly mapped.
#
# Even with the lite backend now default, this table is still the canonical
# map: the lite backend builds a dict with exactly these key names so the
# same `_normalize` consumes both paths' output.
_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("SourceFile",         "path"),
    ("DateTimeOriginal",   "datetime"),  # 'YYYY-MM-DD HH:MM:SS'; `date`, `time` derived below
    ("SubSecTimeOriginal", "_subsec_raw"),  # fractional second as a digit string, e.g. '048' = .048s
    ("Make",               "maker"),
    ("Model",              "model"),
    ("LensModel",          "lens"),
    # Lock to EXIF group: Pentax/Ricoh writes a private logarithmic
    # sensitivity index in MakerNotes:ISO that exiftool's `-n` mode
    # surfaces as a small integer (e.g. 13 instead of 500). Without
    # the EXIF: prefix that value silently overwrites EXIF:ISO.
    ("EXIF:ISO",           "iso"),
    # Same MakerNotes-pollution shape: Leica M11 Monochrom writes a
    # garbage MakerNotes:FNumber=1.0 and skips EXIF:FNumber entirely,
    # storing the real aperture only in EXIF:ApertureValue (APEX).
    # Lock to EXIF:FNumber and fall back to ApertureValue in _normalize.
    ("EXIF:FNumber",       "fnumber"),
    ("EXIF:ApertureValue", "_apex_raw"),
    ("ExposureTime",       "shutter"),
    ("FocalLength",        "focal"),
    ("ExposureCompensation", "bias"),
    ("Rating",             "rating"),
    ("ImageWidth",         "image_width"),
    ("ImageHeight",        "image_height"),
    ("PreviewImageWidth",  "preview_width"),
    ("PreviewImageHeight", "preview_height"),
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


# --- public entry point: routes to the active backend -----------------------

def batch_read(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Read EXIF for every path, returning normalized rawkit records.

    Backend selection (env var `RAWKIT_BACKEND`):
      * unset / `lite`     → fast rawpy + in-process TIFF parser (default)
      * `exiftool`         → original exiftool subprocess path

    Use `lite` (the default) unless you're debugging a field discrepancy or
    hitting a RAW format the lite parser doesn't yet support, in which case
    set RAWKIT_BACKEND=exiftool for the run.
    """
    paths_list = list(paths)
    if not paths_list:
        return []
    backend = os.environ.get("RAWKIT_BACKEND", "lite").strip().lower()
    if backend == "exiftool":
        return _batch_read_exiftool(paths_list)
    return _batch_read_lite(paths_list)


# --- lite backend (rawpy + _exif_lite) --------------------------------------

# Number of worker threads for parallel metadata reads. Default scales with
# the machine; users with weird filesystems (slow NAS, NFS) can override.
# Cap at 8: past 8 workers we see diminishing returns and risk thrashing
# on shared metadata caches.
def _default_workers() -> int:
    env = os.environ.get("RAWKIT_WORKERS")
    if env and env.isdigit() and int(env) > 0:
        return int(env)
    return min(8, (os.cpu_count() or 4))


# Show a progress bar on stderr when:
#   (a) the batch is large enough that the wait is visible, AND
#   (b) stderr is a TTY (skipping in scripts / pipes / CI logs)
# The bar is informational only; stdout is untouched. Threshold tuned so
# the bar appears within the first second of any user-visible operation
# even on a fast internal SSD (≈ 500 files/s in the lite backend).
_PROGRESS_THRESHOLD = 50


def _batch_read_lite(paths_list: list[Path]) -> list[dict[str, Any]]:
    # Lazy import: rawpy pulls in libraw + numpy, ~80 ms on first import.
    # Keeping it lazy means `rawkit --help` and `rawkit ls` on tiny dirs
    # stay snappy.
    import rawpy  # type: ignore

    results: list[dict[str, Any] | None] = [None] * len(paths_list)

    # --- Cache lookup (Stage 1) --------------------------------------------
    # The cache turns "parse 38 k RAW files (~20 s)" into "stat 38 k files
    # + one SQLite range read (~1.5 s)" when all files are unchanged. We
    # skip it entirely for tiny batches where the bookkeeping overhead
    # would exceed the parse it's supposed to avoid.
    cache = None
    miss_indices: list[int]
    if len(paths_list) >= _PROGRESS_THRESHOLD:
        # Local import: keeps `rawkit --help` from paying sqlite3's import
        # cost (~3 ms) — small individually, but it accumulates across the
        # ten-or-so commands that don't read EXIF at all.
        from rawkit._cache import ExifCache
        cache = ExifCache.open()
        if cache is not None:
            hits, miss_indices = cache.get_many(paths_list)
            for i, rec in hits.items():
                results[i] = rec
        else:
            miss_indices = list(range(len(paths_list)))
    else:
        miss_indices = list(range(len(paths_list)))

    # --- Parse the misses (Stage 2) ----------------------------------------
    miss_paths = [paths_list[i] for i in miss_indices]
    miss_count = len(miss_paths)

    show_progress = (
        miss_count >= _PROGRESS_THRESHOLD
        and sys.stderr.isatty()
        and not os.environ.get("RAWKIT_NO_PROGRESS")
    )

    workers = _default_workers()

    def work(slot: int, path: Path) -> None:
        results[slot] = _normalize(_read_one_lite(path, rawpy))

    if miss_count > 0:
        if show_progress:
            # Local import so non-progress paths don't pay the rich.progress
            # import cost (a few ms, but adds up across `rawkit --help`).
            from rich.console import Console
            from rich.progress import (
                Progress, SpinnerColumn, BarColumn, MofNCompleteColumn,
                TextColumn, TaskProgressColumn,
            )
            # IMPORTANT: write to stderr, not the default stdout. Otherwise
            # piping (`rawkit ls ... | head`) makes rich see a non-TTY stdout
            # and suppress the bar entirely — even though stderr is still a
            # terminal. We've already gated on stderr being a TTY above.
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold]reading EXIF"),
                BarColumn(),
                MofNCompleteColumn(),
                TextColumn("•"),
                # Percentage instead of TimeRemainingColumn: the rolling
                # ETA estimate jitters badly when I/O is bursty (NAS, big
                # DNGs interleaved with small CR3s) and the back-and-forth
                # is more distracting than informative.
                TaskProgressColumn(),
                transient=True,   # bar disappears on completion → no stderr noise
                console=Console(stderr=True),
            ) as progress:
                task = progress.add_task("", total=miss_count)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    # We want per-finish progress updates, so submit + iterate
                    # in completion order rather than pool.map (which would
                    # only update in submission order at chunk granularity).
                    from concurrent.futures import as_completed
                    futures = {
                        pool.submit(work, miss_indices[k], p): k
                        for k, p in enumerate(miss_paths)
                    }
                    for fut in as_completed(futures):
                        # Re-raise any exception that escaped work() (none do
                        # by construction, but be defensive about future churn).
                        fut.result()
                        progress.update(task, advance=1)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                # consume the iterator so map() actually runs to completion
                list(pool.map(
                    lambda kp: work(miss_indices[kp[0]], kp[1]),
                    enumerate(miss_paths),
                ))

    # --- Cache write-back (Stage 3) ----------------------------------------
    # Only newly-parsed records get written. Records that came from a hit
    # are already in the db (and would just rewrite themselves identically).
    if cache is not None:
        try:
            to_put = [
                (paths_list[i], results[i])
                for i in miss_indices
                if results[i] is not None
            ]
            cache.put_many(to_put)
        finally:
            cache.close()

    # The None-typed sentinel can't actually leak (work() always assigns),
    # but the type checker doesn't know that. Filter for safety.
    return [r for r in results if r is not None]


def _read_one_lite(path: Path, rawpy_mod) -> dict[str, Any]:
    """Read one RAW file, return an exiftool-shape record dict (raw EXIF
    keys before `_normalize` collapses them).

    Strategy: try the pure-stdlib TIFF/EXIF parser first. If it returns a
    populated record (has Make/Model), we're done — no LibRaw work needed,
    so we skip the multi-hundred-millisecond `rawpy.imread()` call. If
    the parser came up empty (unknown format, truncated file, ...), fall
    back to rawpy so we still return *something* for the file (matching
    the long-standing rawkit guarantee that any file LibRaw can decode
    appears in `ls` output).

    Per-file errors are swallowed: a corrupted file yields a record with
    just `SourceFile`, never crashes the batch.
    """
    rec: dict[str, Any] = {"SourceFile": str(path)}

    try:
        exif_block = _exif_lite.read_metadata(path)
    except _exif_lite.ExifLiteError:
        exif_block = {}
    except Exception:
        # Defensive: any parser bug must not nuke the whole batch.
        exif_block = {}
    rec.update(exif_block)

    # Fast path: if the TIFF parser got the basics (Make + Model present),
    # we have everything rawkit cares about. Don't pay the rawpy tax.
    have_basics = "Make" in exif_block and "Model" in exif_block

    if not have_basics:
        # Fallback: ask rawpy/LibRaw. Slow (≈50–350 ms / file on USB SSD)
        # but handles formats / makers our TIFF parser can't reach.
        try:
            with rawpy_mod.imread(str(path)) as raw:
                _augment_from_rawpy(rec, raw)
        except (rawpy_mod.LibRawError, OSError, MemoryError):
            # File unreadable by LibRaw too — record stays empty-ish.
            pass

    # Match exiftool's "Rating defaults to 0 when no rating tag found"
    # behaviour: if we read ANY metadata but no explicit Rating, set 0.
    # Skipping this would make `--where rating==0` always-empty on the
    # lite backend (different from the exiftool path → user-visible drift).
    if (exif_block or len(rec) > 1) and "Rating" not in rec:
        rec["Rating"] = 0
    return rec


def _augment_from_rawpy(rec: dict[str, Any], raw: Any) -> None:
    """Fill in fields rawpy/LibRaw exposes that are either missing from
    our EXIF parse, or that LibRaw resolves more reliably (e.g. ISO from
    Panasonic MakerNotes).

    setdefault() ordering matters: standard EXIF wins over LibRaw because
    (a) it's authoritative for the standard tags, and (b) the exiftool
    backend uses standard EXIF too — matching that for cross-backend
    consistency.
    """
    other = getattr(raw, "other", None)
    lens = getattr(raw, "lens", None)
    sizes = getattr(raw, "sizes", None)

    if other is not None:
        # DateTimeOriginal: LibRaw stores as a Python datetime. Convert to
        # the colon-separated 'YYYY:MM:DD HH:MM:SS' wire format exiftool
        # emits — the existing _normalize() expects that shape and rewrites
        # the date colons to dashes.
        #
        # Skip 1970 — LibRaw uses Unix epoch 0 as its "no DateTime found"
        # sentinel and surfaces it as datetime.fromtimestamp(0), which after
        # local-timezone conversion lands at 1970-01-01 HH:00 (HH = your UTC
        # offset). Without filtering, files with no/corrupt date metadata
        # land in user output as fake 1970 captures. Real raws are never
        # from 1970 — digital photography started in the late 80s.
        ts = getattr(other, "timestamp", None)
        if (
            isinstance(ts, datetime)
            and "DateTimeOriginal" not in rec
            and ts.year > 1970
        ):
            rec["DateTimeOriginal"] = ts.strftime("%Y:%m:%d %H:%M:%S")

        iso = getattr(other, "iso_speed", 0) or 0
        if iso > 0:
            rec.setdefault("ISO", int(iso))

        ap = getattr(other, "aperture", 0) or 0
        if ap > 0:
            rec.setdefault("FNumber", float(ap))

        sh = getattr(other, "shutter_speed", 0) or 0
        if sh > 0:
            rec.setdefault("ExposureTime", float(sh))

        # rawpy renamed `focal_len` → `focal_length` somewhere along the
        # way; accept either rather than picking one and breaking on the
        # other version of rawpy.
        fl = getattr(other, "focal_length", None) or getattr(other, "focal_len", None) or 0
        if fl and fl > 0:
            rec.setdefault("FocalLength", float(fl))

    if lens is not None:
        lm = getattr(lens, "model", b"")
        if isinstance(lm, bytes):
            lm = lm.decode("utf-8", "replace")
        if isinstance(lm, str) and lm.strip():
            rec.setdefault("LensModel", lm.strip())

    if sizes is not None:
        # `sizes.height`/`width` is the demosaiced output (slightly smaller
        # than raw_height/width — sensor border crop). That matches what
        # exiftool's ImageWidth/ImageHeight report for the rendered image.
        h = getattr(sizes, "height", 0) or 0
        w = getattr(sizes, "width", 0) or 0
        if h > 0 and w > 0:
            rec.setdefault("ImageHeight", int(h))
            rec.setdefault("ImageWidth", int(w))
        # LibRaw's flip is its OWN encoding (0/3/5/6), not EXIF Orientation.
        # Translate so _normalize sees the EXIF int it expects.
        if "Orientation" not in rec:
            flip = getattr(sizes, "flip", -1)
            mapped = _libraw_flip_to_exif_orientation(flip)
            if mapped is not None:
                rec["Orientation"] = mapped


# LibRaw `flip` → standard EXIF Orientation (the one rawkit's _normalize
# already buckets into 'portrait'/'landscape'):
#   flip 0  = no rotation       → EXIF 1 (landscape, top-left)
#   flip 3  = 180°               → EXIF 3 (landscape, bottom-right)
#   flip 5  = CCW 90° (portrait) → EXIF 8
#   flip 6  = CW  90° (portrait) → EXIF 6
# Anything else (rare; e.g. flip=-1 "unknown") returns None so _normalize
# treats it as missing rather than guessing.
_FLIP_TO_ORIENTATION = {0: 1, 3: 3, 5: 8, 6: 6}


def _libraw_flip_to_exif_orientation(flip: int) -> int | None:
    return _FLIP_TO_ORIENTATION.get(flip)


# --- exiftool backend (fallback) --------------------------------------------

def _batch_read_exiftool(paths_list: list[Path]) -> list[dict[str, Any]]:
    """Read EXIF for every path in ONE exiftool invocation.

    Same return shape as `_batch_read_lite`. Kept as the fallback backend
    for diagnostic comparison and for any RAW format whose IFD layout the
    lite parser doesn't yet handle.
    """
    path_strs = [str(p) for p in paths_list]
    if not path_strs:
        return []

    require_exiftool()
    # Pass paths via stdin using exiftool's `-@ -` argfile option instead of
    # argv. A recursive scan over a large library easily produces tens of
    # thousands of paths whose combined argv length exceeds the OS ARG_MAX
    # (~256KB on macOS), at which point execve() fails with E2BIG before
    # exiftool even starts. The argfile path has no such limit and preserves
    # the "one fork for the whole batch" performance contract above.
    args = (
        ["exiftool", "-j", "-n"]
        + [f"-{tag}" for tag, _key in _FIELD_MAP if tag != "SourceFile"]
        + ["-@", "-"]
    )
    # `-@` treats every non-empty line as one argument with no shell quoting,
    # so paths containing spaces (e.g. '/Volumes/T7 Shield/底片') work as-is.
    # Newlines in filenames would corrupt the stream, but POSIX paths and
    # macOS HFS+/APFS don't permit '\n' in filenames in practice.
    stdin_data = "\n".join(path_strs) + "\n"
    proc = subprocess.run(
        args, input=stdin_data, capture_output=True, text=True, check=False
    )
    # exiftool exits 1 when it emits warnings about individual files but
    # still produces valid JSON for the rest. Treat that as success.
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            f"exiftool failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    raw_records: list[dict[str, Any]] = json.loads(proc.stdout or "[]")
    return [_normalize(r) for r in raw_records]


# --- normalizer (shared by both backends) -----------------------------------

def _normalize(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tag, key in _FIELD_MAP:
        # exiftool's JSON output keys by the bare tag name even when the
        # request was group-qualified (`-EXIF:ISO` still emits `"ISO"`).
        json_key = tag.rsplit(":", 1)[-1]
        if json_key in record and record[json_key] not in (None, ""):
            value = record[json_key]
            # Strip trailing/leading whitespace on string fields. Some cameras
            # (e.g. Panasonic) write fixed-length EXIF strings padded out with
            # spaces — LensModel = 'DC VARIO-SUMMILUX ... ASPH.' + 19 spaces.
            # That padding is never part of the value and silently widens
            # every aligned table downstream.
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    continue
            out[key] = value

    _split_datetime(out, out.pop("_subsec_raw", None))
    _derive_date_time_buckets(out)
    _apply_apex_aperture_fallback(out, out.pop("_apex_raw", None))
    _classify_orientation(out, out.pop("_orientation_raw", None))
    _decode_flash(out, out.pop("_flash_raw", None))
    _strip_maker_prefix_from_model(out)

    if "gps_lat" in out and "gps_lon" in out:
        out["gps"] = True

    return out


def _split_datetime(out: dict[str, Any], subsec: Any) -> None:
    """Split `DateTimeOriginal` ('YYYY:MM:DD HH:MM:SS') into `datetime`/`date`/
    `time` strings, stitching in `SubSecTimeOriginal` when the camera wrote it.

    We expose all three so DSL queries can target the precision the user
    actually means:
      datetime = 'YYYY-MM-DD HH:MM:SS[.NNN]'   (full, lexicographically sortable)
      date     = 'YYYY-MM-DD'                  (calendar day)
      time     = 'HH:MM:SS[.NNN]'              (time of day; sub-second when present)

    SubSec stitching matters because burst frames within the same second
    only sort correctly when the fractional part survives.
    """
    dt = out.get("datetime")
    if not (isinstance(dt, str) and len(dt) >= 19 and dt[4] == ":" and dt[7] == ":"):
        return
    normalized = dt[:4] + "-" + dt[5:7] + "-" + dt[8:]
    # exiftool returns SubSecTimeOriginal as str for the ASCII case (most
    # cameras) and as int in `-n` mode when the value is all digits. The
    # lite backend always returns str. Accept both.
    subsec_str: str | None = None
    if isinstance(subsec, str) and subsec.strip():
        subsec_str = subsec.strip()
    elif isinstance(subsec, int):
        subsec_str = str(subsec)
    suffix = ("." + subsec_str) if subsec_str else ""
    out["datetime"] = normalized + suffix
    out["date"] = normalized[:10]
    out["time"] = normalized[11:19] + suffix


def _derive_date_time_buckets(out: dict[str, Any]) -> None:
    """Add integer bucket fields `year`/`month`/`day`/`hour` from the
    derived date/time strings, for DSL comparisons like `month==11` or
    `hour>=18`. These are bucket IDs, not time cutoffs — `hour > 6`
    means hour>=7, NOT 'after 06:00:00' (use `time > "06:00:00"` for that).
    """
    date = out.get("date")
    if isinstance(date, str) and len(date) >= 10:
        try:
            out["year"]  = int(date[0:4])
            out["month"] = int(date[5:7])
            out["day"]   = int(date[8:10])
        except ValueError:
            pass
    time_str = out.get("time")
    if isinstance(time_str, str) and len(time_str) >= 2:
        try:
            out["hour"] = int(time_str[0:2])
        except ValueError:
            pass


def _apply_apex_aperture_fallback(out: dict[str, Any], apex_raw: Any) -> None:
    """When EXIF:FNumber is absent (Leica M11M and similar minimalist DNGs
    only write APEX ApertureValue), reconstruct f-number from APEX:
    N = 2^(av/2) — av=2 → f/2; av=4 → f/4.
    """
    if "fnumber" in out or apex_raw is None:
        return
    try:
        out["fnumber"] = round(2.0 ** (float(apex_raw) / 2.0), 1)
    except (TypeError, ValueError):
        pass


def _classify_orientation(out: dict[str, Any], raw: Any) -> None:
    """Collapse EXIF Orientation 1..8 into a coarse `portrait`/`landscape`
    label for the DSL (`--where orientation=='portrait'`). 1..4 are upright
    variants → landscape; 5..8 are 90°-rotated variants → portrait.
    """
    if raw is None:
        return
    try:
        o = int(raw)
    except (TypeError, ValueError):
        return
    if o in (5, 6, 7, 8):
        out["orientation"] = "portrait"
    elif o in (1, 2, 3, 4):
        out["orientation"] = "landscape"


def _decode_flash(out: dict[str, Any], raw: Any) -> None:
    """EXIF Flash is a bitfield; bit 0 = 'flash fired'. We only expose the
    boolean — anyone needing the rest of the bits (red-eye mode, return
    detection, etc.) should call exiftool directly.
    """
    if raw is None:
        return
    try:
        out["flash"] = bool(int(raw) & 1)
    except (TypeError, ValueError):
        pass


def _strip_maker_prefix_from_model(out: dict[str, Any]) -> None:
    """Canon/Nikon/Leica/Ricoh write Model as `"<MAKER> <body>"` ('Canon EOS R5',
    'NIKON Z5_2', 'LEICA M11 Monochrom'). We already expose `maker` separately,
    so repeating the maker bloats the model column in `ls`. Sony / Fuji / OM /
    Hasselblad don't add the prefix, so they're untouched.
    """
    model = out.get("model")
    maker = out.get("maker")
    if not (isinstance(model, str) and isinstance(maker, str) and maker):
        return
    prefix = maker.split()[0] if maker.split() else ""
    if not prefix:
        return
    pfx_with_space = (prefix + " ").upper()
    if model.upper().startswith(pfx_with_space):
        stripped = model[len(prefix):].lstrip()
        if stripped:  # don't reduce model to empty string
            out["model"] = stripped


# --- typer-friendly error wrapper -------------------------------------------

def safe_batch_read(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Like `batch_read` but converts ExiftoolMissing into a typer.Exit
    with a human-readable stderr message. CLI commands should call this."""
    try:
        return batch_read(paths)
    except ExiftoolMissing as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
