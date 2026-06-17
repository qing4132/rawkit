from pathlib import Path
import typer
import subprocess
import json

app = typer.Typer()


def _read_exif(path: Path):
    try:
        res = subprocess.run(["exiftool", "-j", str(path)], capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        return data[0] if data else {}
    except Exception:
        return {}


@app.command("ls")
def ls(dir: Path = Path('.'), where: str = ""):
    """List files with optional simple where filter (substring on EXIF fields)."""
    items = []
    for p in dir.rglob("*"):
        if p.is_file():
            ex = _read_exif(p)
            if where:
                if where.lower() in str(ex).lower():
                    items.append((p, ex))
            else:
                items.append((p, ex))
    for p, ex in items:
        typer.echo(f"{p} -> {ex.get('Model','-')} | {ex.get('FNumber','-')} | {ex.get('ISO','-')}")
