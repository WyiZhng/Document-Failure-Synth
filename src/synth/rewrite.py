from __future__ import annotations

import os
import re
from copy import deepcopy

from bs4 import BeautifulSoup, NavigableString, Tag

from src.synth.config import SynthConfig

REWRITE_PROMPT = """You translate marked HTML sections from Chinese to English.

HARD RULES:
1. Keep every original element with data-node-id exactly as-is. Never drop, rename, or change any attribute (including src, class).
2. Original Chinese blocks have data-lang="zh". Do not modify their text content.
3. For EACH block with data-lang="zh" AND data-category in {translate_categories} (text blocks only, NOT img):
   insert immediately AFTER it one English translation element with:
   - the SAME data-node-id
   - the SAME data-category
   - data-lang="en"
   - translated English text inside (use div for text blocks)
4. Do NOT add English blocks for image/chart/seal/table/header/footer blocks or any img element.
5. Wrapper elements may exist but must not contain visible text outside [data-node-id] elements.
6. Do not add extra prose, titles, or commentary without markers.
7. Return ONLY this section's HTML (may include the <section> tag). No markdown fences.

Input section HTML:
"""


class RewriteError(Exception):
    """Raised when LLM rewrite output fails post-validation."""


def rewrite_html(
    source_html: str,
    cfg: SynthConfig,
    client=None,
    seed: int | None = None,
) -> str:
    if client is None:
        from openai import OpenAI

        client = OpenAI(
            api_key=os.environ[cfg.llm.api_key_env],
            base_url=os.environ.get(cfg.llm.base_url_env) or None,
            timeout=300,
        )

    soup = BeautifulSoup(source_html, "lxml")
    sections = soup.select("section.src-page")
    if not sections:
        raise RewriteError("no section.src-page found in source html")

    model = os.environ.get(cfg.llm.model_env, "")
    prompt_prefix = REWRITE_PROMPT.format(
        translate_categories=", ".join(cfg.translate_categories)
    )

    for section in sections:
        original_section = deepcopy(section)
        section_html = str(section)
        last_errors: list[str] = []

        for _ in range(cfg.llm.max_retries + 1):
            response = client.chat.completions.create(
                model=model,
                temperature=cfg.llm.temperature,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a careful HTML rewriter. "
                            "You never drop or alter data-* markers."
                        ),
                    },
                    {"role": "user", "content": prompt_prefix + section_html},
                ],
                extra_body=(
                    {"enable_thinking": False, "seed": seed}
                    if seed is not None
                    else {"enable_thinking": False}
                ),
            )
            raw = response.choices[0].message.content
            if not isinstance(raw, str) or not raw.strip():
                last_errors = ["empty LLM response"]
                continue

            rewritten_section = _parse_section_output(strip_fences(raw))
            errors = validate_section(original_section, rewritten_section, cfg)
            if not errors:
                _replace_section(section, rewritten_section)
                break
            last_errors = errors
        else:
            raise RewriteError("; ".join(last_errors))

    return str(soup)


def strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:html)?\s*(.*?)\s*```$", text, re.S | re.I)
    if match:
        return match.group(1).strip()
    return text


def _parse_section_output(raw: str) -> Tag:
    parsed = BeautifulSoup(raw, "lxml")
    section = parsed.select_one("section.src-page")
    if section is not None:
        return section
    body = parsed.body
    if body is not None and len(body.contents) == 1:
        only = body.contents[0]
        if isinstance(only, Tag) and only.name == "section":
            return only
    wrapper = parsed.new_tag("section", attrs={"class": "src-page"})
    container = body if body is not None else parsed
    for child in list(container.contents):
        if isinstance(child, Tag) and child.name == "html":
            continue
        wrapper.append(child.extract() if isinstance(child, Tag) else child)
    return wrapper


def _replace_section(section: Tag, rewritten: Tag) -> None:
    section.clear()
    for attr, value in rewritten.attrs.items():
        section[attr] = value
    for child in list(rewritten.contents):
        section.append(child.extract() if isinstance(child, Tag) else child)


def _zh_blocks(section: Tag) -> list[Tag]:
    return section.select("[data-node-id][data-lang='zh']")


def _block_text(el: Tag) -> str:
    return el.get_text(strip=True)


def _should_translate(el: Tag, cfg: SynthConfig) -> bool:
    if el.name == "img":
        return False
    return (
        el.get("data-lang") == "zh"
        and el.get("data-category") in cfg.translate_categories
    )


def _immediate_en_sibling(el: Tag) -> Tag | None:
    sibling = el.next_sibling
    while sibling is not None:
        if isinstance(sibling, NavigableString):
            if not sibling.strip():
                sibling = sibling.next_sibling
                continue
            return None
        if not isinstance(sibling, Tag):
            sibling = sibling.next_sibling
            continue
        if sibling.get("data-node-id") == el.get("data-node-id") and sibling.get(
            "data-lang"
        ) == "en":
            return sibling
        return None
    return None


def _unmarked_text_errors(section: Tag) -> list[str]:
    errors: list[str] = []
    for text_node in section.find_all(string=True):
        text = text_node.strip()
        if not text:
            continue
        parent = text_node.parent
        if not isinstance(parent, Tag):
            continue
        if parent.name in {"script", "style", "head", "title", "meta"}:
            continue
        ancestor = parent
        marked = False
        while isinstance(ancestor, Tag):
            if ancestor.get("data-node-id"):
                marked = True
                break
            if ancestor is section:
                break
            ancestor = ancestor.parent
        if not marked:
            errors.append(f"unmarked visible text: {text[:40]!r}")
    return errors


def validate_section(original: Tag, rewritten: Tag, cfg: SynthConfig) -> list[str]:
    original_zh = _zh_blocks(original)
    rewritten_zh = _zh_blocks(rewritten)

    original_ids = {el.get("data-node-id") for el in original_zh}
    rewritten_ids = {el.get("data-node-id") for el in rewritten_zh}
    if original_ids != rewritten_ids:
        missing = sorted(original_ids - rewritten_ids)
        extra = sorted(rewritten_ids - original_ids)
        parts: list[str] = []
        if missing:
            parts.append(f"missing zh node_id={missing[0]}")
        if extra:
            parts.append(f"extra zh node_id={extra[0]}")
        return parts or ["zh node_id set mismatch"]

    original_by_id = {el.get("data-node-id"): el for el in original_zh}
    rewritten_by_id = {el.get("data-node-id"): el for el in rewritten_zh}

    errors: list[str] = []
    for node_id, orig_el in original_by_id.items():
        new_el = rewritten_by_id[node_id]
        if _block_text(orig_el) != _block_text(new_el):
            errors.append(f"zh text mutated node_id={node_id}")
            continue

        if _should_translate(orig_el, cfg):
            en_sib = _immediate_en_sibling(new_el)
            if en_sib is None:
                errors.append(f"missing en sibling node_id={node_id}")
            elif not _block_text(en_sib):
                errors.append(f"empty en translation node_id={node_id}")
        else:
            en_sib = _immediate_en_sibling(new_el)
            if en_sib is not None:
                errors.append(f"unexpected en sibling node_id={node_id}")

    errors.extend(_unmarked_text_errors(rewritten))
    return errors
