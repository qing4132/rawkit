from typer import Typer

app = Typer()

from . import thumb, exif, ls  # noqa: F401

# expose top-level Typer app for console entry
app.add_typer(thumb.app, name="thumb")
app.add_typer(exif.app, name="exif")
app.add_typer(ls.app, name="ls")

