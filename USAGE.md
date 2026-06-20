# Usage

Five commands: `ls` / `info` / `extract` / `render` / `organize`.

## Install

```bash
brew install exiftool                       # macOS
apt install libimage-exiftool-perl          # Debian/Ubuntu

uv tool install --from git+https://github.com/qing4132/rawkit.git rawkit
```

Requires Python 3.14+.

---

## `ls` — list RAWs as a table

```bash
rawkit ls [PATHS...] [-w EXPR] [-s KEY,...] [-r] [-R] [--json]
```

Default columns: `file datetime model lens focal aperture shutter bias iso`. Default sort: `datetime` ascending.

Output shape adapts to stdout: a human-readable table when it's a terminal, one absolute path per line when it's piped or redirected. `--json` forces JSONL for structured downstream tools.

| flag | meaning |
|------|---------|
| `PATHS` | files or dirs. Default = `.`, top-level only unless `-R` |
| `-w / --where EXPR` | filter by EXIF (see DSL below) |
| `-s / --sort KEY[,KEY,...]` | sort keys: file / datetime / date / time / model / lens / focal / aperture / shutter / bias / iso |
| `-r / --reverse` | reverse sort |
| `-R / --recursive` | recurse into subdirs |
| `--json` | force JSONL (one object per file) on stdout |

```bash
rawkit ls ~/Pictures/2024-trip                       # table
rawkit ls *.CR3 -s iso -r                            # by ISO descending
rawkit ls . -w 'iso>3200 and lens~"50"'              # filter
rawkit ls . --json | jq '.path'                      # JSONL for jq
rawkit ls -R -w 'rating>=4' | rawkit reveal          # piped → paths → Finder
```

---

## `info` — full per-file EXIF detail

```bash
rawkit info [PATHS...] [-w EXPR] [-R] [--json]
rawkit info -                  # read paths from stdin
rawkit ls -w '...' | rawkit info
```

One detail block per RAW, blank line between. Accepts files, directories (top-level unless `-R`), `-`, or piped paths.

| flag | meaning |
|------|---------|
| `PATHS` | files / dirs / `-`. Default = `.` |
| `-w / --where EXPR` | filter (same DSL as `ls`) |
| `-R / --recursive` | walk subdirs |
| `--json` | JSONL (one object per RAW) |

Detail block:

```
Path          /path/to/IMG_0001.CR3
Size          51.8 MiB (54348886 B)
DateTime      2022-05-13 16:38:09.01
Maker         Canon
Camera        EOS R5
Lens          RF50mm F1.8 STM
ISO           400
Aperture      f/1.8
Shutter       1/250
Focal length  50mm
Bias          0 EV
Rating        0
Orientation   landscape
Flash         False
Image         8192x5464
GPS           31.200000, 121.500000
Embedded      JPEG 8192x5464 (5.37 MiB)
```

```bash
rawkit info shot.CR3                            # one file
rawkit info ~/Pictures/2024-trip -R             # every RAW recursively
rawkit ls -R -w 'rating>=4' | rawkit info       # only the keepers
```

For aggregate stats (count, ranges, distributions), see `summary` below.

---

## `summary` — aggregate stats over a set

```bash
rawkit summary [PATHS...] [-w EXPR] [-R] [--by DIM] [--json]
rawkit summary -               # read paths from stdin
rawkit ls -w '...' | rawkit summary [--by DIM]
```

Default = a scalar KV block (count, total size, date range, top maker/camera/lens, exposure ranges, GPS coverage). `--by DIM` switches to a per-bucket breakdown. Path ingestion mirrors `info` / `ls`.

| flag | meaning |
|------|---------|
| `PATHS` | files / dirs / `-`. Default = `.` |
| `-w / --where EXPR` | filter (same DSL as `ls`) |
| `-R / --recursive` | walk subdirs |
| `--by DIM` | partition by one dim (see list below); suppresses the default KV view |
| `--json` | one JSON object with the full aggregation |

### Default KV block

```
Path          ~/Pictures/2024-trip
File          29 RAWs (1.53 GiB)
Date range    2024-06-01 → 2024-06-15  (15 days)
Hour          06–09, 14–19
Maker         3 (Canon, SONY, FUJIFILM)
Camera        4 (EOS R5, X-E5, ILCE-7RM4A, +1 others)
Lens          8
ISO           100 – 6400
Aperture      f/1.4 – f/16
Shutter       1/8000 – 30s
Focal length  14mm – 200mm
Bias          -2 EV – +1 EV
Rating        29 (unrated)
Orientation   25 (landscape), 4 (portrait)
Flash         1 (on), 28 (off)
GPS           3 (yes), 26 (no)
```

Maker / Camera / Lens rows auto-shrink (drop name examples, keep count + "+others") to fit terminal width. Non-TTY output keeps the full text.

### `--by`

```bash
rawkit summary samples/ --by camera
rawkit summary samples/ --by month
rawkit summary samples/ --by aperture -w 'iso>=3200'

# the killer pipe: aggregate over any curated subset, not just one folder
rawkit ls -R -w 'rating>=4' | rawkit summary --by lens
```

Output: title + indented `key  count  pct%` rows. No bar chart, no horizontal rule.

Available dims (shared with `ls --where` field names and `organize --by`):

| group | dims |
|-------|------|
| time | `year` / `month` / `day` / `hour` |
| gear | `camera` (= `model`) / `lens` / `maker` |
| exposure | `iso` / `aperture` (= `fnumber`) / `focal` / `shutter` / `bias` / `rating` |
| frame | `orientation` |

Multi-dim `--by A,B` is not implemented (exits 2).

---

## `extract` — pull embedded SOOC JPEG

```bash
rawkit extract [PATHS...] -o DIR [-R] [-f] [-w EXPR]
                          [--long N | --short N | --mp N] [-q N]
```

Without resize flags: hands back the camera's embedded JPEG bytes verbatim (~30ms per file). With resize: decodes + LANCZOS + re-encodes (EXIF Orientation baked into pixels).

| flag | meaning |
|------|---------|
| `-o / --output DIR` | output directory (default `./jpegs`) |
| `--long N` | downscale long edge ≤ N px |
| `--short N` | downscale short edge ≤ N px |
| `--mp N` | downscale to ≤ N megapixels total |
| `-q / --quality N` | JPEG quality after resize (default 90) |
| `-f / --overwrite` | overwrite existing output |
| `-w / --where EXPR` | filter inputs |
| `-R / --recursive` | recurse |

Output path rules:
- dir input + `-R`: output **mirrors** source subdir structure under `-o`
- file input: just `-o/<basename>.jpg`
- intra-run collisions (incl. case-insensitive on APFS/Windows): **fail fast**, no file touched

```bash
rawkit extract ~/Pictures/2024-trip -o /tmp/peek           # full speed
rawkit extract . -o /tmp/peek --long 2000                  # 2000 px long edge
rawkit extract . -o /tmp/keepers -w 'rating>=4'            # rated only
```

---

## `render` — libraw demosaic to JPEG/TIFF/PNG

```bash
rawkit render [PATHS...] -o DIR [-R] [-f] [-w EXPR]
                         [--format jpeg|tiff|png] [-q N]
                         [--long N | --short N | --mp N]
```

Slower than extract (~0.5–2s per file, real demosaic work). Colour drifts from SOOC — libraw uses neutral sRGB defaults, not camera Picture Styles. Use for files whose embedded JPEG is too small (Sony A7R IV embeds only 1616×1080) or when you want full-sensor output regardless.

| flag | meaning |
|------|---------|
| `-o / --output DIR` | output dir (default `./renders`) |
| `--format FMT` | jpeg / tiff / png (default jpeg) |
| `-q / --quality N` | JPEG quality (default 90; ignored for tiff/png) |
| `--long / --short / --mp N` | downscale (mutually exclusive) |
| `-f / --overwrite` | overwrite |
| `-w / --where EXPR` | filter |
| `-R / --recursive` | recurse + mirror subtree under output |

```bash
rawkit render *.ARW -o out/                                # default jpeg q90
rawkit render . -o web/ --long 2400 -q 85                 # web sizing
rawkit render . -o tiff/ --format tiff -w 'rating==5'      # archival TIFF
```

Same output-path rules as `extract` (subtree mirror, intra-run collision fail-fast).

---

## `organize` — move RAWs into a folder tree by EXIF

```bash
rawkit organize [PATHS...] [-o DIR] [--by DIM[,DIM,...]] [-R] [-w EXPR]
                           [--copy] [--prune] [-n / --dry-run] [-f]
```

| flag | meaning |
|------|---------|
| `PATHS` | sources (default `.`) |
| `-o / --output DIR` | destination. **If omitted, defaults to first input dir** (in-place organize) |
| `--by DIM[,DIM,...]` | nest by these dims. Omit → flat dump into DEST (good with `--where` to cherry-pick) |
| `-R / --recursive` | scan sources recursively |
| `-w / --where EXPR` | filter |
| `--copy` | copy instead of move (default = move) |
| `--prune` | rmdir empty source subdirs after moving. Skips hidden dirs (`.git/` etc.); treats `.DS_Store`-only dirs as empty |
| `-n / --dry-run` | print plan, don't touch fs |
| `-f / --overwrite` | overwrite existing files at destination |

Behaviour:
- default action is **MOVE** (not copy)
- same-stem `.xmp` / `.jpg` sidecars move along with the RAW (so LrC ratings / develop edits aren't orphaned)
- files missing the relevant EXIF value land in `_unknown/`
- `/` in bucket names (`f/2.8`, `1/250`) is replaced with `_` (`f_2.8`, `1_250`)
- intra-run target collisions (incl. case-insensitive): fail fast before any move

`--by` vocabulary is identical to `info --by`.

```bash
# typical layouts
rawkit organize ~/dump -o ~/sorted --by month
rawkit organize ~/dump -o ~/sorted --by year,month -R
rawkit organize ~/dump -o ~/sorted --by camera,year -R

# in-place (no -o → uses first input dir)
rawkit organize ~/Pictures --by month

# cherry-pick (no --by, use --where)
rawkit organize ~/dump -o ~/keepers -R -w 'rating>=4'

# always preview a new layout first
rawkit organize ~/Pictures -o ~/sorted --by month -n
```

---

## `reveal` — open Finder window(s) with files selected (macOS)

```bash
rawkit reveal PATH...
rawkit reveal -                    # read paths from stdin
rawkit ls -w '...' | rawkit reveal # auto-detect pipe; ls emits paths when not a TTY
```

Pure action command — no `--where`, no `--sort`, no filter logic. It takes paths and reveals them. Filtering / sorting / picking is `ls`'s job (or the shell's, via `head` / `sed`).

Paths sharing a parent directory are grouped into one Finder window with all of them selected; different parents open separate windows. Missing files are reported to stderr but don't abort the rest.

macOS only — uses `osascript` + Finder's `reveal`. Non-macOS exits 2 with a friendly message.

```bash
# show all 4★+ shots in Finder, grouped by their actual folder
rawkit ls -R -w 'rating>=4' | rawkit reveal

# show top-5 highest-ISO files
rawkit ls -R -w 'iso>=3200' -s iso -r | head -5 | rawkit reveal
```

---

## `--where` DSL

Shared across `ls`, `info`, `extract`, `render`, `organize`. lark-based parser, no `eval()`.

### Fields

| type | fields |
|------|--------|
| number | `iso` · `fnumber` (= `aperture`) · `shutter` (seconds) · `focal` (mm) · `bias` (EV) · `rating` (0–5) · `gps_lat` · `gps_lon` |
| int bucket | `hour` (0–23) · `year` · `month` (1–12) · `day` (1–31) |
| string | `lens` · `model` · `maker` · `orientation` (`portrait` / `landscape`) |
| time | `datetime` · `date` (YYYY-MM-DD) · `time` (HH:MM[:SS[.NNN]]) |
| bool | `gps` (has GPS) · `flash` (fired) |

### Bucket field semantics

`hour` / `year` / `month` / `day` are integer bucket IDs. Comparison is bucket-ID comparison, not timestamp comparison:

- `hour > 6` ≡ `hour >= 7` (means "in hour bucket 7 or later"; 6:30 is in bucket 6, not after)
- For a real timestamp cutoff use `time > "06:00:00"`
- `--where month==11` selects "any year's November" — pairs with `--by month` for "my November density across years"

### Operators

- comparison: `>` `<` `>=` `<=` `==` `!=`
- substring (case-insensitive): `lens~"50mm"`
- boolean: `and` `or` `not`, parens

### `aperture` reverse semantics

`aperture` is `fnumber` with reversed comparison (matches photographer intuition: f/1.4 is "bigger" than f/8). In `--where`:

```
aperture >= 2.8       ≡       fnumber <= 2.8
```

In `--sort` and `--by`, both behave identically (small fnumber first: f/1 → f/22).

### Examples

```bash
ls -w 'iso>3200 and lens~"50"'
ls -w '(focal>=70 and focal<=200) or lens~"70-200"'
ls -w 'date>="2024-06-01" and not model~"iPhone"'
ls -w 'orientation=="portrait" and rating>=4'
ls -w 'aperture>=1.4'                          # f/1.4 or wider
ls -w 'hour>=18 and hour<=22'                  # evening
ls -w 'month==11 and year>=2023'               # Novembers 2023+
```

---

## Conventions

- exit codes: **0** = success / **1** = partial failure or fail-fast refusal / **2** = usage error
- stdout = data only; progress + errors go to stderr (pipe-friendly)
- `--json` is JSONL (newline-delimited objects, one per file)
- file-not-found errors are human-readable, no Python traceback

## Known quirks

- ISO and aperture are read from the `EXIF:` group with APEX fallback to handle non-standard makers (Leica M11 Monochrom, Pentax/Ricoh ISO logarithm, etc.). Other fields may still surprise.
- `focal` is the actual lens focal length, **not** crop-factor normalized. APS-C / m4/3 users see raw values, not 35mm-equivalent.
- exiftool 12+ recommended.
