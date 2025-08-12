from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

from . import image as image_mod
from . import svg as svg_mod
from . import font as font_mod


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
    ".avif",
}

SVG_EXTENSIONS = {".svg"}

FONT_EXTENSIONS = {
    ".ttf",
    ".otf",
}


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _is_svg(path: Path) -> bool:
    return path.suffix.lower() in SVG_EXTENSIONS


def _is_font(path: Path) -> bool:
    return path.suffix.lower() in FONT_EXTENSIONS


def _group_inputs(paths: Iterable[Path]) -> tuple[list[Path], list[Path], list[Path]]:
    images: list[Path] = []
    svgs: list[Path] = []
    fonts: list[Path] = []
    for p in paths:
        if _is_svg(p):
            svgs.append(p)
        elif _is_image(p):
            images.append(p)
        elif _is_font(p):
            fonts.append(p)
        else:
            raise SystemExit(f"Unsupported file type: {p}")
    return images, svgs, fonts


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="optifuse",
        description=(
            "Optimize frontend assets. Automatically routes SVG, images, and fonts "
            "to type-specific optimizers with compatibility-first defaults (metadata "
            "stripped, progressive/interlaced fallbacks, unicode-range CSS for fonts)."
        ),
    )
    parser.add_argument(
        "inputs",
        type=Path,
        nargs="+",
        help="One or more files to optimize (SVG, images, fonts)",
    )
    parser.add_argument(
        "-d",
        "--out",
        type=Path,
        help="Output directory (default: alongside each input)",
    )

    # Shared policy: never keep metadata
    # (We keep this flag for future extensibility but default to the required behavior)
    parser.add_argument(
        "--keep-metadata",
        action="store_true",
        help="Keep metadata (not recommended). By default metadata is stripped.",
    )

    # Image-specific options
    img = parser.add_argument_group("Image options")
    img.add_argument(
        "--img-lossless",
        action="store_true",
        default=False,
        help="Write WebP losslessly (default: off)",
    )
    img.add_argument(
        "--img-quality",
        type=int,
        default=85,
        help="WebP quality when not lossless (1-100)",
    )
    img.add_argument(
        "--img-max",
        type=int,
        metavar="PX",
        help="Resize images so the longest side is ≤ PX",
    )
    img.add_argument(
        "--img-alt",
        type=str,
        help="Alt text for generated <picture> snippet (default: stem name)",
    )

    # SVG-specific options
    svg = parser.add_argument_group("SVG options")
    svg.add_argument(
        "--svg-pretty",
        action="store_true",
        help="Pretty-print SVG output (default: compact)",
    )

    # Font-specific options
    fnt = parser.add_argument_group("Font options")
    fnt.add_argument(
        "--font-family",
        type=str,
        help="CSS font-family name for generated @font-face rules (default: filename)",
    )
    fnt.add_argument(
        "--font-weight",
        type=str,
        default="normal",
        help="CSS font-weight (default: normal)",
    )
    fnt.add_argument(
        "--font-style",
        type=str,
        default="normal",
        help="CSS font-style (default: normal)",
    )
    fnt.add_argument(
        "--font-split",
        choices=["auto", "none", "by-256"],
        default="auto",
        help=(
            "Subsetting strategy: auto (large common ranges as-is, others per 256), "
            "none (no subsetting), by-256 (always chunk by 256)."
        ),
    )
    fnt.add_argument(
        "--font-keep-hinting",
        action="store_true",
        help="Retain hinting (default: drop for smaller size)",
    )
    fnt.add_argument(
        "--font-jobs",
        type=int,
        help="Parallel processes for font subsetting/encoding (default: CPU count)",
    )

    args = parser.parse_args(argv)

    inputs: list[Path] = [p.resolve() for p in args.inputs]
    for p in inputs:
        if not p.exists():
            parser.error(f"{p} not found")

    images, svgs, fonts = _group_inputs(inputs)

    created_paths: list[Path] = []

    # SVGs
    for src in svgs:
        dest_dir = (args.out or src.parent)
        dest_dir.mkdir(parents=True, exist_ok=True)
        optimized = svg_mod.optimize(
            src,
            dest_dir,
            pretty=args.svg_pretty,
            strip_metadata=not args.keep_metadata,
        )
        created_paths.append(optimized)

    # Images
    for src in images:
        dest_dir = (args.out or src.parent)
        dest_dir.mkdir(parents=True, exist_ok=True)
        webp, fallback, html = image_mod.optimize(
            src,
            dest_dir,
            lossless=args.img_lossless,
            quality=args.img_quality,
            max_side=args.img_max,
            strip=not args.keep_metadata,
            alt_text=args.img_alt,
        )
        created_paths.extend([webp, fallback, html])

    # Fonts
    for src in fonts:
        dest_dir = (args.out or src.parent)
        dest_dir.mkdir(parents=True, exist_ok=True)
        results = font_mod.subset_and_convert(
            src,
            dest_dir,
            family=args.font_family,
            weight=args.font_weight,
            style=args.font_style,
            split_strategy=args.font_split,
            keep_hinting=args.font_keep_hinting,
            strip_metadata=not args.keep_metadata,
            jobs=args.font_jobs,
        )
        created_paths.extend(results)

    # Summary
    if created_paths:
        print("Created:")
        for p in created_paths:
            print(f" • {p}")

    return 0


def main() -> None:
    raise SystemExit(cli())


if __name__ == "__main__":
    main()
