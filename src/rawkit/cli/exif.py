from pathlib import Path
import json
import subprocess
import typer

app = typer.Typer()


@app.command("exif")
def exif_ls(file: Path):
    """Print structured EXIF via exiftool (JSON)."""
    if not file.exists():
        typer.echo(f"File not found: {file}")
        raise typer.Exit(code=1)
    try:
        res = subprocess.run(["exiftool", "-j", str(file)], capture_output=True, text=True, check=True)
        # exiftool outputs a JSON array
        data = json.loads(res.stdout)
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
    except FileNotFoundError:
        typer.echo("exiftool not found. Please install exiftool.")
        raise typer.Exit(code=1)
    except subprocess.CalledProcessError as e:
        typer.echo(f"exiftool error: {e}")
        raise typer.Exit(code=1)
