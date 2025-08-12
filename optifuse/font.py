from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import os
import unicodedata

from fontTools.ttLib import TTFont
from fontTools.subset import Subsetter, Options


# Latin blocks we consider "common" to seed visible Latin coverage
LATIN_BLOCKS: list[tuple[int, int]] = [
    (0x0020, 0x007E),  # ASCII printable (includes space)
    (0x00A0, 0x00FF),  # Latin-1 Supplement (skip C0 controls)
    (0x0100, 0x017F),  # Latin Extended-A
]

# Han ideographs blocks (common Chinese characters)
HAN_BLOCKS: list[tuple[int, int]] = [
    (0x3400, 0x4DBF),   # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x20000, 0x2A6DF), # Extension B
    (0x2A700, 0x2B73F), # Extension C
    (0x2B740, 0x2B81F), # Extension D
    (0x2B820, 0x2CEAF), # Extension E
    (0x2CEB0, 0x2EBEF), # Extension F
    (0x30000, 0x3134F), # Extension G
]

MAX_CHUNK_SIZE = 300
MIN_CHUNK_SIZE = 128  # heuristic to avoid too-small subsets


@dataclass(frozen=True)
class UnicodeRange:
    start: int
    end: int
    label: str

    def to_css(self) -> str:
        return f"U+{self.start:04X}-{self.end:04X}"


def _font_coverage(font: TTFont) -> set[int]:
    cmap = font.getBestCmap() or {}
    return set(cmap.keys())


def _intersect_coverage(r: tuple[int, int], coverage: set[int]) -> set[int]:
    start, end = r
    return {cp for cp in coverage if start <= cp <= end}


def _chunk_256(coverage: set[int]) -> list[UnicodeRange]:
    blocks: dict[int, set[int]] = {}
    for cp in coverage:
        block_start = (cp // 256) * 256
        blocks.setdefault(block_start, set()).add(cp)
    ranges: list[UnicodeRange] = []
    for block_start, cps in sorted(blocks.items()):
        block_end = block_start + 255
        ranges.append(UnicodeRange(block_start, block_end, f"u{block_start:04X}-{block_end:04X}"))
    return ranges


def _is_private_or_invisible(cp: int) -> bool:
    cat = unicodedata.category(chr(cp)) if 0 <= cp <= 0x10FFFF else "Cn"
    # Cc: control, Cf: format, Cs: surrogate, Co: private use, Cn: unassigned
    return cat in {"Cc", "Cf", "Cs", "Co", "Cn"}


def _is_han(cp: int) -> bool:
    for start, end in HAN_BLOCKS:
        if start <= cp <= end:
            return True
    return False


def _partition_han_chunks(han_cps: set[int]) -> list[UnicodeRange]:
    if not han_cps:
        return []
    sorted_cps = sorted(han_cps)
    chunks: list[list[int]] = []
    current: list[int] = []
    for cp in sorted_cps:
        if not current:
            current = [cp]
            continue
        # If contiguous or close, try to add while respecting max size
        if cp == current[-1] + 1 and len(current) < MAX_CHUNK_SIZE:
            current.append(cp)
        else:
            chunks.append(current)
            current = [cp]
    if current:
        chunks.append(current)

    # Merge small chunks with neighbors where possible to avoid too-small subsets
    merged: list[list[int]] = []
    i = 0
    while i < len(chunks):
        group = chunks[i]
        while len(group) < MIN_CHUNK_SIZE and i + 1 < len(chunks):
            next_group = chunks[i + 1]
            if len(group) + len(next_group) <= MAX_CHUNK_SIZE:
                group = group + next_group
                i += 1
            else:
                break
        merged.append(group)
        i += 1

    ranges: list[UnicodeRange] = []
    for grp in merged:
        start, end = grp[0], grp[-1]
        ranges.append(UnicodeRange(start, end, f"han-u{start:04X}-{end:04X}"))
    return ranges


def _partition_limited_chunks(cps: set[int], prefix: str) -> list[UnicodeRange]:
    if not cps:
        return []
    sorted_cps = sorted(cps)
    chunks: list[list[int]] = []
    current: list[int] = []
    for cp in sorted_cps:
        if not current:
            current = [cp]
            continue
        if cp == current[-1] + 1 and len(current) < MAX_CHUNK_SIZE:
            current.append(cp)
        else:
            chunks.append(current)
            current = [cp]
    if current:
        chunks.append(current)

    merged: list[list[int]] = []
    i = 0
    while i < len(chunks):
        group = chunks[i]
        while len(group) < MIN_CHUNK_SIZE and i + 1 < len(chunks):
            next_group = chunks[i + 1]
            if len(group) + len(next_group) <= MAX_CHUNK_SIZE:
                group = group + next_group
                i += 1
            else:
                break
        merged.append(group)
        i += 1

    ranges: list[UnicodeRange] = []
    for grp in merged:
        start, end = grp[0], grp[-1]
        ranges.append(UnicodeRange(start, end, f"{prefix}-u{start:04X}-{end:04X}"))
    return ranges


def _collect_in_blocks(coverage: set[int], blocks: list[tuple[int, int]]) -> set[int]:
    result: set[int] = set()
    for start, end in blocks:
        for cp in coverage:
            if start <= cp <= end:
                result.add(cp)
    return result


def _split_visible_sets(coverage: set[int]) -> tuple[set[int], set[int]]:
    visible = {cp for cp in coverage if not _is_private_or_invisible(cp)}
    invisible_or_pua = coverage - visible
    return visible, invisible_or_pua


def _by256_with_visibility(coverage: set[int]) -> list[UnicodeRange]:
    visible, invis = _split_visible_sets(coverage)
    ranges: list[UnicodeRange] = []
    for r in _chunk_256(visible):
        ranges.append(UnicodeRange(r.start, r.end, f"vis-{r.label}"))
    for r in _chunk_256(invis):
        ranges.append(UnicodeRange(r.start, r.end, f"invis-{r.label}"))
    return ranges


def _auto_ranges(coverage: set[int]) -> list[UnicodeRange]:
    # Separate visible vs invisible/private first
    visible, invis = _split_visible_sets(coverage)
    remaining_vis = set(visible)
    selected: list[UnicodeRange] = []

    # Latin common blocks (visible only), chunked like Han
    latin_used = _collect_in_blocks(remaining_vis, LATIN_BLOCKS)
    selected.extend(_partition_limited_chunks(latin_used, "latin"))
    remaining_vis -= latin_used

    # Han (Chinese) chunks with size <= 300, avoiding small chunks via merging (visible only)
    han_used = {cp for cp in remaining_vis if _is_han(cp)}
    selected.extend(_partition_han_chunks(han_used))
    remaining_vis -= han_used

    # Remaining visible: per-256
    selected.extend([UnicodeRange(r.start, r.end, f"vis-{r.label}") for r in _chunk_256(remaining_vis)])

    # Invisible/private/unassigned: per-256 into their own subsets
    selected.extend([UnicodeRange(r.start, r.end, f"invis-{r.label}") for r in _chunk_256(invis)])

    return selected


def _subset_font(
    font: TTFont,
    unicodes: Iterable[int],
    *,
    keep_hinting: bool,
) -> TTFont:
    options = Options()
    options.hinting = keep_hinting
    options.desubroutinize = True
    options.recommended_glyphs = True
    options.glyph_names = False
    subsetter = Subsetter(options=options)
    subsetter.populate(unicodes=set(unicodes))
    subsetter.subset(font)
    return font


def _save_as_woff_variants(font: TTFont, out_base: Path) -> list[Path]:
    paths: list[Path] = []
    # WOFF2 (requires brotli)
    font.flavor = "woff2"
    out_woff2 = out_base.with_suffix(".woff2")
    font.save(out_woff2)
    paths.append(out_woff2)

    # WOFF (zlib)
    font.flavor = "woff"
    out_woff = out_base.with_suffix(".woff")
    font.save(out_woff)
    paths.append(out_woff)

    # Clear flavor to keep the object reusable in some environments
    font.flavor = None
    return paths


def _emit_css(
    dest_dir: Path,
    family: str,
    weight: str,
    style: str,
    records: list[tuple[str, UnicodeRange]],
) -> Path:
    css_path = dest_dir / f"{family.replace(' ', '-')}.css"
    lines: list[str] = []
    for label, urange in records:
        stem = label
        lines.append(
            "@font-face {\n"
            f"  font-family: '{family}';\n"
            f"  font-style: {style};\n"
            f"  font-weight: {weight};\n"
            "  font-display: swap;\n"
            f"  src: url('{stem}.woff2') format('woff2'), url('{stem}.woff') format('woff');\n"
            f"  unicode-range: {urange.to_css()};\n"
            "}\n\n"
        )
    css_path.write_text("".join(lines), encoding="utf-8")
    return css_path


def subset_and_convert(
    source: Path,
    dest_dir: Path,
    *,
    family: str | None,
    weight: str,
    style: str,
    split_strategy: str = "auto",
    keep_hinting: bool = False,
    strip_metadata: bool = True,  # reserved; not aggressively dropping name table for safety
    jobs: int | None = None,
) -> list[Path]:
    """Subset a font and produce WOFF2+WOFF along with a CSS file.

    - split_strategy:
        - 'none': one subset for all glyphs
        - 'by-256': subsets per 256-codepoint blocks present in the font
        - 'auto': common ranges (Latin) as large groups, rest by-256
    Returns list of created file paths including the CSS at the end.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    with TTFont(str(source), recalcBBoxes=True, recalcTimestamp=True) as base_font:
        coverage = _font_coverage(base_font)
        fam = family or source.stem

        if split_strategy == "none":
            ranges = [UnicodeRange(min(coverage) if coverage else 0, max(coverage) if coverage else 0xFFFF, "full")]
        elif split_strategy == "by-256":
            ranges = _by256_with_visibility(coverage)
        else:  # auto
            ranges = _auto_ranges(coverage)

        created: list[Path] = []
        css_records: list[tuple[str, UnicodeRange]] = []

        # Prepare tasks
        tasks: list[tuple[UnicodeRange, list[int]]] = []
        for ur in ranges:
            unicodes = [cp for cp in coverage if ur.start <= cp <= ur.end]
            if not unicodes:
                continue
            tasks.append((ur, unicodes))

        # Threaded processing to speed up WOFF/WOFF2 encoding
        max_workers = jobs or max(1, (os.cpu_count() or 4))
        base_bytes = Path(source).read_bytes()

        def _process(ur: UnicodeRange, unicodes: list[int]) -> tuple[list[Path], tuple[str, UnicodeRange]]:
            # Use in-memory font to avoid repeated disk reads
            with TTFont(io.BytesIO(base_bytes), recalcBBoxes=True, recalcTimestamp=True) as font:
                sub_font = _subset_font(font, unicodes, keep_hinting=keep_hinting)
                out_base = dest_dir / f"{fam.replace(' ', '-')}-{ur.label}"
                paths = _save_as_woff_variants(sub_font, out_base)
                return paths, (out_base.name, ur)

        with ThreadPoolExecutor(max_workers=max_workers) as exe:
            future_to_task = {exe.submit(_process, ur, unicodes): (ur, unicodes) for ur, unicodes in tasks}
            for fut in as_completed(future_to_task):
                paths, css_rec = fut.result()
                created.extend(paths)
                css_records.append(css_rec)

    css_path = _emit_css(dest_dir, fam, weight, style, css_records)
    created.append(css_path)
    return created