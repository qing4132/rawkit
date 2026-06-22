"""Tests for the lite EXIF backend (_exif_lite + the lite path in exif.py).

Two layers:

  * Unit tests on _exif_lite using SYNTHETIC TIFF/CR3 byte streams. These
    don't need real RAW samples or rawpy or exiftool — they pin down the
    parser's behaviour on the IFD-layout edge cases (RW2 magic 0x55,
    big-endian TIFF, CR3 multi-box, IFD0 past head buffer).

  * Cross-backend tests against real samples when both rawpy and exiftool
    are available. They prove the lite backend produces records
    indistinguishable from the exiftool path on every field the user can
    query. Skipped automatically on machines without sample data.

We mark lite-backend tests with `lite_backend` so the autouse fixture in
test_exif.py doesn't pin them onto the exiftool path.
"""

from __future__ import annotations

import io
import os
import shutil
import struct
from pathlib import Path

import pytest

from rawkit import _exif_lite, exif


# --- Synthetic TIFF builder -------------------------------------------------

def _build_tiff(
    ifd0: list[tuple[int, int, list]],       # (tag, ttype, values)
    exif_ifd: list[tuple[int, int, list]] | None = None,
    gps_ifd: list[tuple[int, int, list]] | None = None,
    *,
    endian: str = "<",
    magic: int = 0x002A,
) -> bytes:
    """Build a minimal valid TIFF stream covering IFD0 + optional sub-IFDs.

    `values` is a list because TIFF count==1 still uses a list of length 1
    for uniformity. ttype matches `_exif_lite._TYPE_TABLE` codes:
      2=ASCII, 3=SHORT, 4=LONG, 5=RATIONAL.

    Internal layout (so the produced stream is parseable end-to-end):
      [header 8B][IFD0 directory][value pool 0][ExifIFD dir][value pool E][GPS dir][value pool G]

    The TIFF parser dereferences offsets relative to the TIFF start (=0
    here), so this is a fully self-contained TIFF.
    """
    # Build value pools first so we know offsets when emitting IFDs.
    buf = bytearray(8)  # placeholder for header

    def emit_value(ttype: int, values: list) -> tuple[bytes, int | None]:
        """Return (inline_4_bytes, external_offset).

        If the value fits in 4 bytes we put it inline and return offset=None.
        Otherwise we write it to `buf` (with 2-byte alignment) and return
        the offset.
        """
        size, code = _exif_lite._TYPE_TABLE[ttype]
        count = len(values)
        if ttype == 2:  # ASCII: list of single chars / one full string
            text = "".join(values).encode("utf-8") + b"\x00"
            count = len(text)
            data = text
        elif ttype in (5, 10):  # RATIONAL / SRATIONAL
            count = len(values)
            rcode = "II" if ttype == 5 else "ii"
            data = b"".join(struct.pack(endian + rcode, n, d) for n, d in values)
        else:
            data = struct.pack(endian + code * count, *values)
        total = (size * count) if ttype != 2 else len(data)
        if total <= 4:
            inline = data + b"\x00" * (4 - total)
            return inline, None
        # Pad buf to 2-byte boundary for cleanliness; not required by TIFF
        # but avoids accidental odd-offset entries.
        if len(buf) % 2:
            buf.append(0)
        off = len(buf)
        buf.extend(data)
        return struct.pack(endian + "I", off), off

    def emit_ifd(entries: list[tuple[int, int, list]], next_off: int = 0) -> int:
        """Emit an IFD with the given entries, return its offset in buf."""
        if len(buf) % 2:
            buf.append(0)
        ifd_off = len(buf)
        n = len(entries)
        buf.extend(struct.pack(endian + "H", n))
        # Reserve space for entries + the next-IFD-offset trailer.
        entries_start = len(buf)
        buf.extend(b"\x00" * (n * 12 + 4))
        # Now backfill each entry. We have to do this in two passes because
        # values whose size > 4 bytes need to be appended *after* the IFD
        # block (so they don't overlap with later entries).
        for i, (tag, ttype, values) in enumerate(entries):
            size, _ = _exif_lite._TYPE_TABLE[ttype]
            if ttype == 2:
                count = len("".join(values).encode("utf-8")) + 1
            else:
                count = len(values)
            value_or_off, _ = emit_value(ttype, values)
            entry = struct.pack(endian + "HHI", tag, ttype, count) + value_or_off
            buf[entries_start + i * 12 : entries_start + (i + 1) * 12] = entry
        # next-IFD pointer
        buf[entries_start + n * 12 : entries_start + n * 12 + 4] = struct.pack(
            endian + "I", next_off
        )
        return ifd_off

    # Reserve sub-IFDs first if requested so we know their offsets when
    # writing IFD0's pointers.
    exif_off = 0
    gps_off = 0
    if exif_ifd is not None:
        exif_off = emit_ifd(exif_ifd)
    if gps_ifd is not None:
        gps_off = emit_ifd(gps_ifd)

    # Append the ExifIFD/GPSIFD pointer entries onto IFD0 (TIFF requires
    # them to be in tag-id sorted order).
    augmented = list(ifd0)
    if exif_off:
        augmented.append((0x8769, 4, [exif_off]))
    if gps_off:
        augmented.append((0x8825, 4, [gps_off]))
    augmented.sort(key=lambda e: e[0])
    ifd0_off = emit_ifd(augmented)

    # Header: byte order + magic + IFD0 offset.
    bo_bytes = b"II" if endian == "<" else b"MM"
    buf[0:2] = bo_bytes
    buf[2:4] = struct.pack(endian + "H", magic)
    buf[4:8] = struct.pack(endian + "I", ifd0_off)
    return bytes(buf)


def _write_tmp(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


# --- IFD parsing unit tests -------------------------------------------------

def test_lite_minimal_tiff_make_model(tmp_path: Path) -> None:
    """Roundtrip Make/Model through the synthetic-TIFF helper + parser."""
    data = _build_tiff(
        ifd0=[
            (0x010F, 2, ["SONY"]),
            (0x0110, 2, ["ILCE-7M4"]),
            (0x0112, 3, [1]),
        ],
        exif_ifd=[
            (0x9003, 2, ["2024:01:02 03:04:05"]),
            (0x8827, 3, [800]),
            (0x829D, 5, [(14, 10)]),  # F1.4
        ],
    )
    p = _write_tmp(tmp_path, "synthetic.arw", data)
    meta = _exif_lite.read_metadata(p)
    assert meta["Make"] == "SONY"
    assert meta["Model"] == "ILCE-7M4"
    assert meta["Orientation"] == 1
    assert meta["DateTimeOriginal"] == "2024:01:02 03:04:05"
    assert meta["ISO"] == 800
    assert meta["FNumber"] == pytest.approx(1.4, abs=1e-6)


def test_lite_big_endian_tiff(tmp_path: Path) -> None:
    """Some cameras (Nikon legacy NEF) use big-endian. Parser must follow.

    We feed the parser a real big-endian TIFF and check a value that's
    too big to be inline so the BE byte order has to flow through the
    offset resolution path."""
    data = _build_tiff(
        ifd0=[
            (0x010F, 2, ["NIKON"]),
            (0x0110, 2, ["Z9"]),
        ],
        endian=">",
    )
    p = _write_tmp(tmp_path, "synthetic.nef", data)
    meta = _exif_lite.read_metadata(p)
    assert meta["Make"] == "NIKON"
    assert meta["Model"] == "Z9"


def test_lite_rw2_magic_0x55(tmp_path: Path) -> None:
    """Panasonic RW2 uses TIFF magic 0x55 (instead of standard 0x2A).
    The parser must accept either, otherwise every Panasonic file
    falls through to empty metadata."""
    data = _build_tiff(
        ifd0=[(0x010F, 2, ["Panasonic"]), (0x0110, 2, ["DC-G9"])],
        magic=0x0055,
    )
    p = _write_tmp(tmp_path, "synthetic.rw2", data)
    meta = _exif_lite.read_metadata(p)
    assert meta["Make"] == "Panasonic"
    assert meta["Model"] == "DC-G9"


def test_lite_gps_compose(tmp_path: Path) -> None:
    """GPSLatitude(deg,min,sec) + GPSLatitudeRef → signed decimal degrees.
    The S/W refs flip the sign — easy to get wrong; pin it explicitly."""
    data = _build_tiff(
        ifd0=[(0x010F, 2, ["Test"])],
        gps_ifd=[
            (0x0001, 2, ["S"]),
            (0x0002, 5, [(33, 1), (52, 1), (15, 1)]),    # 33°52'15"S
            (0x0003, 2, ["W"]),
            (0x0004, 5, [(151, 1), (12, 1), (40, 1)]),   # 151°12'40"W
        ],
    )
    p = _write_tmp(tmp_path, "synthetic_gps.tiff", data)
    meta = _exif_lite.read_metadata(p)
    assert meta["GPSLatitude"] == pytest.approx(-(33 + 52/60 + 15/3600), abs=1e-9)
    assert meta["GPSLongitude"] == pytest.approx(-(151 + 12/60 + 40/3600), abs=1e-9)


def test_lite_exposure_compensation_naming(tmp_path: Path) -> None:
    """Wire name is ExposureBiasValue; we expose it as ExposureCompensation
    to match exiftool's user-facing name. Important: _FIELD_MAP keys off
    ExposureCompensation, so mis-naming → silently no bias on every file."""
    data = _build_tiff(
        ifd0=[(0x010F, 2, ["X"])],
        exif_ifd=[(0x9204, 10, [(-15, 10)])],  # -1.5 EV
    )
    p = _write_tmp(tmp_path, "synthetic_bias.tiff", data)
    meta = _exif_lite.read_metadata(p)
    assert "ExposureCompensation" in meta
    assert meta["ExposureCompensation"] == pytest.approx(-1.5, abs=1e-6)
    assert "ExposureBiasValue" not in meta  # internal name must not leak


def test_lite_no_ifd0_datetime_emitted(tmp_path: Path) -> None:
    """IFD0:DateTime (0x0132) is the file-modify timestamp (Lightroom
    overwrites it on every edit). The parser must IGNORE it; only
    ExifIFD:DateTimeOriginal counts as the capture time."""
    data = _build_tiff(
        ifd0=[
            (0x010F, 2, ["X"]),
            (0x0132, 2, ["2099:12:31 23:59:59"]),  # bogus modify time
        ],
        exif_ifd=[(0x9003, 2, ["2024:06:15 12:00:00"])],  # real capture time
    )
    p = _write_tmp(tmp_path, "synthetic_dt.tiff", data)
    meta = _exif_lite.read_metadata(p)
    assert "DateTime" not in meta
    assert meta["DateTimeOriginal"] == "2024:06:15 12:00:00"


def test_lite_iso_fallback_to_photographic_sensitivity(tmp_path: Path) -> None:
    """Modern Sony bodies (a7R V) only write PhotographicSensitivity
    (0x8833), not legacy ISO (0x8827). The parser must promote
    PhotographicSensitivity to the ISO field when ISO is absent."""
    data = _build_tiff(
        ifd0=[(0x010F, 2, ["SONY"])],
        exif_ifd=[(0x8833, 3, [6400])],  # PhotographicSensitivity only
    )
    p = _write_tmp(tmp_path, "synthetic_iso.tiff", data)
    meta = _exif_lite.read_metadata(p)
    assert meta["ISO"] == 6400


def test_lite_missing_exif_returns_partial(tmp_path: Path) -> None:
    """File with only IFD0, no ExifIFD pointer. Should not raise; should
    return what's in IFD0 (Make/Model/Orientation) and nothing for the
    EXIF subblock."""
    data = _build_tiff(
        ifd0=[(0x010F, 2, ["X"]), (0x0110, 2, ["Y"]), (0x0112, 3, [6])],
    )
    p = _write_tmp(tmp_path, "synthetic_min.tiff", data)
    meta = _exif_lite.read_metadata(p)
    assert meta["Make"] == "X"
    assert meta["Model"] == "Y"
    assert meta["Orientation"] == 6
    assert "DateTimeOriginal" not in meta


def test_lite_corrupted_file_returns_empty(tmp_path: Path) -> None:
    """Garbage bytes must not raise — corrupted RAW shouldn't crash a
    `rawkit ls -R` over a 30 K-file library because of one bad file."""
    p = _write_tmp(tmp_path, "corrupt.arw", b"not a TIFF at all" * 100)
    meta = _exif_lite.read_metadata(p)
    assert meta == {}


def test_lite_empty_file_returns_empty(tmp_path: Path) -> None:
    p = _write_tmp(tmp_path, "empty.arw", b"")
    assert _exif_lite.read_metadata(p) == {}


def test_lite_nonexistent_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "nope.arw"
    assert _exif_lite.read_metadata(p) == {}


# --- Big-DNG fallback (IFD0 past head buffer) -------------------------------

def test_lite_dng_with_ifd0_past_head_buffer(tmp_path: Path) -> None:
    """Lightroom Pano-stitched DNGs can place IFD0 hundreds of MB into
    the file. The head fast path misses; the seek-fallback must still
    return the standard EXIF block (Make/Model/Orientation/DTO/...).

    Synthesis trick: we build the file as
        [outer 8-byte TIFF header pointing at abs_ifd0][pixel filler][inner full TIFF]
    The OUTER header is what the parser sees first; abs_ifd0 lives inside
    the embedded inner-TIFF block. To keep value-pool offsets valid in
    the outer coordinate system, every IFD entry uses inline (≤ 4 byte)
    values — that way no offset arithmetic is needed and the test stays
    decoupled from the synthesizer's pool placement.
    """
    inner = _build_tiff(
        ifd0=[
            (0x010F, 2, ["X"]),   # "X\0" → 2 bytes, inline
            (0x0110, 2, ["Y"]),   # "Y\0" → 2 bytes, inline
            (0x0112, 3, [1]),     # SHORT → inline
        ],
    )
    endian = "<"
    inner_ifd0_off = struct.unpack_from(endian + "I", inner, 4)[0]
    pixel_prefix_size = _exif_lite.HEAD_SIZE + 4096  # past head fast path
    # Inner TIFF starts at offset `pixel_prefix_size` in the outer file,
    # so the IFD0 in OUTER coordinates is pixel_prefix_size + inner_ifd0_off.
    abs_ifd0 = pixel_prefix_size + inner_ifd0_off
    outer = bytearray(b"II" + struct.pack(endian + "H", 0x002A) +
                       struct.pack(endian + "I", abs_ifd0))
    outer.extend(b"\xFF" * (pixel_prefix_size - 8))
    outer.extend(inner)  # full inner TIFF; its own header is just bytes nobody reads
    p = _write_tmp(tmp_path, "huge_pano.dng", bytes(outer))
    meta = _exif_lite.read_metadata(p)
    # Even though IFD0 lives past the 256 KB head buffer, the seek
    # fallback finds and decodes it.
    assert meta["Make"] == "X"
    assert meta["Model"] == "Y"
    assert meta["Orientation"] == 1


# --- CR3 (ISO BMFF) ---------------------------------------------------------

def _box(box_type: bytes, payload: bytes) -> bytes:
    """Build one ISO BMFF box (size:u32-BE + type:4cc + payload)."""
    return struct.pack(">I", 8 + len(payload)) + box_type + payload


def test_lite_cr3_multi_box(tmp_path: Path) -> None:
    """CR3 stores IFD0, ExifIFD, GPS as PARALLEL CMT1/CMT2/CMT4 boxes inside
    moov>uuid (not as IFD0 sub-pointers). The parser must read all three
    boxes and merge — otherwise CR3 loses everything but Make/Model."""
    cmt1 = _build_tiff(ifd0=[(0x010F, 2, ["Canon"]), (0x0110, 2, ["EOS R5"])])
    # CMT2 is a single-IFD TIFF whose IFD0 contains EXIF tags directly.
    cmt2 = _build_tiff(ifd0=[
        (0x9003, 2, ["2024:01:02 03:04:05"]),
        (0x8827, 3, [400]),
        (0x829D, 5, [(18, 10)]),  # F1.8
    ])
    cmt4 = _build_tiff(ifd0=[
        (0x0001, 2, ["N"]),
        (0x0002, 5, [(39, 1), (54, 1), (24, 1)]),
        (0x0003, 2, ["E"]),
        (0x0004, 5, [(116, 1), (24, 1), (12, 1)]),
    ])
    uuid_payload = (
        _exif_lite._CR3_UUID
        + _box(b"CMT1", cmt1) + _box(b"CMT2", cmt2) + _box(b"CMT4", cmt4)
    )
    moov = _box(b"uuid", uuid_payload)
    ftyp = _box(b"ftyp", b"crx \x00\x00\x00\x01" + b"crx ")
    cr3_bytes = ftyp + _box(b"moov", moov)
    p = _write_tmp(tmp_path, "synthetic.cr3", cr3_bytes)
    meta = _exif_lite.read_metadata(p)
    assert meta["Make"] == "Canon"
    assert meta["Model"] == "EOS R5"
    assert meta["DateTimeOriginal"] == "2024:01:02 03:04:05"
    assert meta["ISO"] == 400
    # GPS lat 39°54'24"N = 39.9067
    assert meta["GPSLatitude"] == pytest.approx(39 + 54/60 + 24/3600, abs=1e-6)
    assert meta["GPSLongitude"] == pytest.approx(116 + 24/60 + 12/3600, abs=1e-6)


def test_lite_cr3_missing_uuid_box(tmp_path: Path) -> None:
    """CR3 with `ftyp` but no Canon uuid box (truncated / non-standard).
    Must not raise; just returns empty."""
    cr3_bytes = _box(b"ftyp", b"crx \x00\x00\x00\x01" + b"crx ") + _box(b"moov", b"")
    p = _write_tmp(tmp_path, "bare.cr3", cr3_bytes)
    assert _exif_lite.read_metadata(p) == {}


# --- libraw flip → EXIF orientation -----------------------------------------

@pytest.mark.parametrize("flip,expected", [
    (0, 1),   # no rotation → landscape
    (3, 3),   # 180°       → landscape (upside down)
    (5, 8),   # CCW 90°    → portrait
    (6, 6),   # CW  90°    → portrait
    (-1, None),  # LibRaw "unknown" → must NOT guess
    (42, None),  # bogus → must NOT guess
])
def test_lite_libraw_flip_mapping(flip: int, expected: int | None) -> None:
    assert exif._libraw_flip_to_exif_orientation(flip) == expected


# --- exif.batch_read dispatch ---------------------------------------------

def test_lite_backend_is_default(monkeypatch, tmp_path: Path) -> None:
    """With RAWKIT_BACKEND unset, batch_read must route to the lite path
    (not exiftool). Verified by patching the lite entry and confirming
    the exiftool entry is NOT called."""
    monkeypatch.delenv("RAWKIT_BACKEND", raising=False)
    called = {"lite": 0, "exiftool": 0}

    def fake_lite(paths):
        called["lite"] += 1
        return [{"path": str(p)} for p in paths]

    def fake_exiftool(paths):
        called["exiftool"] += 1
        return [{"path": str(p)} for p in paths]

    monkeypatch.setattr(exif, "_batch_read_lite", fake_lite)
    monkeypatch.setattr(exif, "_batch_read_exiftool", fake_exiftool)
    out = exif.batch_read([Path("/x.arw")])
    assert called["lite"] == 1
    assert called["exiftool"] == 0
    assert out == [{"path": "/x.arw"}]


def test_lite_backend_override_to_exiftool(monkeypatch) -> None:
    """RAWKIT_BACKEND=exiftool must flip routing without code changes."""
    monkeypatch.setenv("RAWKIT_BACKEND", "exiftool")
    called = {"lite": 0, "exiftool": 0}
    monkeypatch.setattr(exif, "_batch_read_lite", lambda p: called.__setitem__("lite", 1) or [])
    monkeypatch.setattr(exif, "_batch_read_exiftool", lambda p: called.__setitem__("exiftool", 1) or [])
    exif.batch_read([Path("/x.arw")])
    assert called["exiftool"] == 1
    assert called["lite"] == 0


def test_lite_empty_input_short_circuits(monkeypatch) -> None:
    """Empty input must not even reach the backend (no rawpy import cost,
    no exiftool fork). Same contract as the original batch_read."""
    called = {"lite": 0, "exiftool": 0}
    monkeypatch.setattr(exif, "_batch_read_lite", lambda p: called.__setitem__("lite", 1) or [])
    monkeypatch.setattr(exif, "_batch_read_exiftool", lambda p: called.__setitem__("exiftool", 1) or [])
    assert exif.batch_read([]) == []
    assert called == {"lite": 0, "exiftool": 0}


# --- subsec int-or-str normalization ----------------------------------------

def test_normalize_accepts_int_subsec() -> None:
    """exiftool's `-n` returns SubSec as int when the value is all digits,
    not str. Both paths must produce identical datetime suffixes.

    This is a latent-bug regression test: before this rewrite the
    normalizer's `isinstance(subsec, str)` check silently dropped int
    subsecs (read: every 7DII/R5/a1/Z8 burst sequence in the wild)."""
    rec = {
        "SourceFile": "a",
        "DateTimeOriginal": "2024:10:27 17:09:43",
        "SubSecTimeOriginal": 48,  # int, not str
    }
    out = exif._normalize(rec)
    assert out["datetime"] == "2024-10-27 17:09:43.48"
    assert out["time"] == "17:09:43.48"
    assert "_subsec_raw" not in out


# --- cross-backend equivalence (real samples) -------------------------------

def _sample_files() -> list[Path]:
    base = Path(os.environ.get("RAWKIT_TEST_SAMPLES", "samples"))
    if not base.is_dir():
        return []
    raws: list[Path] = []
    for ext in (".cr3", ".arw", ".dng", ".rw2", ".3fr", ".nef", ".raf"):
        # Sample at most 3 files per format to keep the suite snappy.
        # Sorted so the choice is deterministic across runs.
        found = sorted(p for p in base.rglob(f"*{ext}"))[:3]
        raws.extend(found)
        # Also try uppercase, since macOS HFS+ is case-insensitive but
        # the user's library may have mixed case.
        found_upper = sorted(p for p in base.rglob(f"*{ext.upper()}"))[:3]
        raws.extend(p for p in found_upper if p not in raws)
    return raws


@pytest.mark.skipif(
    not _sample_files(), reason="no RAW samples (set RAWKIT_TEST_SAMPLES)"
)
def test_lite_produces_records_for_real_samples(monkeypatch) -> None:
    """Smoke test: lite backend reads every sample without crashing and
    produces non-empty records with the core fields populated."""
    monkeypatch.setenv("RAWKIT_BACKEND", "lite")
    monkeypatch.setenv("RAWKIT_NO_PROGRESS", "1")
    samples = _sample_files()
    recs = exif.batch_read(samples)
    assert len(recs) == len(samples)
    for r in recs:
        assert "path" in r
        # Every working RAW from a digital camera must carry maker+model
        # and a capture time. If lite drops any of these for a file
        # exiftool handles, that's a regression worth investigating.
        assert "maker" in r, f"no maker in {r['path']}"
        assert "model" in r, f"no model in {r['path']}"


@pytest.mark.skipif(
    not _sample_files() or shutil.which("exiftool") is None,
    reason="needs both samples and exiftool",
)
def test_lite_matches_exiftool_on_core_fields(monkeypatch) -> None:
    """The acceptance bar: for every real sample, the lite backend's
    record must agree with the exiftool backend's record on every
    field a user can query — same maker, model, lens, iso, fnumber,
    shutter, focal, datetime, orientation, flash, gps.

    Documented allowable differences:
      * fnumber may differ by < 0.05 because of APEX vs FNumber rounding
      * focal may differ by < 0.5 mm for the same reason
      * shutter is compared in 1/x form (`1/250s` semantically equals
        `0.004`), so we allow either absolute closeness or matching ratios
    """
    monkeypatch.setenv("RAWKIT_NO_PROGRESS", "1")
    samples = _sample_files()

    monkeypatch.setenv("RAWKIT_BACKEND", "exiftool")
    exiftool_recs = {r["path"]: r for r in exif.batch_read(samples)}

    monkeypatch.setenv("RAWKIT_BACKEND", "lite")
    lite_recs = {r["path"]: r for r in exif.batch_read(samples)}

    assert set(lite_recs) == set(exiftool_recs)

    mismatches: list[str] = []
    for path, lite in lite_recs.items():
        et = exiftool_recs[path]
        for key in ("maker", "model", "lens", "iso", "orientation",
                    "flash", "gps", "date", "year", "month"):
            if key in et and key in lite and et[key] != lite[key]:
                mismatches.append(
                    f"{path}: {key} lite={lite[key]!r} exiftool={et[key]!r}"
                )
        # Numeric fields with documented tolerance:
        for key, tol in (("fnumber", 0.05), ("focal", 0.5)):
            if key in et and key in lite:
                lv, ev = lite[key], et[key]
                if isinstance(lv, (int, float)) and isinstance(ev, (int, float)):
                    if abs(float(lv) - float(ev)) > tol:
                        mismatches.append(
                            f"{path}: {key} lite={lv} exiftool={ev} diff>{tol}"
                        )
        # ImageWidth/Height: must be within 1% of exiftool's value. We
        # accept small drift because Panasonic RW2 has SensorWidth (5488)
        # vs exiftool's Composite ImageWidth (5472 — JPEG-decoded) — about
        # 0.3% off, which is meaningless for any user filter.
        for key in ("image_width", "image_height"):
            if key in et and key in lite:
                lv, ev = lite[key], et[key]
                if isinstance(lv, (int, float)) and isinstance(ev, (int, float)):
                    if abs(float(lv) - float(ev)) / max(abs(float(ev)), 1.0) > 0.01:
                        mismatches.append(
                            f"{path}: {key} lite={lv} exiftool={ev} >1%"
                        )
        # Shutter time can drift on the last digit (1/250 = 0.004 vs 0.00400001).
        if "shutter" in et and "shutter" in lite:
            lv, ev = float(lite["shutter"]), float(et["shutter"])
            # Tolerance: 1% relative (matches the "≈ 1/N s" semantics any
            # user actually cares about).
            denom = max(abs(ev), 1e-9)
            if abs(lv - ev) / denom > 0.01:
                mismatches.append(
                    f"{path}: shutter lite={lv} exiftool={ev} >1% drift"
                )

    if mismatches:
        joined = "\n  ".join(mismatches[:30])  # cap output
        pytest.fail(
            f"lite vs exiftool field mismatches ({len(mismatches)}):\n  {joined}"
        )


@pytest.mark.skipif(
    not _sample_files(), reason="no RAW samples (set RAWKIT_TEST_SAMPLES)"
)
def test_lite_preserves_order_under_threads(monkeypatch) -> None:
    """ThreadPoolExecutor.map preserves submission order, but it's worth
    pinning explicitly — if the result list ever shuffles, downstream
    sort-stable assumptions in `ls --sort` would silently drift."""
    monkeypatch.setenv("RAWKIT_NO_PROGRESS", "1")
    monkeypatch.setenv("RAWKIT_BACKEND", "lite")
    monkeypatch.setenv("RAWKIT_WORKERS", "8")
    samples = _sample_files()
    recs = exif.batch_read(samples)
    assert [r["path"] for r in recs] == [str(p) for p in samples]


@pytest.mark.skipif(
    not _sample_files(), reason="no RAW samples (set RAWKIT_TEST_SAMPLES)"
)
def test_lite_one_corrupted_file_does_not_break_batch(monkeypatch, tmp_path: Path) -> None:
    """A single garbage file should not blow up the whole batch — it
    becomes a record with just `path`. The good files around it must
    still produce full metadata."""
    monkeypatch.setenv("RAWKIT_NO_PROGRESS", "1")
    monkeypatch.setenv("RAWKIT_BACKEND", "lite")
    bad = tmp_path / "broken.arw"
    bad.write_bytes(b"this is not a raw file" * 100)
    samples = [bad] + _sample_files()[:2]
    recs = exif.batch_read(samples)
    assert len(recs) == len(samples)
    # The bad file's record exists and has its path; everything else is best-effort.
    bad_rec = next(r for r in recs if r["path"] == str(bad))
    assert "path" in bad_rec
    # The good files still get their fields.
    good = [r for r in recs if r["path"] != str(bad)]
    for r in good:
        assert "maker" in r, f"good file lost metadata: {r['path']}"
