from typer.testing import CliRunner

from rawkit import __version__
from rawkit.cli import app


runner = CliRunner()


def test_version() -> None:
    assert __version__ == "0.0.1"


def test_ls_help() -> None:
    result = runner.invoke(app, ["ls", "--help"])
    assert result.exit_code == 0
    assert "RAW files" in result.stdout


def test_ls_empty_dir(tmp_path) -> None:
    result = runner.invoke(app, ["ls", str(tmp_path)])
    assert result.exit_code == 0
    assert result.stdout == ""


def test_ls_finds_raws(tmp_path) -> None:
    (tmp_path / "a.ARW").write_bytes(b"")
    (tmp_path / "b.cr3").write_bytes(b"")
    (tmp_path / "ignore.jpg").write_bytes(b"")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.NEF").write_bytes(b"")

    result = runner.invoke(app, ["ls", str(tmp_path)])
    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 3
    assert any(line.endswith("a.ARW") for line in lines)
    assert any(line.endswith("b.cr3") for line in lines)
    assert any(line.endswith("c.NEF") for line in lines)


def test_ls_skips_unreadable_subdir(tmp_path) -> None:
    """A permission-denied subtree must not abort the whole scan."""
    (tmp_path / "ok.ARW").write_bytes(b"")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "hidden.CR3").write_bytes(b"")
    locked.chmod(0o000)
    try:
        result = runner.invoke(app, ["ls", str(tmp_path)])
    finally:
        locked.chmod(0o700)  # restore so pytest can clean tmp_path

    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    assert any(line.endswith("ok.ARW") for line in lines)


def test_ls_does_not_follow_symlinks(tmp_path) -> None:
    """Symlinked subdirs must not be descended into (cycle guard)."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "inside.ARW").write_bytes(b"")
    link = tmp_path / "link_to_real"
    link.symlink_to(real)

    result = runner.invoke(app, ["ls", str(tmp_path)])
    assert result.exit_code == 0
    lines = result.stdout.strip().splitlines()
    # inside.ARW should appear exactly once (via real/), not also via link/
    matching = [ln for ln in lines if ln.endswith("inside.ARW")]
    assert len(matching) == 1, lines


def test_ls_covers_many_raw_extensions(tmp_path) -> None:
    """Sanity-check that the extension whitelist isn't silently shrunk."""
    from rawkit.cli import RAW_EXTS

    assert len(RAW_EXTS) >= 35, f"RAW_EXTS shrank to {len(RAW_EXTS)} entries"
    # Spot-check the maker coverage we promise in the source comment.
    for must_have in (".cr3", ".arw", ".nef", ".raf", ".dng", ".3fr", ".iiq", ".mrw", ".kdc", ".x3f"):
        assert must_have in RAW_EXTS
