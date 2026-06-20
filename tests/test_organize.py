"""Tests for the rawkit organize command."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from rawkit.cli import app

runner = CliRunner()


@pytest.fixture
def fake_exif(monkeypatch):
    """Synthetic EXIF keyed on basename so different tests can mark files
    with different cameras / dates / etc."""

    def fake(paths):
        records = []
        for p in paths:
            name = Path(p).name.lower()
            rec: dict = {
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
                "orientation": "landscape",
            }
            # Markers in the filename drive variation.
            if "sony" in name:
                rec["maker"] = "SONY"
                rec["model"] = "ILCE-7RM4A"
            if "feb" in name:
                rec["date"] = "2024-02-15"
                rec["datetime"] = "2024-02-15 12:00:00"
            if "nomodel" in name:
                rec.pop("model", None)
            records.append(rec)
        return records

    monkeypatch.setattr("rawkit.cli.safe_batch_read", fake)
    return fake


def test_organize_no_by_is_flat(tmp_path, fake_exif) -> None:
    """Omitting --by drops files directly into DEST with no bucket layer.
    Useful with --where to cherry-pick a subset."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.ARW").write_bytes(b"x")
    (src / "b.CR3").write_bytes(b"y")
    out = tmp_path / "keepers"

    result = runner.invoke(app, ["organize", str(src), "-o", str(out)])
    assert result.exit_code == 0
    assert (out / "a.ARW").exists()
    assert (out / "b.CR3").exists()
    # No bucket subdirs created.
    assert sorted(p.name for p in out.iterdir()) == ["a.ARW", "b.CR3"]


def test_organize_no_by_with_where_cherry_picks(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")           # Canon
    (tmp_path / "b_sony.ARW").write_bytes(b"y")      # SONY
    out = tmp_path / "canon-only"

    result = runner.invoke(
        app,
        ["organize", str(tmp_path), "-o", str(out), "-w", 'maker~"Canon"'],
    )
    assert result.exit_code == 0
    assert (out / "a.ARW").exists()
    assert not (out / "b_sony.ARW").exists()
    assert (tmp_path / "b_sony.ARW").exists()  # untouched


def test_organize_unknown_dim(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    result = runner.invoke(
        app,
        [
            "organize",
            str(tmp_path),
            "-o",
            str(tmp_path / "out"),
            "--by",
            "color",
        ],
    )
    assert result.exit_code == 2
    assert "unknown dimension" in result.stderr


def test_organize_duplicate_dim(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")
    result = runner.invoke(
        app,
        [
            "organize",
            str(tmp_path),
            "-o",
            str(tmp_path / "out"),
            "--by",
            "month,month",
        ],
    )
    assert result.exit_code == 2
    assert "duplicate" in result.stderr


def test_organize_moves_single_dim(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x" * 4)
    (tmp_path / "b_feb.CR3").write_bytes(b"y" * 4)
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["organize", str(tmp_path), "-o", str(out), "--by", "month"],
    )
    assert result.exit_code == 0
    assert not (tmp_path / "a.ARW").exists()
    assert not (tmp_path / "b_feb.CR3").exists()
    assert (out / "2024-01" / "a.ARW").exists()
    assert (out / "2024-02" / "b_feb.CR3").exists()


def test_organize_nested_dims(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x" * 4)
    (tmp_path / "b_sony.ARW").write_bytes(b"y" * 4)
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["organize", str(tmp_path), "-o", str(out), "--by", "maker,month"],
    )
    assert result.exit_code == 0
    assert (out / "Canon" / "2024-01" / "a.ARW").exists()
    assert (out / "SONY" / "2024-01" / "b_sony.ARW").exists()


def test_organize_copy_keeps_source(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x" * 4)
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        [
            "organize",
            str(tmp_path),
            "-o",
            str(out),
            "--by",
            "month",
            "--copy",
        ],
    )
    assert result.exit_code == 0
    assert (tmp_path / "a.ARW").exists()
    assert (out / "2024-01" / "a.ARW").exists()
    assert "copied" in result.stderr


def test_organize_dry_run_no_filesystem_changes(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x" * 4)
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        [
            "organize",
            str(tmp_path),
            "-o",
            str(out),
            "--by",
            "month",
            "-n",
        ],
    )
    assert result.exit_code == 0
    assert (tmp_path / "a.ARW").exists()
    assert not (out / "2024-01" / "a.ARW").exists()
    assert "[dry-run]" in result.stderr
    assert "planned" in result.stderr


def test_organize_sidecars_follow(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x" * 4)
    (tmp_path / "a.xmp").write_bytes(b"<xmp/>")
    (tmp_path / "a.jpg").write_bytes(b"\xff\xd8FAKE")
    # Different stem — should NOT move.
    (tmp_path / "other.xmp").write_bytes(b"<unrelated/>")

    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["organize", str(tmp_path), "-o", str(out), "--by", "month"],
    )
    assert result.exit_code == 0
    assert (out / "2024-01" / "a.ARW").exists()
    assert (out / "2024-01" / "a.xmp").exists()
    assert (out / "2024-01" / "a.jpg").exists()
    assert (tmp_path / "other.xmp").exists()  # untouched


def test_organize_unknown_dim_value_goes_to_unknown_bucket(tmp_path, fake_exif) -> None:
    (tmp_path / "nomodel.ARW").write_bytes(b"x" * 4)
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["organize", str(tmp_path), "-o", str(out), "--by", "camera"],
    )
    assert result.exit_code == 0
    assert (out / "_unknown" / "nomodel.ARW").exists()


def test_organize_sanitizes_slash_in_bucket_name(tmp_path, fake_exif) -> None:
    # Default fnumber=1.8 → aperture bucket "f/1.8", which must be sanitized
    # to "f_1.8" so it's a single directory level, not a nested f/ tree.
    (tmp_path / "a.ARW").write_bytes(b"x" * 4)
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["organize", str(tmp_path), "-o", str(out), "--by", "aperture"],
    )
    assert result.exit_code == 0
    assert (out / "f_1.8" / "a.ARW").exists()
    assert not (out / "f").exists()


def test_organize_collision_fails_fast(tmp_path, fake_exif) -> None:
    a = tmp_path / "dir_a"
    b = tmp_path / "dir_b"
    a.mkdir()
    b.mkdir()
    (a / "shared.ARW").write_bytes(b"x")
    (b / "shared.CR3").write_bytes(b"y")
    out = tmp_path / "out"

    # Both files have the same EXIF month → target dir 2024-01/ — but
    # their basenames differ (shared.ARW vs shared.CR3) so they wouldn't
    # actually collide. Force collision by using the same basename:
    (b / "shared.CR3").unlink()
    (b / "shared.ARW").write_bytes(b"z")

    result = runner.invoke(
        app,
        [
            "organize",
            str(a / "shared.ARW"),
            str(b / "shared.ARW"),
            "-o",
            str(out),
            "--by",
            "month",
        ],
    )
    assert result.exit_code == 1
    assert "target collision" in result.stderr
    # Neither file moved.
    assert (a / "shared.ARW").exists()
    assert (b / "shared.ARW").exists()
    assert not (out / "2024-01" / "shared.ARW").exists()


def test_organize_skips_existing_without_f(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"new content")
    out = tmp_path / "out"
    (out / "2024-01").mkdir(parents=True)
    (out / "2024-01" / "a.ARW").write_bytes(b"already there")

    result = runner.invoke(
        app,
        ["organize", str(tmp_path), "-o", str(out), "--by", "month"],
    )
    assert result.exit_code == 0
    assert (out / "2024-01" / "a.ARW").read_bytes() == b"already there"
    assert (tmp_path / "a.ARW").exists()  # source untouched (skipped)
    assert "skip" in result.stderr


def test_organize_overwrites_with_f(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"new content")
    out = tmp_path / "out"
    (out / "2024-01").mkdir(parents=True)
    (out / "2024-01" / "a.ARW").write_bytes(b"already there")

    result = runner.invoke(
        app,
        ["organize", str(tmp_path), "-o", str(out), "--by", "month", "-f"],
    )
    assert result.exit_code == 0
    assert (out / "2024-01" / "a.ARW").read_bytes() == b"new content"
    assert not (tmp_path / "a.ARW").exists()


def test_organize_in_place_already_in_bucket_is_skip(tmp_path, fake_exif) -> None:
    """source == dest, file already where it'd be moved: silent no-op."""
    src_root = tmp_path / "shoot"
    src_root.mkdir()
    bucket = src_root / "2024-01"
    bucket.mkdir()
    (bucket / "a.ARW").write_bytes(b"x")

    result = runner.invoke(
        app,
        ["organize", str(src_root), "-R", "-o", str(src_root), "--by", "month"],
    )
    assert result.exit_code == 0
    assert (bucket / "a.ARW").exists()
    # Skip counted, no spurious failure.
    assert "1 moved" in result.stderr or "1 skipped" in result.stderr


def test_organize_where_filters(tmp_path, fake_exif) -> None:
    (tmp_path / "a.ARW").write_bytes(b"x")           # Canon
    (tmp_path / "b_sony.ARW").write_bytes(b"y")      # SONY
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        [
            "organize",
            str(tmp_path),
            "-o",
            str(out),
            "--by",
            "month",
            "--where",
            'maker~"Canon"',
        ],
    )
    assert result.exit_code == 0
    assert (out / "2024-01" / "a.ARW").exists()
    assert not (out / "2024-01" / "b_sony.ARW").exists()
    assert (tmp_path / "b_sony.ARW").exists()  # excluded by --where → untouched


def test_organize_no_raws(tmp_path) -> None:
    out = tmp_path / "out"
    result = runner.invoke(
        app, ["organize", str(tmp_path), "-o", str(out), "--by", "month"]
    )
    assert result.exit_code == 0
    assert "no RAW files" in result.stderr
    assert not out.exists()


def test_organize_default_output_is_in_place(tmp_path, fake_exif) -> None:
    """When -o is omitted, the first input directory is used as DEST.
    That gives natural in-place organize (no surprise ./organized/ layer)."""
    (tmp_path / "a.ARW").write_bytes(b"x")

    result = runner.invoke(
        app, ["organize", str(tmp_path), "--by", "month"]
    )
    assert result.exit_code == 0
    assert (tmp_path / "2024-01" / "a.ARW").exists()
    assert not (tmp_path / "organized").exists()
    assert not (tmp_path / "a.ARW").exists()


def test_organize_prune_removes_empty_source_subdirs(tmp_path, fake_exif) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "shoot_a").mkdir()
    (src / "shoot_a" / "x.ARW").write_bytes(b"x")
    (src / "shoot_b" / "nested").mkdir(parents=True)
    (src / "shoot_b" / "nested" / "y.ARW").write_bytes(b"y")
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["organize", str(src), "-R", "-o", str(out), "--by", "month", "--prune"],
    )
    assert result.exit_code == 0
    # Files moved to out.
    assert (out / "2024-01" / "x.ARW").exists()
    assert (out / "2024-01" / "y.ARW").exists()
    # Empty source subdirs gone.
    assert not (src / "shoot_a").exists()
    assert not (src / "shoot_b" / "nested").exists()
    assert not (src / "shoot_b").exists()
    # Source root itself preserved.
    assert src.exists()


def test_organize_prune_skips_dirs_with_non_raw_files(tmp_path, fake_exif) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "mixed").mkdir()
    (src / "mixed" / "a.ARW").write_bytes(b"x")
    (src / "mixed" / "notes.txt").write_bytes(b"hello")
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["organize", str(src), "-R", "-o", str(out), "--by", "month", "--prune"],
    )
    assert result.exit_code == 0
    # The RAW moved; the txt stayed; therefore the dir is not empty; not pruned.
    assert (out / "2024-01" / "a.ARW").exists()
    assert (src / "mixed").exists()
    assert (src / "mixed" / "notes.txt").exists()


def test_organize_prune_dry_run_simulates(tmp_path, fake_exif) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "shoot").mkdir()
    (src / "shoot" / "a.ARW").write_bytes(b"x")
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        [
            "organize",
            str(src),
            "-R",
            "-o",
            str(out),
            "--by",
            "month",
            "--prune",
            "-n",
        ],
    )
    assert result.exit_code == 0
    # Nothing actually changed.
    assert (src / "shoot" / "a.ARW").exists()
    assert not (out / "2024-01").exists()
    # But the simulation reports the planned move and the planned rmdir.
    assert "[dry-run]" in result.stderr
    assert "rmdir" in result.stderr


def test_organize_prune_sweeps_ds_store(tmp_path, fake_exif) -> None:
    """A directory containing only the macOS .DS_Store metadata file
    counts as effectively empty: the .DS_Store is swept and the dir
    rmdir'd as part of the prune."""
    src = tmp_path / "src"
    src.mkdir()
    shoot = src / "shoot"
    shoot.mkdir()
    (shoot / "a.ARW").write_bytes(b"x")
    (shoot / ".DS_Store").write_bytes(b"\x00\x00\x00")
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["organize", str(src), "-R", "-o", str(out), "--by", "month", "--prune"],
    )
    assert result.exit_code == 0
    assert (out / "2024-01" / "a.ARW").exists()
    # shoot/ contained only .DS_Store after the move → pruned, .DS_Store gone.
    assert not shoot.exists()
    assert src.exists()


def test_organize_prune_skips_dir_with_user_file_beside_ds_store(
    tmp_path, fake_exif
) -> None:
    """A user file (notes.txt) keeps the dir alive even when .DS_Store
    is also present — .DS_Store sweeping isn't an excuse to delete dirs
    with real content."""
    src = tmp_path / "src"
    src.mkdir()
    mixed = src / "mixed"
    mixed.mkdir()
    (mixed / "a.ARW").write_bytes(b"x")
    (mixed / "notes.txt").write_bytes(b"keep me")
    (mixed / ".DS_Store").write_bytes(b"\x00")
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["organize", str(src), "-R", "-o", str(out), "--by", "month", "--prune"],
    )
    assert result.exit_code == 0
    assert mixed.exists()
    assert (mixed / "notes.txt").exists()
    assert (mixed / ".DS_Store").exists()  # left alone since dir wasn't pruned


def test_organize_prune_removes_preexisting_empty_dir(tmp_path, fake_exif) -> None:
    """A directory that was empty BEFORE this run (leftover from a
    previous reorganization) should still be pruned. The point of
    --prune is opportunistic cleanup of source-tree cruft, not just
    of dirs we ourselves emptied."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.ARW").write_bytes(b"x")
    (src / "leftover_2021").mkdir()  # untouched by this run
    (src / "leftover_with_ds_store").mkdir()
    (src / "leftover_with_ds_store" / ".DS_Store").write_bytes(b"\x00")
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["organize", str(src), "-R", "-o", str(out), "--by", "month", "--prune"],
    )
    assert result.exit_code == 0
    assert not (src / "leftover_2021").exists()
    assert not (src / "leftover_with_ds_store").exists()


def test_organize_prune_skips_hidden_dirs(tmp_path, fake_exif) -> None:
    """Hidden directories (.git/, .venv/, .anything) are NEVER touched
    by prune, even when they're empty. They're presumed to be
    infrastructure the user explicitly created."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.ARW").write_bytes(b"x")
    (src / ".git").mkdir()
    (src / ".git" / "objects").mkdir()
    (src / ".cache").mkdir()
    out = tmp_path / "out"

    result = runner.invoke(
        app,
        ["organize", str(src), "-R", "-o", str(out), "--by", "month", "--prune"],
    )
    assert result.exit_code == 0
    assert (src / ".git").exists()
    assert (src / ".git" / "objects").exists()
    assert (src / ".cache").exists()


# --- stdin / pipe ingestion -------------------------------------------------

def test_organize_reads_paths_from_stdin(tmp_path, fake_exif) -> None:
    """Pipe the keepers in, move them flat into -o."""
    src = tmp_path / "src"
    src.mkdir()
    a = src / "a.ARW"
    b = src / "b.CR3"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    dst = tmp_path / "keepers"

    result = runner.invoke(
        app, ["organize", "-", "-o", str(dst)], input=f"{a}\n{b}\n"
    )
    assert result.exit_code == 0
    assert (dst / "a.ARW").exists()
    assert (dst / "b.CR3").exists()


def test_organize_pipe_requires_output(tmp_path, fake_exif) -> None:
    """Without -o, pipe input has no dir to fall back to → usage error."""
    a = tmp_path / "a.ARW"
    a.write_bytes(b"x")

    result = runner.invoke(app, ["organize", "-"], input=f"{a}\n")
    assert result.exit_code == 2
    assert "-o" in result.stderr or "--output" in result.stderr
    # File must not have been moved.
    assert a.exists()
