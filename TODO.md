# TODO

Personal notes on what's deferred and what I deliberately won't do. Not a public roadmap.

## Deferred (might do, no plan)

- **`info --by FOO -l`** — list file paths under each bucket. Solves "I see bucket ≤100 ISO has 4 files, which 4?" without re-typing `ls -w`. ~30 lines, low risk.
- **`info --by A,B`** — multi-dim nested breakdown (mirrors `organize --by A,B`'s directory chain). Currently exits 2.
- **35mm-equivalent focal length** — `focal` is raw lens value, no crop-factor normalization. Would need maker→crop table or read `FocalLengthIn35mmFormat` EXIF tag. Adds a semantic fork ("does `--where focal>=70` mean raw or equivalent?") so not done lightly.
- **`verify`** — file integrity check (magic number, exiftool reads cleanly, bytes read without error). Useful for card transfer / bit rot detection.
- **`duplicates`** — find duplicate RAWs by content hash or datetime+model. Useful when merging cards or cleaning old drives.
- **List all embedded JPEGs in `info`** — currently shows only the one libraw picks. Listing all needs exiftool enumeration. Not done because `extract` only returns the one libraw picks; info showing more would create an asymmetry that immediately demands an extract picker.

## Permanently rejected (don't re-debate)

- **Edit / develop / colour adjustments** — that's GUI territory (LrC, Capture One, darktable). rawkit's `render` is libraw defaults only.
- **AI features** — not here.
- **Web UI / GUI** — CLI-first stays.
- **LrC XMP read/write** — explicitly not interop with Adobe.
- **File watchers / daemons** — violates stateless.
- **Catalog / database / index** — violates stateless.
- **Standalone `stats` command** — folded into `info --by`.
- **`--sort` and `--by` merged** — they mean different things (ORDER BY vs GROUP BY).
- **Remembering "last `--where`" / cross-command state** — shell history handles this.
- **stdin path reading / cross-command pipes** — every command has `--where`; pipes add no expressiveness.
- **`--prune` default-on** — rmdir is irreversible; must be opt-in.
- **`tag` / `rate` / `keyword` write commands** — would mean rawkit-managed sidecars next to RAWs, crossing into "managing my photo library" territory that LrC already does well enough for me.

## Dogfood findings (apply as hit)

MakerNotes-pollution pattern: some makers write garbage into MakerNotes that exiftool's `-n` mode surfaces over the real EXIF value. Examples seen:

- Pentax / Ricoh: ISO 13 (logarithmic index) instead of 500
- Leica M11 Monochrom: FNumber 1.0 (placeholder) instead of real aperture

Fix recipe: lock the field to `EXIF:` group prefix, add APEX fallback if applicable. Apply to any new field that misbehaves.
