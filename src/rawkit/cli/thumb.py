from pathlib import Path
from typing import Optional

import rawpy
from PIL import Image
import typer

app = typer.Typer()


@app.command("thumb")
def thumb(files: list[str], output: Optional[Path] = None, size: int = 512):
    """Generate thumbnails for RAW files.

    Example: rawkit thumb images/ --output thumbs/ --size 512
    """
    out = output or Path.cwd() / "thumbs"
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in files:
        p = Path(f)
        if p.is_dir():
            # iterate files
            for rf in p.rglob("*"):
                try:
                    with rawpy.imread(str(rf)) as raw:
                        thumb = raw.extract_thumb()
                        if isinstance(thumb.data, bytes):
                            jpg_output_path = out / f"{rf.stem}.jpg"
                            with open(jpg_output_path, "wb") as fh:
                                fh.write(thumb.data)
                            count += 1
                except Exception:
                    continue
        elif p.is_file():
            try:
                with rawpy.imread(str(p)) as raw:
                    thumb = raw.extract_thumb()
                    if isinstance(thumb.data, bytes):
                        jpg_output_path = out / f"{p.stem}.jpg"
                        with open(jpg_output_path, "wb") as fh:
                            fh.write(thumb.data)
                        count += 1
            except Exception:
                continue
    typer.echo(f"✅ Total {count} JPG thumbnails extracted to {out}")
