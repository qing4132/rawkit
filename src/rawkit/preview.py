"""Extract the largest embedded SOOC JPEG preview from a RAW file via libraw/rawpy.

Every camera embeds a JPEG preview inside the RAW so it can be drawn on the
back-of-camera LCD and in the LrC import grid. We just hand that JPEG back —
no demosaicing, no colour-management pipeline, no re-encoding. The result is
**100% SOOC**: Canon Picture Style / Fuji Film Simulation / Sony Creative
Look / Leica monochrome — exactly what the camera baked in.

Single engine — rawpy (LibRaw). Per-file benchmarking on samples/ showed
it's 30-40x faster than shelling out to exiftool (which pays a fresh
Perl-interpreter startup per file) and reaches every embedded JPEG worth
having, including ones exiftool's named tags don't expose (Hasselblad 3FR's
IFD0 JPEG, Ricoh GR III DNG's 6000x4000 SOOC frame).

The 160x120-class navigation thumbnail is intentionally out of scope —
it's too small to be useful. If a user really wants it we'll add a flag.

Re-rendering from raw Bayer (with possible colour drift) is `rawkit render`,
not this command.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PreviewResult:
    data: bytes
    width: int
    height: int


class PreviewExtractError(RuntimeError):
    """LibRaw could not produce an embedded JPEG preview from this file."""


def _read_jpeg_size(data: bytes) -> tuple[int, int]:
    """Walk JPEG segment markers to find SOFn and pull (width, height).
    Returns (0, 0) when not a parseable JPEG. We do this ourselves instead
    of pulling in Pillow just to read two integers."""
    n = len(data)
    if n < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return 0, 0
    i = 2
    while i < n - 1:
        # Markers can be preceded by fill 0xFF bytes; skip them.
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            return 0, 0
        marker = data[i]
        i += 1
        # Standalone markers (no length field): RSTn, SOI, EOI, TEM.
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > n:
            return 0, 0
        seg_len = (data[i] << 8) | data[i + 1]
        # SOF0..SOF15 carry the frame dimensions, except DHT/JPG/DAC.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 7 > n:
                return 0, 0
            h = (data[i + 3] << 8) | data[i + 4]
            w = (data[i + 5] << 8) | data[i + 6]
            return w, h
        i += seg_len
    return 0, 0


def extract_preview(
    path: Path,
    *,
    long_edge: int | None = None,
    short_edge: int | None = None,
    megapixels: float | None = None,
    quality: int = 90,
) -> PreviewResult:
    """Extract the largest embedded SOOC JPEG. Raises PreviewExtractError on failure.

    When any of `long_edge` / `short_edge` / `megapixels` is set, the JPEG is
    decoded, LANCZOS-downscaled to the requested upper bound, and re-encoded
    at `quality` (default 90). This is **a second JPEG roundtrip** — slight
    quality loss vs the raw embedded bytes. Without any resize parameter the
    embedded JPEG bytes are returned verbatim (no decode, no re-encode).

    Failure modes:
      - rawpy not installed (shouldn't happen — it's a hard dependency)
      - LibRaw can't open the file (corrupt / unrecognised format)
      - the embedded preview isn't a JPEG (BITMAP path — uncommon; we'd
        need to encode it ourselves which isn't worth a Pillow dep yet)
    """
    try:
        import rawpy
    except ImportError as e:
        raise PreviewExtractError(
            "rawpy is not installed in the current environment. "
            "If you installed rawkit globally with `uv tool install`, "
            "reinstall after dependency changes:\n"
            "  cd <rawkit checkout> && uv tool install --reinstall --editable ."
        ) from e

    try:
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()  # libraw API name; we expose it as "preview"
    except Exception as e:
        raise PreviewExtractError(f"libraw failed: {e}") from e

    if getattr(thumb.format, "name", "") != "JPEG":
        raise PreviewExtractError(
            f"embedded preview is {thumb.format!r}, not JPEG "
            "(BITMAP path not yet supported)"
        )

    w, h = _read_jpeg_size(thumb.data)
    if w == 0 or h == 0:
        raise PreviewExtractError("could not parse JPEG dimensions")

    # Fast path: no resize requested → hand back the embedded bytes verbatim.
    if long_edge is None and short_edge is None and megapixels is None:
        return PreviewResult(thumb.data, w, h)

    # Resize path: decode → LANCZOS → re-encode.
    try:
        from PIL import Image, ImageOps
    except ImportError as e:
        raise PreviewExtractError(
            f"Pillow is required for --long/--short/--mp resizing: {e}"
        ) from e

    from rawkit._resize import resize_pil

    import io
    try:
        img = Image.open(io.BytesIO(thumb.data))
        img.load()  # force decode now so format errors fail here, not later
    except Exception as e:
        raise PreviewExtractError(f"decoding embedded JPEG failed: {e}") from e

    # Bake EXIF Orientation into the pixels themselves. Without this, our
    # re-encoded JPEG loses the rotation tag (Pillow's save() doesn't preserve
    # it unless we pass exif= explicitly), and any client that doesn't honour
    # EXIF Orientation shows portrait shots as sideways landscapes.
    # exif_transpose returns a new Image with pixels physically rotated and
    # the Orientation tag stripped from info["exif"].
    img = ImageOps.exif_transpose(img)

    try:
        resized = resize_pil(
            img,
            long_edge=long_edge,
            short_edge=short_edge,
            megapixels=megapixels,
        )
    except ValueError as e:  # >1 resize dimension set
        raise PreviewExtractError(str(e)) from e

    # NOTE: once we've decoded for the resize path we always re-encode, even
    # if the resize ended up a no-op. exif_transpose may have rotated pixels
    # (Orientation != 1 source), so the original bytes are no longer correct.

    out = io.BytesIO()
    # subsampling=0 (4:4:4) keeps colour fidelity on the second encode; the
    # tiny extra bytes are worth it when the user explicitly asked for resize.
    # Preserve the rest of the EXIF block (ISO, lens, GPS, etc.) — only
    # Orientation was stripped by exif_transpose. info["exif"] may be empty
    # bytes if the source had no APP1 segment; passing b"" is fine.
    save_kwargs: dict = {
        "format": "JPEG",
        "quality": int(quality),
        "subsampling": 0,
        "optimize": True,
    }
    exif_bytes = img.info.get("exif")
    if exif_bytes:
        save_kwargs["exif"] = exif_bytes
    resized.save(out, **save_kwargs)
    new_w, new_h = resized.size
    return PreviewResult(out.getvalue(), new_w, new_h)
