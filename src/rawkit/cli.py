from pathlib import Path

import typer

app = typer.Typer(
    help="rawkit — RAW photography swiss-army CLI",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root() -> None:
    # Forces typer to keep subcommand structure even when only one command exists.
    pass

# v0.0.1: minimal RAW extension whitelist. Expand as new sample formats arrive.
RAW_EXTS: frozenset[str] = frozenset({
    ".arw",   # Sony
    ".cr2",   # Canon (older)
    ".cr3",   # Canon (newer)
    ".nef",   # Nikon
    ".nrw",   # Nikon (small)
    ".raf",   # Fujifilm
    ".dng",   # Adobe / Leica / Pentax / Ricoh
    ".orf",   # Olympus / OM System
    ".rw2",   # Panasonic
    ".3fr",   # Hasselblad
    ".fff",   # Hasselblad / Imacon
    ".rwl",   # Leica
    ".pef",   # Pentax
    ".srw",   # Samsung
    ".x3f",   # Sigma
    ".iiq",   # Phase One
    ".gpr",   # GoPro
})


@app.command()
def ls(
    directory: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Directory to scan (recursive).",
    ),
) -> None:
    """List RAW files recursively under DIRECTORY (paths only, one per line)."""
    for p in sorted(directory.rglob("*")):
        if p.is_file() and p.suffix.lower() in RAW_EXTS:
            typer.echo(p)
