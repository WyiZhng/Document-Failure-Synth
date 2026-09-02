from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
import math
import re
from collections.abc import Collection, Mapping
from typing import Any

from src.synth.config import SynthConfig
from src.synth.material import IMAGE_CATEGORIES, Material, iter_source_blocks
from src.synth.merge_compat import validate_merge_projection
from src.synth.render import PlacedBlock
from src.synth.translation_types import BlockPlan, TranslationBundle


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    stats: dict


def _is_translatable(category: str, cfg: SynthConfig) -> bool:
    return category in cfg.translate_categories and category not in IMAGE_CATEGORIES


def _is_splittable_category(category: str) -> bool:
    return category == "text"


def _english_ratio(text: str) -> float:
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return 0.0
    ascii_letters = sum(1 for c in non_ws if c.isascii() and c.isalpha())
    return ascii_letters / len(non_ws)


def _x_ranges_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    ax1, _, ax2, _ = a
    bx1, _, bx2, _ = b
    return ax1 < bx2 and bx1 < ax2


def _placed_sort_key(block: PlacedBlock) -> tuple[int, int, int, float, float]:
    return (
        block.order,
        block.fragment_index,
        block.page,
        block.bbox[1],
        block.bbox[0],
    )


def _fragment_sort_key(block: PlacedBlock) -> tuple[int, int, int, float, float]:
    return (
        block.fragment_index,
        block.page,
        block.order,
        block.bbox[1],
        block.bbox[0],
    )


def _validate_doc_legacy(
    source_tree: list[dict],
    placed: list[PlacedBlock],
    cfg: SynthConfig,
    *,
    material: Material | None = None,
) -> ValidationResult:
    errors: list[str] = []

    zh_blocks = sorted((p for p in placed if p.lang == "zh"), key=_placed_sort_key)
    en_blocks = sorted((p for p in placed if p.lang == "en"), key=_placed_sort_key)
    if material is None:
        source_blocks = [
            block
            for block in iter_source_blocks(source_tree)
            if cfg.max_source_pages is None
            or int(block["page"]) < cfg.max_source_pages
        ]
    else:
        source_blocks = [
            {
                "id": block.id,
                "page": block.page,
                "category": block.category,
                "text": block.text,
            }
            for block in material.blocks
        ]
    source_by_id = {str(b["id"]): b for b in source_blocks}

    zh_by_id: dict[str, list[PlacedBlock]] = defaultdict(list)
    for zh in zh_blocks:
        zh_by_id[zh.node_id].append(zh)
    en_by_id: dict[str, list[PlacedBlock]] = defaultdict(list)
    for en in en_blocks:
        en_by_id[en.node_id].append(en)
    for fragments in (*zh_by_id.values(), *en_by_id.values()):
        fragments.sort(key=_fragment_sort_key)

    zh_ids = [b.node_id for b in zh_blocks]
    zh_unique_ids: list[str] = []
    seen_zh_ids: set[str] = set()
    for node_id in zh_ids:
        if node_id not in seen_zh_ids:
            seen_zh_ids.add(node_id)
            zh_unique_ids.append(node_id)

    source_ids = [str(b["id"]) for b in source_blocks]
    zh_id_set = set(zh_by_id)
    source_id_set = set(source_ids)

    for node_id in sorted(source_id_set - zh_id_set):
        errors.append(f"missing zh block: {node_id}")
    for node_id in sorted(zh_id_set - source_id_set):
        errors.append(f"extra zh block: {node_id}")

    if source_id_set == zh_id_set and zh_unique_ids != source_ids:
        for idx, (zh_id, src_id) in enumerate(zip(zh_unique_ids, source_ids)):
            if zh_id != src_id:
                errors.append(
                    f"zh order mismatch at index {idx}: expected {src_id}, got {zh_id}"
                )
                break

    for node_id, zh_list in sorted(zh_by_id.items()):
        src = source_by_id.get(node_id)
        if src is None:
            continue
        if len(zh_list) > 1 and not _is_splittable_category(src["category"]):
            errors.append(f"duplicate zh block: {node_id}")
        if any(zh.category != src["category"] for zh in zh_list):
            errors.append(
                f"category mismatch for {node_id}: expected {src['category']}"
            )
        joined_text = "".join(zh.text for zh in zh_list).strip()
        if joined_text != str(src["text"]).strip():
            errors.append(f"text mismatch for {node_id}")

    for en in en_blocks:
        if en.node_id not in zh_by_id:
            errors.append(f"en block without zh: {en.node_id}")

    for node_id, zh_list in sorted(zh_by_id.items()):
        en_list = en_by_id.get(node_id, [])
        category = zh_list[0].category if zh_list else "text"

        if _is_translatable(category, cfg):
            if len(en_list) == 0:
                errors.append(f"missing en block: {node_id}")
            elif len(en_list) > 1 and not _is_splittable_category(category):
                errors.append(f"duplicate en block: {node_id}")
            else:
                joined_en = "".join(en.text for en in en_list).strip()
                if not joined_en:
                    errors.append(f"empty en text: {node_id}")
                elif _english_ratio(joined_en) <= 0.5:
                    errors.append(f"en text not sufficiently English: {node_id}")
        elif en_list:
            errors.append(f"unexpected en block for non-translatable: {node_id}")

    for block in placed:
        x1, y1, x2, y2 = block.bbox
        node_id = block.node_id
        if not (0 <= x1 < x2 <= cfg.page.width):
            errors.append(f"invalid bbox x for {node_id} ({block.lang}): {block.bbox}")
        if not (0 <= y1 < y2 <= cfg.page.height):
            errors.append(f"invalid bbox y for {node_id} ({block.lang}): {block.bbox}")

    for node_id, zh_list in sorted(zh_by_id.items()):
        en_list = en_by_id.get(node_id, [])
        if not en_list:
            continue
        for zh in zh_list:
            for en in en_list:
                if zh.page == en.page and _x_ranges_overlap(zh.bbox, en.bbox):
                    errors.append(f"zh/en x overlap on page {zh.page}: {node_id}")

    en_cross_page = sum(
        1
        for node_id, zh_list in zh_by_id.items()
        if (en_list := en_by_id.get(node_id, []))
        and max(en.page for en in en_list) > max(zh.page for zh in zh_list)
    )

    link_stats = {
        "link_count": 0,
        "unique_target_count": 0,
        "materialized_target_block_count": 0,
        "virtual_target_count": 0,
        "unresolved_link_count": 0,
    }
    if material is not None:
        relations = material.relations
        source_member_ids = set(source_by_id)
        target_ids = {relation.target_id for relation in relations}
        link_stats.update(
            {
                "link_count": len(relations),
                "unique_target_count": len(target_ids),
                "virtual_target_count": sum(
                    1
                    for target_id in target_ids
                    if material.nodes_by_id.get(target_id, {}).get("is_virtual")
                ),
            }
        )

        target_member_ids: dict[str, set[str]] = {}
        for target_id in target_ids:
            target = material.nodes_by_id.get(target_id)
            if target is None:
                target_member_ids[target_id] = set()
                continue
            target_member_ids[target_id] = {
                str(block["id"])
                for block in iter_source_blocks([target])
            }
        materialized_target_ids: set[str] = set()
        for member_ids in target_member_ids.values():
            materialized_target_ids.update(member_ids & source_member_ids)
        link_stats["materialized_target_block_count"] = len(materialized_target_ids)

        for relation in relations:
            anchor = material.nodes_by_id.get(relation.anchor_id)
            anchor_ids = (
                {
                    str(block["id"])
                    for block in iter_source_blocks([anchor])
                }
                if anchor is not None
                else set()
            )
            if not anchor_ids & source_member_ids:
                continue

            target_ids_for_relation = target_member_ids.get(relation.target_id, set())
            if not target_ids_for_relation or not target_ids_for_relation <= source_member_ids:
                errors.append(
                    f"unresolved link: {relation.anchor_id} -> {relation.target_id}"
                )
                link_stats["unresolved_link_count"] += 1

    stats = {
        "n_zh": len(zh_blocks),
        "n_en": len(en_blocks),
        "en_cross_page": en_cross_page,
        "n_errors": len(errors),
        **link_stats,
    }

    return ValidationResult(ok=not errors, errors=errors, stats=stats)


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _target_script_ratio(text: str, language: str) -> float:
    cjk = sum(1 for char in text if _CJK_RE.fullmatch(char))
    latin = sum(1 for char in text if _LATIN_RE.fullmatch(char))
    denominator = cjk + latin
    if denominator == 0:
        return 0.0
    return (cjk if language == "zh" else latin) / denominator


def _bundle_source_blocks(
    source_tree: list[dict],
    material: Material | None,
    cfg: SynthConfig,
) -> list[dict[str, Any]]:
    if material is not None:
        return [
            {
                "id": block.id,
                "page": block.page,
                "category": block.category,
                "text": block.text,
                "image_path": block.image_path,
            }
            for block in material.blocks
        ]
    return [
        block
        for block in iter_source_blocks(source_tree)
        if cfg.max_source_pages is None
        or int(block["page"]) < cfg.max_source_pages
    ]


def _visible_bundle_source(
    block: dict[str, Any],
    plan: BlockPlan | None,
    plans: TranslationBundle,
) -> bool:
    if plan is None:
        return False
    if str(block.get("text", "")).strip():
        return True
    if block.get("image_path"):
        return True
    # Geometry-bearing empty relation nodes are rendered as invisible
    # one-pixel placeholders. This gives them a layout page/bbox instead of
    # incorrectly reusing their source-document page index.
    return plan.action == "source_only"


def _validate_doc_bundle(
    source_tree: list[dict],
    placed: list[PlacedBlock],
    cfg: SynthConfig,
    *,
    material: Material | None,
    plans: TranslationBundle,
) -> ValidationResult:
    errors: list[str] = []
    source_blocks = _bundle_source_blocks(source_tree, material, cfg)
    source_by_id = {str(block["id"]): block for block in source_blocks}

    expected: list[tuple[dict[str, Any], BlockPlan]] = []
    for block in source_blocks:
        node_id = str(block["id"])
        plan = plans.plan_for(node_id)
        if not _visible_bundle_source(block, plan, plans):
            continue
        assert plan is not None
        if plan.category != str(block["category"]):
            errors.append(
                f"plan category mismatch for {node_id}: expected {block['category']}"
            )
        if plan.source_text != str(block.get("text", "")):
            errors.append(f"plan source text mismatch for {node_id}")
        expected.append((block, plan))

    expected_by_id = {str(block["id"]): (block, plan) for block, plan in expected}
    source_ids = [str(block["id"]) for block, _ in expected]
    expected_source_lang: dict[str, str] = {
        node_id: plan.source_lang for node_id, (_, plan) in expected_by_id.items()
    }
    expected_target: dict[str, tuple[str, str]] = {}
    for node_id, (_, plan) in expected_by_id.items():
        if (
            plan.action in {"translate", "copy"}
            and plan.target_lang in {"zh", "en"}
            and isinstance(plan.target_text, str)
            and plan.target_text.strip()
            and node_id not in plans.dropped
        ):
            expected_target[node_id] = (plan.target_lang, plan.target_text)

    by_lang_id: dict[tuple[str, str], list[PlacedBlock]] = defaultdict(list)
    for block in placed:
        by_lang_id[(block.node_id, block.lang)].append(block)
    for values in by_lang_id.values():
        values.sort(key=_fragment_sort_key)

    actual_source_ids: list[str] = []
    seen_source_ids: set[str] = set()
    for block in sorted(placed, key=_placed_sort_key):
        node_id = block.node_id
        expected_lang = expected_source_lang.get(node_id)
        if expected_lang == block.lang and node_id not in seen_source_ids:
            seen_source_ids.add(node_id)
            actual_source_ids.append(node_id)

    expected_source_id_set = set(expected_by_id)
    for node_id in sorted(expected_source_id_set - set(actual_source_ids)):
        lang = expected_source_lang[node_id]
        errors.append(f"missing {lang} source block: {node_id}")
    for node_id in sorted(set(actual_source_ids) - expected_source_id_set):
        errors.append(f"extra source block: {node_id}")
    if expected_source_id_set == set(actual_source_ids) and actual_source_ids != source_ids:
        for index, (actual, expected_id) in enumerate(zip(actual_source_ids, source_ids)):
            if actual != expected_id:
                errors.append(
                    f"source order mismatch at index {index}: expected {expected_id}, got {actual}"
                )
                break

    for node_id, (source, plan) in expected_by_id.items():
        source_list = by_lang_id.get((node_id, plan.source_lang), [])
        if len(source_list) > 1 and plan.category != "text":
            errors.append(f"duplicate {plan.source_lang} block: {node_id}")
        if any(block.category != plan.category for block in source_list):
            errors.append(f"category mismatch for {node_id}")
        joined_source = "".join(block.text for block in source_list).strip()
        if joined_source != plan.source_text.strip():
            errors.append(f"source text mismatch for {node_id}")

        target_lang_text = expected_target.get(node_id)
        for language in ("zh", "en"):
            if language == plan.source_lang:
                continue
            target_list = by_lang_id.get((node_id, language), [])
            if target_lang_text is None:
                if target_list:
                    errors.append(f"unexpected {language} block for {node_id}")
                continue
            target_lang, expected_text = target_lang_text
            if language != target_lang:
                continue
            if not target_list:
                errors.append(f"missing {target_lang} block: {node_id}")
                continue
            if len(target_list) > 1 and plan.category != "text":
                errors.append(f"duplicate {target_lang} block: {node_id}")
            joined_target = "".join(block.text for block in target_list).strip()
            if joined_target != expected_text.strip():
                errors.append(f"target text mismatch for {node_id}")
            if plan.action == "translate" and _target_script_ratio(joined_target, target_lang) <= 0.25:
                errors.append(
                    f"{target_lang} text not sufficiently target-language: {node_id}"
                )

    expected_pair_ids = set(expected_by_id)
    for block in placed:
        pair = expected_by_id.get(block.node_id)
        if pair is None:
            errors.append(f"extra {block.lang} block: {block.node_id}")
            continue
        _, plan = pair
        if block.lang not in {plan.source_lang, plan.target_lang}:
            errors.append(f"unexpected language for {block.node_id}: {block.lang}")

    for block in placed:
        x1, y1, x2, y2 = block.bbox
        if not (0 <= x1 < x2 <= cfg.page.width):
            errors.append(f"invalid bbox x for {block.node_id} ({block.lang}): {block.bbox}")
        if not (0 <= y1 < y2 <= cfg.page.height):
            errors.append(f"invalid bbox y for {block.node_id} ({block.lang}): {block.bbox}")

    for node_id in expected_pair_ids:
        _, plan = expected_by_id[node_id]
        if node_id not in expected_target:
            continue
        source_list = by_lang_id.get((node_id, plan.source_lang), [])
        target_lang = expected_target[node_id][0]
        target_list = by_lang_id.get((node_id, target_lang), [])
        for source in source_list:
            for target in target_list:
                if source.page == target.page and _x_ranges_overlap(source.bbox, target.bbox):
                    errors.append(f"source/target x overlap on page {source.page}: {node_id}")

    direction_cross_page = sum(
        1
        for node_id, (_, plan) in expected_by_id.items()
        if node_id in expected_target
        and (source_list := by_lang_id.get((node_id, plan.source_lang), []))
        and (target_list := by_lang_id.get((node_id, expected_target[node_id][0]), []))
        and max(block.page for block in target_list) > max(block.page for block in source_list)
    )

    link_stats = {
        "link_count": 0,
        "unique_target_count": 0,
        "materialized_target_block_count": 0,
        "virtual_target_count": 0,
        "unresolved_link_count": 0,
    }
    if material is not None:
        relations = material.relations
        visible_source_ids = set(expected_by_id)
        target_ids = {relation.target_id for relation in relations}
        link_stats.update(
            {
                "link_count": len(relations),
                "unique_target_count": len(target_ids),
                "virtual_target_count": sum(
                    1
                    for target_id in target_ids
                    if material.nodes_by_id.get(target_id, {}).get("is_virtual")
                ),
            }
        )
        target_member_ids: dict[str, set[str]] = {}
        target_raw_blocks: dict[str, list[dict[str, Any]]] = {}
        for target_id in target_ids:
            target = material.nodes_by_id.get(target_id)
            raw_blocks = list(iter_source_blocks([target])) if target else []
            target_raw_blocks[target_id] = raw_blocks
            target_member_ids[target_id] = {
                str(block["id"])
                for block in raw_blocks
                if str(block.get("text", "")).strip()
                or str(block.get("category", "")) in IMAGE_CATEGORIES
            }
        materialized_target_ids: set[str] = set()
        for member_ids in target_member_ids.values():
            materialized_target_ids.update(member_ids & visible_source_ids)
        link_stats["materialized_target_block_count"] = len(materialized_target_ids)
        for relation in relations:
            anchor = material.nodes_by_id.get(relation.anchor_id)
            anchor_ids = {
                str(block["id"])
                for block in iter_source_blocks([anchor])
            } if anchor is not None else set()
            if not anchor_ids & visible_source_ids:
                continue
            target_ids_for_relation = target_member_ids.get(relation.target_id, set())
            if not target_ids_for_relation:
                # Empty link/structural targets are valid relation-only nodes.
                continue
            if not target_ids_for_relation <= visible_source_ids:
                errors.append(f"unresolved link: {relation.anchor_id} -> {relation.target_id}")
                link_stats["unresolved_link_count"] += 1

    stats = {
        "n_zh": sum(1 for block in placed if block.lang == "zh"),
        "n_en": sum(1 for block in placed if block.lang == "en"),
        "en_cross_page": direction_cross_page,
        "translation_cross_page": direction_cross_page,
        "dropped_node_count": len(plans.dropped),
        "translation_warning_count": len(plans.warnings),
        "dropped_node_ids": list(plans.dropped),
        "translation_warnings": list(plans.warnings),
        "n_errors": len(errors),
        **link_stats,
    }
    return ValidationResult(ok=not errors, errors=errors, stats=stats)


def validate_doc(
    source_tree: list[dict],
    placed: list[PlacedBlock],
    cfg: SynthConfig,
    *,
    material: Material | None = None,
    plans: TranslationBundle | None = None,
) -> ValidationResult:
    if plans is None:
        return _validate_doc_legacy(source_tree, placed, cfg, material=material)
    return _validate_doc_bundle(
        source_tree,
        placed,
        cfg,
        material=material,
        plans=plans,
    )


def validate_page_projection(doc: list[dict]) -> list[str]:
    """镜像下游 build_single_tree.parents_for_page + validate_parent_preorder。

    双语译文跨页会造成"祖先在本页、子孙也在本页、但中间夹着父不在本页的孤儿节点"
    的三明治结构,下游按页投影树时会因越出先序而整份失败。
    在合成侧提前检出,让 runner 丢弃重试,而不是流到 pipeline 才炸。
    """
    all_pages: set[int] = set()

    def collect(node: dict) -> None:
        for page in node.get("page_index") or []:
            all_pages.add(int(page))
        for child in node.get("children") or []:
            if isinstance(child, dict):
                collect(child)

    for root in doc:
        collect(root)

    errors: list[str] = []
    for target in sorted(all_pages):
        emitted: list[tuple[str, str | None]] = []

        def visit(node: dict, parent_id: str | None) -> None:
            pages = [int(p) for p in node.get("page_index") or []]
            is_virtual = bool(node.get("is_virtual"))
            appears = (
                bool(pages) and pages[0] == target
                if is_virtual
                else target in pages
            )
            child_parent = None
            if appears:
                node_id = str(node["id"])
                emitted.append((node_id, parent_id))
                child_parent = node_id
            for child in node.get("children") or []:
                if isinstance(child, dict):
                    visit(child, child_parent)

        for root in doc:
            visit(root, None)

        seen: set[str] = set()
        active_path: list[str] = []
        for index, (node_id, parent_id) in enumerate(emitted):
            if parent_id is None:
                active_path.clear()
            else:
                if parent_id not in seen:
                    errors.append(
                        f"page {target}: parent not seen at index {index}: "
                        f"{node_id} -> {parent_id}"
                    )
                    break
                if parent_id not in active_path:
                    errors.append(
                        f"page {target}: parent leaves preorder at index {index}: "
                        f"{node_id} -> {parent_id}"
                    )
                    break
                del active_path[active_path.index(parent_id) + 1 :]
            seen.add(node_id)
            active_path.append(node_id)
    return errors


# Keep this list in sync with Trainer's link target discovery.  A target may
# be wrapped by virtual/list nodes, so validation searches the whole target
# subtree rather than requiring the wrapper itself to carry the category.
_TRAINER_TARGET_ROOT_CATEGORIES = {
    "reference",
    "table_title",
    "figure_title",
    "seal",
    "form_title",
}
_FINAL_NODE_FIELDS = (
    "id",
    "page_index",
    "member",
    "children",
    "category",
    "bbox",
    "text",
    "is_virtual",
    "link",
    "link_to",
)


def _final_error(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def _final_id(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    value = str(value).strip()
    return value or None


def _final_list(value: Any) -> list[Any] | None:
    return value if isinstance(value, list) else None


def _final_page_values(
    value: Any,
    *,
    path: str,
    rendered_pages: set[int],
    errors: list[str],
) -> list[int]:
    raw_values = _final_list(value)
    if raw_values is None:
        _final_error(errors, path, "must be a list")
        return []
    pages: list[int] = []
    for index, raw_page in enumerate(raw_values):
        if isinstance(raw_page, bool) or not isinstance(raw_page, (int, float)):
            _final_error(errors, f"{path}[{index}]", "must be an integer page index")
            continue
        try:
            page_float = float(raw_page)
        except (OverflowError, ValueError):
            _final_error(errors, f"{path}[{index}]", f"invalid page index {raw_page!r}")
            continue
        if not math.isfinite(page_float):
            _final_error(errors, f"{path}[{index}]", f"invalid page index {raw_page!r}")
            continue
        page = int(raw_page)
        if page_float != page or page < 0:
            _final_error(errors, f"{path}[{index}]", f"invalid page index {raw_page!r}")
            continue
        pages.append(page)
        if page not in rendered_pages:
            _final_error(
                errors,
                f"{path}[{index}]",
                f"page {page} has no rendered page image",
            )
    return pages


def _final_bbox(
    value: Any,
    *,
    path: str,
    page_width: float,
    page_height: float,
    errors: list[str],
) -> list[float] | None:
    raw_bbox = _final_list(value)
    if raw_bbox is None or len(raw_bbox) != 4:
        _final_error(errors, path, "bbox must contain four numbers")
        return None
    try:
        bbox = [float(item) for item in raw_bbox]
    except (TypeError, ValueError):
        _final_error(errors, path, "bbox must contain four numbers")
        return None
    if not all(math.isfinite(item) for item in bbox):
        _final_error(errors, path, "bbox contains a non-finite number")
        return None
    x1, y1, x2, y2 = bbox
    if not (0 <= x1 < x2 <= float(page_width)):
        _final_error(errors, path, f"bbox x is outside page bounds: {bbox}")
    if not (0 <= y1 < y2 <= float(page_height)):
        _final_error(errors, path, f"bbox y is outside page bounds: {bbox}")
    return bbox


def _final_categories(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _final_target_has_trainer_category(node: Mapping[str, Any]) -> bool:
    categories = _final_categories(node.get("category"))
    if set(categories) & _TRAINER_TARGET_ROOT_CATEGORIES:
        return True
    for child in node.get("children", []):
        if isinstance(child, Mapping) and _final_target_has_trainer_category(child):
            return True
    return False


def validate_final_tree(
    doc: Any,
    *,
    rendered_pages: Collection[int],
    page_width: float,
    page_height: float,
) -> list[str]:
    """Validate the JSON tree that will actually be handed to Trainer.

    ``validate_doc`` checks the HTML/material correspondence.  This validator
    deliberately runs after GT construction and checks the downstream shape:
    Trainer indexes ``children[0].bbox[0]`` for every virtual node, indexes all
    aligned arrays by ``member``, and only discovers a fixed set of link target
    categories.  Empty ``text`` values remain valid because Trainer fills them
    from OCR later.
    """

    errors: list[str] = []
    page_set: set[int] = set()
    for raw_page in rendered_pages:
        try:
            page = int(raw_page)
        except (TypeError, ValueError):
            _final_error(errors, "rendered_pages", f"invalid page index {raw_page!r}")
            continue
        if page < 0:
            _final_error(errors, "rendered_pages", f"invalid page index {raw_page!r}")
            continue
        page_set.add(page)

    if isinstance(doc, Mapping) and "doc" in doc:
        doc = doc["doc"]
    if not isinstance(doc, list):
        _final_error(errors, "doc", "must be a list")
        return errors
    if not doc:
        _final_error(errors, "doc", "must not be empty")

    main_ids: set[str] = set()
    main_members: set[str] = set()
    link_pairs: list[tuple[str, Mapping[str, Any]]] = []

    def visit(
        raw_node: Any,
        path: str,
        *,
        main_tree: bool,
        relation_path: tuple[str, ...],
    ) -> None:
        if not isinstance(raw_node, Mapping):
            _final_error(errors, path, "node must be an object")
            return
        node = raw_node
        node_id = _final_id(node.get("id"))
        if node_id is None:
            _final_error(errors, path, "node id must be a non-empty scalar")
            node_id = f"<invalid:{path}>"
        elif main_tree:
            if node_id in main_ids:
                _final_error(errors, path, f"duplicate main-tree node id {node_id!r}")
            main_ids.add(node_id)

        if not node_id.startswith("<invalid:"):
            path = f"{path}[id={node_id!r}]"

        if not main_tree and node_id in relation_path:
            _final_error(
                errors,
                path,
                f"link_to cycle detected: {' -> '.join((*relation_path, node_id))}",
            )
            return

        missing = [field for field in _FINAL_NODE_FIELDS if field not in node]
        for field in missing:
            _final_error(errors, path, f"missing field {field!r}")

        is_virtual = node.get("is_virtual")
        if not isinstance(is_virtual, bool):
            _final_error(errors, path, "is_virtual must be boolean")
            is_virtual = bool(is_virtual)
        link = node.get("link")
        if not isinstance(link, bool):
            _final_error(errors, path, "link must be boolean")
            link = bool(link)

        pages = _final_page_values(
            node.get("page_index"),
            path=f"{path}.page_index",
            rendered_pages=page_set,
            errors=errors,
        )
        members = _final_list(node.get("member"))
        children = _final_list(node.get("children"))
        categories = _final_list(node.get("category"))
        bboxes = _final_list(node.get("bbox"))
        texts = _final_list(node.get("text"))
        link_to = _final_list(node.get("link_to"))

        if members is None:
            _final_error(errors, f"{path}.member", "must be a list")
            members = []
        if children is None:
            _final_error(errors, f"{path}.children", "must be a list")
            children = []
        if bboxes is None:
            _final_error(errors, f"{path}.bbox", "must be a list")
            bboxes = []
        if link_to is None:
            _final_error(errors, f"{path}.link_to", "must be a list")
            link_to = []

        if is_virtual:
            if not pages:
                _final_error(errors, path, "virtual node must have a page_index")
            elif len(pages) != 1:
                _final_error(
                    errors,
                    path,
                    "virtual node page_index must contain exactly one page",
                )
            if not children:
                _final_error(
                    errors,
                    f"{path}.children",
                    "virtual node must have at least one child; Trainer indexes children[0]",
                )
            else:
                first = children[0]
                if not isinstance(first, Mapping):
                    _final_error(errors, f"{path}.children[0]", "node must be an object")
                elif bool(first.get("is_virtual")):
                    _final_error(
                        errors,
                        f"{path}.children[0]",
                        "first child must be a non-virtual materialized node",
                    )
                else:
                    first_pages = _final_list(first.get("page_index")) or []
                    first_page = first_pages[0] if first_pages else None
                    first_page_float: float | None = None
                    if isinstance(first_page, (int, float)) and not isinstance(
                        first_page, bool
                    ):
                        try:
                            first_page_float = float(first_page)
                        except (OverflowError, ValueError):
                            first_page_float = None
                    if (
                        pages
                        and first_page is not None
                        and (
                            isinstance(first_page, bool)
                            or not isinstance(first_page, (int, float))
                            or first_page_float is None
                            or not math.isfinite(first_page_float)
                            or int(first_page) != first_page
                            or int(first_page) != pages[0]
                        )
                    ):
                        _final_error(
                            errors,
                            f"{path}.children[0]",
                            "first child page must match virtual node page",
                        )
                    first_bboxes = _final_list(first.get("bbox")) or []
                    if not first_bboxes:
                        _final_error(
                            errors,
                            f"{path}.children[0]",
                            "first child must have a materialized bbox",
                        )
                    else:
                        _final_bbox(
                            first_bboxes[0],
                            path=f"{path}.children[0].bbox[0]",
                            page_width=page_width,
                            page_height=page_height,
                            errors=errors,
                        )
            if not isinstance(node.get("category"), (str, list)):
                _final_error(errors, f"{path}.category", "must be a string or list")
            if isinstance(node.get("category"), list) and not _final_categories(node.get("category")):
                _final_error(errors, f"{path}.category", "must not be empty")
            if not isinstance(node.get("text"), (str, list)):
                _final_error(errors, f"{path}.text", "must be a string or list")
        else:
            if not members:
                _final_error(errors, f"{path}.member", "non-virtual node must have members")
            expected = len(members)
            aligned = {
                "page_index": pages,
                "category": categories,
                "bbox": bboxes,
                "text": texts,
            }
            for field, values in aligned.items():
                if not isinstance(values, list) or len(values) != expected:
                    _final_error(
                        errors,
                        f"{path}.{field}",
                        f"aligned array length must equal member length {expected}",
                    )
            if not isinstance(node.get("category"), list):
                _final_error(errors, f"{path}.category", "must be a list")
            elif any(not str(value).strip() for value in node["category"]):
                _final_error(errors, f"{path}.category", "categories must be non-empty strings")
            if not isinstance(node.get("text"), list):
                _final_error(errors, f"{path}.text", "must be a list")
            for index, raw_member in enumerate(members):
                member_id = _final_id(raw_member)
                if member_id is None:
                    _final_error(errors, f"{path}.member[{index}]", "must be a non-empty scalar")
                elif main_tree and member_id in main_members:
                    _final_error(errors, f"{path}.member[{index}]", f"duplicate member id {member_id!r}")
                elif main_tree:
                    main_members.add(member_id)
            for index, raw_bbox in enumerate(bboxes):
                _final_bbox(
                    raw_bbox,
                    path=f"{path}.bbox[{index}]",
                    page_width=page_width,
                    page_height=page_height,
                    errors=errors,
                )

        if link and not link_to:
            _final_error(errors, path, "link=true requires at least one link_to target")
        if not link and link_to:
            _final_error(errors, path, "link_to targets require link=true")

        for index, child in enumerate(children):
            visit(
                child,
                f"{path}.children[{index}]",
                main_tree=main_tree,
                relation_path=relation_path,
            )

        for index, target in enumerate(link_to):
            target_path = f"{path}.link_to[{index}]"
            if not isinstance(target, Mapping):
                _final_error(errors, target_path, "target must be an object")
                continue
            target_id = _final_id(target.get("id"))
            if target_id is None:
                _final_error(errors, target_path, "target id must be non-empty")
            else:
                link_pairs.append((node_id, target))
            visit(
                target,
                target_path,
                main_tree=False,
                relation_path=(*relation_path, node_id),
            )

    for index, root in enumerate(doc):
        visit(root, f"doc[{index}]", main_tree=True, relation_path=())

    for anchor_id, target in link_pairs:
        target_id = _final_id(target.get("id")) or "<invalid>"
        if not _final_target_has_trainer_category(target):
            _final_error(
                errors,
                f"link {anchor_id!r} -> {target_id!r}",
                "target has no Trainer-supported target category",
            )

    return errors


def validate_final_case(
    doc: Any,
    *,
    rendered_pages: Collection[int],
    cfg: SynthConfig,
) -> ValidationResult:
    """Compose final-tree, page-ledger, projection, and link validation."""

    raw_rendered_pages = list(rendered_pages)
    pages_set: set[int] = set()
    for raw_page in raw_rendered_pages:
        if isinstance(raw_page, bool):
            continue
        try:
            page_float = float(raw_page)
            page = int(raw_page)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(page_float) and page_float == page and page >= 0:
            pages_set.add(page)
    pages = sorted(pages_set)
    errors = validate_final_tree(
        doc,
        rendered_pages=raw_rendered_pages,
        page_width=cfg.page.width,
        page_height=cfg.page.height,
    )
    ledger_errors: list[str] = []
    if pages and pages != list(range(pages[-1] + 1)):
        ledger_errors.append(f"rendered page ledger is not contiguous: {pages}")
    errors.extend(ledger_errors)

    projection_errors: list[str] = []
    if isinstance(doc, Mapping) and "doc" in doc:
        projection_doc = doc.get("doc")
    else:
        projection_doc = doc
    if isinstance(projection_doc, list):
        try:
            projection_errors = validate_page_projection(projection_doc)
        except (KeyError, TypeError, ValueError) as exc:
            projection_errors = [f"final tree projection could not be evaluated: {exc}"]
    errors.extend(projection_errors)

    merge_errors: list[str] = []
    if isinstance(projection_doc, list):
        merge_errors = validate_merge_projection(projection_doc)
    errors.extend(merge_errors)

    return ValidationResult(
        ok=not errors,
        errors=errors,
        stats={
            "rendered_page_count": len(pages),
            "rendered_pages": pages,
            "final_tree_error_count": (
                len(errors)
                - len(ledger_errors)
                - len(projection_errors)
                - len(merge_errors)
            ),
            "page_ledger_error_count": len(ledger_errors),
            "projection_error_count": len(projection_errors),
            "merge_error_count": len(merge_errors),
            "n_errors": len(errors),
        },
    )
