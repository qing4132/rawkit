"""Render a RAW to JPEG/TIFF/PNG via libraw demosaic + Pillow encode.

This is the **opposite** of `extract`. Where extract hands back the SOOC JPEG
the camera already baked in, render decodes the raw Bayer pattern ourselves
through libraw, getting an RGB array we encode fresh.

Trade-offs vs `extract`:

| concern         | extract                          | render                                        |
|-----------------|----------------------------------|-----------------------------------------------|
| colour science  | 100% SOOC (camera's pipeline)    | libraw's neutral sRGB — **WILL drift** from SOOC |
| speed           | ~30ms / file (just memcpy)       | ~0.5–2s / file (real demosaic)                |
| size ceiling    | whatever the camera embedded     | the sensor's native resolution                |
| use when…       | you trust the camera's look      | the camera didn't embed a big enough JPEG      |

So:
- Sony A7R IV users want render (only 1616×1080 embedded)
- Canon R5 / Sony A1 / Leica M11 users probably want extract (full-res already SOOC)
- Hasselblad 3FR / Fuji GFX users get a 3000-class JPEG from `extract`, but
  if they want native 1-billion-pixel output, render is the only path

We deliberately do NOT expose white-balance / curves / sharpening knobs in v1.
Those are LrC's job; rawkit only does the "decode at all" step. If you want
SOOC colour, use `extract`. If you want fine-grained control, use LrC/C1.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path


# Supported output formats. Keys are the user-facing `--format` values,
# values are (Pillow format name, suffix). JPEG = small/lossy; TIFF/PNG = lossless.
_FORMATS: dict[str, tuple[str, str]] = {
    "jpeg": ("JPEG", ".jpg"),
    "tiff": ("TIFF", ".tiff"),
    "png":  ("PNG",  ".png"),
}


@dataclass(frozen=True)
class RenderResult:
    data: bytes
    width: int          # final encoded width (after optional resize)
    height: int         # final encoded height
    format: str         # "jpeg" / "tiff" / "png"


class RenderError(RuntimeError):
    """libraw could not decode this file, or encoding failed."""


def _to_pil(rgb):
    """Convert libraw's ndarray to a PIL Image.

    libraw normally returns shape (H, W, 3) for colour RAWs. Monochrome
    sensors (Leica M Monochrom family, Phase One Achromatic) return shape
    (H, W, 1) — Pillow rejects that with 'Cannot handle this data type'.
    Squeeze the trailing 1-axis so Pillow sees a 2D array and reads it as
    'L' (8-bit grayscale).
    """
    from PIL import Image

    if rgb.ndim == 3 and rgb.shape[2] == 1:
        rgb = rgb[:, :, 0]
    return Image.fromarray(rgb)


def render(
    path: Path,
    *,
    output_format: str = "jpeg",
    quality: int = 90,
    long_edge: int | None = None,
    short_edge: int | None = None,
    megapixels: float | None = None,
) -> RenderResult:
    """Demosaic `path` through libraw, encode as `output_format`.

    `quality` is only consulted for JPEG (PNG/TIFF are lossless).
    One optional resize bound may be set: `long_edge`, `short_edge`, or
    `megapixels`.
    """
    if output_format not in _FORMATS:
        valid = ", ".join(sorted(_FORMATS))
        raise RenderError(f"unknown format {output_format!r}; valid: {valid}")

    try:
        import rawpy
    except ImportError as e:
        raise RenderError(
            "rawpy is not installed in the current environment. "
            "If you installed rawkit globally with `uv tool install`, "
            "reinstall after dependency changes:\n"
            "  cd <rawkit checkout> && uv tool install --reinstall --editable ."
        ) from e

    try:
        from PIL import Image  # noqa: F401  (imported lazily by _to_pil)
    except ImportError as e:
        raise RenderError(f"Pillow is required: {e}") from e

    try:
        with rawpy.imread(str(path)) as raw:
            # Default postprocess: bilinear demosaic, sRGB, 8-bit, camera WB.
            # We deliberately keep this minimal — exposing demosaic algorithm /
            # WB knobs is LrC territory, not rawkit's.
            rgb = raw.postprocess()
    except Exception as e:
        raise RenderError(f"libraw failed: {e}") from e

    img = _to_pil(rgb)
    if long_edge or short_edge or megapixels:
        from rawkit._resize import resize_pil

        try:
            img = resize_pil(
                img,
                long_edge=long_edge,
                short_edge=short_edge,
                megapixels=megapixels,
            )
        except ValueError as e:
            raise RenderError(str(e)) from e

    buf = io.BytesIO()
    pil_format, _suffix = _FORMATS[output_format]
    save_kwargs: dict = {}
    if output_format == "jpeg":
        # subsampling=0 = 4:4:4 (no chroma loss) — small extra bytes for visibly
        # better fine-colour handling; matches the "render" intent of producing
        # the highest-quality fresh JPEG, not a thumbnail.
        save_kwargs = {"quality": int(quality), "subsampling": 0, "optimize": True}
    try:
        img.save(buf, format=pil_format, **save_kwargs)
    except Exception as e:
        raise RenderError(f"encoding to {output_format} failed: {e}") from e

    w, h = img.size
    return RenderResult(buf.getvalue(), w, h, output_format)


def suffix_for(output_format: str) -> str:
    """Return the file extension (with dot) for an output format."""
    return _FORMATS[output_format][1]
