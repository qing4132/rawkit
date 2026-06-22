"""rawkit EXIF cache — SQLite-backed, stat-keyed, zero-dependency.

Why this exists
---------------
The lite EXIF backend parses ~38 k RAW files in ~20 s on a fast external SSD.
That's a 130× win over the old exiftool path but it's still 20 s the user pays
EVERY TIME `rawkit ls -R` runs on a big library. This module makes those 20 s
into ~1.5 s by remembering the parsed record on disk and validating freshness
via the (dev, ino, size, mtime_ns) 4-tuple — the same staleness signal git and
ripgrep use.

Cost model (on the canonical 38 729-file library):
  cold run (0% hit): 20 s → ~22 s   (+1.5 s INSERT overhead, +5–10%)
  warm run (100% hit): 20 s → ~1.5 s  (~13× speedup; floored by stat() throughput)
  mixed run (50% hit): 20 s → ~11 s   (~2× speedup)

Storage shape
-------------
One SQLite database at the platform's canonical cache directory (macOS:
~/Library/Caches/rawkit/v1/index.sqlite; XDG fallback: $XDG_CACHE_HOME/rawkit/).

Schema (v1):
  PRAGMA user_version = 1                 # bumped → whole db torn down + rebuilt
  CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)
  CREATE TABLE exif_cache (
      abspath  TEXT PRIMARY KEY,          # os.path.abspath(path) — symlink-preserving
      dev      INTEGER NOT NULL,          # st_dev
      ino      INTEGER NOT NULL,          # st_ino
      size     INTEGER NOT NULL,          # st_size
      mtime_ns INTEGER NOT NULL,          # st_mtime_ns (nanosecond precision)
      backend  TEXT    NOT NULL,          # 'lite' — we never cache exiftool output
      payload  BLOB    NOT NULL           # the normalized record as UTF-8 JSON
  )

Freshness invariant
-------------------
A cache hit requires ALL of (dev, ino, size, mtime_ns) on disk to equal the
stored values. Any difference → miss → re-parse → INSERT OR REPLACE.
Empirically this catches every legitimate file change (Lightroom export,
re-import, rename-in-place) without ever needing to read the file's bytes.

Concurrency
-----------
WAL mode enables multi-reader / single-writer concurrency. Two `rawkit ls`
processes can read at once. INSERT events serialize at the SQLite layer but
each rawkit run only commits one transaction at the end, so contention is
short and bounded.

Disable paths
-------------
1. Per-invocation:   `RAWKIT_NO_CACHE=1 rawkit ls ...`
2. Persistent:       `rawkit cache disable` (writes meta.enabled='false')
3. Nuclear:          `rawkit cache clear`   (deletes the db file)
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# Schema version. Bump when the on-disk record shape or stat-key columns
# change in a way old rows can't represent. The cache layer detects the
# mismatch via `PRAGMA user_version` and silently rebuilds — users never
# have to know they own a cache.
SCHEMA_VERSION = 1

# Conservative chunk size for `WHERE abspath IN (?, ?, ...)` queries.
# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 32766 on modern builds but
# was 999 on older ones. 500 stays portable while keeping the round-trip
# count low: 38 729 / 500 ≈ 78 chunks, each ~200 µs → ~15 ms total.
_IN_CHUNK = 500


def _default_cache_path() -> Path:
    """Where the cache db lives by default.

    Priority:
      1. `RAWKIT_CACHE_DIR`   (test seam; also a documented override)
      2. `~/Library/Caches/rawkit/v1/`     (macOS native)
      3. `$XDG_CACHE_HOME/rawkit/v1/`      (Linux/freedesktop)
      4. `~/.cache/rawkit/v1/`             (last-resort fallback)
    """
    override = os.environ.get("RAWKIT_CACHE_DIR")
    if override:
        return Path(override).expanduser() / "index.sqlite"

    home = Path.home()
    # macOS-native location. rawkit is macOS-first; this is the right home
    # for the data because Time Machine excludes ~/Library/Caches by default
    # (Apple's documented convention for "regenerable" caches).
    mac_caches = home / "Library" / "Caches"
    if mac_caches.is_dir():
        return mac_caches / "rawkit" / "v1" / "index.sqlite"

    # XDG fallback for the Linux/CI case.
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else home / ".cache"
    return base / "rawkit" / "v1" / "index.sqlite"


def env_disabled() -> bool:
    """True when the current invocation should bypass the cache entirely.

    Recognized: `RAWKIT_NO_CACHE` set to any non-empty, non-'0' value.
    """
    v = os.environ.get("RAWKIT_NO_CACHE", "")
    return bool(v) and v.strip() not in ("0", "false", "False", "")


class ExifCache:
    """SQLite-backed EXIF cache. Open one per process; close before exit.

    Use the `open()` classmethod, not `__init__()` directly — it folds in
    the env-bypass and persisted-disable checks and returns None when the
    cache shouldn't be used. Callers can then write `if cache:` cleanly.
    """

    __slots__ = ("path", "_conn")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # `isolation_level=None` puts us in autocommit mode so we can issue
        # PRAGMAs without an implicit transaction; explicit BEGIN/COMMIT is
        # used for the batch INSERT path.
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        # WAL gives readers + one writer concurrently and is durable on macOS
        # APFS. synchronous=NORMAL is safe under WAL and 2–3× faster than FULL.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._ensure_schema()

    # ------------------------------------------------------------------ open
    @classmethod
    def open(cls, *, path: Optional[Path] = None, ignore_disabled: bool = False) -> Optional["ExifCache"]:
        """Return a cache instance, or None when caching should be skipped.

        `ignore_disabled=True` lets `rawkit cache enable/info/vacuum/clear`
        operate even when the persisted disable flag is set.
        """
        if env_disabled():
            return None
        target = path or _default_cache_path()
        try:
            cache = cls(target)
        except sqlite3.DatabaseError:
            # Corrupted db on disk. Don't crash: log behaviour matches "no cache",
            # and the user can `rawkit cache clear` to recover.
            return None
        if not ignore_disabled and not cache._is_enabled():
            cache.close()
            return None
        return cache

    # -------------------------------------------------------------- schema
    def _ensure_schema(self) -> None:
        cur = self._conn.execute("PRAGMA user_version")
        existing = cur.fetchone()[0]
        if existing == 0:
            # Brand new db.
            self._create_schema(initial=True)
        elif existing != SCHEMA_VERSION:
            # Stale schema. Drop everything and rebuild — silent on purpose;
            # the user doesn't need to know about an internal format bump.
            self._conn.execute("DROP TABLE IF EXISTS exif_cache")
            self._conn.execute("DROP TABLE IF EXISTS meta")
            self._create_schema(initial=True)

    def _create_schema(self, *, initial: bool) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS exif_cache (
                abspath  TEXT PRIMARY KEY,
                dev      INTEGER NOT NULL,
                ino      INTEGER NOT NULL,
                size     INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                backend  TEXT    NOT NULL,
                payload  BLOB    NOT NULL
            );
            """
        )
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        if initial:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._conn.executemany(
                "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
                [
                    ("rawkit_version",   _read_rawkit_version()),
                    ("created_at",       now),
                    ("last_vacuum_at",   ""),
                    ("enabled",          "true"),
                ],
            )

    # ----------------------------------------------------------- enabled
    def _is_enabled(self) -> bool:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'enabled'"
        ).fetchone()
        # Missing row → treat as enabled (defensive: a brand-new db with
        # corrupt meta should still serve hits, not silently no-op).
        return row is None or row[0] != "false"

    @classmethod
    def set_enabled(cls, enabled: bool, *, path: Optional[Path] = None) -> None:
        """Persist the enable/disable flag. Always opens (or creates) the db."""
        target = path or _default_cache_path()
        # ignore_disabled=True so we can re-enable an already-disabled cache.
        cache = cls(target)
        try:
            cache._conn.execute(
                "INSERT INTO meta(key, value) VALUES ('enabled', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("true" if enabled else "false",),
            )
        finally:
            cache.close()

    # ----------------------------------------------------------- get_many
    def get_many(
        self, paths: list[Path]
    ) -> tuple[dict[int, dict[str, Any]], list[int]]:
        """Partition `paths` into (hits, miss_indices).

        Returns:
          hits: { positional_index : cached_record_dict }
          miss_indices: positional indices that need re-parsing.

        For each path we do exactly one `os.stat()`. Hits require the
        (dev, ino, size, mtime_ns) 4-tuple to match the stored row.
        """
        if not paths:
            return {}, []

        # Resolve to absolute strings once. `abspath` (not `resolve`) so
        # symlinked photos stay distinct from their targets — which matches
        # what the user typed and what every other command displays.
        abs_paths = [os.path.abspath(str(p)) for p in paths]

        # Build {abspath -> first_index} so we can map result rows back.
        # If the same abspath appears twice (unusual but possible after
        # `_collect_raws` dedup misses), both indices get the same record.
        abspath_to_indices: dict[str, list[int]] = {}
        for i, ap in enumerate(abs_paths):
            abspath_to_indices.setdefault(ap, []).append(i)

        # Fetch in chunks to respect SQLite's variable-count limit.
        rows: dict[str, tuple[int, int, int, int, bytes]] = {}
        unique_abs = list(abspath_to_indices.keys())
        for start in range(0, len(unique_abs), _IN_CHUNK):
            chunk = unique_abs[start:start + _IN_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            cur = self._conn.execute(
                f"SELECT abspath, dev, ino, size, mtime_ns, payload "
                f"FROM exif_cache WHERE abspath IN ({placeholders})",
                chunk,
            )
            for ap, dev, ino, size, mtime_ns, payload in cur:
                rows[ap] = (dev, ino, size, mtime_ns, payload)

        hits: dict[int, dict[str, Any]] = {}
        misses: list[int] = []
        for ap, indices in abspath_to_indices.items():
            row = rows.get(ap)
            if row is None:
                misses.extend(indices)
                continue
            # Staleness check: any st_* mismatch → miss. Wrap the stat in
            # a try because the path could disappear between collection
            # and now — treat that as a miss; caller will fail naturally.
            try:
                st = os.stat(ap)
            except OSError:
                misses.extend(indices)
                continue
            dev, ino, size, mtime_ns, payload = row
            if (
                st.st_dev != dev
                or st.st_ino != ino
                or st.st_size != size
                or st.st_mtime_ns != mtime_ns
            ):
                misses.extend(indices)
                continue
            try:
                record = json.loads(payload)
            except (ValueError, TypeError):
                # Corrupted payload — treat as miss, will be overwritten.
                misses.extend(indices)
                continue
            for idx in indices:
                hits[idx] = record

        # Misses must be sorted to preserve input ordering for downstream
        # progress / re-assembly. abspath_to_indices iterates by insertion
        # order (dict), but we still want a single contiguous index list.
        misses.sort()
        return hits, misses

    # ----------------------------------------------------------- put_many
    def put_many(
        self,
        items: list[tuple[Path, dict[str, Any]]],
        *,
        backend: str = "lite",
    ) -> None:
        """Insert/replace cache entries for these (path, record) pairs.

        One transaction commits the whole batch — crash-safe and ~100×
        faster than committing per-row. Path is re-stat'd here (not
        passed in) so the stored 4-tuple is consistent with the bytes
        the parser saw moments ago.
        """
        if not items:
            return

        rows: list[tuple[str, int, int, int, int, str, bytes]] = []
        for path, record in items:
            ap = os.path.abspath(str(path))
            try:
                st = os.stat(ap)
            except OSError:
                # File vanished between parse and cache write. Skip — the
                # caller already returned the record to the user; we just
                # don't immortalize a now-impossible row.
                continue
            payload = json.dumps(
                record, ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")
            rows.append(
                (ap, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, backend, payload)
            )

        if not rows:
            return

        self._conn.execute("BEGIN")
        try:
            self._conn.executemany(
                "INSERT INTO exif_cache(abspath, dev, ino, size, mtime_ns, backend, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(abspath) DO UPDATE SET "
                "    dev      = excluded.dev,"
                "    ino      = excluded.ino,"
                "    size     = excluded.size,"
                "    mtime_ns = excluded.mtime_ns,"
                "    backend  = excluded.backend,"
                "    payload  = excluded.payload",
                rows,
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    # ------------------------------------------------------- organize hook
    def relocate(self, old: Path, new: Path) -> None:
        """After a successful organize-move: re-key the cache row to `new`.

        Reads the existing row (if any), updates its stat 4-tuple from the
        new path on disk, and DELETEs the old key. If no row existed for
        the old path, this is a no-op — the next read will populate the
        new path naturally.
        """
        old_ap = os.path.abspath(str(old))
        new_ap = os.path.abspath(str(new))
        if old_ap == new_ap:
            return  # nothing actually moved (in-place organize bucket already correct)
        try:
            st = os.stat(new_ap)
        except OSError:
            # Target file vanished — bail. Old row stays as harmless orphan.
            return
        self._conn.execute("BEGIN")
        try:
            row = self._conn.execute(
                "SELECT backend, payload FROM exif_cache WHERE abspath = ?",
                (old_ap,),
            ).fetchone()
            if row is None:
                # Nothing to migrate. (The cache lookup that drove organize
                # may have been below the size threshold, or the file was
                # missed for some other reason.) Move on.
                self._conn.execute("COMMIT")
                return
            backend, payload = row
            # Path stored in payload's "path" key is now stale; rewrite it
            # so that `rawkit ls` after the move displays the new location.
            try:
                rec = json.loads(payload)
                if isinstance(rec, dict) and "path" in rec:
                    rec["path"] = new_ap
                    payload = json.dumps(
                        rec, ensure_ascii=False, separators=(",", ":"),
                    ).encode("utf-8")
            except (ValueError, TypeError):
                pass  # leave payload as-is; "path" field is informational

            self._conn.execute(
                "INSERT INTO exif_cache(abspath, dev, ino, size, mtime_ns, backend, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(abspath) DO UPDATE SET "
                "    dev = excluded.dev, ino = excluded.ino,"
                "    size = excluded.size, mtime_ns = excluded.mtime_ns,"
                "    backend = excluded.backend, payload = excluded.payload",
                (new_ap, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, backend, payload),
            )
            self._conn.execute("DELETE FROM exif_cache WHERE abspath = ?", (old_ap,))
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def duplicate(self, src: Path, dst: Path) -> None:
        """After a successful organize-copy: clone the src row under dst.

        Old row stays put (the user still has the source file at its
        original path). Stat is taken from `dst`.
        """
        src_ap = os.path.abspath(str(src))
        dst_ap = os.path.abspath(str(dst))
        if src_ap == dst_ap:
            return
        try:
            st = os.stat(dst_ap)
        except OSError:
            return
        row = self._conn.execute(
            "SELECT backend, payload FROM exif_cache WHERE abspath = ?",
            (src_ap,),
        ).fetchone()
        if row is None:
            return
        backend, payload = row
        try:
            rec = json.loads(payload)
            if isinstance(rec, dict) and "path" in rec:
                rec["path"] = dst_ap
                payload = json.dumps(
                    rec, ensure_ascii=False, separators=(",", ":"),
                ).encode("utf-8")
        except (ValueError, TypeError):
            pass
        self._conn.execute(
            "INSERT INTO exif_cache(abspath, dev, ino, size, mtime_ns, backend, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(abspath) DO UPDATE SET "
            "    dev = excluded.dev, ino = excluded.ino,"
            "    size = excluded.size, mtime_ns = excluded.mtime_ns,"
            "    backend = excluded.backend, payload = excluded.payload",
            (dst_ap, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, backend, payload),
        )

    # ---------------------------------------------------------- admin ops
    def info(self) -> dict[str, Any]:
        """Snapshot of the cache for `rawkit cache info`."""
        row_count = self._conn.execute(
            "SELECT COUNT(*) FROM exif_cache"
        ).fetchone()[0]
        try:
            size_bytes = self.path.stat().st_size
        except OSError:
            size_bytes = 0
        meta = dict(self._conn.execute("SELECT key, value FROM meta").fetchall())
        return {
            "path":            str(self.path),
            "schema_version":  SCHEMA_VERSION,
            "enabled":         meta.get("enabled", "true") != "false",
            "row_count":       row_count,
            "size_bytes":      size_bytes,
            "rawkit_version":  meta.get("rawkit_version", "?"),
            "created_at":      meta.get("created_at", "?"),
            "last_vacuum_at":  meta.get("last_vacuum_at", "") or "never",
        }

    def clear(self) -> int:
        """Delete every row. Returns the number of rows removed."""
        n = self._conn.execute("SELECT COUNT(*) FROM exif_cache").fetchone()[0]
        self._conn.execute("DELETE FROM exif_cache")
        # Don't VACUUM here — clear is supposed to be fast. The user can
        # run `cache vacuum` separately if they care about disk space.
        return n

    def vacuum(self) -> int:
        """Sweep orphan rows (path no longer exists) and reclaim disk space.

        Returns the number of orphan rows removed. SQLite VACUUM runs after
        the orphan sweep so the file size actually shrinks.
        """
        # Stream abspaths in chunks. On a 100 k-row db this is fast (~50 ms)
        # because we never materialize the whole table client-side.
        orphans: list[str] = []
        cur = self._conn.execute("SELECT abspath FROM exif_cache")
        for (ap,) in cur:
            if not os.path.exists(ap):
                orphans.append(ap)
        if orphans:
            self._conn.execute("BEGIN")
            try:
                for start in range(0, len(orphans), _IN_CHUNK):
                    chunk = orphans[start:start + _IN_CHUNK]
                    placeholders = ",".join("?" * len(chunk))
                    self._conn.execute(
                        f"DELETE FROM exif_cache WHERE abspath IN ({placeholders})",
                        chunk,
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        # VACUUM rebuilds the db file; only worth it if we freed something.
        if orphans:
            self._conn.execute("VACUUM")
        # Record the timestamp regardless — useful even when no orphans found.
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES ('last_vacuum_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
        )
        return len(orphans)

    # ---------------------------------------------------------- lifecycle
    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "ExifCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _read_rawkit_version() -> str:
    """Read rawkit's own version string from package metadata.

    Stored once when the cache db is created. Used in `rawkit cache info`
    so debugging "this cache was written by version X" is one command away.
    Falls back gracefully if package metadata is unavailable (editable
    install with no dist-info, partial install, etc.).
    """
    try:
        from importlib.metadata import version
        return version("rawkit")
    except Exception:
        return "?"
