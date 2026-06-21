"""Shared image-resize logic for the extract command.

We accept three independent target dimensions:
  - long_edge   : downscale so max(w, h) ≤ N
  - short_edge  : downscale so min(w, h) ≤ N      (think social-media sizes)
  - megapixels  : downscale so total pixels ≤ N * 1_000_000

At most one may be set; the CLI layer validates this for nicer error messages.
All targets are UPPER BOUNDS — images already smaller than the target are
returned unchanged (we never upscale; upscaling RAW-embedded JPEGs is meaningless).
"""

from __future__ import annotations


def compute_target_size(
    width: int,
    height: int,
    *,
    long_edge: int | None = None,
    short_edge: int | None = None,
    megapixels: float | None = None,
) -> tuple[int, int]:
    """Return (target_w, target_h) preserving aspect ratio.

    Returns (width, height) unchanged when no target is set or when the
    image is already small enough.
    """
    set_count = sum(x is not None for x in (long_edge, short_edge, megapixels))
    if set_count == 0:
        return width, height
    if set_count > 1:
        raise ValueError(
            "at most one of long_edge / short_edge / megapixels may be set"
        )

    long_side = max(width, height)
    short_side = min(width, height)
    pixels = width * height

    if long_edge is not None:
        if long_side <= long_edge:
            return width, height
        ratio = long_edge / long_side
    elif short_edge is not None:
        if short_side <= short_edge:
            return width, height
        ratio = short_edge / short_side
    else:  # megapixels
        target_pixels = megapixels * 1_000_000
        if pixels <= target_pixels:
            return width, height
        # area scales as ratio² → linear ratio is sqrt
        ratio = (target_pixels / pixels) ** 0.5

    new_w = max(1, round(width * ratio))
    new_h = max(1, round(height * ratio))
    return new_w, new_h


def resize_pil(
    img,
    *,
    long_edge: int | None = None,
    short_edge: int | None = None,
    megapixels: float | None = None,
):
    """Resize a PIL Image using `compute_target_size`. Returns the original
    `img` unchanged when no resize is needed (saves a Pillow round-trip)."""
    from PIL import Image

    w, h = img.size
    new_w, new_h = compute_target_size(
        w, h,
        long_edge=long_edge,
        short_edge=short_edge,
        megapixels=megapixels,
    )
    if (new_w, new_h) == (w, h):
        return img
    # LANCZOS is the right kernel for one-shot photographic downscales.
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)
