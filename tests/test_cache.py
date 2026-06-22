"""Tests for rawkit's EXIF cache (`rawkit._cache` + integration points).

Three layers:

  1. Unit tests for `ExifCache` itself: schema setup, get_many partition,
     put_many round-trip, stat-key staleness, relocate/duplicate, info/
     clear/vacuum, and the persisted disable flag.

  2. Integration with `rawkit.exif._batch_read_lite`: cache populates on
     first run, second run hits, files mutate → re-parse, files deleted →
     drop out cleanly.

  3. End-to-end with the CLI: `rawkit organize` updates cache rows on
     move/copy, `rawkit cache info/clear/vacuum/enable/disable` work
     against the on-disk db.

Every test points `RAWKIT_CACHE_DIR` at a `tmp_path` so the user's real
cache (~/Library/Caches/rawkit/) is never touched. The fixture is autouse
so even tests that don't think about caching get sandboxed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rawkit import _cache as cache_mod
from rawkit._cache import ExifCache, _default_cache_path, env_disabled
from rawkit.cli import app

runner = CliRunner()


# --- Sandbox fixture --------------------------------------------------------

@pytest.fixture(autouse=True)
def _sandbox_cache_dir(tmp_path, monkeypatch):
    """Redirect every cache write to a fresh tmp dir per test.

    Side benefit: makes parallel pytest workers safe (each gets its own
    cache file via per-process tmp_path).
    """
    monkeypatch.setenv("RAWKIT_CACHE_DIR", str(tmp_path / "cache"))
    # Some tests set RAWKIT_NO_CACHE — pop it preemptively so the autouse
    # fixture's effect is deterministic regardless of test ordering.
    monkeypatch.delenv("RAWKIT_NO_CACHE", raising=False)
    yield


# --- Helpers ---------------------------------------------------------------

def _make_raw_like(p: Path, content: bytes = b"raw-bytes") -> None:
    """Create a stand-in 'RAW' file. The cache layer doesn't care about
    the contents — only stat() — so any bytes work."""
    p.write_bytes(content)


def _record(path: Path, **overrides) -> dict:
    """Minimal but realistic rawkit EXIF record dict."""
    base = {
        "path":      str(path),
        "datetime":  "2024-01-02 03:04:05",
        "date":      "2024-01-02",
        "time":      "03:04:05",
        "maker":     "Canon",
        "model":     "EOS R5",
        "iso":       800,
        "fnumber":   1.8,
        "shutter":   0.004,
        "focal":     50.0,
    }
    base.update(overrides)
    return base


# --- env / path resolution --------------------------------------------------

def test_default_cache_path_honors_env(tmp_path, monkeypatch) -> None:
    """RAWKIT_CACHE_DIR redirects the db location; sandboxed by fixture."""
    monkeypatch.setenv("RAWKIT_CACHE_DIR", str(tmp_path / "x"))
    assert _default_cache_path() == tmp_path / "x" / "index.sqlite"


def test_default_cache_path_macos_fallback(tmp_path, monkeypatch) -> None:
    """When RAWKIT_CACHE_DIR is unset on macOS, db lands under ~/Library/Caches."""
    monkeypatch.delenv("RAWKIT_CACHE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "Library" / "Caches").mkdir(parents=True)
    p = _default_cache_path()
    assert p == tmp_path / "Library" / "Caches" / "rawkit" / "v1" / "index.sqlite"


def test_env_disabled_recognizes_values(monkeypatch) -> None:
    """Truthy values disable; '0'/'false'/empty don't."""
    for val, want in [("1", True), ("yes", True), ("anything", True),
                      ("0", False), ("false", False), ("", False)]:
        if val == "":
            monkeypatch.delenv("RAWKIT_NO_CACHE", raising=False)
        else:
            monkeypatch.setenv("RAWKIT_NO_CACHE", val)
        assert env_disabled() is want, f"value={val!r}"


def test_open_returns_none_when_env_disabled(monkeypatch) -> None:
    monkeypatch.setenv("RAWKIT_NO_CACHE", "1")
    assert ExifCache.open() is None


# --- schema & lifecycle -----------------------------------------------------

def test_open_creates_db_with_schema_and_meta() -> None:
    cache = ExifCache.open()
    assert cache is not None
    info = cache.info()
    cache.close()
    assert info["schema_version"] == cache_mod.SCHEMA_VERSION
    assert info["enabled"] is True
    assert info["row_count"] == 0
    assert info["last_vacuum_at"] == "never"


def test_open_after_close_reuses_file() -> None:
    """Second open sees the same persisted db."""
    cache = ExifCache.open()
    assert cache is not None
    path = Path(cache.info()["path"])
    cache.close()
    assert path.exists()

    cache2 = ExifCache.open()
    assert cache2 is not None
    assert Path(cache2.info()["path"]) == path
    cache2.close()


def test_schema_mismatch_silently_rebuilds(tmp_path, monkeypatch) -> None:
    """Bumping SCHEMA_VERSION → existing db dropped, no user error.

    The on-disk db has user_version=N; a code upgrade bumps the constant
    to N+something. The opener must detect that and rebuild silently.
    user_version=0 is reserved for "brand new", so we set 1 explicitly
    even though that's the current value — the test then bumps the
    in-process constant to 99.
    """
    cache = ExifCache.open()
    assert cache is not None
    p = tmp_path / "x.CR3"; _make_raw_like(p)
    cache.put_many([(p, _record(p))])
    cache.close()

    # The opener should detect the drift (on-disk=1, code=99) and rebuild.
    monkeypatch.setattr(cache_mod, "SCHEMA_VERSION", 99)
    cache2 = ExifCache.open()
    assert cache2 is not None
    assert cache2.info()["row_count"] == 0  # old rows wiped
    cache2.close()


def test_corrupted_db_open_returns_none(tmp_path, monkeypatch) -> None:
    """A garbage db file shouldn't crash callers — they should see None
    and continue without caching."""
    db = _default_cache_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"not a sqlite database, just random bytes" * 100)
    assert ExifCache.open() is None


# --- get_many / put_many round trip -----------------------------------------

def test_put_and_get_round_trip_exact(tmp_path) -> None:
    a = tmp_path / "a.CR3"; _make_raw_like(a, b"a-bytes")
    b = tmp_path / "b.ARW"; _make_raw_like(b, b"bb-bytes")

    cache = ExifCache.open()
    assert cache is not None
    rec_a = _record(a, iso=400)
    rec_b = _record(b, iso=12800, maker="SONY")
    cache.put_many([(a, rec_a), (b, rec_b)])

    hits, misses = cache.get_many([a, b])
    cache.close()
    assert misses == []
    assert hits[0] == rec_a
    assert hits[1] == rec_b


def test_get_many_partitions_hits_and_misses(tmp_path) -> None:
    a = tmp_path / "a.CR3"; _make_raw_like(a)
    b = tmp_path / "b.ARW"; _make_raw_like(b)
    c = tmp_path / "c.NEF"; _make_raw_like(c)

    cache = ExifCache.open()
    assert cache is not None
    cache.put_many([(a, _record(a)), (c, _record(c))])

    hits, misses = cache.get_many([a, b, c])
    cache.close()
    # a and c hit (positions 0, 2); b misses (position 1).
    assert set(hits.keys()) == {0, 2}
    assert misses == [1]


def test_empty_paths_returns_empty(tmp_path) -> None:
    cache = ExifCache.open()
    assert cache is not None
    hits, misses = cache.get_many([])
    cache.close()
    assert hits == {} and misses == []


def test_mtime_change_invalidates_hit(tmp_path) -> None:
    """The whole point of the staleness 4-tuple: mtime drift → miss."""
    a = tmp_path / "a.CR3"; _make_raw_like(a)
    cache = ExifCache.open()
    assert cache is not None
    cache.put_many([(a, _record(a))])

    # Bump mtime forward by 10 s.
    st = a.stat()
    os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))

    hits, misses = cache.get_many([a])
    cache.close()
    assert hits == {} and misses == [0]


def test_size_change_invalidates_hit(tmp_path) -> None:
    a = tmp_path / "a.CR3"; _make_raw_like(a, b"short")
    cache = ExifCache.open()
    assert cache is not None
    cache.put_many([(a, _record(a))])

    # Append to the file — size changes, mtime usually does too. Force both.
    st_before = a.stat()
    a.write_bytes(b"short" + b"longer")
    # Preserve mtime so the test is specifically about size mismatch.
    os.utime(a, ns=(st_before.st_atime_ns, st_before.st_mtime_ns))

    hits, misses = cache.get_many([a])
    cache.close()
    assert hits == {} and misses == [0]


def test_missing_file_is_miss_not_crash(tmp_path) -> None:
    """File in cache but gone from disk → returned as a miss without
    raising. Upstream parser will then fail naturally."""
    a = tmp_path / "a.CR3"; _make_raw_like(a)
    cache = ExifCache.open()
    assert cache is not None
    cache.put_many([(a, _record(a))])
    a.unlink()

    hits, misses = cache.get_many([a])
    cache.close()
    assert hits == {} and misses == [0]


def test_put_many_skips_vanished_files(tmp_path) -> None:
    """If a file disappeared between parse and write-back, just skip it —
    don't crash. The user already got the record back from the parser."""
    a = tmp_path / "a.CR3"; _make_raw_like(a)
    b = tmp_path / "b.CR3"  # never created

    cache = ExifCache.open()
    assert cache is not None
    cache.put_many([(a, _record(a)), (b, _record(b))])
    info = cache.info()
    cache.close()
    assert info["row_count"] == 1  # only `a` got cached


def test_corrupted_payload_is_miss(tmp_path) -> None:
    """Manually inject a non-JSON payload — get_many should treat it as
    a miss rather than blowing up the user's `rawkit ls`."""
    a = tmp_path / "a.CR3"; _make_raw_like(a)
    cache = ExifCache.open()
    assert cache is not None
    cache.put_many([(a, _record(a))])
    # Corrupt the payload directly via SQL.
    cache._conn.execute(
        "UPDATE exif_cache SET payload = ? WHERE abspath = ?",
        (b"\xff\xff not json \xff", os.path.abspath(str(a))),
    )
    hits, misses = cache.get_many([a])
    cache.close()
    assert hits == {} and misses == [0]


def test_put_many_overwrites_existing(tmp_path) -> None:
    """Re-parsing the same file gives a new record — the row must be
    replaced wholesale, not appended to."""
    a = tmp_path / "a.CR3"; _make_raw_like(a)
    cache = ExifCache.open()
    assert cache is not None
    cache.put_many([(a, _record(a, iso=400))])
    cache.put_many([(a, _record(a, iso=12800))])
    hits, _ = cache.get_many([a])
    cache.close()
    assert hits[0]["iso"] == 12800


# --- organize hooks ---------------------------------------------------------

def test_relocate_moves_row_to_new_path(tmp_path) -> None:
    src = tmp_path / "old" / "a.CR3"; src.parent.mkdir(); _make_raw_like(src)
    cache = ExifCache.open(); assert cache is not None
    cache.put_many([(src, _record(src, iso=1600))])

    dst = tmp_path / "new" / "a.CR3"; dst.parent.mkdir()
    src.rename(dst)  # simulate organize-move
    cache.relocate(src, dst)

    hits, misses = cache.get_many([dst])
    cache.close()
    assert misses == []
    assert hits[0]["iso"] == 1600
    # Path field inside payload was rewritten so `rawkit ls` shows new loc.
    assert hits[0]["path"] == os.path.abspath(str(dst))


def test_relocate_drops_old_row(tmp_path) -> None:
    """After a move, looking up the OLD path must be a miss."""
    src = tmp_path / "old" / "a.CR3"; src.parent.mkdir(); _make_raw_like(src)
    cache = ExifCache.open(); assert cache is not None
    cache.put_many([(src, _record(src))])

    dst = tmp_path / "new" / "a.CR3"; dst.parent.mkdir()
    src.rename(dst)
    cache.relocate(src, dst)

    # Recreate something at the old path so get_many can stat it.
    src.parent.mkdir(exist_ok=True)
    _make_raw_like(src)
    hits, misses = cache.get_many([src])
    cache.close()
    assert hits == {} and misses == [0]


def test_relocate_unknown_source_is_noop(tmp_path) -> None:
    """If the src wasn't in the cache (e.g. organize on a fresh db),
    relocate must not error and must not populate anything."""
    src = tmp_path / "a.CR3"; _make_raw_like(src)
    dst = tmp_path / "b.CR3"
    src.rename(dst)

    cache = ExifCache.open(); assert cache is not None
    cache.relocate(src, dst)  # should not raise
    info = cache.info(); cache.close()
    assert info["row_count"] == 0


def test_relocate_same_path_is_noop(tmp_path) -> None:
    """In-place organize (file already in correct bucket) — no change."""
    a = tmp_path / "a.CR3"; _make_raw_like(a)
    cache = ExifCache.open(); assert cache is not None
    cache.put_many([(a, _record(a))])
    cache.relocate(a, a)
    info = cache.info(); cache.close()
    assert info["row_count"] == 1


def test_duplicate_clones_row_keeps_source(tmp_path) -> None:
    """Copy semantics: the source row must remain valid; the destination
    gets its own row."""
    src = tmp_path / "a.CR3"; _make_raw_like(src)
    cache = ExifCache.open(); assert cache is not None
    cache.put_many([(src, _record(src))])

    dst = tmp_path / "dup" / "a.CR3"; dst.parent.mkdir()
    import shutil as _sh
    _sh.copy2(src, dst)
    cache.duplicate(src, dst)

    hits_src, _ = cache.get_many([src])
    hits_dst, _ = cache.get_many([dst])
    cache.close()
    assert 0 in hits_src
    assert 0 in hits_dst


# --- enable / disable ------------------------------------------------------

def test_disable_persists_across_open(tmp_path) -> None:
    ExifCache.set_enabled(False)
    # Default opener honors the disable flag.
    assert ExifCache.open() is None
    # `ignore_disabled` lets admin commands still poke at the db.
    forced = ExifCache.open(ignore_disabled=True)
    assert forced is not None
    forced.close()


def test_enable_reverses_disable() -> None:
    ExifCache.set_enabled(False)
    assert ExifCache.open() is None
    ExifCache.set_enabled(True)
    cache = ExifCache.open()
    assert cache is not None
    cache.close()


# --- info / clear / vacuum --------------------------------------------------

def test_info_keys_and_types(tmp_path) -> None:
    cache = ExifCache.open(); assert cache is not None
    a = tmp_path / "a.CR3"; _make_raw_like(a)
    cache.put_many([(a, _record(a))])
    info = cache.info(); cache.close()
    assert set(info.keys()) == {
        "path", "schema_version", "enabled", "row_count", "size_bytes",
        "rawkit_version", "created_at", "last_vacuum_at",
    }
    assert info["row_count"] == 1
    assert info["size_bytes"] > 0
    assert info["enabled"] is True


def test_clear_empties_table(tmp_path) -> None:
    cache = ExifCache.open(); assert cache is not None
    for i in range(5):
        p = tmp_path / f"x{i}.CR3"; _make_raw_like(p)
        cache.put_many([(p, _record(p))])
    n = cache.clear()
    info = cache.info(); cache.close()
    assert n == 5
    assert info["row_count"] == 0


def test_vacuum_removes_orphans(tmp_path) -> None:
    cache = ExifCache.open(); assert cache is not None
    alive = tmp_path / "alive.CR3"; _make_raw_like(alive)
    dead = tmp_path / "dead.CR3"; _make_raw_like(dead)
    cache.put_many([(alive, _record(alive)), (dead, _record(dead))])
    # Delete one file: its row becomes an orphan.
    dead.unlink()

    n = cache.vacuum()
    info = cache.info(); cache.close()
    assert n == 1
    assert info["row_count"] == 1
    assert info["last_vacuum_at"] != "never"


def test_vacuum_zero_orphans_still_stamps_meta(tmp_path) -> None:
    cache = ExifCache.open(); assert cache is not None
    a = tmp_path / "a.CR3"; _make_raw_like(a)
    cache.put_many([(a, _record(a))])
    n = cache.vacuum()
    info = cache.info(); cache.close()
    assert n == 0
    assert info["last_vacuum_at"] != "never"


# --- integration with _batch_read_lite -------------------------------------

def test_batch_read_lite_caches_then_hits(monkeypatch, tmp_path) -> None:
    """First call parses every file; second call hits the cache for all.

    We monkey-patch the actual lite reader to count parse invocations
    without needing real RAW files.
    """
    from rawkit import exif

    # 60 files (> _PROGRESS_THRESHOLD so the cache path activates).
    paths = []
    for i in range(60):
        p = tmp_path / f"f{i:02d}.CR3"
        _make_raw_like(p, f"f{i}".encode())
        paths.append(p)

    call_count = {"n": 0}

    def fake_read_one(path, _rawpy):
        call_count["n"] += 1
        return {
            "SourceFile": str(path),
            "Make": "Canon",
            "Model": "EOS R5",
            "EXIF:ISO": 400,
        }

    monkeypatch.setattr(exif, "_read_one_lite", fake_read_one)
    # Stub out the rawpy import inside _batch_read_lite.
    monkeypatch.setitem(sys.modules, "rawpy", type(sys)("rawpy"))
    # Make sure stderr is non-TTY so the progress bar code path doesn't try
    # to render a live display in the test runner.
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)

    # Cold run: all 60 parses happen.
    recs1 = exif._batch_read_lite(paths)
    assert len(recs1) == 60
    assert call_count["n"] == 60

    # Warm run: cache should serve every file → zero parses.
    recs2 = exif._batch_read_lite(paths)
    assert len(recs2) == 60
    assert call_count["n"] == 60  # unchanged


def test_batch_read_lite_under_threshold_skips_cache(monkeypatch, tmp_path) -> None:
    """Tiny batches don't pay the sqlite overhead — they always parse."""
    from rawkit import exif

    paths = []
    for i in range(5):  # well under _PROGRESS_THRESHOLD = 50
        p = tmp_path / f"f{i}.CR3"; _make_raw_like(p)
        paths.append(p)

    call_count = {"n": 0}

    def fake_read_one(path, _rawpy):
        call_count["n"] += 1
        return {"SourceFile": str(path), "Make": "Canon", "Model": "X"}

    monkeypatch.setattr(exif, "_read_one_lite", fake_read_one)
    monkeypatch.setitem(sys.modules, "rawpy", type(sys)("rawpy"))
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)

    exif._batch_read_lite(paths)
    exif._batch_read_lite(paths)
    # Both calls parsed every file: no cache db should even exist.
    assert call_count["n"] == 10
    assert not _default_cache_path().exists()


def test_batch_read_lite_invalidates_on_mtime_change(monkeypatch, tmp_path) -> None:
    """Touch a file → cache must re-parse only that one."""
    from rawkit import exif

    paths = [tmp_path / f"f{i:02d}.CR3" for i in range(55)]
    for p in paths:
        _make_raw_like(p)

    parsed: list[str] = []

    def fake_read_one(path, _rawpy):
        parsed.append(str(path))
        return {"SourceFile": str(path), "Make": "Canon", "Model": "X"}

    monkeypatch.setattr(exif, "_read_one_lite", fake_read_one)
    monkeypatch.setitem(sys.modules, "rawpy", type(sys)("rawpy"))
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)

    exif._batch_read_lite(paths)
    parsed.clear()

    # Touch just one file forward.
    target = paths[7]
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))

    exif._batch_read_lite(paths)
    assert parsed == [str(target)]  # only the touched one re-parsed


def test_batch_read_lite_respects_no_cache_env(monkeypatch, tmp_path) -> None:
    from rawkit import exif

    monkeypatch.setenv("RAWKIT_NO_CACHE", "1")
    paths = [tmp_path / f"f{i:02d}.CR3" for i in range(55)]
    for p in paths:
        _make_raw_like(p)

    call_count = {"n": 0}

    def fake_read_one(path, _rawpy):
        call_count["n"] += 1
        return {"SourceFile": str(path), "Make": "Canon", "Model": "X"}

    monkeypatch.setattr(exif, "_read_one_lite", fake_read_one)
    monkeypatch.setitem(sys.modules, "rawpy", type(sys)("rawpy"))
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)

    exif._batch_read_lite(paths)
    exif._batch_read_lite(paths)
    assert call_count["n"] == 2 * len(paths)
    assert not _default_cache_path().exists()


# --- end-to-end via CLI -----------------------------------------------------

def test_cli_cache_info_before_any_run() -> None:
    """`rawkit cache info` is callable even with no db yet — no crash,
    helpful message."""
    result = runner.invoke(app, ["cache", "info"])
    assert result.exit_code == 0
    assert "not yet created" in result.stdout


def test_cli_cache_info_after_population(tmp_path) -> None:
    """Populate the cache directly, then read its summary via the CLI."""
    a = tmp_path / "a.CR3"; _make_raw_like(a)
    cache = ExifCache.open(); assert cache is not None
    cache.put_many([(a, _record(a))]); cache.close()

    result = runner.invoke(app, ["cache", "info"])
    assert result.exit_code == 0
    assert "rows:           1" in result.stdout
    assert "enabled:        yes" in result.stdout


def test_cli_cache_clear_requires_yes_in_script(tmp_path) -> None:
    """Without --yes and with non-TTY stdin, refuse to clear (safety)."""
    a = tmp_path / "a.CR3"; _make_raw_like(a)
    cache = ExifCache.open(); assert cache is not None
    cache.put_many([(a, _record(a))]); cache.close()

    result = runner.invoke(app, ["cache", "clear"])
    assert result.exit_code == 2
    assert "refusing" in result.stderr.lower()
    # Row still there.
    assert ExifCache.open().info()["row_count"] == 1  # type: ignore[union-attr]


def test_cli_cache_clear_yes_works(tmp_path) -> None:
    a = tmp_path / "a.CR3"; _make_raw_like(a)
    cache = ExifCache.open(); assert cache is not None
    cache.put_many([(a, _record(a))]); cache.close()

    result = runner.invoke(app, ["cache", "clear", "--yes"])
    assert result.exit_code == 0
    assert "cleared 1 row" in result.stdout

    cache2 = ExifCache.open(); assert cache2 is not None
    assert cache2.info()["row_count"] == 0
    cache2.close()


def test_cli_cache_vacuum_reports_orphans(tmp_path) -> None:
    alive = tmp_path / "alive.CR3"; _make_raw_like(alive)
    dead = tmp_path / "dead.CR3"; _make_raw_like(dead)
    cache = ExifCache.open(); assert cache is not None
    cache.put_many([(alive, _record(alive)), (dead, _record(dead))]); cache.close()
    dead.unlink()

    result = runner.invoke(app, ["cache", "vacuum"])
    assert result.exit_code == 0
    assert "removed 1 orphan" in result.stdout


def test_cli_cache_disable_enable_cycle() -> None:
    """`cache disable` persists; subsequent normal opens get None;
    `cache enable` reverses."""
    result = runner.invoke(app, ["cache", "disable"])
    assert result.exit_code == 0
    assert "disabled" in result.stdout
    assert ExifCache.open() is None

    result = runner.invoke(app, ["cache", "enable"])
    assert result.exit_code == 0
    assert "enabled" in result.stdout
    cache = ExifCache.open(); assert cache is not None
    cache.close()


def test_cli_cache_info_with_env_disabled(monkeypatch, tmp_path) -> None:
    """Even with RAWKIT_NO_CACHE set, `cache info` still gives a useful
    message — we want users to be able to debug their disabled state."""
    a = tmp_path / "a.CR3"; _make_raw_like(a)
    cache = ExifCache.open(); assert cache is not None
    cache.put_many([(a, _record(a))]); cache.close()

    # CliRunner doesn't propagate env to the subprocess; mutate the parent
    # env so the in-process invocation sees it.
    monkeypatch.setenv("RAWKIT_NO_CACHE", "1")
    result = runner.invoke(app, ["cache", "info"])
    assert result.exit_code == 0
    assert "RAWKIT_NO_CACHE" in result.stdout


# --- end-to-end with organize ----------------------------------------------

def test_organize_move_rekeys_cache(tmp_path, monkeypatch) -> None:
    """Moving a RAW under `rawkit organize` must update the cache row to
    the new path, so the next `rawkit ls` doesn't re-parse it."""
    src_dir = tmp_path / "src"; src_dir.mkdir()
    a = src_dir / "a.CR3"; _make_raw_like(a)

    # Pre-populate the cache so we can assert relocation.
    cache = ExifCache.open(); assert cache is not None
    cache.put_many([(a, _record(a, iso=6400))]); cache.close()

    out = tmp_path / "by-iso"

    # Stub safe_batch_read so organize doesn't need real EXIF.
    def fake_read(paths):
        return [_record(Path(p), iso=6400) for p in paths]
    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake_read)

    result = runner.invoke(
        app, ["organize", str(src_dir), "-o", str(out), "--by", "iso"],
    )
    assert result.exit_code == 0, result.stderr

    # The moved file should be under out/6400/a.CR3 (or similar bucket).
    moved = list(out.rglob("a.CR3"))
    assert len(moved) == 1

    # Cache should now know the new path with no miss.
    cache2 = ExifCache.open(); assert cache2 is not None
    hits, misses = cache2.get_many(moved)
    assert misses == []
    assert hits[0]["iso"] == 6400
    # Old path is gone from the cache.
    info = cache2.info()
    cache2.close()
    assert info["row_count"] == 1


def test_organize_copy_duplicates_cache_row(tmp_path, monkeypatch) -> None:
    """`organize --copy`: both src and dst should be cached after the run."""
    src_dir = tmp_path / "src"; src_dir.mkdir()
    a = src_dir / "a.CR3"; _make_raw_like(a)
    cache = ExifCache.open(); assert cache is not None
    cache.put_many([(a, _record(a, iso=200))]); cache.close()

    out = tmp_path / "copy-dest"

    def fake_read(paths):
        return [_record(Path(p), iso=200) for p in paths]
    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake_read)

    result = runner.invoke(
        app, ["organize", str(src_dir), "-o", str(out), "--copy", "--by", "iso"],
    )
    assert result.exit_code == 0, result.stderr

    copied = list(out.rglob("a.CR3"))
    assert len(copied) == 1
    assert a.exists()  # source preserved

    cache2 = ExifCache.open(); assert cache2 is not None
    # Both src and dst hit.
    hits, misses = cache2.get_many([a, copied[0]])
    info = cache2.info()
    cache2.close()
    assert misses == []
    assert len(hits) == 2
    assert info["row_count"] == 2


def test_organize_dry_run_leaves_cache_alone(tmp_path, monkeypatch) -> None:
    """--dry-run prints plans but moves nothing → cache must be untouched."""
    src_dir = tmp_path / "src"; src_dir.mkdir()
    a = src_dir / "a.CR3"; _make_raw_like(a)
    cache = ExifCache.open(); assert cache is not None
    cache.put_many([(a, _record(a, iso=100))]); cache.close()

    def fake_read(paths):
        return [_record(Path(p), iso=100) for p in paths]
    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake_read)

    out = tmp_path / "dest"
    result = runner.invoke(
        app, ["organize", str(src_dir), "-o", str(out), "--by", "iso", "--dry-run"],
    )
    assert result.exit_code == 0

    cache2 = ExifCache.open(); assert cache2 is not None
    hits, misses = cache2.get_many([a])
    info = cache2.info()
    cache2.close()
    # The original `a` row is still cached, unchanged.
    assert misses == []
    assert hits[0]["iso"] == 100
    assert info["row_count"] == 1
