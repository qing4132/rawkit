"""Quick exiftool-vs-lite backend benchmark.

Usage:
    python scripts/bench_exif.py /path/to/raw/library [N]

Picks N random files (default 500) from a recursive walk of the given
directory, then times `exif.batch_read` once with RAWKIT_BACKEND=exiftool
and once with RAWKIT_BACKEND=lite. Reports total time, throughput, and
the speedup ratio. Designed to be run from the repo root.

The same file list is fed to both backends so the comparison is apples-
to-apples (no random sampling drift between runs).
"""

from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

# Make sure we hit the local source tree, not whichever rawkit happens to
# be installed system-wide.
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from rawkit import exif  # noqa: E402

RAW_EXTS = {".cr3", ".arw", ".dng", ".rw2", ".3fr", ".nef", ".raf", ".cr2"}


def collect(root: Path, n: int) -> list[Path]:
    print(f"Walking {root} ...", flush=True)
    t0 = time.perf_counter()
    files = [p for p in root.rglob("*") if p.suffix.lower() in RAW_EXTS]
    print(f"  found {len(files)} RAW files in {time.perf_counter()-t0:.1f}s", flush=True)
    if len(files) > n:
        random.seed(42)
        files = random.sample(files, n)
    print(f"  using {len(files)} for the benchmark", flush=True)
    return files


def run(label: str, backend: str, files: list[Path]) -> float:
    os.environ["RAWKIT_BACKEND"] = backend
    os.environ["RAWKIT_NO_PROGRESS"] = "1"
    t0 = time.perf_counter()
    recs = exif.batch_read(files)
    dt = time.perf_counter() - t0
    assert len(recs) == len(files)
    rate = len(files) / dt
    print(f"  {label:10s}  {dt:8.2f}s   {rate:7.1f} files/s   ({len(recs)} records)", flush=True)
    return dt


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    root = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        sys.exit(2)

    files = collect(root, n)
    if not files:
        print("no RAW files found", file=sys.stderr)
        sys.exit(3)

    print("\nbenchmark (lower is better):")
    t_lite = run("lite",     "lite",     files)
    t_etool = run("exiftool", "exiftool", files)

    print()
    if t_lite > 0:
        print(f"speedup: lite is {t_etool / t_lite:.1f}x faster than exiftool")


if __name__ == "__main__":
    main()
