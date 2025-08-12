OptiFuse
========

An all-in-one frontend asset optimizer. Routes SVGs, raster images, and fonts to type-specific optimizers with compatibility-first defaults.

- Strips metadata by default for all asset types
- Maximizes compression while preserving fidelity
- Generates compatible fallbacks (progressive/interlaced images, WOFF/WOFF2 fonts)

Installation
------------

Requires Python 3.10+

```bash
uv sync
```

Or via pip within a virtual environment:

```bash
pip install -e .
```

CLI
---

```bash
optifuse --help
```

You can also run without installing the console script:

```bash
uv run --no-project -q python -m optifuse.main --help
```

Usage
-----

```bash
optifuse [options] INPUTS...
```

- Inputs: one or more files (SVG, PNG, JPG/JPEG, GIF, BMP, TIF/TIFF, WEBP, AVIF, TTF, OTF)
- `-d, --out DIR`: output directory (default: alongside each input)
- `--keep-metadata`: retain metadata (off by default)

Image options
-------------
- `--img-lossless`: write WebP losslessly (default: off)
- `--img-quality N`: WebP quality when not lossless (1-100; default 85)
- `--img-max PX`: downscale to ensure the longest side is ≤ PX
- `--img-alt TEXT`: alt text for the generated `<picture>` snippet

Behavior:
- Produces two outputs per image: a WebP and an interlaced/progressive fallback with the most compatible format for the input:
  - JPEG fallback: progressive JPEG with metadata stripped
  - PNG fallback: Adam7 interlaced PNG (verified; rewritten with PyPNG if needed)
  - GIF fallback: interlaced GIF (single-frame)
- WebP:
  - Lossy by default with adaptive quality search to ensure it’s smaller than the fallback (target ≤ 95% of fallback size). Set `--img-lossless` to force lossless WebP.
- Always removes metadata by default (EXIF, ICC). Set `--keep-metadata` to opt out.
- Emits a small HTML `<picture>` snippet per image (same basename, `.html`).

SVG options
-----------
- `--svg-pretty`: pretty-print SVG output (default: compact)

Behavior:
- Optimizes with Scour and post-filters: removes comments, class attributes, `<title>`, `<desc>`, `<metadata>`, XML prolog; aggressively compacts unless `--svg-pretty` is set.

Font options
------------
- `--font-family NAME`: CSS font-family for generated `@font-face`
- `--font-weight VALUE`: CSS font-weight (default: normal)
- `--font-style VALUE`: CSS font-style (default: normal)
- `--font-split {auto,none,by-256}`:
  - `auto`:
    - Visible Latin ranges and common Han characters are chunked into contiguous subsets with size limits (≤ 300 code points, try to merge to ≥ 128 when possible)
    - Remaining visible code points chunked by 256
    - Invisible/PUA/unassigned code points are chunked separately by 256
  - `none`: single full-range subset
  - `by-256`: visible code points chunked by 256; invisible/PUA/unassigned chunked separately by 256
- `--font-keep-hinting`: retain hinting (default drops it for smaller files)
- `--font-jobs N`: parallel workers for subsetting/encoding (default: CPU count)

Behavior:
- Generates WOFF2 and WOFF for each subset and a CSS file named after the font family with `unicode-range` rules referencing each subset.
- Parallelized subsetting/encoding for speed.
- Default limits: max chunk size 300, preferred minimum 128 for merging.

Examples
--------

- Optimize a mix of assets to `out/`:
```bash
optifuse a.svg b.jpg font.ttf -d out/
```

- Generate smaller lossy WebP while keeping a progressive JPEG fallback:
```bash
optifuse photo.jpg --img-quality 82
```

- Downscale large images and keep metadata:
```bash
optifuse hero.png --img-max 1920 --keep-metadata
```

- Fonts with automatic ranges and 8 parallel workers:
```bash
optifuse MyFont.ttf --font-family "My Font" --font-weight 700 --font-split auto --font-jobs 8
```

Notes
-----
- WebP may be larger than the fallback on very high-resolution images at high quality; the tool adapts quality to stay under the fallback where possible.
- PNG Adam7 is enforced; if Pillow doesn’t set the interlace bit, PyPNG is used as a fallback.
- Invisible/PUA/unassigned code points are never mixed with visible ranges, to avoid unnecessary font downloads for low-visibility characters.

