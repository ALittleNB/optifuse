from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable
import sys
import io
import zipfile

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
        type=str,
        nargs="*",
        help="One or more files to optimize (SVG, images, fonts). Omit to read from stdin.",
    )
    parser.add_argument(
        "-d",
        "--out",
        type=Path,
        help="Output directory (default: alongside each input)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write the primary artifact to stdout (for pipes). When reading stdin, this is implied if no --out is given.",
    )
    parser.add_argument(
        "--stdout-artifact",
        choices=["auto", "svg", "webp", "fallback", "html", "css", "zip"],
        default="auto",
        help=(
            "Select which artifact to stream to stdout: svg (optimized SVG), webp/fallback/html (images), "
            "css or zip (fonts). Default 'auto': svg→svg, images→webp, fonts→zip."
        ),
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

    stdin_mode = len(args.inputs) == 0
    created_paths: list[Path] = []

    if stdin_mode:
        # Read all stdin bytes once for performance
        data = sys.stdin.buffer.read()
        if not data:
            parser.error("No input received on stdin")

        # Detect mime via python-magic if available, else fast signature sniffing
        mime = None
        try:
            import magic  # type: ignore

            m = magic.Magic(mime=True)  # may raise if libmagic not present
            mime = m.from_buffer(data)
        except Exception:
            pass

        if mime is None:
            head = data[:32]
            if head.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif head[0:3] == b"GIF":
                mime = "image/gif"
            elif head[0:2] == b"\xff\xd8":
                mime = "image/jpeg"
            elif head.startswith(b"RIFF") and b"WEBP" in head:
                mime = "image/webp"
            elif head[4:8] == b"ftyp" and b"avif" in head[8:16]:
                mime = "image/avif"
            elif head.startswith(b"OTTO"):
                mime = "font/otf"
            elif head.startswith(b"\x00\x01\x00\x00") or head.startswith(b"true") or head.startswith(b"ttcf"):
                mime = "font/ttf"
            else:
                # Heuristic: sniff SVG by leading tag
                if data.lstrip().startswith(b"<") and b"<svg" in data[:200].lower():
                    mime = "image/svg+xml"
                else:
                    mime = "application/octet-stream"
        # Decide type by mime and dispatch to a temp file-less path when possible
        # We write to a temporary file for PIL/TTFont/scour compatibility
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            # Map mime to extension for routing
            ext = {
                "image/svg+xml": ".svg",
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/gif": ".gif",
                "image/webp": ".webp",
                "image/avif": ".avif",
                "font/ttf": ".ttf",
                "font/otf": ".otf",
                # Some OSes report these generically
                "application/font-sfnt": ".ttf",
                "application/x-font-ttf": ".ttf",
                "application/x-font-otf": ".otf",
                "application/octet-stream": "",  # fallback guessed below
            }.get(mime, "")

            # Heuristic fallback: sniff SVG by leading tag
            if not ext and data.lstrip().startswith(b"<") and b"<svg" in data[:200].lower():
                ext = ".svg"

            stem = "stdin"
            tmp_path = tmpdir / f"{stem}{ext or '.bin'}"
            tmp_path.write_bytes(data)
            out_dir = (args.out or Path.cwd())
            out_dir.mkdir(parents=True, exist_ok=True)

            # Decide stdout behavior
            stdout_requested = args.stdout or (args.out is None)

            if _is_svg(tmp_path):
                optimized = svg_mod.optimize(tmp_path, out_dir, pretty=args.svg_pretty, strip_metadata=not args.keep_metadata)
                if stdout_requested:
                    # Stream optimized SVG
                    sys.stdout.buffer.write(optimized.read_bytes())
                    return 0
                created_paths.append(optimized)
            elif _is_image(tmp_path):
                webp, fallback, html = image_mod.optimize(
                    tmp_path,
                    out_dir,
                    lossless=args.img_lossless,
                    quality=args.img_quality,
                    max_side=args.img_max,
                    strip=not args.keep_metadata,
                    alt_text=args.img_alt or "stdin",
                )
                if stdout_requested:
                    artifact = args.stdout_artifact
                    if artifact == "auto":
                        artifact = "webp"
                    if artifact == "webp":
                        sys.stdout.buffer.write(webp.read_bytes())
                    elif artifact == "fallback":
                        sys.stdout.buffer.write(fallback.read_bytes())
                    elif artifact == "html":
                        sys.stdout.buffer.write(html.read_bytes())
                    else:
                        parser.error(f"Unsupported stdout artifact for image: {artifact}")
                    return 0
                created_paths.extend([webp, fallback, html])
            elif _is_font(tmp_path):
                results = font_mod.subset_and_convert(
                    tmp_path,
                    out_dir,
                    family=args.font_family or "stdin",
                    weight=args.font_weight,
                    style=args.font_style,
                    split_strategy=args.font_split,
                    keep_hinting=args.font_keep_hinting,
                    strip_metadata=not args.keep_metadata,
                    jobs=args.font_jobs,
                )
                if stdout_requested:
                    artifact = args.stdout_artifact
                    if artifact == "auto":
                        artifact = "zip"
                    if artifact == "css":
                        # Find css in results
                        css = next((p for p in results if p.suffix == ".css"), None)
                        if not css:
                            parser.error("CSS not generated")
                        sys.stdout.buffer.write(css.read_bytes())
                    elif artifact == "zip":
                        buf = io.BytesIO()
                        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                            for p in results:
                                zf.write(p, arcname=p.name)
                        sys.stdout.buffer.write(buf.getvalue())
                    else:
                        parser.error(f"Unsupported stdout artifact for font: {artifact}")
                    return 0
                created_paths.extend(results)
            else:
                parser.error(f"Unsupported stdin MIME: {mime}")
    else:
        input_paths: list[Path] = [Path(p).resolve() for p in args.inputs]
        for p in input_paths:
            if not p.exists():
                parser.error(f"{p} not found")

        # stdout mode only allowed with a single input
        if args.stdout and len(input_paths) != 1:
            parser.error("--stdout requires exactly one input file")

        images, svgs, fonts = _group_inputs(input_paths)

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
            if args.stdout:
                artifact = args.stdout_artifact
                if artifact == "auto":
                    artifact = "webp"
                if artifact == "webp":
                    sys.stdout.buffer.write(webp.read_bytes())
                elif artifact == "fallback":
                    sys.stdout.buffer.write(fallback.read_bytes())
                elif artifact == "html":
                    sys.stdout.buffer.write(html.read_bytes())
                else:
                    parser.error(f"Unsupported stdout artifact for image: {artifact}")
                return 0
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
            if args.stdout:
                artifact = args.stdout_artifact
                if artifact == "auto":
                    artifact = "zip"
                if artifact == "css":
                    css = next((p for p in results if p.suffix == ".css"), None)
                    if not css:
                        parser.error("CSS not generated")
                    sys.stdout.buffer.write(css.read_bytes())
                elif artifact == "zip":
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                        for p in results:
                            zf.write(p, arcname=p.name)
                    sys.stdout.buffer.write(buf.getvalue())
                else:
                    parser.error(f"Unsupported stdout artifact for font: {artifact}")
                return 0
            created_paths.extend(results)

    # Summary
    if created_paths and not args.stdout:
        print("Created:")
        for p in created_paths:
            print(f" • {p}")

    return 0


def main() -> None:
    raise SystemExit(cli())


if __name__ == "__main__":
    main()
