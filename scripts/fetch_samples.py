"""Download extra RAW samples from a manifest into samples/extras/.

Why this exists
---------------
samples/ ships ~8 RAW formats from the maintainer's own gear (CR3/ARW/NEF/
DNG/RAF/RW2/ORF/3FR). _exif_lite.py is supposed to handle every format in
cli.RAW_EXTS (38 in total). The other ~30 formats — CR2, X3F, PEF, NRW, IIQ,
SRW, CRW, RWL, MEF, MOS, ERF, ... — need test coverage but nobody owns all
those cameras.

Solution: list a few public-domain / CC0 samples per format in
tests/fixtures/extra_samples.toml, download them on demand, verify by sha256
(nix/bazel style: empty hash means "first download — print observed hash for
me to fill in"), and let the existing RAWKIT_TEST_SAMPLES-driven equivalence
tests run against them.

Run:
    uv run python scripts/fetch_samples.py
    # then:
    RAWKIT_TEST_SAMPLES=samples/extras uv run pytest tests/test_exif_lite.py -v

Network failures are non-fatal: an unreachable URL is reported and skipped,
the rest of the manifest still downloads. This is a developer convenience
tool, not part of the install or test pipeline.

CLI:
    --manifest PATH   path to the TOML manifest (default: tests/fixtures/extra_samples.toml)
    --dest PATH       download dir (default: samples/extras)
    --force           re-download even if file already exists
    --skip-verify     don't check sha256 (debug only)
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "tests" / "fixtures" / "extra_samples.toml"
DEFAULT_DEST = REPO_ROOT / "samples" / "extras"

# Honor the manifest's "be a polite citizen" intent: identify ourselves so
# server operators (raw.pixls.us etc.) can rate-limit or block if needed.
USER_AGENT = "rawkit-fetch-samples/0.1 (+https://github.com/qing4132/rawkit)"


@dataclass
class Entry:
    url: str
    sha256: str
    format: str
    camera: str
    license: str
    source: str
    tier: str
    notes: str

    @property
    def filename(self) -> str:
        """Derive a stable on-disk name from the URL's last path segment.

        We don't trust Content-Disposition; the URL tail is what's reviewable
        in the manifest. URL-encoded characters (`%20` etc.) are kept as-is
        — they're valid filename characters and round-trip cleanly.
        """
        tail = self.url.rsplit("/", 1)[-1]
        # Some download URLs end with `?query=foo`; strip the query.
        tail = tail.split("?", 1)[0]
        return tail or f"sample.{self.format}"


def load_manifest(path: Path) -> list[Entry]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    raw_entries = data.get("file", []) or []
    out: list[Entry] = []
    for i, raw in enumerate(raw_entries):
        try:
            out.append(Entry(
                url=str(raw["url"]).strip(),
                sha256=str(raw.get("sha256", "")).strip().lower(),
                format=str(raw["format"]).strip().lower(),
                camera=str(raw.get("camera", "")).strip(),
                license=str(raw.get("license", "")).strip(),
                source=str(raw.get("source", "")).strip(),
                # tier defaults to 'mainstream' so older manifest entries that
                # predate the field don't silently get reclassified to legacy.
                tier=str(raw.get("tier", "mainstream")).strip().lower(),
                notes=str(raw.get("notes", "")).strip(),
            ))
        except KeyError as e:
            print(f"[skip] entry #{i}: missing required field {e}", file=sys.stderr)
    return out


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    """Stream-download to a .part file, then atomic-rename on success.

    Atomic rename keeps the destination either fully present or absent —
    a SIGINT mid-download won't leave a truncated file masquerading as
    a finished one (which would then sha256-mismatch confusingly later).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    part.replace(dest)


def process(entry: Entry, dest_dir: Path, *, force: bool, skip_verify: bool) -> str:
    """Returns one of: 'ok', 'cached', 'new-hash', 'mismatch', 'failed'."""
    dest = dest_dir / entry.filename

    if dest.exists() and not force:
        if skip_verify or not entry.sha256:
            return "cached"
        got = sha256_of(dest)
        if got == entry.sha256:
            return "cached"
        print(
            f"  ! existing file's sha256 mismatches manifest:\n"
            f"      file:     {dest}\n"
            f"      expected: {entry.sha256}\n"
            f"      got:      {got}\n"
            f"      (re-run with --force to redownload)",
            file=sys.stderr,
        )
        return "mismatch"

    print(f"  downloading {entry.url} ...")
    try:
        download(entry.url, dest)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"  ! download failed: {e}", file=sys.stderr)
        return "failed"

    if skip_verify:
        return "ok"

    got = sha256_of(dest)
    if not entry.sha256:
        # First-time download: print the observed hash so the user can
        # paste it back into the manifest. We keep the file (it's likely
        # what they wanted) but flag it so they don't forget to pin.
        print(
            f"  + downloaded; manifest sha256 was empty. Observed:\n"
            f"      sha256   = \"{got}\"\n"
            f"    → paste this into the [[file]] entry in {DEFAULT_MANIFEST.relative_to(REPO_ROOT)} "
            "to pin it."
        )
        return "new-hash"
    if got != entry.sha256:
        print(
            f"  ! sha256 mismatch — file was downloaded but is suspect:\n"
            f"      expected: {entry.sha256}\n"
            f"      got:      {got}",
            file=sys.stderr,
        )
        return "mismatch"
    return "ok"


def summarize(results: Iterable[tuple[Entry, str]]) -> int:
    """Print a one-line summary table and return a process exit code.

    Exit non-zero only on hard failures (download error, sha256 mismatch
    against a pinned hash). 'new-hash' is informational, not an error —
    a fresh manifest entry is expected to land here on first run.
    """
    counts: dict[str, int] = {}
    for _, status in results:
        counts[status] = counts.get(status, 0) + 1
    parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"\nsummary: {parts or '(no entries)'}")
    bad = counts.get("failed", 0) + counts.get("mismatch", 0)
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--skip-verify", action="store_true")
    ap.add_argument(
        "--legacy",
        action="store_true",
        help="also pull tier='legacy' entries (discontinued / dead-end formats "
             "kept as 'lite must not crash' regression samples). Default off.",
    )
    args = ap.parse_args(argv)

    if not args.manifest.exists():
        print(f"manifest not found: {args.manifest}", file=sys.stderr)
        return 2

    entries = load_manifest(args.manifest)
    if not entries:
        print(
            f"manifest is empty: {args.manifest}\n"
            "Add [[file]] entries (see comments in the manifest for the format) "
            "and re-run."
        )
        return 0

    if not args.legacy:
        skipped = [e for e in entries if e.tier == "legacy"]
        entries = [e for e in entries if e.tier != "legacy"]
        if skipped:
            print(
                f"skipping {len(skipped)} legacy entr{'y' if len(skipped) == 1 else 'ies'} "
                f"(pass --legacy to include): "
                + ", ".join(f"{e.format}/{e.camera}" for e in skipped[:6])
                + (" ..." if len(skipped) > 6 else "")
            )

    # Quick preview before any network I/O so the user sees what's about to
    # happen — useful when the manifest grows or you forget --legacy.
    print(f"manifest: {args.manifest}")
    print(f"dest:     {args.dest}")
    print("to pull:  " + ", ".join(
        f"{e.format}({e.camera})" for e in entries[:8]
    ) + (" ..." if len(entries) > 8 else ""))
    print(f"count:    {len(entries)}\n")

    args.dest.mkdir(parents=True, exist_ok=True)

    results: list[tuple[Entry, str]] = []
    for i, entry in enumerate(entries, 1):
        print(f"[{i}/{len(entries)}] {entry.format} — {entry.camera or '?'} ({entry.source or '?'})")
        status = process(entry, args.dest, force=args.force, skip_verify=args.skip_verify)
        results.append((entry, status))

    return summarize(results)


if __name__ == "__main__":
    sys.exit(main())
