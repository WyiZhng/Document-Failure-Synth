from __future__ import annotations

import html
from pathlib import Path

from src.synth.config import SynthConfig
from src.synth.material import Material
from src.synth.translation_types import BlockPlan, TranslationBundle

_BASE_STYLES = """
body { font-family: 'Noto Serif CJK SC', 'Songti SC', serif; margin: 32px; }
.src-page { margin-bottom: 48px; padding-bottom: 24px; border-bottom: 1px solid #ccc; }
.block { margin: 8px 0; line-height: 1.6; }
div.block { min-height: 1px; }
.block[data-category="doc_title"] { font-size: 22px; font-weight: 700; text-align: center; }
.block[data-category="paragraph_title"] { font-size: 18px; font-weight: 700; }
.block[data-category="header"], .block[data-category="footer"] { color: #555; font-size: 12px; }
/* 译文用小一号字体:双语文档常见排式 */
.block[data-lang="en"] { font-size: 0.82em; line-height: 1.45; color: #222; }
.block[data-relation-only="true"] { visibility: hidden; min-height: 1px; height: 1px; }
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


def _block_attrs(block_id: str, category: str, language: str) -> str:
    return (
        f'data-node-id="{html.escape(block_id, quote=True)}" '
        f'data-category="{html.escape(category, quote=True)}" '
        f'data-lang="{html.escape(language, quote=True)}"'
    )


def _append_visible_block(
    parts: list[str],
    material: Material,
    block,
    language: str,
    text: str,
    *,
    relation_only: bool = False,
) -> None:
    attrs = _block_attrs(block.id, block.category, language)
    if relation_only:
        attrs += ' data-relation-only="true"'
    if block.image_path:
        src = html.escape(_image_src(material, block.image_path), quote=True)
        parts.append(f'<img class="block" {attrs} src="{src}" alt="" />')
    else:
        parts.append(f'<div class="block" {attrs}>{html.escape(text)}</div>')


def build_bilingual_html(
    material: Material,
    bundle: TranslationBundle,
    cfg: SynthConfig,
) -> str:
    """Build bilingual source HTML from Material and an immutable bundle."""

    del cfg  # layout is selected by the renderer, not by HTML assembly
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
        plan: BlockPlan | None = bundle.plan_for(block.id)
        if plan is None:
            # A missing plan is only expected for an empty leaf. Keep this
            # fallback source-only behavior for callers constructing bundles
            # manually, while never fabricating target text.
            if not block.text.strip() and not block.image_path:
                continue
            source_lang = "zh"
            action = "source_only"
            target_lang = None
            target_text = None
        else:
            if (
                not block.text.strip()
                and not block.image_path
                and plan.action != "source_only"
            ):
                # Empty leaves that were dropped by translation do not get a
                # physical block. Empty relation-only source plans do.
                continue
            source_lang = plan.source_lang
            action = plan.action
            target_lang = plan.target_lang
            target_text = plan.target_text

        if block.page != current_page:
            if current_page is not None:
                parts.append("</section>")
            current_page = block.page
            parts.append(f'<section class="src-page" data-src-page="{current_page}">')

        relation_only = (
            not block.text.strip()
            and not block.image_path
            and plan is not None
            and plan.action == "source_only"
        )
        if not block.text.strip() and not block.image_path and not relation_only:
            continue

        _append_visible_block(
            parts,
            material,
            block,
            source_lang,
            block.text,
            relation_only=relation_only,
        )
        if (
            not block.image_path
            and action in {"translate", "copy"}
            and target_lang in {"zh", "en"}
            and isinstance(target_text, str)
            and target_text.strip()
        ):
            _append_visible_block(parts, material, block, target_lang, target_text)

    if current_page is not None:
        parts.append("</section>")
    parts.extend(["</article>", "</body>", "</html>"])
    return "\n".join(parts)
