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
                "datetime": "2024-01-02 03:04:05",
                "date":     "2024-01-02",
                "time":     "03:04:05",
                "maker": "FAKE",
                "model": "FakeCam X1",
                "lens": "Fake 50mm F1.4",
                "iso": 800,
                "fnumber": 1.4,
                "shutter": 0.004,
                "focal": 50,
                "bias": -1.0,
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

    # -R because the third RAW is one level down; default is non-recursive.
    result = runner.invoke(app, ["ls", str(tmp_path), "-R", "--json"])
    assert result.exit_code == 0
    paths = [json.loads(ln)["path"] for ln in result.stdout.splitlines() if ln.strip()]
    names = {Path(p).name for p in paths}
    assert names == {"a.ARW", "b.cr3", "c.NEF"}
    assert "ignore.jpg" not in result.stdout


def test_ls_skips_unreadable_subdir(tmp_path, fake_exif) -> None:
    """A permission-denied subtree (in recursive mode) must not abort the
    whole scan."""
    (tmp_path / "ok.ARW").write_bytes(b"")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "hidden.CR3").write_bytes(b"")
    locked.chmod(0o000)
    try:
        result = runner.invoke(app, ["ls", str(tmp_path), "-R", "--json"])
    finally:
        locked.chmod(0o700)

    assert result.exit_code == 0
    names = {Path(json.loads(ln)["path"]).name
             for ln in result.stdout.splitlines() if ln.strip()}
    assert names == {"ok.ARW"}


def test_ls_does_not_follow_symlinks(tmp_path, fake_exif) -> None:
    """Symlinked subdirs must not be descended into (cycle guard).
    Only matters in recursive mode — default ls won't enter the symlink anyway."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "inside.ARW").write_bytes(b"")
    (tmp_path / "link_to_real").symlink_to(real)

    result = runner.invoke(app, ["ls", str(tmp_path), "-R", "--json"])
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
    """0.004s → '1/250'; 1.4 → 'f/1.4'; date trimmed to minute; bias signed."""
    (tmp_path / "a.ARW").write_bytes(b"")

    result = runner.invoke(app, ["ls", str(tmp_path)])
    assert result.exit_code == 0
    out = result.stdout
    assert "1/250" in out
    assert "f/1.4" in out
    assert "2024-01-02 03:04" in out
    assert "50mm" in out
    assert "bias" in out      # header is present
    assert "-1" in out        # bias value rendered with sign-aware format


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


def test_bias_formatting() -> None:
    """bias displays with explicit sign so +/- is visible at a glance."""
    from rawkit.cli import _fmt_bias

    assert _fmt_bias(None) == "-"          # absent (no Bias tag)
    assert _fmt_bias(0) == "0"              # in-camera says 'no compensation'
    assert _fmt_bias(0.0) == "0"
    assert _fmt_bias(1) == "+1"             # whole stops drop trailing zeros
    assert _fmt_bias(0.6666666) == "+0.67"  # +2/3 EV, rounded to 2 decimals
    assert _fmt_bias(-2.416666667) == "-2.42"
    assert _fmt_bias("weird") == "weird"


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


# --- recursion (default OFF) ------------------------------------------------

def test_ls_default_is_non_recursive(tmp_path, fake_exif) -> None:
    (tmp_path / "top.ARW").write_bytes(b"")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.CR3").write_bytes(b"")

    result = runner.invoke(app, ["ls", str(tmp_path), "--json"])
    assert result.exit_code == 0
    names = {Path(json.loads(ln)["path"]).name
             for ln in result.stdout.splitlines() if ln.strip()}
    assert names == {"top.ARW"}  # nested.CR3 must NOT appear by default


def test_ls_recursive_flag_descends(tmp_path, fake_exif) -> None:
    (tmp_path / "top.ARW").write_bytes(b"")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.CR3").write_bytes(b"")

    result = runner.invoke(app, ["ls", str(tmp_path), "-R", "--json"])
    assert result.exit_code == 0
    names = {Path(json.loads(ln)["path"]).name
             for ln in result.stdout.splitlines() if ln.strip()}
    assert names == {"top.ARW", "nested.CR3"}


# --- sort + reverse --------------------------------------------------------

@pytest.fixture
def varied_exif(monkeypatch):
    """Three records with deliberately mixed values, so sort tests can
    distinguish ordering by every key."""
    def fake(paths):
        # Map by basename so the test files (created via tmp_path) get
        # matched to a stable record regardless of the random tmp dir.
        records_by_name = {
            "a.ARW": {
                "datetime": "2024-03-15 12:00:00",
                "date": "2024-03-15", "time": "12:00:00",
                "model": "Canon EOS R5", "lens": "RF50mm",
                "iso": 800, "fnumber": 1.8, "shutter": 0.004,
                "bias": 1.0, "focal": 50,
            },
            "b.CR3": {
                "datetime": "2022-05-13 16:38:09",
                "date": "2022-05-13", "time": "16:38:09",
                "model": "Sony A7", "lens": "FE 24-70",
                "iso": 200, "fnumber": 11, "shutter": 0.001,
                "bias": -2.0, "focal": 24,
            },
            "c.NEF": {
                "datetime": "2023-08-20 09:15:30",
                "date": "2023-08-20", "time": "09:15:30",
                "model": "Nikon Z8", "lens": "Z 70-200",
                "iso": 6400, "fnumber": 2.8, "shutter": 0.5,
                "bias": 0, "focal": 200,
            },
        }
        out = []
        for p in paths:
            base = Path(p).name
            rec = {"path": str(p), **records_by_name.get(base, {})}
            out.append(rec)
        return out

    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)


def _make_three(tmp_path):
    (tmp_path / "a.ARW").write_bytes(b"")
    (tmp_path / "b.CR3").write_bytes(b"")
    (tmp_path / "c.NEF").write_bytes(b"")


def _basenames_from_json(out: str) -> list[str]:
    return [Path(json.loads(ln)["path"]).name
            for ln in out.splitlines() if ln.strip()]


def test_ls_default_sort_is_datetime(tmp_path, varied_exif) -> None:
    _make_three(tmp_path)
    result = runner.invoke(app, ["ls", str(tmp_path), "--json"])
    assert result.exit_code == 0
    # Records: b=2022-05, c=2023-08, a=2024-03 -> ascending by datetime
    assert _basenames_from_json(result.stdout) == ["b.CR3", "c.NEF", "a.ARW"]


def test_ls_sort_by_iso(tmp_path, varied_exif) -> None:
    _make_three(tmp_path)
    result = runner.invoke(app, ["ls", str(tmp_path), "--sort", "iso", "--json"])
    assert result.exit_code == 0
    # ISO: b=200, a=800, c=6400
    assert _basenames_from_json(result.stdout) == ["b.CR3", "a.ARW", "c.NEF"]


def test_ls_sort_by_file_short_flag(tmp_path, varied_exif) -> None:
    _make_three(tmp_path)
    result = runner.invoke(app, ["ls", str(tmp_path), "-s", "file", "--json"])
    assert result.exit_code == 0
    # Filename alphabetical: a, b, c
    assert _basenames_from_json(result.stdout) == ["a.ARW", "b.CR3", "c.NEF"]


def test_ls_reverse_flag(tmp_path, varied_exif) -> None:
    _make_three(tmp_path)
    result = runner.invoke(app, ["ls", str(tmp_path), "--sort", "iso", "-r", "--json"])
    assert result.exit_code == 0
    # ISO desc: c=6400, a=800, b=200
    assert _basenames_from_json(result.stdout) == ["c.NEF", "a.ARW", "b.CR3"]


def test_ls_sort_missing_values_go_last_ascending(tmp_path, monkeypatch) -> None:
    """In ascending sort, records missing the key must appear AFTER all
    records that have it (NULLS LAST). The same holds for descending."""
    def fake(paths):
        return [
            {"path": str(paths[0]), "iso": 100},
            {"path": str(paths[1])},               # no iso at all
            {"path": str(paths[2]), "iso": 800},
        ]
    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)
    _make_three(tmp_path)

    asc = runner.invoke(app, ["ls", str(tmp_path), "--sort", "iso", "--json"])
    assert asc.exit_code == 0
    # Asc: 100, 800, then the missing one
    assert _basenames_from_json(asc.stdout) == ["a.ARW", "c.NEF", "b.CR3"]

    desc = runner.invoke(app, ["ls", str(tmp_path), "--sort", "iso", "-r", "--json"])
    assert desc.exit_code == 0
    # Desc: 800, 100, then missing one (STILL last)
    assert _basenames_from_json(desc.stdout) == ["c.NEF", "a.ARW", "b.CR3"]


def test_ls_sort_invalid_key_errors(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    result = runner.invoke(app, ["ls", str(tmp_path), "--sort", "nonexistent"])
    assert result.exit_code != 0
    out = (result.stderr or result.output).lower()
    assert "sort" in out or "invalid" in out or "value" in out


def test_ls_sort_breaks_ties_by_subsecond(tmp_path, monkeypatch) -> None:
    """Burst frames at the same wall-clock second must sort by sub-second."""
    def fake(paths):
        # Three frames recorded within one second, written out of order.
        return [
            {"path": str(paths[0]), "datetime": "2024-01-01 00:00:00.900"},  # latest
            {"path": str(paths[1]), "datetime": "2024-01-01 00:00:00.247"},
            {"path": str(paths[2]), "datetime": "2024-01-01 00:00:00.01"},   # earliest
        ]
    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)
    _make_three(tmp_path)

    result = runner.invoke(app, ["ls", str(tmp_path), "--json"])
    assert result.exit_code == 0
    # Expected chronological order: .01 → .247 → .9
    assert _basenames_from_json(result.stdout) == ["c.NEF", "b.CR3", "a.ARW"]


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


# --- header sort indicator + cell color (via direct _render_table call) ----

def _capture_render(records, sort_key, reverse, monkeypatch, capsys, force_color=True):
    """Render via _render_table with color forced on/off, capture stdout.
    Accepts a single sort_key for convenience; wraps it into the list form."""
    from rawkit import cli as cli_mod
    monkeypatch.setattr(cli_mod, "_color_enabled", lambda: force_color)
    cli_mod._render_table(records, sort_keys=[sort_key], reverse=reverse)
    return capsys.readouterr().out


def test_header_marks_active_sort_column_with_arrow(monkeypatch, capsys) -> None:
    from rawkit.cli import SortKey
    recs = [{"path": "/x/a.ARW", "datetime": "2024-01-01 00:00:00", "iso": 100,
             "fnumber": 2.8, "shutter": 0.004, "focal": 50}]
    out = _capture_render(recs, SortKey.datetime, reverse=False,
                          monkeypatch=monkeypatch, capsys=capsys, force_color=False)
    header = out.splitlines()[0]
    # ascending arrow appears on the datetime column header
    assert "datetime\u2191" in header
    # other headers don't get an arrow
    assert "iso\u2191" not in header
    assert "iso\u2193" not in header


def test_header_arrow_flips_on_reverse(monkeypatch, capsys) -> None:
    from rawkit.cli import SortKey
    recs = [{"path": "/x/a.ARW", "iso": 100}]
    out = _capture_render(recs, SortKey.iso, reverse=True,
                          monkeypatch=monkeypatch, capsys=capsys, force_color=False)
    header = out.splitlines()[0]
    assert "iso\u2193" in header
    assert "iso\u2191" not in header


def test_header_arrow_shows_even_without_color(monkeypatch, capsys) -> None:
    """The arrow is information (which sort), so it survives NO_COLOR / pipe."""
    from rawkit.cli import SortKey
    recs = [{"path": "/x/a.ARW", "iso": 100}]
    out = _capture_render(recs, SortKey.iso, reverse=False,
                          monkeypatch=monkeypatch, capsys=capsys, force_color=False)
    assert "iso\u2191" in out
    # but no ANSI escape sequences anywhere
    assert "\x1b[" not in out


def test_sort_by_date_or_time_highlights_datetime_column(monkeypatch, capsys) -> None:
    """date / time sort keys aren't visible columns; they highlight datetime."""
    from rawkit.cli import SortKey
    recs = [{"path": "/x/a.ARW", "datetime": "2024-01-01 00:00:00"}]
    out = _capture_render(recs, SortKey.date, reverse=False,
                          monkeypatch=monkeypatch, capsys=capsys, force_color=False)
    assert "datetime\u2191" in out.splitlines()[0]


def test_no_color_env_var_disables_color(monkeypatch) -> None:
    """no-color.org compliance: any value of NO_COLOR disables color."""
    import os
    from rawkit.cli import _color_enabled
    # Force isatty True via monkeypatch on sys.stdout
    class FakeStdout:
        def isatty(self_inner): return True
    monkeypatch.setattr("rawkit.cli.sys.stdout", FakeStdout())

    monkeypatch.setenv("NO_COLOR", "1")
    assert _color_enabled() is False
    monkeypatch.setenv("NO_COLOR", "")   # even empty value disables
    assert _color_enabled() is False
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert _color_enabled() is True


# --- multi-key sort (primary, secondary, ...) ------------------------------

def test_ls_multi_key_sort_model_then_datetime(tmp_path, monkeypatch) -> None:
    """`--sort model,datetime`: same-model files break ties by datetime."""
    def fake(paths):
        # Three Canon, three Sony, intentionally out of chronological order.
        return [
            {"path": str(paths[0]), "model": "Canon EOS R5", "datetime": "2024-03-15 12:00:00"},
            {"path": str(paths[1]), "model": "Sony A1",      "datetime": "2022-01-01 09:00:00"},
            {"path": str(paths[2]), "model": "Canon EOS R5", "datetime": "2023-06-10 18:00:00"},
            {"path": str(paths[3]), "model": "Sony A1",      "datetime": "2024-10-27 17:00:00"},
            {"path": str(paths[4]), "model": "Canon EOS R5", "datetime": "2022-05-01 14:00:00"},
            {"path": str(paths[5]), "model": "Sony A1",      "datetime": "2023-07-04 11:00:00"},
        ]
    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)
    for n in ("a", "b", "c", "d", "e", "f"):
        (tmp_path / f"{n}.ARW").write_bytes(b"")

    result = runner.invoke(app, ["ls", str(tmp_path), "--sort", "model,datetime", "--json"])
    assert result.exit_code == 0
    rows = [json.loads(ln) for ln in result.stdout.splitlines() if ln.strip()]
    names = [Path(r["path"]).name for r in rows]
    # Canon group first (alphabetic), ascending datetime within;
    # then Sony group, ascending datetime within.
    assert names == ["e.ARW", "c.ARW", "a.ARW",     # Canon: 2022 → 2023 → 2024
                     "b.ARW", "f.ARW", "d.ARW"]    # Sony:  2022 → 2023 → 2024


def test_ls_multi_key_sort_reverse_flips_all_levels(tmp_path, monkeypatch) -> None:
    """`-r` with multi-key reverses BOTH levels (global direction)."""
    def fake(paths):
        return [
            {"path": str(paths[0]), "model": "A", "iso": 100},
            {"path": str(paths[1]), "model": "B", "iso": 100},
            {"path": str(paths[2]), "model": "A", "iso": 800},
        ]
    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)
    for n in ("a", "b", "c"):
        (tmp_path / f"{n}.ARW").write_bytes(b"")

    result = runner.invoke(app, ["ls", str(tmp_path), "--sort", "model,iso", "-r", "--json"])
    assert result.exit_code == 0
    names = [Path(json.loads(ln)["path"]).name
             for ln in result.stdout.splitlines() if ln.strip()]
    # Model desc: B group first, then A group; iso desc within each.
    assert names == ["b.ARW",            # B, iso 100
                     "c.ARW", "a.ARW"]   # A, iso 800 then 100


def test_ls_multi_key_missing_secondary_goes_after_in_group(tmp_path, monkeypatch) -> None:
    """Within same primary key, records missing the secondary key still
    follow the per-key NULLS LAST rule."""
    def fake(paths):
        return [
            {"path": str(paths[0]), "model": "X", "iso": 800},
            {"path": str(paths[1]), "model": "X"},               # iso missing
            {"path": str(paths[2]), "model": "X", "iso": 200},
        ]
    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)
    for n in ("a", "b", "c"):
        (tmp_path / f"{n}.ARW").write_bytes(b"")

    result = runner.invoke(app, ["ls", str(tmp_path), "--sort", "model,iso", "--json"])
    assert result.exit_code == 0
    names = [Path(json.loads(ln)["path"]).name
             for ln in result.stdout.splitlines() if ln.strip()]
    # Ascending iso within model X: 200, 800, then the missing-iso row last.
    assert names == ["c.ARW", "a.ARW", "b.ARW"]


def test_ls_sort_invalid_key_in_list_errors(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    result = runner.invoke(app, ["ls", str(tmp_path), "--sort", "model,nonsense"])
    assert result.exit_code != 0
    msg = (result.stderr or result.output).lower()
    assert "nonsense" in msg or "unknown" in msg


def test_ls_sort_duplicate_key_in_list_errors(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    result = runner.invoke(app, ["ls", str(tmp_path), "--sort", "model,model"])
    assert result.exit_code != 0
    assert "duplicate" in (result.stderr or result.output).lower()


def test_ls_sort_single_key_still_works(tmp_path, fake_exif) -> None:
    """Backward-compatibility: single key still accepted."""
    (tmp_path / "a.ARW").write_bytes(b"")
    result = runner.invoke(app, ["ls", str(tmp_path), "--sort", "iso", "--json"])
    assert result.exit_code == 0


def test_header_arrow_marks_primary_key_only(monkeypatch, capsys) -> None:
    """With `--sort model,iso`, the arrow goes on the model column, not iso."""
    from rawkit.cli import SortKey, _render_table
    monkeypatch.setattr("rawkit.cli._color_enabled", lambda: False)
    _render_table(
        [{"path": "/x/a.ARW", "model": "X", "iso": 100}],
        sort_keys=[SortKey.model, SortKey.iso],
        reverse=False,
    )
    out = capsys.readouterr().out
    header = out.splitlines()[0]
    assert "model\u2191" in header
    assert "iso\u2191" not in header
