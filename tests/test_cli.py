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
