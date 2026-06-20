# rawkit

A personal RAW photo CLI. Browse, describe, extract embedded JPEGs, demosaic-render, and organize files by EXIF.

## Status

This is my (@qing4132) tool for my own RAW workflow. Public so anyone interested can use it, but the roadmap is driven by what I personally need. Flags and commands may change without notice. Issues and PRs are welcome but replies are sporadic — fork if you need stability.

## Commands

- `ls` — table of RAW files with key EXIF (auto-emits paths when piped)
- `info` — full per-file EXIF detail; accepts files, dirs, or piped paths
- `summary` — aggregate stats over a set (count, ranges, top values; `--by FIELD` for bucket breakdown)
- `extract` — pull the embedded SOOC JPEG out of each RAW (fast, no decode)
- `render` — libraw demosaic → JPEG/TIFF/PNG (slower, full sensor resolution)
- `organize` — move/copy files into a folder tree keyed by EXIF dimensions
- `reveal` — open Finder window(s) with selected RAWs (macOS only)

They compose through pipes: `ls` selects, the rest consume. e.g. `rawkit ls -R -w 'rating>=4' | rawkit summary --by lens`.

See [USAGE.md](USAGE.md) for details.

## Install

Requires Python 3.14+ and `exiftool`:

```bash
brew install exiftool                       # macOS
apt install libimage-exiftool-perl          # Debian/Ubuntu
```

Then install rawkit itself with [uv](https://github.com/astral-sh/uv):

```bash
uv tool install --from git+https://github.com/qing4132/rawkit.git rawkit
```

Upgrade:

```bash
uv tool upgrade rawkit
```

## Design

Read-only on RAW files. Local-only — no cloud, no catalog, no index. Each command answers one question and fits on one screen.

Shared infrastructure:
- `--where` DSL (lark-based, no eval()) — used by every command that takes a set of files
- `--by` vocabulary — shared between `summary` and `organize`
- `--json` on `ls` / `info` / `summary` for pipe-friendly output
- Path ingestion: every set-taking command accepts files, dirs (with `-R`), `-`, or pipe

Not here, on purpose: edit/develop, AI, GUI, Adobe XMP interop, sidecar writes, file watchers, catalogs. See [TODO.md](TODO.md) for the deferred / rejected list.
