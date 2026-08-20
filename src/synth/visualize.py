from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src.synth.render import PlacedBlock

_LANG_COLORS = {"zh": (255, 0, 0), "en": (0, 0, 255)}
_DEFAULT_LANG_COLOR = (128, 128, 128)


def _discover_page_numbers(images_dir: Path) -> list[int]:
    pages: list[int] = []
    for path in sorted(images_dir.glob("raw-page-*.png")):
        stem = path.stem  # raw-page-N
        try:
            pages.append(int(stem.rsplit("-", 1)[-1]) - 1)
        except ValueError:
            continue
    return pages


def _load_font(size: int = 12) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in (
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_label(
    overlay: Image.Image,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    draw = ImageDraw.Draw(overlay)
    bbox = draw.textbbox((x, y), text, font=font)
    padding = 2
    bg = (
        bbox[0] - padding,
        bbox[1] - padding,
        bbox[2] + padding,
        bbox[3] + padding,
    )
    draw.rectangle(bg, fill=(0, 0, 0, 160))
    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)


def _draw_block(
    base: Image.Image,
    overlay: Image.Image,
    block: PlacedBlock,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    x1, y1, x2, y2 = (int(v) for v in block.bbox)
    color = _LANG_COLORS.get(block.lang, _DEFAULT_LANG_COLOR)
    line_width = max(2, round(min(base.size) / 500))

    draw = ImageDraw.Draw(base)
    draw.rectangle((x1, y1, x2, y2), outline=color, width=line_width)

    label_x = max(0, x1)
    label_y = max(0, y1)
    _draw_label(overlay, label_x, label_y, block.node_id, font)


def draw_overlays(
    images_dir: Path,
    placed: list[PlacedBlock],
    out_dir: Path,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    by_page: dict[int, list[PlacedBlock]] = defaultdict(list)
    for block in placed:
        by_page[block.page].append(block)

    page_numbers = _discover_page_numbers(images_dir)
    if not page_numbers and by_page:
        page_numbers = sorted(by_page)

    font = _load_font(size=12)
    outputs: list[Path] = []

    for page in page_numbers:
        src = images_dir / f"raw-page-{page + 1}.png"
        if not src.is_file():
            continue

        base = Image.open(src).convert("RGB")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))

        for block in by_page.get(page, []):
            _draw_block(base, overlay, block, font)

        composed = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
        out_path = out_dir / f"page-{page + 1}.png"
        composed.save(out_path)
        outputs.append(out_path)

    return outputs
