from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from src.synth.config import load_config
from src.synth.render import render_pages


def make_test_html(n_blocks: int = 8, lang: str = "zh", block_height: int = 150) -> str:
    blocks = []
    for i in range(n_blocks):
        blocks.append(
            f'<div class="block" data-node-id="b{i}" data-category="text" '
            f'data-lang="{lang}" style="height:{block_height}px;background:#ccc;">'
            f"Block {i}</div>"
        )
    return f"<!DOCTYPE html><html><body>{''.join(blocks)}</body></html>"


def make_bilingual_test_html() -> str:
    parts: list[str] = []
    for i in range(3):
        parts.append(
            f'<div class="block" data-node-id="n{i}" data-category="text" data-lang="zh" '
            f'style="height:100px;background:#eee;">中文{i + 1}</div>'
        )
        parts.append(
            f'<div class="block" data-node-id="n{i}" data-category="text" data-lang="en" '
            f'style="height:500px;background:#ddd;">English {i + 1}</div>'
        )
    return f"<!DOCTYPE html><html><body>{''.join(parts)}</body></html>"


@pytest.fixture
def cfg():
    return load_config(Path("src/synth/config/synth.yaml"))


@pytest.mark.render
def test_render_zh_only_single_column(cfg, tmp_path):
    html = make_test_html(n_blocks=8, lang="zh")
    placed = render_pages(html, tmp_path, cfg)
    assert len(placed) == 8 and all(p.lang == "zh" for p in placed)
    assert (tmp_path / "raw-page-1.png").exists()
    for p in placed:
        assert 0 <= p.bbox[0] < p.bbox[2] <= cfg.page.width
        assert 0 <= p.bbox[1] < p.bbox[3] <= cfg.page.height


@pytest.mark.render
def test_render_bilingual_two_columns_overflow(cfg, tmp_path):
    html = make_bilingual_test_html()
    placed = render_pages(html, tmp_path, cfg)
    en_pages = {p.page for p in placed if p.lang == "en"}
    assert max(en_pages) == 1
    zh = [p for p in placed if p.lang == "zh"]
    en = [p for p in placed if p.lang == "en" and p.page == 0]
    assert max(b.bbox[2] for b in zh) <= min(b.bbox[0] for b in en)
    zh_page = {p.node_id: p.page for p in placed if p.lang == "zh"}
    for block in placed:
        if block.lang == "en":
            assert block.page == zh_page[block.node_id]


@pytest.mark.render
def test_screenshot_pixel_matches_bbox(cfg, tmp_path):
    html = (
        "<!DOCTYPE html><html><body>"
        '<div class="block" data-node-id="red1" data-category="text" data-lang="zh" '
        'style="height:200px;background:rgb(255,0,0);">Red</div>'
        "</body></html>"
    )
    placed = render_pages(html, tmp_path, cfg)
    assert len(placed) == 1
    p = placed[0]
    img = Image.open(tmp_path / "raw-page-1.png")
    x1, y1, x2, y2 = (int(v) for v in p.bbox)
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    pixel = img.getpixel((cx, cy))
    assert pixel[0] > 200 and pixel[1] < 50 and pixel[2] < 50
