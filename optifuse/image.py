#!/usr/bin/env python3
"""
Image optimization helpers.

For any raster image, produce:
- A WebP (lossy by default with adaptive quality, opt-in lossless)
- An interlaced/progressive fallback in a compatible format (progressive JPEG, Adam7 PNG, interlaced GIF)
- A small HTML <picture> snippet referencing both
"""

from __future__ import annotations

import argparse
from pathlib import Path
from PIL import Image
import shutil
import io

try:
    import piexif  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    piexif = None

try:
    import png  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    png = None


def _save_interlaced_png_with_pypng(im: Image.Image, target: Path) -> bool:
    if png is None:
        return False
    # Normalize to RGB/RGBA to simplify writer config
    has_alpha = "A" in im.getbands()
    conv = im.convert("RGBA" if has_alpha else "RGB")
    width, height = conv.size
    channels = 4 if has_alpha else 3
    # PyPNG expects rows as sequences of integers, not raw bytes
    # Provide rows as iterables of per-pixel values
    def gen_rows():
        it = iter(conv.getdata())
        for _y in range(height):
            row = []
            for _x in range(width):
                px = next(it)
                if has_alpha:
                    row.extend((px[0], px[1], px[2], px[3]))
                else:
                    row.extend((px[0], px[1], px[2]))
            yield row
    rows = gen_rows()
    writer = png.Writer(
        width,
        height,
        greyscale=False,
        alpha=has_alpha,
        interlace=1,  # Adam7
        bitdepth=8,
    )
    with open(target, "wb") as f:
        writer.write(f, rows)
    return True


def _html_snippet_path(dest_dir: Path, stem: str) -> Path:
    return dest_dir / f"{stem}.html"


def _write_picture_html(dest_dir: Path, src_stem: str, alt_text: str | None, fallback_ext: str) -> Path:
    html_path = _html_snippet_path(dest_dir, src_stem)
    alt = alt_text or src_stem
    html = (
        f"<picture>\n"
        f"  <source srcset=\"{src_stem}.webp\" type=\"image/webp\"/>\n"
        f"  <img src=\"{src_stem}{fallback_ext}\" alt=\"{alt}\" loading=\"lazy\" decoding=\"async\"/>\n"
        f"</picture>\n"
    )
    html_path.write_text(html, encoding="utf-8")
    return html_path


def optimize(
    source: Path,
    dest_dir: Path,
    *,
    lossless: bool = False,
    quality: int = 85,
    max_side: int | None = None,
    strip: bool = True,
    alt_text: str | None = None,
) -> tuple[Path, Path, Path]:
    """
    Convert `source` to:
      - dest_dir/source.webp   (WebP, lossless by default)
      - dest_dir/source.png    (interlaced PNG fallback)
      - dest_dir/source.html   (<picture> snippet referencing the above)

    Returns paths in the order: (webp_path, fallback_path, html_path).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    suffix = source.suffix.lower()
    # Decide fallback extension to guarantee interlaced/progressive scanning
    if suffix in {".jpg", ".jpeg"}:
        fallback_ext = suffix
    elif suffix == ".png":
        fallback_ext = ".png"
    elif suffix == ".gif":
        fallback_ext = ".gif"
    else:
        # For formats without interlace/progressive, use PNG interlaced fallback
        fallback_ext = ".png"
    fallback_path = dest_dir / f"{source.stem}{fallback_ext}"

    if suffix in {".jpg", ".jpeg"}:
        # Re-encode JPEG with same quantization (quality='keep') and strip metadata
        with Image.open(source) as im:
            if getattr(im, "n_frames", 1) > 1:
                im.seek(0)
            if max_side:
                im.thumbnail((max_side, max_side), Image.LANCZOS)
            buf = io.BytesIO()
            save_kwargs = {
                "format": "JPEG",
                "quality": "keep",
                "optimize": True,
                "progressive": True,
                "subsampling": "keep",
                "icc_profile": "" if strip else im.info.get("icc_profile"),
            }
            if strip:
                save_kwargs["exif"] = b""
            elif "exif" in im.info:
                save_kwargs["exif"] = im.info.get("exif")
            im.save(buf, **save_kwargs)
            fallback_path.write_bytes(buf.getvalue())

    elif fallback_ext == ".png":
        # PNG is lossless; recompress, strip metadata and enforce Adam7 interlacing
        with Image.open(source) as im:
            if getattr(im, "n_frames", 1) > 1:
                im.seek(0)
            if max_side:
                im.thumbnail((max_side, max_side), Image.LANCZOS)
            # Try Pillow first (may not set interlace bit per some builds)
            im.save(
                fallback_path,
                format="PNG",
                optimize=True,
                interlace=True,
                compress_level=9,
                icc_profile="" if strip else im.info.get("icc_profile"),
            )
            # Verify interlace bit; if not interlaced, rewrite with PyPNG
            try:
                with open(fallback_path, "rb") as f:
                    sig = f.read(8)
                    if sig == b"\x89PNG\r\n\x1a\n":
                        f.read(4)  # len
                        if f.read(4) == b"IHDR":
                            ihdr = f.read(13)
                            interlace_flag = ihdr[12]
                            if interlace_flag == 0:
                                raise RuntimeError("not interlaced")
            except Exception:
                # Rewrite with PyPNG if available
                with Image.open(source) as im2:
                    if getattr(im2, "n_frames", 1) > 1:
                        im2.seek(0)
                    if max_side:
                        im2.thumbnail((max_side, max_side), Image.LANCZOS)
                    if not _save_interlaced_png_with_pypng(im2, fallback_path):
                        # As a last resort, keep Pillow output (may be non-interlaced on some builds)
                        pass
    elif fallback_ext == ".gif":
        # Re-encode GIF with interlace; keep single frame to be consistent with static handling
        with Image.open(source) as im:
            if getattr(im, "n_frames", 1) > 1:
                im.seek(0)
            if max_side:
                im.thumbnail((max_side, max_side), Image.LANCZOS)
            im.save(
                fallback_path,
                format="GIF",
                optimize=True,
                interlace=True,
                save_all=False,
            )

    # Create WebP from the (possibly optimized) fallback
    with Image.open(fallback_path) as im:
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        webp_path = dest_dir / f"{source.stem}.webp"
        if lossless:
            save_kwargs = {
                "format": "WebP",
                "lossless": True,
                "method": 6,
            }
            if strip:
                save_kwargs["icc_profile"] = ""
                save_kwargs["exif"] = b""
            else:
                save_kwargs["icc_profile"] = im.info.get("icc_profile")
                if "exif" in im.info:
                    save_kwargs["exif"] = im.info.get("exif")
            im.save(webp_path, **save_kwargs)
        else:
            # Adaptive lossy quality: try descending qualities until <= 95% of fallback size
            fallback_size = fallback_path.stat().st_size
            candidates = []
            tried: list[int] = []
            for q in [quality, 85, 80, 75, 70, 65, 60, 55]:
                if isinstance(q, int) and q not in tried:
                    tried.append(q)
                    buf = io.BytesIO()
                    save_kwargs = {
                        "format": "WebP",
                        "lossless": False,
                        "quality": q,
                        "method": 6,
                    }
                    if strip:
                        save_kwargs["icc_profile"] = ""
                        save_kwargs["exif"] = b""
                    else:
                        save_kwargs["icc_profile"] = im.info.get("icc_profile")
                        if "exif" in im.info:
                            save_kwargs["exif"] = im.info.get("exif")
                    im.save(buf, **save_kwargs)
                    data = buf.getvalue()
                    size = len(data)
                    candidates.append((size, data, q))
                    if size <= fallback_size * 0.95:
                        break
            # Choose the smallest candidate
            best = min(candidates, key=lambda t: t[0]) if candidates else None
            if best is not None:
                webp_path.write_bytes(best[1])
            else:
                # Fallback: write at provided quality
                im.save(webp_path, format="WebP", lossless=False, quality=quality, method=6)

    html_path = _write_picture_html(dest_dir, source.stem, alt_text, fallback_path.suffix)
    return webp_path, fallback_path, html_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert any image to WebP + interlaced fallback and emit <picture> snippet."
    )
    parser.add_argument("src", type=Path, help="Source image file")
    parser.add_argument("-d", "--dest", type=Path, help="Output directory (default: same as source)")
    parser.add_argument("--lossless", action="store_true", help="Lossless WebP instead of lossy")
    parser.add_argument("-q", "--quality", type=int, default=85, help="WebP quality 1-100 (default 85)")
    parser.add_argument("--max", type=int, metavar="PX", help="Resize so longest side ≤ PX pixels")
    parser.add_argument("--keep-meta", action="store_true", help="Keep ICC profile & EXIF")
    parser.add_argument("--alt", type=str, help="Alt text for generated <picture> snippet")
    args = parser.parse_args()

    if not args.src.exists():
        parser.error(f"{args.src} not found")

    dest = args.dest or args.src.parent
    webp, png, html = optimize(
        args.src,
        dest,
        lossless=args.lossless,
        quality=args.quality,
        max_side=args.max,
        strip=not args.keep_meta,
        alt_text=args.alt,
    )
    print("Created:")
    print(" •", webp)
    print(" •", png)
    print(" •", html)


if __name__ == "__main__":
    main()