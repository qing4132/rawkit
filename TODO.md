# TODO

Personal notes on what's deferred and what I deliberately won't do.
Not a public roadmap.

> Status legend: ⭐ = "do next when time appears", regular = parked.
> Each item has a one-line "why this is worth it" or it shouldn't be here.

---

## Deferred — high signal, do when time appears

These came out of CHANGELOG.md §8 (PERF-FIX "free wins" section) review on
2026-06-23. They survive the filter "would this actually change my own
workflow or measurably improve correctness".

- ⭐ **`extract` via PreviewImage IFD tag instead of `rawpy.extract_thumb`**
  IFD0:0x0111 (StripOffsets) + 0x0117 (StripByteCounts) point straight at
  the embedded JPEG bytes — we can `read` that range and write it out,
  skipping LibRaw entirely. ~60× faster (300 ms/file → ~5 ms), and removes
  rawpy from the `extract` hot path. CR3 still needs BMFF `PRVW` box walk.
  Origin: CHANGELOG §8.3.

- ⭐ **Lift `_xmp_fill_missing` to all format branches in `read_metadata`**
  Currently only the standard-TIFF branch calls it. RAF / CR3 with empty
  EXIF + populated XMP would silently miss Make/Model. ~5 lines, zero cost
  (head is already in memory). Origin: CHANGELOG §6 known limitation.

- ⭐ **Expose existing fields in the `--where` DSL**: `lens_make`, `bias`
  (ExposureCompensation), `subsec` (sub-second of capture time).
  All three already land in records — just missing from the DSL parser's
  identifier whitelist. Origin: CHANGELOG §8.1.

## Deferred — medium signal, no plan

- **`summary --by FOO -l`** — list file paths under each bucket. Solves
  "I see bucket ≤100 ISO has 4 files, which 4?" without re-typing `ls -w`.
  ~30 lines, low risk.

- **`summary --by A,B`** — multi-dim nested breakdown (mirrors
  `organize --by A,B`'s directory chain). Currently exits 2.

- **35mm-equivalent focal length** — `focal` is the raw lens value, no
  crop-factor normalization. Would need maker→crop table or read
  `FocalLengthIn35mmFormat` EXIF tag. Adds a semantic fork
  ("does `--where focal>=70` mean raw or equivalent?") so not done lightly.

- **`verify`** — file integrity check (magic number, exiftool reads
  cleanly, bytes read without error). Useful for card transfer / bit rot
  detection. Cheap given lite's per-file cost.

- **`duplicates`** — find duplicate RAWs by `date+subsec+model` (no
  content hash). Useful when merging cards or cleaning old drives.

- **Auto-detect prime vs zoom lens** via LibRaw's
  `lens.min_focal == max_focal`. Lands in fallback path with ~5 lines.
  Adds `--where lens_kind=='prime'` semantics. Origin: CHANGELOG §8.2.

- **List all embedded JPEGs in `info`** — currently shows only the one
  LibRaw picks. Listing all needs exiftool enumeration. Not done because
  `extract` only returns the one LibRaw picks; `info` showing more would
  create an asymmetry that immediately demands an `extract` picker.

---

## Permanently rejected (don't re-debate)

- **Edit / develop / colour adjustments** — that's GUI territory (LrC,
  Capture One, darktable). rawkit doesn't demosaic at all anymore; if the
  embedded SOOC JPEG isn't enough, develop in your usual RAW processor.
- **AI features** — not here.
- **Web UI / GUI** — CLI-first stays.
- **TUI** (`rawkit tui`) — same reason as above; CHANGELOG §8.4 floated it,
  rejected here.
- **`rawkit watch`** — file watcher / daemon. Violates stateless.
- **`rawkit health`** — overlaps with `verify`; if I add anything it's
  `verify`, not a second name for similar checks.
- **LrC XMP read/write** — explicitly not interop with Adobe.
- **Catalog / database / index** — violates stateless. The SQLite cache
  is *not* a catalog; it's a transparent stat-keyed memo of lite reads.
- **Standalone `stats` command** — the aggregation verb is `summary`,
  not `stats`.
- **`--sort` and `--by` merged** — they mean different things
  (ORDER BY vs GROUP BY).
- **Remembering "last `--where`" / cross-command state** — shell history
  handles this.
- **`--prune` default-on** — rmdir is irreversible; must be opt-in.
- **`tag` / `rate` / `keyword` write commands** — would mean rawkit-managed
  sidecars next to RAWs, crossing into "managing my photo library"
  territory that LrC already does well enough for me.

### Cross-backend parity items (rejected after review)

These were in CHANGELOG §8.2's "free wins from LibRaw" table but don't
clear the "would I use this" bar:

- color matrix / tone curve / black level / white balance — GUI tool data,
  not rawkit data.
- `color_desc` / `num_colors` for Foveon detection — X3F parser was
  removed (2026-06-23), no longer relevant.
- `raw_width` vs `width` — sensor vs demosaic difference; not in workflow.
- Canon CMT3 / Sony MakerNote decode — Canon-only / Sony-only specials,
  violates "format neutral" parser design.

---

## Dogfood findings (apply as hit)

MakerNotes-pollution pattern: some makers write garbage into MakerNotes
that exiftool's `-n` mode surfaces over the real EXIF value. Examples
seen so far:

- Pentax / Ricoh: ISO 13 (logarithmic index) instead of 500
- Leica M11 Monochrom: FNumber 1.0 (placeholder) instead of real aperture

Fix recipe: lock the field to `EXIF:` group prefix, add APEX fallback if
applicable. Apply to any new field that misbehaves.
