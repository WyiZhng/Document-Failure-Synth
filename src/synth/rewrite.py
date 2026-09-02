from __future__ import annotations

import json
import os
import re
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from typing import Any, Sequence

from bs4 import BeautifulSoup, NavigableString, Tag

from src.synth.config import SynthConfig
from src.synth.material import IMAGE_CATEGORIES, Material, SourceBlock
from src.synth.translation_types import (
    BlockPlan,
    Language,
    TranslationBundle,
)


STRUCTURED_TRANSLATION_PROMPT = """Translate each structured record independently from its declared source_lang to its declared target_lang.

Rules:
- Return JSON only. Do not return HTML, Markdown, explanations, or records that were not requested.
- Return at most one object for each requested node_id.
- Keep names, numeric values, identifiers, formulas, and codes unchanged when the record requires copying them.
- The source-side fields are immutable. Use the requested node_id, source_lang, target_lang, and source_text exactly as provided.
- Each translation object must contain node_id, source_lang, target_lang, and translation.

Records:
"""

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_PURE_NUMBER_RE = re.compile(r"^[\d\s.,:/()+\-=%#]+$")
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_./:+#()\-]*$")


def _opposite_language(language: Language) -> Language:
    return "en" if language == "zh" else "zh"


def _script_counts(text: str) -> tuple[int, int]:
    return (
        sum(1 for char in text if _CJK_RE.fullmatch(char)),
        sum(1 for char in text if _LATIN_RE.fullmatch(char)),
    )


def _infer_text_language(text: str, fallback: Language) -> Language:
    cjk, latin = _script_counts(text)
    if cjk > latin:
        return "zh"
    if latin > cjk:
        return "en"
    if cjk:
        return "zh"
    if latin:
        return "en"
    return fallback


def infer_default_language(blocks: Sequence[SourceBlock]) -> Language:
    """Infer the document fallback language from readable source blocks."""

    counts: Counter[Language] = Counter()
    for block in blocks:
        if not str(block.text).strip():
            continue
        language = _infer_text_language(str(block.text), "zh")
        cjk, latin = _script_counts(str(block.text))
        if cjk or latin:
            counts[language] += max(cjk, latin, 1)
    if counts["en"] > counts["zh"]:
        return "en"
    return "zh"


def is_neutral_text(text: str, category: str) -> bool:
    """Return whether a block can be copied without an LLM translation call."""

    value = str(text).strip()
    if not value:
        return False
    category_key = str(category).strip().lower().replace("-", "_")
    cjk, latin = _script_counts(value)
    if _PURE_NUMBER_RE.fullmatch(value):
        return True
    if cjk:
        # A readable label such as ``日期：2024`` is translatable even when its
        # category is date/id/signature. Pure CJK names are not classified as
        # neutral because the model may need to translate the surrounding label.
        return False
    if category_key in {
        "formula",
        "equation",
        "math",
        "code",
        "identifier",
        "serial_number",
        "patent_number",
        "numeric",
        "number",
        "id",
    }:
        return True
    if _CODE_RE.fullmatch(value) and (latin or any(char.isdigit() for char in value)):
        return True
    return False


def split_translation_batches(
    blocks: Sequence[SourceBlock],
    max_chars: int,
) -> list[list[SourceBlock]]:
    """Split source blocks by page and character budget without splitting blocks."""

    if max_chars <= 0:
        raise ValueError("max_chars must be greater than 0")

    batches: list[list[SourceBlock]] = []
    current: list[SourceBlock] = []
    current_page: int | None = None
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_page, current_chars
        if current:
            batches.append(current)
        current = []
        current_page = None
        current_chars = 0

    for block in blocks:
        block_page = int(block.page)
        block_chars = max(1, len(str(block.text)))
        if current and block_page != current_page:
            flush()
        if current and current_chars + block_chars > max_chars:
            flush()
        current_page = block_page if current_page is None else current_page
        current.append(block)
        current_chars += block_chars
    flush()
    return batches


def _relation_only_block(material: Material, block: SourceBlock) -> bool:
    if block.text.strip():
        return False
    if any(relation.target_id == block.id for relation in material.relations):
        return True
    for node in material.nodes_by_id.values():
        member_ids = node.get("member") or []
        if not isinstance(member_ids, list):
            member_ids = [member_ids]
        if block.id not in {str(item) for item in member_ids}:
            continue
        if node.get("link") or node.get("link_to") or node.get("children"):
            return True
    return False


def _source_plan(
    block: SourceBlock,
    source_lang: Language,
    *,
    action: str,
    target_text: str | None = None,
) -> BlockPlan:
    target_lang: Language | None = (
        _opposite_language(source_lang)
        if action in {"translate", "copy"}
        else None
    )
    return BlockPlan(
        node_id=block.id,
        category=block.category,
        source_text=block.text,
        source_lang=source_lang,
        target_lang=target_lang,
        action=action,  # type: ignore[arg-type]
        target_text=target_text,
    )


def _openai_client(cfg: SynthConfig) -> Any:
    from openai import OpenAI

    return OpenAI(
        api_key=os.environ[cfg.llm.api_key_env],
        base_url=os.environ.get(cfg.llm.base_url_env) or None,
        timeout=300,
    )


def _response_content(response: Any) -> str:
    raw = response.choices[0].message.content
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif hasattr(item, "text") and isinstance(item.text, str):
                parts.append(item.text)
        return "".join(parts)
    return ""


def _strip_json_fences(text: str) -> str:
    value = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", value, re.S | re.I)
    return match.group(1).strip() if match else value


def _response_records(raw: str) -> list[dict[str, Any]]:
    payload = json.loads(_strip_json_fences(raw))
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = None
        for key in ("translations", "results", "items", "records"):
            if isinstance(payload.get(key), list):
                records = payload[key]
                break
        if records is None and all(isinstance(value, dict) for value in payload.values()):
            records = [
                {"node_id": str(node_id), **value}
                for node_id, value in payload.items()
            ]
        if records is None:
            raise ValueError("translation response must contain a records list")
    else:
        raise ValueError("translation response must be a JSON list or object")
    if not all(isinstance(item, dict) for item in records):
        raise ValueError("translation response contains a non-object record")
    return [dict(item) for item in records]


def _has_target_script(text: str, language: Language) -> bool:
    cjk, latin = _script_counts(text)
    denominator = cjk + latin
    if denominator == 0:
        return False
    target_count = cjk if language == "zh" else latin
    return target_count / denominator > 0.25


def _validate_translation_response(
    raw: str,
    records: Sequence[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Return successes, per-node failures, and non-node warnings."""

    expected = {str(record["node_id"]): record for record in records}
    successes: dict[str, str] = {}
    failures: dict[str, str] = {}
    warnings: list[str] = []
    try:
        response_records = _response_records(raw)
    except Exception as exc:
        reason = f"invalid JSON response: {exc}"
        return {}, {node_id: reason for node_id in expected}, warnings

    seen: set[str] = set()
    for item in response_records:
        node_id = str(item.get("node_id", "")).strip()
        if node_id not in expected:
            warnings.append(f"unknown node_id={node_id or '<missing>'}")
            continue
        if node_id in seen:
            failures[node_id] = "duplicate node_id in translation response"
            successes.pop(node_id, None)
            continue
        seen.add(node_id)
        record = expected[node_id]
        if item.get("source_lang") != record["source_lang"]:
            failures[node_id] = "source language changed in translation response"
            continue
        if item.get("target_lang") != record["target_lang"]:
            failures[node_id] = "wrong target language in translation response"
            continue
        if "category" in item and item["category"] != record["category"]:
            failures[node_id] = "source category changed in translation response"
            continue
        for field in ("source_text", "text"):
            if field in item and item[field] != record["source_text"]:
                failures[node_id] = f"source text changed in translation response"
                break
        if node_id in failures:
            continue
        translation = item.get("translation")
        if not isinstance(translation, str) or not translation.strip():
            failures[node_id] = "empty translation"
            continue
        if not _has_target_script(translation, record["target_lang"]):
            failures[node_id] = "translation does not contain target-language script"
            continue
        successes[node_id] = translation

    for node_id in expected:
        if node_id not in seen:
            failures.setdefault(node_id, "missing node_id in translation response")
    return successes, failures, warnings


def _translation_record(block: SourceBlock, plan: BlockPlan) -> dict[str, Any]:
    return {
        "node_id": block.id,
        "category": block.category,
        "source_text": block.text,
        "text": block.text,
        "source_lang": plan.source_lang,
        "target_lang": plan.target_lang,
        "action": plan.action,
    }


def _structured_translation_prompt(
    records: Sequence[dict[str, Any]],
    failure_reasons: dict[str, list[str]] | None = None,
) -> str:
    prompt = STRUCTURED_TRANSLATION_PROMPT
    if failure_reasons:
        prompt = prompt.rsplit("Records:\n", 1)[0]
        prompt += (
            "\nThis is the single retry for the failed records below. "
            "Return only corrected records for these node_id values. "
            "Address the validation errors listed here:\n"
        )
        for node_id in (str(record["node_id"]) for record in records):
            reasons = failure_reasons.get(node_id) or ["previous response failed validation"]
            prompt += f"- {node_id}: {'; '.join(reasons)}\n"
        prompt += "\nRecords:\n"
    return prompt + json.dumps(records, ensure_ascii=False)


def translate_material(
    material: Material,
    cfg: SynthConfig,
    client: Any | None = None,
    seed: int | None = None,
) -> TranslationBundle:
    """Translate material blocks in bounded structured batches.

    Translation failures are deliberately represented in the returned bundle;
    they never become a source-level exception.
    """

    default_lang = infer_default_language(material.blocks)
    plans: dict[str, BlockPlan] = {}
    dropped: dict[str, str] = {}
    warnings: list[dict[str, Any]] = []
    candidates: list[SourceBlock] = []

    for block in material.blocks:
        source_lang = _infer_text_language(block.text, default_lang) if block.text else default_lang
        if not block.text.strip():
            if block.image_path or _relation_only_block(material, block):
                plans[block.id] = _source_plan(
                    block,
                    source_lang,
                    action="source_only",
                )
            else:
                dropped[block.id] = "empty leaf block"
            continue
        if block.image_path or block.category in IMAGE_CATEGORIES:
            plans[block.id] = _source_plan(block, source_lang, action="source_only")
            continue
        if block.category not in cfg.translate_categories:
            plans[block.id] = _source_plan(block, source_lang, action="source_only")
            continue
        if is_neutral_text(block.text, block.category):
            plans[block.id] = _source_plan(
                block,
                source_lang,
                action="copy",
                target_text=block.text,
            )
            continue
        plans[block.id] = _source_plan(block, source_lang, action="translate")
        candidates.append(block)

    if not candidates:
        return TranslationBundle(plans=plans, dropped=dropped, warnings=warnings)

    llm_client = client
    model = os.environ.get(cfg.llm.model_env, "")
    plans_by_id = {block.id: plans[block.id] for block in candidates}
    for batch in split_translation_batches(candidates, cfg.llm.batch_max_chars):
        pending = list(batch)
        failure_history: dict[str, list[str]] = {block.id: [] for block in batch}
        unknown_warnings: list[tuple[int, str]] = []

        for attempt in (1, 2):
            records = [_translation_record(block, plans_by_id[block.id]) for block in pending]
            try:
                if llm_client is None:
                    llm_client = _openai_client(cfg)
                response = llm_client.chat.completions.create(
                    model=model,
                    temperature=cfg.llm.temperature,
                    messages=[
                        {
                            "role": "system",
                            "content": "You return only validated structured translation JSON.",
                        },
                        {
                            "role": "user",
                            "content": _structured_translation_prompt(
                                records,
                                failure_history if attempt == 2 else None,
                            ),
                        },
                    ],
                    extra_body=(
                        {"enable_thinking": False, "seed": seed}
                        if seed is not None
                        else {"enable_thinking": False}
                    ),
                )
                raw = _response_content(response)
                successes, failures, response_warnings = _validate_translation_response(
                    raw, records
                )
            except Exception as exc:
                successes = {}
                failures = {block.id: f"translation API error: {exc}" for block in pending}
                response_warnings = []
            unknown_warnings.extend((attempt, warning) for warning in response_warnings)

            for node_id, translation in successes.items():
                plans[node_id] = replace(plans_by_id[node_id], target_text=translation)
            for node_id, reason in failures.items():
                failure_history[node_id].append(reason)

            if not failures:
                pending = []
                break
            pending = [block for block in pending if block.id in failures]
            if attempt == 2:
                for block in pending:
                    node_id = block.id
                    reason = "; ".join(failure_history[node_id]) or "translation failed"
                    dropped[node_id] = reason
                    warnings.append(
                        {
                            "node_id": node_id,
                            "category": block.category,
                            "reason": reason,
                            "attempts": 2,
                            "dropped": True,
                            "relation_only": _relation_only_block(material, block),
                        }
                    )
                pending = []

        for attempts, unknown in unknown_warnings:
            warnings.append(
                {
                    "node_id": None,
                    "category": None,
                    "reason": unknown,
                    "attempts": attempts,
                    "dropped": False,
                    "relation_only": False,
                }
            )

    return TranslationBundle(plans=plans, dropped=dropped, warnings=warnings)

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
