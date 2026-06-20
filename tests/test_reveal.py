"""Tests for `rawkit reveal` and `ls`'s auto-path output when piped.

reveal uses macOS's Finder via osascript; we patch subprocess.run to
capture the AppleScript invocations rather than actually opening windows.
`ls` under CliRunner sees a non-TTY stdout, so it auto-emits one absolute
path per line — same shape reveal consumes downstream.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rawkit.cli import app

runner = CliRunner()


@pytest.fixture
def fake_exif(monkeypatch):
    def fake(paths):
        return [
            {
                "path": str(p),
                "datetime": "2024-01-02 03:04:05",
                "date": "2024-01-02",
                "time": "03:04:05",
                "maker": "Canon",
                "model": "EOS R5",
                "lens": "RF50",
                "iso": 800,
                "fnumber": 1.8,
                "shutter": 0.004,
                "focal": 50.0,
            }
            for p in paths
        ]

    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)
    return fake


@pytest.fixture
def capture_osascript(monkeypatch):
    """Capture every osascript invocation reveal makes."""
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr("rawkit.cli.sys", sys)  # ensure cli.sys is the real one
    monkeypatch.setattr("subprocess.run", fake_run)
    # Force macOS even when running tests on Linux CI.
    monkeypatch.setattr(sys, "platform", "darwin")
    return calls


# --- ls auto-path on pipe -------------------------------------------------

def test_ls_pipe_emits_one_path_per_line(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    (tmp_path / "b.CR3").write_bytes(b"x")

    result = runner.invoke(app, ["ls", str(tmp_path)])
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2
    for ln in lines:
        assert ln.endswith(".ARW") or ln.endswith(".CR3")
    # No table headers / column padding leaking through.
    assert "datetime" not in result.stdout
    assert "model" not in result.stdout


def test_ls_pipe_respects_where(tmp_path, fake_exif, monkeypatch) -> None:
    def fake(paths):
        return [
            {"path": str(p), "iso": 100 if "low" in Path(p).name else 6400,
             "model": "EOS R5", "lens": "RF50"}
            for p in paths
        ]
    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)

    (tmp_path / "low.ARW").write_bytes(b"x")
    (tmp_path / "high.ARW").write_bytes(b"x")

    result = runner.invoke(app, ["ls", str(tmp_path), "-w", "iso>=3200"])
    assert result.exit_code == 0
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0].endswith("high.ARW")


def test_ls_tty_renders_table(tmp_path, fake_exif, monkeypatch) -> None:
    """When stdout looks like a terminal, ls renders the human table — not paths."""
    monkeypatch.setattr("rawkit.cli._stdout_is_tty", lambda: True)
    (tmp_path / "a.ARW").write_bytes(b"x")

    result = runner.invoke(app, ["ls", str(tmp_path)])
    assert result.exit_code == 0
    # Table has a header row; path-mode has none.
    assert "datetime" in result.stdout
    assert "model" in result.stdout


# --- reveal ----------------------------------------------------------------

def test_reveal_groups_by_parent(tmp_path, capture_osascript) -> None:
    """Two files in same parent → one osascript invocation."""
    a = tmp_path / "shoot1"
    a.mkdir()
    (a / "x.ARW").write_bytes(b"x")
    (a / "y.CR3").write_bytes(b"x")

    result = runner.invoke(app, ["reveal", str(a / "x.ARW"), str(a / "y.CR3")])
    assert result.exit_code == 0
    assert len(capture_osascript) == 1
    # AppleScript references both files.
    script = capture_osascript[0][-1]
    assert "x.ARW" in script
    assert "y.CR3" in script
    assert "tell application \"Finder\"" in script


def test_reveal_separate_windows_for_different_parents(tmp_path, capture_osascript) -> None:
    a = tmp_path / "dir_a"
    b = tmp_path / "dir_b"
    a.mkdir()
    b.mkdir()
    (a / "x.ARW").write_bytes(b"x")
    (b / "y.ARW").write_bytes(b"x")

    result = runner.invoke(app, ["reveal", str(a / "x.ARW"), str(b / "y.ARW")])
    assert result.exit_code == 0
    assert len(capture_osascript) == 2


def test_reveal_from_stdin_with_dash(tmp_path, capture_osascript) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    (tmp_path / "b.ARW").write_bytes(b"x")

    stdin = f"{tmp_path / 'a.ARW'}\n{tmp_path / 'b.ARW'}\n"
    result = runner.invoke(app, ["reveal", "-"], input=stdin)
    assert result.exit_code == 0
    assert len(capture_osascript) == 1


def test_reveal_missing_file_reports_but_continues(tmp_path, capture_osascript) -> None:
    (tmp_path / "real.ARW").write_bytes(b"x")
    ghost = tmp_path / "ghost.ARW"

    result = runner.invoke(
        app, ["reveal", str(tmp_path / "real.ARW"), str(ghost)]
    )
    assert result.exit_code == 0
    assert "not found" in result.stderr
    assert "ghost.ARW" in result.stderr
    assert len(capture_osascript) == 1  # real one still revealed


def test_reveal_empty_stdin_exits_1(tmp_path, capture_osascript) -> None:
    # CliRunner's stdin is non-TTY → reveal reads stdin, gets nothing,
    # exits 1 (nothing to reveal) rather than 2 (usage error).
    result = runner.invoke(app, ["reveal"])
    assert result.exit_code == 1
    assert "no paths" in result.stderr.lower()
    assert capture_osascript == []


def test_reveal_all_missing_exits_1(tmp_path, capture_osascript) -> None:
    result = runner.invoke(
        app,
        ["reveal", str(tmp_path / "nope.ARW"), str(tmp_path / "also-nope.ARW")],
    )
    assert result.exit_code == 1
    assert "not found" in result.stderr
    assert capture_osascript == []


def test_reveal_rejects_non_macos(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    (tmp_path / "a.ARW").write_bytes(b"x")

    result = runner.invoke(app, ["reveal", str(tmp_path / "a.ARW")])
    assert result.exit_code == 2
    assert "macOS" in result.stderr
