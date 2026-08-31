from __future__ import annotations

from dataclasses import dataclass
from collections import Counter

from src.synth.config import SynthConfig
from src.synth.material import IMAGE_CATEGORIES, Material, iter_source_blocks
from src.synth.render import PlacedBlock


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    stats: dict


def _is_translatable(category: str, cfg: SynthConfig) -> bool:
    return category in cfg.translate_categories and category not in IMAGE_CATEGORIES


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


def validate_doc(
    source_tree: list[dict],
    placed: list[PlacedBlock],
    cfg: SynthConfig,
    *,
    material: Material | None = None,
) -> ValidationResult:
    errors: list[str] = []

    zh_blocks = sorted((p for p in placed if p.lang == "zh"), key=lambda p: p.order)
    en_blocks = [p for p in placed if p.lang == "en"]
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
    source_by_id = {b["id"]: b for b in source_blocks}

    zh_ids = [b.node_id for b in zh_blocks]
    source_ids = [b["id"] for b in source_blocks]
    zh_id_set = set(zh_ids)
    source_id_set = set(source_ids)

    for node_id in sorted(source_id_set - zh_id_set):
        errors.append(f"missing zh block: {node_id}")
    for node_id in sorted(zh_id_set - source_id_set):
        errors.append(f"extra zh block: {node_id}")

    for node_id, count in sorted(Counter(zh_ids).items()):
        if count > 1:
            errors.append(f"duplicate zh block: {node_id}")

    if source_id_set == zh_id_set and zh_ids != source_ids:
        for idx, (zh, src) in enumerate(zip(zh_blocks, source_blocks)):
            if zh.node_id != src["id"]:
                errors.append(
                    f"zh order mismatch at index {idx}: expected {src['id']}, got {zh.node_id}"
                )
                break

    for zh in zh_blocks:
        src = source_by_id.get(zh.node_id)
        if src is None:
            continue
        if zh.category != src["category"]:
            errors.append(
                f"category mismatch for {zh.node_id}: expected {src['category']}, got {zh.category}"
            )
        if zh.text.strip() != src["text"]:
            errors.append(f"text mismatch for {zh.node_id}")

    en_by_id: dict[str, list[PlacedBlock]] = {}
    for en in en_blocks:
        en_by_id.setdefault(en.node_id, []).append(en)

    zh_by_id = {b.node_id: b for b in zh_blocks}

    for en in en_blocks:
        if en.node_id not in zh_by_id:
            errors.append(f"en block without zh: {en.node_id}")

    for zh in zh_blocks:
        node_id = zh.node_id
        en_list = en_by_id.get(node_id, [])

        if _is_translatable(zh.category, cfg):
            if len(en_list) == 0:
                errors.append(f"missing en block: {node_id}")
            elif len(en_list) > 1:
                errors.append(f"duplicate en block: {node_id}")
            else:
                en = en_list[0]
                if not en.text.strip():
                    errors.append(f"empty en text: {node_id}")
                elif _english_ratio(en.text) <= 0.5:
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

    for zh in zh_blocks:
        en_list = en_by_id.get(zh.node_id, [])
        if not en_list:
            continue
        en = en_list[0]
        if en.page != zh.page:
            errors.append(
                f"zh/en page split for {zh.node_id}: zh={zh.page} en={en.page}"
            )
        if zh.page == en.page and _x_ranges_overlap(zh.bbox, en.bbox):
            errors.append(f"zh/en x overlap on page {zh.page}: {zh.node_id}")

    en_cross_page = sum(
        1
        for zh in zh_blocks
        if (en_list := en_by_id.get(zh.node_id, [])) and en_list[0].page > zh.page
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
