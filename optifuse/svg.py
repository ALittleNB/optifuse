from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys


def _remove_simple_metadata(svg_text: str) -> str:
    """Best-effort removal of comments and common descriptive elements.

    This is used in addition to Scour to enforce the requested policy of
    removing comments, class attributes, and descriptive elements.
    """
    # Remove XML/HTML comments
    svg_text = re.sub(r"<!--.*?-->", "", svg_text, flags=re.S)

    # Remove <metadata>, <desc>, <title> blocks (cheap regex-based)
    for tag in ("metadata", "desc", "title"):
        svg_text = re.sub(rf"<\s*{tag}[^>]*>.*?<\s*/\s*{tag}\s*>", "", svg_text, flags=re.S | re.I)

    # Remove class attributes
    svg_text = re.sub(r"\sclass=\".*?\"", "", svg_text)
    svg_text = re.sub(r"\sclass='.*?'", "", svg_text)

    # Trim redundant whitespace between tags
    svg_text = re.sub(r">\s+<", "><", svg_text)
    return svg_text.strip()


def optimize(source: Path, dest_dir: Path, *, pretty: bool = False, strip_metadata: bool = True) -> Path:
    """Optimize an SVG using Scour; ensure comments/classes/descriptive elements are removed.

    Returns the path to the optimized SVG file.
    """
    dest_path = dest_dir / f"{source.stem}.svg"

    # First attempt: call Scour via module CLI to ensure full optimizer is used
    args = [
        "-m",
        "scour",
        "-i",
        str(source),
        "-o",
        str(dest_path),
    ]
    if not pretty:
        args += ["--no-line-breaks", "--indent=none"]
    if strip_metadata:
        args += [
            "--remove-metadata",
            "--remove-descriptive-elements",
            "--enable-comment-stripping",
            "--enable-id-stripping",
            "--shorten-ids",
            "--strip-xml-prolog",
        ]

    try:
        subprocess.run([sys.executable, *args], check=True, capture_output=True)
        # Additional cleanup to enforce user policy (e.g., remove class)
        if strip_metadata or not pretty:
            text = dest_path.read_text(encoding="utf-8")
            text = _remove_simple_metadata(text) if strip_metadata else text
            if not pretty:
                text = re.sub(r"\s+", " ", text)
                text = re.sub(r">\s+<", "><", text)
            dest_path.write_text(text, encoding="utf-8")
        return dest_path
    except Exception:
        # Programmatic fallback: best-effort sanitize in-process
        text = source.read_text(encoding="utf-8")
        if strip_metadata:
            text = _remove_simple_metadata(text)
        if not pretty:
            text = re.sub(r"\s+", " ", text)
            text = re.sub(r">\s+<", "><", text)
        dest_path.write_text(text, encoding="utf-8")
        return dest_path
