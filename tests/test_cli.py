"""Tests for the rawkit CLI (ls command + walker + output modes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rawkit import __version__
from rawkit.cli import RAW_EXTS, app

runner = CliRunner()


# --- shared fake EXIF backend -----------------------------------------------

@pytest.fixture
def fake_exif(monkeypatch):
    """Patch rawkit.cli.safe_batch_read with deterministic synthetic records.

    Each input path yields a record with the rawkit-normalized field names
    populated with predictable values, so CLI tests don't depend on the real
    exiftool binary or on file contents (test files can be 0 bytes).
    """

    def fake(paths):
        return [
            {
                "path": str(p),
                "date": "2024:01:02 03:04:05",
                "maker": "FAKE",
                "model": "FakeCam X1",
                "lens": "Fake 50mm F1.4",
                "iso": 800,
                "fnumber": 1.4,
                "shutter": 0.004,
                "focal": 50,
            }
            for p in paths
        ]

    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)
    return fake


# --- top-level wiring -------------------------------------------------------

def test_version() -> None:
    assert __version__ == "0.0.1"


def test_ls_help() -> None:
    result = runner.invoke(app, ["ls", "--help"])
    assert result.exit_code == 0
    assert "RAW files" in result.stdout
    assert "--json" in result.stdout


def test_ls_empty_dir(tmp_path, fake_exif) -> None:
    result = runner.invoke(app, ["ls", str(tmp_path)])
    assert result.exit_code == 0
    assert result.stdout == ""


# --- walker behavior --------------------------------------------------------

def test_ls_finds_raws_table(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    (tmp_path / "b.cr3").write_bytes(b"")
    (tmp_path / "ignore.jpg").write_bytes(b"")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.NEF").write_bytes(b"")

    # --json so the walker assertion doesn't fight rich's column-truncation
    # in narrow test terminals.
    result = runner.invoke(app, ["ls", str(tmp_path), "--json"])
    assert result.exit_code == 0
    paths = [json.loads(ln)["path"] for ln in result.stdout.splitlines() if ln.strip()]
    names = {Path(p).name for p in paths}
    assert names == {"a.ARW", "b.cr3", "c.NEF"}
    assert "ignore.jpg" not in result.stdout


def test_ls_skips_unreadable_subdir(tmp_path, fake_exif) -> None:
    """A permission-denied subtree must not abort the whole scan."""
    (tmp_path / "ok.ARW").write_bytes(b"")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "hidden.CR3").write_bytes(b"")
    locked.chmod(0o000)
    try:
        result = runner.invoke(app, ["ls", str(tmp_path), "--json"])
    finally:
        locked.chmod(0o700)

    assert result.exit_code == 0
    names = {Path(json.loads(ln)["path"]).name
             for ln in result.stdout.splitlines() if ln.strip()}
    assert names == {"ok.ARW"}


def test_ls_does_not_follow_symlinks(tmp_path, fake_exif) -> None:
    """Symlinked subdirs must not be descended into (cycle guard)."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "inside.ARW").write_bytes(b"")
    (tmp_path / "link_to_real").symlink_to(real)

    result = runner.invoke(app, ["ls", str(tmp_path), "--json"])
    assert result.exit_code == 0
    paths = [json.loads(ln)["path"] for ln in result.stdout.splitlines() if ln.strip()]
    # inside.ARW must be reached exactly once (via real/), not also via link/.
    assert len(paths) == 1, paths
    assert paths[0].endswith("real/inside.ARW")


# --- extension set integrity ------------------------------------------------

def test_ls_covers_many_raw_extensions() -> None:
    """Sanity-check that the extension whitelist isn't silently shrunk."""
    assert len(RAW_EXTS) >= 35, f"RAW_EXTS shrank to {len(RAW_EXTS)} entries"
    for must_have in (".cr3", ".arw", ".nef", ".raf", ".dng", ".3fr",
                      ".iiq", ".mrw", ".kdc", ".x3f"):
        assert must_have in RAW_EXTS


# --- output modes -----------------------------------------------------------

def test_ls_json_emits_jsonl(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    (tmp_path / "b.CR3").write_bytes(b"")

    result = runner.invoke(app, ["ls", str(tmp_path), "--json"])
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2
    parsed = [json.loads(ln) for ln in lines]
    for r in parsed:
        for key in ("path", "date", "model", "lens", "iso", "fnumber", "shutter"):
            assert key in r, f"missing {key} in {r}"
    assert parsed[0]["iso"] == 800
    assert isinstance(parsed[0]["fnumber"], (int, float))


def test_ls_table_formats_human_values(tmp_path, fake_exif) -> None:
    """0.004s → '1/250'; 1.4 → 'f/1.4'; date trimmed to minute."""
    (tmp_path / "a.ARW").write_bytes(b"")

    result = runner.invoke(app, ["ls", str(tmp_path)])
    assert result.exit_code == 0
    out = result.stdout
    assert "1/250" in out
    assert "f/1.4" in out
    assert "2024-01-02 03:04" in out
    assert "50mm" in out


def test_shutter_formatting_edge_cases() -> None:
    """The 1/1 trap: shutter ≈ 1.0 must display as '1s', never '1/1'."""
    from rawkit.cli import _fmt_shutter

    assert _fmt_shutter(0.004) == "1/250"
    assert _fmt_shutter(0.00625) == "1/160"
    assert _fmt_shutter(0.999) == "0.999s"  # rounds to denom=1 → fall back to seconds
    assert _fmt_shutter(1.0) == "1s"
    assert _fmt_shutter(1.3) == "1.3s"
    assert _fmt_shutter(30) == "30s"
    assert _fmt_shutter(None) == "-"


# --- multi-input handling ---------------------------------------------------

def test_ls_accepts_multiple_dirs(tmp_path, fake_exif) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "first.ARW").write_bytes(b"")
    (b / "second.CR3").write_bytes(b"")

    result = runner.invoke(app, ["ls", str(a), str(b), "--json"])
    assert result.exit_code == 0
    names = {Path(json.loads(ln)["path"]).name
             for ln in result.stdout.splitlines() if ln.strip()}
    assert names == {"first.ARW", "second.CR3"}


def test_ls_accepts_files_directly(tmp_path, fake_exif) -> None:
    f1 = tmp_path / "x.ARW"
    f2 = tmp_path / "y.CR3"
    f1.write_bytes(b"")
    f2.write_bytes(b"")

    result = runner.invoke(app, ["ls", str(f1), str(f2), "--json"])
    assert result.exit_code == 0
    names = {Path(json.loads(ln)["path"]).name
             for ln in result.stdout.splitlines() if ln.strip()}
    assert names == {"x.ARW", "y.CR3"}


def test_ls_mixes_files_and_dirs(tmp_path, fake_exif) -> None:
    f = tmp_path / "loose.ARW"
    f.write_bytes(b"")
    d = tmp_path / "shoot"
    d.mkdir()
    (d / "inside.CR3").write_bytes(b"")

    result = runner.invoke(app, ["ls", str(f), str(d), "--json"])
    assert result.exit_code == 0
    names = {Path(json.loads(ln)["path"]).name
             for ln in result.stdout.splitlines() if ln.strip()}
    assert names == {"loose.ARW", "inside.CR3"}


def test_ls_dedupes_overlapping_inputs(tmp_path, fake_exif) -> None:
    """Passing both a dir AND a file inside that dir must not duplicate."""
    (tmp_path / "shot.ARW").write_bytes(b"")

    result = runner.invoke(
        app, ["ls", str(tmp_path), str(tmp_path / "shot.ARW"), "--json"]
    )
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1


def test_ls_nonexistent_path_errors(tmp_path, fake_exif) -> None:
    result = runner.invoke(app, ["ls", str(tmp_path / "does_not_exist")])
    assert result.exit_code == 1
    assert "no such file" in result.stderr.lower() or "no such file" in result.output.lower()


def test_ls_non_raw_file_warns_and_skips(tmp_path, fake_exif) -> None:
    (tmp_path / "ok.ARW").write_bytes(b"")
    (tmp_path / "skipped.jpg").write_bytes(b"")

    result = runner.invoke(
        app, ["ls", str(tmp_path / "ok.ARW"), str(tmp_path / "skipped.jpg"), "--json"]
    )
    assert result.exit_code == 0
    names = {Path(json.loads(ln)["path"]).name
             for ln in result.stdout.splitlines() if ln.strip()}
    assert names == {"ok.ARW"}
    # Warning about the skip went to stderr (typer CliRunner merges by default)
    assert "skipped.jpg" in (result.stderr or result.output)


# --- long-filename table behavior -------------------------------------------

def test_long_filename_does_not_inflate_other_rows(tmp_path, fake_exif) -> None:
    """A 79-char outlier filename must break alignment only on its own row,
    not pad every other row's file column."""
    short = tmp_path / "short.ARW"
    long_name = tmp_path / ("really_long_" + "x" * 60 + ".ARW")  # 76+ chars
    short.write_bytes(b"")
    long_name.write_bytes(b"")

    result = runner.invoke(app, ["ls", str(tmp_path)])
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    # Header + 2 rows
    assert len(lines) == 3

    # Row width for the short-name row must NOT have been inflated to the
    # long row's width. A naive implementation would pad short's filename
    # cell to ~76 chars; ours caps at 50.
    short_row = next(ln for ln in lines[1:] if "short.ARW" in ln)
    long_row = next(ln for ln in lines[1:] if "really_long_" in ln)
    assert len(short_row) < len(long_row) - 20, (
        f"short row got inflated:\n  short={len(short_row)}\n  long ={len(long_row)}"
    )
