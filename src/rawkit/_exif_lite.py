"""Minimal TIFF/EXIF reader covering every RAW format rawkit cares about.

Why this exists
---------------
The standard EXIF tags rawkit needs (Make, Model, ExposureBiasValue, Rating,
Flash, GPS lat/lon, SubSecTimeOriginal) live inside a TIFF IFD inside every
RAW file. rawpy/LibRaw doesn't expose them; reaching for exiftool to fetch
them costs ~17 ms of Perl-maker-note parsing per file. Pillow can read them
when it can open the file, but Pillow rejects ARW ("Missing dimensions"),
RW2 (non-standard magic 0x55), CR3 (ISO BMFF, not TIFF), etc.

So we parse the bare minimum ourselves:
  * IFD walker that handles BYTE/ASCII/SHORT/LONG/RATIONAL/SRATIONAL/UNDEFINED
    (and arrays of those) — the only types EXIF actually uses for the tags
    rawkit cares about.
  * Per-format "find the TIFF" preamble: most formats start with a TIFF
    header at offset 0; RAF has a 148-byte custom prelude; CR3 hides
    a TIFF block in an ISO BMFF box.

Zero new dependencies. Pure stdlib. ~250 lines.

Read pattern
------------
EXIF is always near the start of the file. We open the file, seek/scan
to find the TIFF block, then read the IFDs. Total bytes read per file is
typically a few KB (IFD pointers + the few values that are larger than
4 bytes inline). The OS page cache makes the rawpy+exif double-open
nearly free.

Wire format references
----------------------
* TIFF 6.0 (1992): IFD layout, type codes
* EXIF 2.32: tag IDs, value semantics
* ISO/IEC 14496-12 (BMFF): box structure
* Canon CR3 layout: https://github.com/lclevy/canon_cr3
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, BinaryIO


# ---- TIFF tag IDs we care about --------------------------------------------
# IFD0 (top-level image directory)
T_IMAGEWIDTH   = 0x0100   # raw or thumbnail width; we pick the largest seen
T_IMAGEHEIGHT  = 0x0101   # raw or thumbnail height; idem
T_MAKE         = 0x010F
T_MODEL        = 0x0110
T_ORIENTATION  = 0x0112
# We deliberately do NOT extract IFD0:DateTime (0x0132). Per EXIF spec it's the
# "file modify time" / "image creation time of the IFD owner" — Lightroom
# rewrites it on every edit, so it doesn't match exiftool's DateTimeOriginal
# semantics. CR3's CMT1 box has DateTime too, but the real capture time lives
# in CMT2's DateTimeOriginal. Stick to ExifIFD:DateTimeOriginal everywhere.
T_SUBIFDS      = 0x014A   # array of offsets to SubIFDs (DNG/3FR/ARW raw)
T_NEWSUBFILE   = 0x00FE   # NewSubfileType: 0 = primary image, 1 = thumbnail
T_RATING       = 0x4746
T_EXIF_IFD     = 0x8769   # offset to ExifIFD
T_GPS_IFD      = 0x8825   # offset to GPSIFD
# Panasonic RW2-specific (magic 0x55) — non-standard tags Pana stows in IFD0.
# `_copy_named` keys these as IFD0 reads; we resolve them only as ISO/size
# fallbacks so we don't pollute non-Panasonic files.
T_RW2_ISO          = 0x0017  # Panasonic stores ISO here; not in standard ExifIFD
T_RW2_SENSORWIDTH  = 0x0002
T_RW2_SENSORHEIGHT = 0x0003

# ExifIFD
T_EXPOSURETIME = 0x829A
T_FNUMBER      = 0x829D
T_ISO          = 0x8827   # ISOSpeedRatings (legacy)
T_DTORIG       = 0x9003   # DateTimeOriginal
T_APEX_AV      = 0x9202   # ApertureValue (APEX); fallback when FNumber missing
T_BIAS         = 0x9204   # ExposureBiasValue
T_FLASH        = 0x9209
T_FOCALLENGTH  = 0x920A
T_SUBSEC_ORIG  = 0x9291   # SubSecTimeOriginal
T_LENSMAKE     = 0xA433
T_LENSMODEL    = 0xA434
T_PSI          = 0x8833   # PhotographicSensitivity (EXIF 2.3+, often duplicates ISO)
# ExifImageWidth/Height (0xA002/0xA003) live in ExifIFD and represent the
# *main image* (i.e. raw frame) dimensions — distinct from IFD0:ImageWidth
# which Phase One IIQ uses for the thumbnail. When present, these override
# the IFD0/SubIFD heuristic because they're the explicit raw-frame size.
T_EXIF_IMAGEWIDTH  = 0xA002
T_EXIF_IMAGEHEIGHT = 0xA003

# GPSIFD
T_GPS_LATREF   = 0x0001
T_GPS_LAT      = 0x0002
T_GPS_LONREF   = 0x0003
T_GPS_LON      = 0x0004

# Wanted = the set of tag IDs we actually deserialize. Anything outside this
# set we skip entirely — saves I/O on the few-byte inline values and avoids
# allocating Python objects for the dozens of tags we'd just throw away.
_IFD0_WANTED = frozenset({
    T_MAKE, T_MODEL, T_ORIENTATION, T_RATING,
    T_IMAGEWIDTH, T_IMAGEHEIGHT, T_SUBIFDS,
    T_RW2_ISO, T_RW2_SENSORWIDTH, T_RW2_SENSORHEIGHT,
    T_EXIF_IFD, T_GPS_IFD,
})
# What we read from each SubIFD: just dimensions + the type discriminator.
_SUBIFD_WANTED = frozenset({T_IMAGEWIDTH, T_IMAGEHEIGHT, T_NEWSUBFILE})
_EXIF_WANTED = frozenset({
    T_EXPOSURETIME, T_FNUMBER, T_ISO, T_PSI, T_DTORIG, T_APEX_AV,
    T_BIAS, T_FLASH, T_FOCALLENGTH, T_SUBSEC_ORIG, T_LENSMAKE, T_LENSMODEL,
    T_EXIF_IMAGEWIDTH, T_EXIF_IMAGEHEIGHT,
})
_GPS_WANTED = frozenset({
    T_GPS_LATREF, T_GPS_LAT, T_GPS_LONREF, T_GPS_LON,
})


# ---- IFD value type table --------------------------------------------------
# (size_in_bytes, struct_code_or_None) per TIFF type code.
# struct_code is the per-element single-value code; None = handled specially
# (ASCII string, UNDEFINED bytes, or rational which is two LONGs).
_TYPE_TABLE: dict[int, tuple[int, str | None]] = {
    1:  (1, "B"),    # BYTE
    2:  (1, None),   # ASCII (null-terminated)
    3:  (2, "H"),    # SHORT (uint16)
    4:  (4, "I"),    # LONG (uint32)
    5:  (8, None),   # RATIONAL (2x LONG)
    6:  (1, "b"),    # SBYTE
    7:  (1, None),   # UNDEFINED (raw bytes)
    8:  (2, "h"),    # SSHORT
    9:  (4, "i"),    # SLONG
    10: (8, None),   # SRATIONAL (2x SLONG)
    11: (4, "f"),    # FLOAT
    12: (8, "d"),    # DOUBLE
}


# ---- Errors ---------------------------------------------------------------

class ExifLiteError(Exception):
    """Raised when the file cannot be parsed as TIFF/EXIF.

    Higher layers should treat this as "no metadata available for this
    file" and continue with whatever rawpy returned. We never crash the
    whole batch over one weird file."""


# ---- Core IFD walker ------------------------------------------------------

def _read_ifd(
    data: bytes,
    ifd_offset: int,
    endian: str,
    wanted: frozenset[int],
) -> dict[int, Any]:
    """Read one IFD starting at `ifd_offset` in `data`.

    `endian` is '<' or '>'. Returns {tag_id: value}. Values:
      * ASCII → str (decoded utf-8 / latin-1 fallback, trailing NUL stripped)
      * RATIONAL/SRATIONAL → float (or list of floats for arrays)
      * scalar numeric → int / float
      * arrays of numeric → list
      * UNDEFINED → bytes
    """
    out: dict[int, Any] = {}
    if ifd_offset + 2 > len(data):
        return out
    n_entries = struct.unpack_from(endian + "H", data, ifd_offset)[0]
    # Sanity bound: a TIFF IFD with > 1000 entries is almost certainly a
    # parse-error chasing a bogus offset. Bail rather than reading garbage.
    if n_entries > 1000:
        return out
    entry_base = ifd_offset + 2
    for i in range(n_entries):
        eo = entry_base + i * 12
        if eo + 12 > len(data):
            break
        tag, ttype, count = struct.unpack_from(endian + "HHI", data, eo)
        if tag not in wanted:
            continue
        if ttype not in _TYPE_TABLE:
            continue
        size, code = _TYPE_TABLE[ttype]
        total = size * count
        # Value or offset lives in the next 4 bytes. Inline if total <= 4.
        if total <= 4:
            value_off = eo + 8
            buf = data[value_off : value_off + total]
        else:
            (off,) = struct.unpack_from(endian + "I", data, eo + 8)
            if off + total > len(data) or off < 0:
                continue  # bad offset, skip rather than IndexError
            buf = data[off : off + total]
        out[tag] = _decode_value(buf, ttype, count, endian, code)
    return out


def _decode_value(
    buf: bytes,
    ttype: int,
    count: int,
    endian: str,
    code: str | None,
) -> Any:
    if ttype == 2:  # ASCII
        # Trim trailing NULs and any padding whitespace.
        s = buf.rstrip(b"\x00")
        try:
            return s.decode("utf-8").strip()
        except UnicodeDecodeError:
            return s.decode("latin-1", errors="replace").strip()
    if ttype == 7:  # UNDEFINED
        return bytes(buf)
    if ttype in (5, 10):  # RATIONAL / SRATIONAL
        rcode = "II" if ttype == 5 else "ii"
        results: list[float] = []
        for k in range(count):
            num, den = struct.unpack_from(endian + rcode, buf, k * 8)
            results.append(num / den if den else 0.0)
        return results[0] if count == 1 else results
    # Numeric scalar / array
    assert code is not None
    if count == 1:
        return struct.unpack_from(endian + code, buf, 0)[0]
    return list(struct.unpack_from(endian + (code * count), buf, 0))


# ---- Per-format entry points ----------------------------------------------

def _parse_tiff(data: bytes, header_off: int = 0) -> dict[str, Any]:
    """Parse a TIFF block (possibly inside a larger file) starting at
    `header_off`. The TIFF magic bytes live at `header_off`.

    Returns a flat dict using string keys that match the rawkit
    field-map shape used in exif.py (Make, Model, DateTimeOriginal, ...).
    Returns {} on any structural failure — caller must treat as no-data.
    """
    if header_off + 8 > len(data):
        return {}
    bo = data[header_off : header_off + 2]
    if bo == b"II":
        endian = "<"
    elif bo == b"MM":
        endian = ">"
    else:
        return {}
    # TIFF magic byte: 0x002A standard, 0x0055 = Panasonic RW2/RAW,
    # 0x4F52 = Olympus ORF ('IIRO' / 'MMOR' header). All three keep the
    # standard IFD layout; only the file-level magic differs.
    magic = struct.unpack_from(endian + "H", data, header_off + 2)[0]
    if magic not in (0x002A, 0x0055, 0x4F52):
        return {}
    (ifd0_off_rel,) = struct.unpack_from(endian + "I", data, header_off + 4)
    # All IFD offsets inside the TIFF block are relative to the TIFF start.
    # We work on a slice so subsequent _read_ifd calls don't need to know
    # about header_off.
    tiff = data[header_off:]
    ifd0 = _read_ifd(tiff, ifd0_off_rel, endian, _IFD0_WANTED)
    out: dict[str, Any] = {}
    _copy_named(out, ifd0, {
        T_MAKE: "Make",
        T_MODEL: "Model",
        T_ORIENTATION: "Orientation",
        T_RATING: "Rating",
    })
    _resolve_dimensions(out, tiff, ifd0, endian)
    exif_off = ifd0.get(T_EXIF_IFD)
    if isinstance(exif_off, int) and exif_off > 0:
        exif_dir = _read_ifd(tiff, exif_off, endian, _EXIF_WANTED)
        _copy_exif(out, exif_dir)
    gps_off = ifd0.get(T_GPS_IFD)
    if isinstance(gps_off, int) and gps_off > 0:
        gps_dir = _read_ifd(tiff, gps_off, endian, _GPS_WANTED)
        out.update(_compose_gps(gps_dir))
    _resolve_panasonic_iso(out, ifd0)
    return out


def _resolve_dimensions(
    out: dict[str, Any], tiff: bytes, ifd0: dict[int, Any], endian: str
) -> None:
    """Pick the user-facing ImageWidth/ImageHeight pair from a TIFF.

    The challenge: IFD0:ImageWidth means different things per format.
      * CR3 (CMT1): IFD0:ImageWidth IS the raw sensor width — use directly.
      * ARW (Sony): IFD0:ImageWidth is absent; the full-raw dims live in
        a SubIFD pointed to by IFD0:SubIFDs (0x014A).
      * DNG / 3FR: IFD0:ImageWidth is the THUMBNAIL (e.g. 160x120);
        the full-raw lives in a SubIFD with NewSubfileType=0.
      * RW2 (Panasonic): IFD0:ImageWidth is absent; Panasonic stows the
        true sensor size in IFD0:0x0002/0x0003.

    Heuristic that handles all five: start with IFD0:ImageWidth/Height
    if present, then walk every SubIFD and override with whichever
    dimensions have the LARGEST area. The full raw is always the largest
    image inside a DNG/3FR/ARW, so picking max is a robust proxy for
    the "NewSubfileType==0" check exiftool does.

    Falls back to Panasonic SensorWidth/Height (close enough to the
    user-visible value — within ~0.3% of exiftool's composite report).
    """
    best_w = 0
    best_h = 0
    w0 = ifd0.get(T_IMAGEWIDTH)
    h0 = ifd0.get(T_IMAGEHEIGHT)
    if isinstance(w0, int) and isinstance(h0, int) and w0 > 0 and h0 > 0:
        best_w, best_h = w0, h0

    subs = ifd0.get(T_SUBIFDS)
    sub_offs: list[int] = []
    if isinstance(subs, int):
        sub_offs = [subs]
    elif isinstance(subs, list):
        sub_offs = [x for x in subs if isinstance(x, int) and x > 0]
    # Cap at 8: pathological files (or parse errors chasing bogus offsets)
    # shouldn't be able to make us read 1000 IFDs.
    for off in sub_offs[:8]:
        sub = _read_ifd(tiff, off, endian, _SUBIFD_WANTED)
        sw = sub.get(T_IMAGEWIDTH)
        sh = sub.get(T_IMAGEHEIGHT)
        if isinstance(sw, int) and isinstance(sh, int) and sw > 0 and sh > 0:
            if sw * sh > best_w * best_h:
                best_w, best_h = sw, sh

    # Panasonic RW2 fallback: when neither IFD0 nor SubIFD gave us a
    # real dimension, use the sensor dims (close to user-visible image
    # size; off by ~16 px due to active-area crop).
    if best_w == 0 or best_h == 0:
        sw = ifd0.get(T_RW2_SENSORWIDTH)
        sh = ifd0.get(T_RW2_SENSORHEIGHT)
        if isinstance(sw, int) and isinstance(sh, int) and sw > 0 and sh > 0:
            best_w, best_h = sw, sh

    if best_w > 0 and best_h > 0:
        out["ImageWidth"] = best_w
        out["ImageHeight"] = best_h


def _resolve_panasonic_iso(out: dict[str, Any], ifd0: dict[int, Any]) -> None:
    """RW2 stores ISO in IFD0:0x0017 instead of the standard ExifIFD slot.
    Use as a last-resort fallback so `--where iso>800` matches RW2 files."""
    if "ISO" in out:
        return
    iso = ifd0.get(T_RW2_ISO)
    if isinstance(iso, int) and iso > 0:
        out["ISO"] = iso


def _copy_exif(out: dict[str, Any], exif_dir: dict[int, Any]) -> None:
    """Copy ExifIFD tags to output using exiftool-compatible names.

    Name alignment with the exiftool path matters because the downstream
    normalizer in exif.py (`_FIELD_MAP`) reads by these keys. The two
    surprises here are:
      * ExposureBiasValue (TIFF tag name) is exposed as ExposureCompensation
        — which is the user-facing name exiftool emits and what
        `_FIELD_MAP` looks for.
      * ISO is preferred over PhotographicSensitivity. exiftool's EXIF:ISO
        is the canonical source the rest of rawkit aligns on.
    """
    _copy_named(out, exif_dir, {
        T_EXPOSURETIME: "ExposureTime",
        T_FNUMBER:      "FNumber",
        T_DTORIG:       "DateTimeOriginal",
        T_APEX_AV:      "ApertureValue",
        T_BIAS:         "ExposureCompensation",
        T_FLASH:        "Flash",
        T_FOCALLENGTH:  "FocalLength",
        T_SUBSEC_ORIG:  "SubSecTimeOriginal",
        T_LENSMAKE:     "LensMake",
        T_LENSMODEL:    "LensModel",
    })
    # ISO source priority: legacy EXIF:ISO (0x8827) first, fall back to
    # PhotographicSensitivity (0x8833) which EXIF 2.3+ uses for the same
    # value. Modern Sony bodies (a7R V, a1) only write 0x8833.
    if T_ISO in exif_dir:
        out["ISO"] = exif_dir[T_ISO]
    elif T_PSI in exif_dir:
        out["ISO"] = exif_dir[T_PSI]
    # ExifImageWidth/Height (0xA002/0xA003) are the explicit main-frame
    # dimensions. Phase One IIQ writes 640x480 (thumbnail) into IFD0 and the
    # real 11608x8708 raw dimensions only here — without an override we'd
    # report the thumbnail. But Canon CR2 writes a 1936x1288 preview into
    # IFD0 (legit, what exiftool's default -ImageWidth selects too) and the
    # raw 3888x2592 into ExifImageWidth — taking the larger there would
    # disagree with the exiftool backend. Heuristic: only override when the
    # current value is small enough to look like a thumbnail (< 1 megapixel).
    # Modern raw sensors are always ≥ 1 MP; thumbnails are almost always
    # below it (640x480 = 0.3 MP, 1024x768 = 0.8 MP).
    ew = exif_dir.get(T_EXIF_IMAGEWIDTH)
    eh = exif_dir.get(T_EXIF_IMAGEHEIGHT)
    if isinstance(ew, int) and isinstance(eh, int) and ew > 0 and eh > 0:
        cur_w = out.get("ImageWidth", 0)
        cur_h = out.get("ImageHeight", 0)
        cur_area = cur_w * cur_h if isinstance(cur_w, int) and isinstance(cur_h, int) else 0
        if cur_area < 1_000_000 and ew * eh > cur_area:
            out["ImageWidth"] = ew
            out["ImageHeight"] = eh


def _copy_named(out: dict[str, Any], src: dict[int, Any], mapping: dict[int, str]) -> None:
    for tag, name in mapping.items():
        if tag in src:
            out[name] = src[tag]


def _compose_gps(gps: dict[int, Any]) -> dict[str, Any]:
    """Convert GPSLatitude([deg,min,sec]) + GPSLatitudeRef('N'/'S') to a
    signed decimal float `GPSLatitude` (+ same for longitude). This matches
    what exiftool produces in `-n` (numeric) mode, which is the source of
    truth the rest of rawkit's normalizer was tuned against."""
    out: dict[str, Any] = {}
    for ref_tag, val_tag, key, neg_chars in (
        (T_GPS_LATREF, T_GPS_LAT, "GPSLatitude", b"Ss"),
        (T_GPS_LONREF, T_GPS_LON, "GPSLongitude", b"Ww"),
    ):
        ref = gps.get(ref_tag)
        val = gps.get(val_tag)
        if not (isinstance(val, list) and len(val) == 3):
            continue
        deg = val[0] + val[1] / 60.0 + val[2] / 3600.0
        # Ref can be ASCII str (already decoded) or single byte; normalize.
        sign = 1.0
        if isinstance(ref, str) and ref and ref[0] in "SsWw":
            sign = -1.0
        elif isinstance(ref, (bytes, bytearray)) and ref and ref[:1] in (b"S", b"s", b"W", b"w"):
            sign = -1.0
        out[key] = sign * deg
    return out


def _read_file_head(path: Path, n: int) -> bytes:
    """Read up to `n` bytes from the start of the file. Returns the actual
    bytes read (may be shorter for tiny files). On OSError returns b''."""
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


# ---- ISO BMFF (CR3) navigator ---------------------------------------------
# Box structure: [4 bytes size][4 bytes type][payload]. size==1 means a
# 64-bit extended size in the next 8 bytes; size==0 means "extends to EOF".
# Standard EXIF in CR3 lives in:
#   moov > uuid (85c0b687-820f-11e0-8111-f4ce462b6a48) > CMT1 (=IFD0 TIFF)
# We just scan for CMT1; if found we feed its payload as a TIFF block.

_CR3_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")


def _walk_bmff(data: bytes, start: int, end: int):
    """Yield (box_type:bytes, payload_off:int, payload_end:int) for every
    top-level box in [start, end). Robust against truncation and weird
    sizes — we just skip and break rather than raise."""
    off = start
    while off + 8 <= end:
        size = int.from_bytes(data[off : off + 4], "big")
        btype = data[off + 4 : off + 8]
        header_len = 8
        if size == 1:
            if off + 16 > end:
                return
            size = int.from_bytes(data[off + 8 : off + 16], "big")
            header_len = 16
        if size == 0:
            # extends to EOF
            payload_off = off + header_len
            yield btype, payload_off, end
            return
        if size < header_len or off + size > end:
            return
        yield btype, off + header_len, off + size
        off += size


def _find_cr3_cmt_boxes(data: bytes) -> dict[bytes, bytes]:
    """Return the CMT1/CMT2/CMT3/CMT4 TIFF blocks found in a CR3 file.

    Canon stores each EXIF block as a SEPARATE top-level TIFF inside the
    Canon-specific uuid box of moov:
      CMT1 = IFD0  (Make, Model, Orientation)
      CMT2 = ExifIFD (DateTimeOriginal, FNumber, ISO, LensModel, Flash, ...)
      CMT3 = MakerNote (Canon-specific; rawkit doesn't read)
      CMT4 = GPSIFD
    Each block is a self-contained little-endian TIFF with its own header,
    so we feed each to _parse_tiff and merge the results.
    """
    found: dict[bytes, bytes] = {}
    for btype, p_off, p_end in _walk_bmff(data, 0, len(data)):
        if btype != b"moov":
            continue
        for inner_t, ip_off, ip_end in _walk_bmff(data, p_off, p_end):
            if inner_t != b"uuid":
                continue
            if data[ip_off : ip_off + 16] != _CR3_UUID:
                continue
            sub_off = ip_off + 16
            for sub_t, so, se in _walk_bmff(data, sub_off, ip_end):
                if sub_t in (b"CMT1", b"CMT2", b"CMT4"):
                    found[sub_t] = data[so:se]
        break  # found moov; no need to keep scanning top level
    return found


# ---- Public entry point ----------------------------------------------------

# How many bytes from the start of the file to slurp for EXIF parsing.
# Justification: EXIF IFD0/ExifIFD/GPSIFD pointers and most of their values
# live within the first ~64 KB of every RAW format we care about. The
# embedded preview JPEG (the big chunk that bloats the file) starts later.
# 256 KB is a safe over-shoot that's still ~1 ms cold from external SSD.
HEAD_SIZE = 256 * 1024


def _find_jpeg_exif_tiff(buf: bytes, jpeg_off: int) -> int | None:
    """Find the offset of the TIFF block inside a JPEG's APP1/Exif segment.

    JPEG layout we walk: starts with 0xFFD8 (SOI), then a sequence of
    marker segments. Most non-standalone markers carry a big-endian
    uint16 length immediately after the marker byte (length includes the
    2 length bytes). APP1 = 0xFFE1; the EXIF flavor of APP1 has a payload
    that begins with the ASCII literal "Exif\\x00\\x00" — the TIFF block
    starts 6 bytes into the payload, right after that signature.

    Returns the absolute offset (within `buf`) of the TIFF header, or
    None if the JPEG is malformed/truncated, the offset is out of range,
    or no APP1/Exif segment is found before SOS (0xFFDA, start of image
    data — past which there's no more metadata).
    """
    n = len(buf)
    if not (0 <= jpeg_off < n - 4):
        return None
    if buf[jpeg_off : jpeg_off + 2] != b"\xff\xd8":
        return None
    i = jpeg_off + 2  # past SOI
    while i < n - 4:
        # Markers may be preceded by fill 0xFF bytes; skip them.
        while i < n and buf[i] == 0xFF:
            i += 1
        if i >= n:
            return None
        marker = buf[i]
        i += 1
        # SOS = start of compressed image data; no more metadata after it.
        if marker == 0xDA:
            return None
        # Standalone markers carry no length (SOI/EOI/TEM/RSTn).
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > n:
            return None
        seg_len = struct.unpack_from(">H", buf, i)[0]
        if seg_len < 2:  # malformed: length must include the 2 length bytes
            return None
        payload_off = i + 2
        payload_end = i + seg_len
        if payload_end > n:
            return None
        # APP1 (0xFFE1) with "Exif\0\0" prefix → TIFF block follows the prefix.
        if marker == 0xE1 and buf[payload_off : payload_off + 6] == b"Exif\x00\x00":
            return payload_off + 6
        i = payload_end
    return None


def _parse_mrw(path: Path) -> dict[str, Any]:
    """Minolta MRW (KONICA MINOLTA / late Minolta DSLRs).

    Container layout: 4-byte magic '\\x00MRM' + big-endian uint32 total
    length, then a sequence of named sub-blocks where each block tag is
    4 bytes: a leading 0x00 followed by 3 ASCII letters (e.g. '\\x00PRD',
    '\\x00WBG', '\\x00RIF', '\\x00TTW'). After the tag comes a big-endian
    uint32 length + that many bytes of payload. The '\\x00TTW' block
    payload is a self-contained standard TIFF that holds the EXIF —
    once we find it, we can hand it to _parse_tiff.
    """
    head = _read_file_head(path, HEAD_SIZE)
    if len(head) < 8 or head[:4] != b"\x00MRM":
        return {}
    i = 8  # first sub-block starts right after the 8-byte file header
    end = len(head)
    while i + 8 <= end:
        name = head[i : i + 4]
        block_len = int.from_bytes(head[i + 4 : i + 8], "big")
        data_off = i + 8
        if name == b"\x00TTW":
            return _parse_tiff(head, data_off)
        i = data_off + block_len
    return {}


def _parse_x3f(path: Path) -> dict[str, Any]:
    """Sigma X3F (Foveon). Not TIFF — Sigma's own container.

    Layout: 'FOVb' magic header, then a sequence of image/property/CAMF
    sections, then a directory at the very end. The last 4 bytes of the
    file are a little-endian uint32 directory offset; the directory
    starts with 'SECd' followed by version + entry count + 12-byte entries
    of (offset, length, type[4]). We're after entries of type 'IMA2',
    which are SECi-wrapped JPEG previews containing the standard
    APP1/Exif TIFF block.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0)
            head4 = f.read(4)
            if head4 != b"FOVb":
                return {}
            f.seek(-4, 2)
            dir_off = struct.unpack("<I", f.read(4))[0]
            f.seek(dir_off)
            dir_head = f.read(12)
            if len(dir_head) < 12 or dir_head[:4] != b"SECd":
                return {}
            count = struct.unpack_from("<I", dir_head, 8)[0]
            # Guard against absurd counts (12 bytes per entry).
            if count <= 0 or count > 100:
                return {}
            entries_blob = f.read(count * 12)
            if len(entries_blob) < count * 12:
                return {}
            # SECi header is 28 bytes: magic[4] + version[4] + type[4] +
            # format[4] + columns[4] + rows[4] + rowsize[4]. format=18=JPEG.
            for k in range(count):
                e_off, e_len, e_type = struct.unpack_from(
                    "<II4s", entries_blob, k * 12
                )
                if e_type != b"IMA2" or e_len < 32:
                    continue
                f.seek(e_off)
                sec_head = f.read(28)
                if len(sec_head) < 28 or sec_head[:4] != b"SECi":
                    continue
                fmt = struct.unpack_from("<I", sec_head, 12)[0]
                if fmt != 18:  # not a JPEG-encoded thumbnail/preview
                    continue
                # Read the JPEG payload. We only need enough of it to walk
                # past APP1/Exif; cap at 256 KB so large preview JPEGs
                # don't balloon memory.
                payload = f.read(min(e_len - 28, HEAD_SIZE))
                tiff_off = _find_jpeg_exif_tiff(payload, 0)
                if tiff_off is None:
                    continue
                return _parse_tiff(payload, tiff_off)
    except OSError:
        return {}
    return {}


def read_metadata(path: Path) -> dict[str, Any]:
    """Read standard EXIF metadata from a RAW file. Format-agnostic.

    Returns a dict using the same string keys as the exiftool path
    (Make, Model, DateTimeOriginal, ExposureTime, FNumber, ISO,
    PhotographicSensitivity, ExposureBiasValue, Flash, FocalLength,
    SubSecTimeOriginal, LensMake, LensModel, ApertureValue, Rating,
    Orientation, GPSLatitude, GPSLongitude).

    Returns {} on any parse error (file truncated, unknown format,
    weird IFD, ...). Caller decides how to handle the absence —
    typically: use whatever rawpy gave and move on.
    """
    suffix = path.suffix.lower()
    # RAF: Fujifilm. 16-byte 'FUJIFILMCCD-RAW ' magic, then version/camera
    # strings, then at offset 0x54 a uint32-BE pointer to the *embedded
    # JPEG preview* (not to a raw TIFF block — that's a docs trap we fell
    # into). The EXIF lives inside that JPEG, in an APP1 segment whose
    # payload starts with the literal "Exif\x00\x00" followed by a
    # standard TIFF header. So we read 0x54..0x58, jump to the JPEG SOI,
    # walk the segment list to the APP1/Exif segment, and parse the
    # TIFF that begins 6 bytes into the APP1 payload.
    if suffix == ".raf":
        head = _read_file_head(path, HEAD_SIZE)
        if len(head) < 0x58:
            return {}
        jpeg_off = int.from_bytes(head[0x54 : 0x58], "big")
        tiff_off = _find_jpeg_exif_tiff(head, jpeg_off)
        if tiff_off is None:
            return {}
        return _parse_tiff(head, tiff_off)

    if suffix == ".x3f":
        return _parse_x3f(path)

    if suffix == ".mrw":
        return _parse_mrw(path)

    head = _read_file_head(path, HEAD_SIZE)
    if not head:
        return {}

    # ISO BMFF formats (CR3, HEIC-ish): first box is 'ftyp'. CR3 is the
    # only one rawkit cares about in this family.
    if len(head) >= 8 and head[4:8] == b"ftyp":
        if suffix == ".cr3":
            cmts = _find_cr3_cmt_boxes(head)
            if not cmts:
                return {}
            out: dict[str, Any] = {}
            # CMT1 = IFD0
            if b"CMT1" in cmts:
                out.update(_parse_cmt_ifd(cmts[b"CMT1"], _IFD0_WANTED, kind="ifd0"))
            # CMT2 = ExifIFD
            if b"CMT2" in cmts:
                out.update(_parse_cmt_ifd(cmts[b"CMT2"], _EXIF_WANTED, kind="exif"))
            # CMT4 = GPSIFD
            if b"CMT4" in cmts:
                out.update(_parse_cmt_ifd(cmts[b"CMT4"], _GPS_WANTED, kind="gps"))
            return out
        return {}

    # Plain TIFF-shaped (ARW, DNG, NEF, RW2, ORF, PEF, 3FR, IIQ, MOS, ...).
    # Even RW2's non-standard magic 0x55 is handled by _parse_tiff.
    if len(head) >= 4 and head[:2] in (b"II", b"MM"):
        result = _parse_tiff(head, 0)
        if not result:
            # Pillow's Pano DNGs (and any other TIFF where IFD0 is placed
            # after the giant pixel payload, e.g. multi-shot Lightroom
            # exports) can have IFD0 at an offset well beyond our 256 KB
            # head buffer. Re-read the file using a wider window pointed
            # at the IFD0 offset.
            result = _parse_tiff_with_offset_seek(path, head)
        # XMP fallback for files that leave Make/Model out of IFD0 but
        # write them into the XMP packet living in head (Leaf MOS does
        # this). Apply only when the standard TIFF parse left a gap, so
        # we never overwrite real IFD0 values.
        _xmp_fill_missing(result, head)
        return result

    return {}


def _xmp_fill_missing(out: dict[str, Any], head: bytes) -> None:
    """Pull tiff:Make / tiff:Model / exif:DateTimeOriginal from an XMP packet
    embedded in head, but only into keys the caller didn't already fill.

    Tag-soup approach by design: full XML parsing is overkill for three
    fixed-name elements, and an XML parser would also fail closed if the
    packet has any minor malformation. The XMP block is bracketed by
    <x:xmpmeta>...</x:xmpmeta> markers; we just find them, then look for
    each element by literal name inside that slice.

    DateTimeOriginal note: XMP stores ISO 8601 ('2025-08-09T12:34:56Z' or
    '...+09:00'); EXIF wire format wants colons in the date part
    ('2025:08:09 12:34:56'). We strip the timezone and 'T' separator
    to produce something _normalize() in exif.py understands.
    """
    if not out:
        return
    i = head.find(b"<x:xmpmeta")
    if i < 0:
        return
    j = head.find(b"</x:xmpmeta>", i)
    if j < 0:
        return
    xmp = head[i:j].decode("utf-8", "replace")

    def _pull(tag: str) -> str | None:
        open_tag, close_tag = "<" + tag + ">", "</" + tag + ">"
        s = xmp.find(open_tag)
        if s < 0:
            return None
        e = xmp.find(close_tag, s + len(open_tag))
        if e < 0:
            return None
        v = xmp[s + len(open_tag) : e].strip()
        return v or None

    if "Make" not in out:
        v = _pull("tiff:Make")
        if v:
            out["Make"] = v
    if "Model" not in out:
        v = _pull("tiff:Model")
        if v:
            out["Model"] = v
    if "DateTimeOriginal" not in out:
        v = _pull("exif:DateTimeOriginal")
        if v and len(v) >= 19 and v[4] == "-" and v[7] == "-" and v[10] == "T":
            # ISO 8601 'YYYY-MM-DDTHH:MM:SS[.fff][Z|±HH:MM]' → EXIF wire
            # 'YYYY:MM:DD HH:MM:SS' that _normalize() understands.
            date = v[0:4] + ":" + v[5:7] + ":" + v[8:10]
            time = v[11:19]  # HH:MM:SS, ignoring sub-second + timezone
            out["DateTimeOriginal"] = date + " " + time


def _parse_cmt_ifd(tiff_block: bytes, wanted: frozenset[int], kind: str) -> dict[str, Any]:
    """Each CR3 CMT* box is a self-contained TIFF header + ONE IFD. We can't
    just call _parse_tiff because that follows IFD0->ExifIFD pointers, which
    CMT2 doesn't have (CMT2 *is* the ExifIFD itself)."""
    if len(tiff_block) < 8:
        return {}
    bo = tiff_block[:2]
    endian = "<" if bo == b"II" else ">" if bo == b"MM" else None
    if endian is None:
        return {}
    magic = struct.unpack_from(endian + "H", tiff_block, 2)[0]
    if magic not in (0x002A, 0x0055, 0x4F52):
        return {}
    (ifd_off,) = struct.unpack_from(endian + "I", tiff_block, 4)
    raw = _read_ifd(tiff_block, ifd_off, endian, wanted)
    out: dict[str, Any] = {}
    if kind == "ifd0":
        _copy_named(out, raw, {
            T_MAKE: "Make", T_MODEL: "Model",
            T_ORIENTATION: "Orientation", T_RATING: "Rating",
        })
        # CR3's CMT1 stores raw dimensions directly in IFD0 — no SubIFD
        # chain to chase, so the dimension resolver runs and finishes on
        # the IFD0 values alone.
        _resolve_dimensions(out, tiff_block, raw, endian)
    elif kind == "exif":
        _copy_exif(out, raw)
    elif kind == "gps":
        out.update(_compose_gps(raw))
    return out


def _parse_tiff_with_offset_seek(path: Path, head: bytes) -> dict[str, Any]:
    """Fallback for TIFF files where IFD0 lives past our 256 KB head buffer.

    Triggers on, e.g., Lightroom-stitched Pano DNGs where IFD0 is placed at
    the END of a multi-hundred-MB file. We can't load the whole file into
    memory, and we can't splice (a 600 MB zero-padded bytearray is comically
    wasteful). So instead we read multiple small windows on demand:
      * window A = the 256 KB head we already have (for inline header magic
        + any IFD entry values that happen to live near the start)
      * window B = a fresh 256 KB read starting at IFD0_off (for the IFD0
        directory itself + values stored after it)
    Each IFD entry's value-offset is checked against both windows; values
    falling in neither are silently dropped. The result: Make/Model/Orientation
    nearly always decode (they live right next to IFD0); ExifIFD/GPSIFD
    sub-IFDs decode iff their offsets land inside window B.
    """
    if len(head) < 8:
        return {}
    bo = head[:2]
    endian = "<" if bo == b"II" else ">" if bo == b"MM" else None
    if endian is None:
        return {}
    magic = struct.unpack_from(endian + "H", head, 2)[0]
    if magic not in (0x002A, 0x0055, 0x4F52):
        return {}
    (ifd0_off,) = struct.unpack_from(endian + "I", head, 4)
    if ifd0_off + 2 <= len(head):
        return {}  # the head fast path should have handled this
    try:
        with open(path, "rb") as f:
            f.seek(max(0, ifd0_off))
            ifd_buf = f.read(HEAD_SIZE)
    except OSError:
        return {}

    windows = [(0, head), (ifd0_off, ifd_buf)]
    ifd0 = _read_ifd_windowed(windows, ifd0_off, endian, _IFD0_WANTED)
    out: dict[str, Any] = {}
    _copy_named(out, ifd0, {
        T_MAKE: "Make", T_MODEL: "Model",
        T_ORIENTATION: "Orientation", T_RATING: "Rating",
    })
    # In the windowed path SubIFDs almost certainly live outside any
    # window we've read — _read_ifd_windowed will drop them. That's
    # acceptable: callers in this branch are huge Pano DNGs, where the
    # thumbnail dims at IFD0 are still better than nothing.
    w0 = ifd0.get(T_IMAGEWIDTH)
    h0 = ifd0.get(T_IMAGEHEIGHT)
    if isinstance(w0, int) and isinstance(h0, int) and w0 > 0 and h0 > 0:
        out["ImageWidth"] = w0
        out["ImageHeight"] = h0
    exif_off = ifd0.get(T_EXIF_IFD)
    if isinstance(exif_off, int) and exif_off > 0:
        exif_dir = _read_ifd_windowed(windows, exif_off, endian, _EXIF_WANTED)
        _copy_exif(out, exif_dir)
    gps_off = ifd0.get(T_GPS_IFD)
    if isinstance(gps_off, int) and gps_off > 0:
        gps_dir = _read_ifd_windowed(windows, gps_off, endian, _GPS_WANTED)
        out.update(_compose_gps(gps_dir))
    _resolve_panasonic_iso(out, ifd0)
    return out


def _slice_from_windows(
    windows: list[tuple[int, bytes]], abs_off: int, length: int
) -> bytes | None:
    """Return `length` bytes starting at file-absolute offset `abs_off` if
    that range falls entirely within one of `windows`. Otherwise None."""
    for win_off, win_data in windows:
        rel = abs_off - win_off
        if 0 <= rel and rel + length <= len(win_data):
            return win_data[rel : rel + length]
    return None


def _read_ifd_windowed(
    windows: list[tuple[int, bytes]],
    ifd_offset: int,
    endian: str,
    wanted: frozenset[int],
) -> dict[int, Any]:
    """Same contract as _read_ifd but works over a list of (abs_off, buf)
    windows instead of a single contiguous buffer. File-absolute offsets
    in IFD entries are resolved by finding the window that contains them.
    Entries whose values straddle window boundaries are skipped.
    """
    header = _slice_from_windows(windows, ifd_offset, 2)
    if header is None:
        return {}
    n_entries = struct.unpack_from(endian + "H", header, 0)[0]
    if n_entries > 1000:
        return {}
    out: dict[int, Any] = {}
    entries_buf = _slice_from_windows(windows, ifd_offset + 2, n_entries * 12)
    if entries_buf is None:
        return out
    for i in range(n_entries):
        eo = i * 12
        tag, ttype, count = struct.unpack_from(endian + "HHI", entries_buf, eo)
        if tag not in wanted or ttype not in _TYPE_TABLE:
            continue
        size, code = _TYPE_TABLE[ttype]
        total = size * count
        if total <= 4:
            buf = entries_buf[eo + 8 : eo + 8 + total]
        else:
            (off,) = struct.unpack_from(endian + "I", entries_buf, eo + 8)
            buf = _slice_from_windows(windows, off, total)
            if buf is None:
                continue  # value lives outside any window we've read
        out[tag] = _decode_value(buf, ttype, count, endian, code)
    return out
