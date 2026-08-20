from __future__ import annotations

import html
from pathlib import Path

from src.synth.config import SynthConfig
from src.synth.material import Material

_BASE_STYLES = """
body { font-family: 'Noto Serif CJK SC', 'Songti SC', serif; margin: 32px; }
.src-page { margin-bottom: 48px; padding-bottom: 24px; border-bottom: 1px solid #ccc; }
.block { margin: 8px 0; line-height: 1.6; }
.block[data-category="doc_title"] { font-size: 22px; font-weight: 700; text-align: center; }
.block[data-category="paragraph_title"] { font-size: 18px; font-weight: 700; }
.block[data-category="header"], .block[data-category="footer"] { color: #555; font-size: 12px; }
/* 译文用小一号字体:双语文档常见排式,同时抑制英文列跨页滞后 */
.block[data-lang="en"] { font-size: 0.82em; line-height: 1.45; color: #222; }
img.block { display: block; max-width: 100%; height: auto; }
""".strip()


def _image_src(material: Material, image_path: str) -> str:
    # 渲染端用 set_content 加载 html 字符串,没有 base URL,
    # 相对路径无法解析,必须用绝对 file:// URI。
    del material
    return Path(image_path).resolve().as_uri()


def build_source_html(material: Material, cfg: SynthConfig) -> str:
    del cfg  # reserved for future layout options
    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh">',
        "<head>",
        '<meta charset="utf-8" />',
        "<title>source</title>",
        f"<style>{_BASE_STYLES}</style>",
        "</head>",
        "<body>",
        '<article class="source-doc">',
    ]

    current_page: int | None = None
    for block in material.blocks:
        if block.page != current_page:
            if current_page is not None:
                parts.append("</section>")
            current_page = block.page
            parts.append(f'<section class="src-page" data-src-page="{current_page}">')

        attrs = (
            f'data-node-id="{html.escape(block.id, quote=True)}" '
            f'data-category="{html.escape(block.category, quote=True)}" '
            f'data-lang="zh"'
        )
        if block.image_path:
            src = html.escape(_image_src(material, block.image_path), quote=True)
            parts.append(f'<img class="block" {attrs} src="{src}" alt="" />')
        else:
            text = html.escape(block.text)
            parts.append(f'<div class="block" {attrs}>{text}</div>')

    if current_page is not None:
        parts.append("</section>")

    parts.extend(["</article>", "</body>", "</html>"])
    return "\n".join(parts)
