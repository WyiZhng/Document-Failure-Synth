from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from src.synth.config import SynthConfig
from src.synth.material import Material, _as_list
from src.synth.render import PlacedBlock
from src.synth.translation_types import TranslationBundle


def _sort_key(block: PlacedBlock) -> tuple[float, float, int]:
    return (block.bbox[1], block.bbox[0], block.order)


def _assign_ids(
    placed: list[PlacedBlock],
    page_width: float,
    reserved_ids: set[str] | None = None,
) -> dict[tuple[str, str], list[PlacedBlock]]:
    by_page: dict[int, list[PlacedBlock]] = defaultdict(list)
    for block in placed:
        by_page[block.page].append(block)

    source_ids = {block.node_id for block in placed}
    if reserved_ids:
        source_ids.update(str(value) for value in reserved_ids)
    used_ids: set[str] = set()
    mid = float(page_width) / 2.0
    mapped: dict[tuple[str, str], list[PlacedBlock]] = defaultdict(list)
    for page in sorted(by_page):
        left: list[PlacedBlock] = []
        right: list[PlacedBlock] = []
        for block in by_page[page]:
            center_x = (block.bbox[0] + block.bbox[2]) / 2.0
            (left if center_x < mid else right).append(block)
        ordered = sorted(left, key=_sort_key) + sorted(right, key=_sort_key)
        for index, block in enumerate(ordered, start=1):
            candidate_index = index
            candidate = f"p{page}-b{candidate_index}"
            while candidate in source_ids or candidate in used_ids:
                candidate_index += 1
                candidate = f"p{page}-b{candidate_index}"
            block.__dict__["new_id"] = candidate
            used_ids.add(candidate)
            mapped[(block.node_id, block.lang)].append(block)
    return mapped


def _mapped_parts(
    mapped: dict[tuple[str, str], list[PlacedBlock]],
    source_id: str,
    lang: str,
) -> list[PlacedBlock]:
    return sorted(
        mapped.get((str(source_id), lang), []),
        key=lambda block: (
            block.fragment_index,
            block.page,
            block.order,
            block.bbox[1],
            block.bbox[0],
        ),
    )


def _member_languages(
    mapped: dict[tuple[str, str], list[PlacedBlock]],
    member_id: str,
    plans: TranslationBundle | None,
) -> list[str]:
    if plans is None:
        return ["zh", "en"]
    plan = plans.plan_for(member_id)
    if plan is None:
        return [
            language
            for language in ("zh", "en")
            if (member_id, language) in mapped
        ]
    languages = [plan.source_lang]
    if (
        plan.action in {"translate", "copy"}
        and plan.target_lang in {"zh", "en"}
        and isinstance(plan.target_text, str)
        and plan.target_text.strip()
        and member_id not in plans.dropped
    ):
        languages.append(plan.target_lang)
    return languages


def _first_page(node: dict) -> int:
    pages = _as_list(node.get("page_index"))
    if pages:
        return int(pages[0])
    return 0


def _node_pages(node: dict) -> set[int]:
    return {int(page) for page in _as_list(node.get("page_index"))}


def _node_order_key(node: dict) -> tuple[int, int, float, float, str]:
    pages = sorted(_node_pages(node))
    first_page = pages[0] if pages else 0
    last_page = pages[-1] if pages else 0
    bbox = _as_list(node.get("bbox"))
    first_bbox = bbox[0] if bbox and isinstance(bbox[0], list) else []
    y1 = float(first_bbox[1]) if len(first_bbox) > 1 else float("inf")
    x1 = float(first_bbox[0]) if first_bbox else float("inf")
    return (first_page, last_page, y1, x1, str(node.get("id", "")))


def _sort_aligned_payload(
    members: list[str],
    pages: list[int],
    categories: list[str],
    bboxes: list[list[float]],
    texts: list[str],
) -> tuple[list[str], list[int], list[str], list[list[float]], list[str]]:
    """Keep every aligned array in physical page/reading order."""

    order = sorted(
        range(len(members)),
        key=lambda index: (
            pages[index],
            bboxes[index][1],
            bboxes[index][0],
            members[index],
        ),
    )
    return (
        [members[index] for index in order],
        [pages[index] for index in order],
        [categories[index] for index in order],
        [bboxes[index] for index in order],
        [texts[index] for index in order],
    )


def _split_noncontiguous_nodes(nodes: list[dict]) -> list[dict]:
    """Split a logical node whose materialized pages contain a gap.

    A single Trainer merge stream cannot replay a node present on p2 and p11
    while unrelated roots occupy p3--p10: placing that node before p3 makes the
    page ledger go backward, while placing it after p3 changes the expected
    root order. Such a gap usually comes from a dropped translation or a
    reflowed child, so keep one page-local node per occupied page instead of
    manufacturing a false cross-page merge.
    """

    output: list[dict] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node = deepcopy(node)
        node["children"] = _split_noncontiguous_nodes(
            [child for child in node.get("children") or [] if isinstance(child, dict)]
        )
        raw_pages = _as_list(node.get("page_index"))
        pages = [int(page) for page in raw_pages]
        unique_pages = sorted(set(pages))
        if (
            node.get("is_virtual")
            or len(unique_pages) <= 1
            or unique_pages == list(range(unique_pages[0], unique_pages[-1] + 1))
        ):
            output.append(node)
            continue

        members = _as_list(node.get("member"))
        categories = _as_list(node.get("category"))
        bboxes = _as_list(node.get("bbox"))
        texts = _as_list(node.get("text"))
        if not (
            len(members)
            == len(pages)
            == len(categories)
            == len(bboxes)
            == len(texts)
        ):
            # The final validator will report malformed alignment explicitly;
            # do not conceal it by attempting a lossy split.
            output.append(node)
            continue

        for page in unique_pages:
            indexes = [index for index, value in enumerate(pages) if value == page]
            split_node = deepcopy(node)
            split_node["member"] = [members[index] for index in indexes]
            split_node["page_index"] = [pages[index] for index in indexes]
            split_node["category"] = [categories[index] for index in indexes]
            split_node["bbox"] = [bboxes[index] for index in indexes]
            split_node["text"] = [texts[index] for index in indexes]
            split_node["children"] = [
                deepcopy(child)
                for child in node["children"]
                if page in _node_pages(child)
            ]
            split_node["id"] = _output_node_id(
                [str(member) for member in split_node["member"]],
                [int(value) for value in split_node["page_index"]],
            )
            output.append(split_node)
    output.sort(key=_node_order_key)
    return output


def _lift_cross_page_children(roots: list[dict]) -> None:
    """Lift children across a page boundary to a merge-stable root level.

    Trainer's page projection removes a parent when that parent is absent from
    the current page. Its merge strategy can then attach the lifted child only
    to an active ancestor. A child whose pages are not a subset of its parent
    would otherwise be attached to a branch that the previous page has
    already pruned. Keeping the child at document-root level preserves all
    node metadata while making the page-wise replay deterministic.
    """

    promoted: list[dict] = []

    def visit(parent: dict) -> bool:
        parent_pages = _node_pages(parent)
        kept: list[dict] = []
        for child in parent.get("children") or []:
            if not isinstance(child, dict):
                continue
            if not visit(child):
                continue
            child_pages = _node_pages(child)
            if child_pages and not child_pages.issubset(parent_pages):
                promoted.append(child)
            else:
                kept.append(child)
        parent["children"] = kept
        parent["children"].sort(key=_node_order_key)
        if parent.get("is_virtual") and not parent["children"]:
            if parent.get("link") or parent.get("link_to"):
                raise ValueError(
                    f"cannot keep empty linked virtual node {parent.get('id')!r}"
                )
            return False
        return True

    kept_roots: list[dict] = []
    for root in roots:
        if isinstance(root, dict):
            if visit(root):
                kept_roots.append(root)
    roots[:] = kept_roots
    roots.extend(promoted)
    roots.sort(key=_node_order_key)


def _virtual_category(node: dict) -> str:
    cat = node.get("category")
    if isinstance(cat, list):
        return str(cat[0]) if cat else "text"
    if cat:
        return str(cat)
    return "text"


def _output_node_id(members: list[str], pages: list[int]) -> str:
    """Give cross-page logical nodes the ID expected by Trainer's merge task."""

    if not members:
        raise ValueError("cannot assign an output id without members")
    if len(set(pages)) > 1:
        return f"merge-{members[0]}"
    return members[0]


def _annotation_only_node(
    source_node: dict,
    rebuilt_children: list[dict],
    *,
    path: str,
) -> dict:
    """Preserve a geometry-bearing empty relation node for OCR fill-in.

    A reference target may intentionally have empty text and no HTML block.
    It is still usable by Trainer when its member/page/bbox metadata is kept;
    only a virtual node with no materialized child is unrecoverable.
    """

    raw_members = _as_list(source_node.get("member"))
    raw_pages = _as_list(source_node.get("page_index"))
    raw_categories = _as_list(source_node.get("category"))
    raw_bboxes = _as_list(source_node.get("bbox"))
    if not raw_members:
        raise ValueError(f"cannot materialize {path}: relation node has no members")
    if not (
        len(raw_members)
        == len(raw_pages)
        == len(raw_categories)
        == len(raw_bboxes)
    ):
        raise ValueError(
            f"cannot materialize {path}: relation node metadata is not aligned"
        )

    members = [str(member) for member in raw_members]
    pages = [int(page) for page in raw_pages]
    categories = [str(category) for category in raw_categories]
    bboxes: list[list[float]] = []
    for bbox in raw_bboxes:
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError(f"cannot materialize {path}: relation node has invalid bbox")
        bboxes.append([float(value) for value in bbox])

    members, pages, categories, bboxes, texts = _sort_aligned_payload(
        members,
        pages,
        categories,
        bboxes,
        ["" for _ in members],
    )

    return {
        "id": _output_node_id(members, pages),
        "page_index": pages,
        "member": members,
        "children": rebuilt_children,
        "category": categories,
        "bbox": bboxes,
        "text": ["" for _ in members],
        "is_virtual": False,
        "link": bool(source_node.get("link")),
        "link_to": [],
    }


def _rebuild_node(
    node: dict,
    mapped: dict[tuple[str, str], list[PlacedBlock]],
    fake_counter: list[int],
    nodes_by_id: dict[str, dict],
    cache: dict[str, dict | None],
    plans: TranslationBundle | None = None,
    force_relation_only: bool = False,
) -> dict | None:
    source_id = str(node.get("id", ""))
    if source_id in cache and (cache[source_id] is not None or not force_relation_only):
        return cache[source_id]
    if source_id in cache and force_relation_only:
        cache.pop(source_id, None)

    source_node = nodes_by_id.get(source_id, node)
    rebuilt_children: list[dict] = []
    for child in source_node.get("children") or []:
        if not isinstance(child, dict):
            continue
        rebuilt = _rebuild_node(
            child,
            mapped,
            fake_counter,
            nodes_by_id,
            cache,
            plans,
            force_relation_only=force_relation_only,
        )
        if rebuilt is not None:
            rebuilt_children.append(rebuilt)
    rebuilt_children.sort(key=_node_order_key)

    if source_node.get("is_virtual"):
        if not rebuilt_children:
            if force_relation_only or source_node.get("link") or source_node.get("link_to"):
                raise ValueError(
                    f"cannot materialize empty link target virtual/source node "
                    f"{source_id!r}"
                )
            cache[source_id] = None
            return None
        page = _first_page(rebuilt_children[0])
        fake_counter[0] += 1
        rebuilt = {
            "id": f"p{page}-fake{fake_counter[0]}",
            "page_index": [page],
            "member": [],
            "children": rebuilt_children,
            "category": _virtual_category(source_node),
            "bbox": [],
            "text": "",
            "is_virtual": True,
            "link": bool(source_node.get("link")),
            "link_to": [],
        }
        cache[source_id] = rebuilt
        _rebuild_link_targets(
            rebuilt,
            source_node,
            mapped,
            fake_counter,
            nodes_by_id,
            cache,
            plans,
        )
        return rebuilt

    members: list[str] = []
    pages: list[int] = []
    bboxes: list[list[float]] = []
    categories: list[str] = []
    texts: list[str] = []

    for member_id in _as_list(source_node.get("member")):
        member_key = str(member_id)
        for language in _member_languages(mapped, member_key, plans):
            for block in _mapped_parts(mapped, member_key, language):
                members.append(str(block.__dict__["new_id"]))
                pages.append(block.page)
                bboxes.append([float(v) for v in block.bbox])
                categories.append(block.category)
                texts.append("")

    if not members:
        if rebuilt_children:
            page = _first_page(rebuilt_children[0])
            fake_counter[0] += 1
            rebuilt = {
                "id": f"p{page}-fake{fake_counter[0]}",
                "page_index": [page],
                "member": [],
                "children": rebuilt_children,
                "category": _virtual_category(source_node),
                "bbox": [],
                "text": "",
                "is_virtual": True,
                "link": bool(source_node.get("link")),
                "link_to": [],
            }
            cache[source_id] = rebuilt
            _rebuild_link_targets(
                rebuilt,
                source_node,
                mapped,
                fake_counter,
                nodes_by_id,
                cache,
                plans,
            )
            return rebuilt
        if force_relation_only or source_node.get("link") or source_node.get("link_to"):
            rebuilt = _annotation_only_node(
                source_node,
                rebuilt_children,
                path=f"node {source_id!r}",
            )
            cache[source_id] = rebuilt
            _rebuild_link_targets(
                rebuilt,
                source_node,
                mapped,
                fake_counter,
                nodes_by_id,
                cache,
                plans,
            )
            return rebuilt
        cache[source_id] = None
        return None

    members, pages, categories, bboxes, texts = _sort_aligned_payload(
        members,
        pages,
        categories,
        bboxes,
        texts,
    )

    rebuilt = {
        "id": _output_node_id(members, pages),
        "page_index": pages,
        "member": members,
        "children": rebuilt_children,
        "category": categories,
        "bbox": bboxes,
        "text": texts,
        "is_virtual": False,
        "link": bool(source_node.get("link")),
        "link_to": [],
    }
    cache[source_id] = rebuilt
    _rebuild_link_targets(
        rebuilt,
        source_node,
        mapped,
        fake_counter,
        nodes_by_id,
        cache,
        plans,
    )
    return rebuilt


def _rebuild_link_targets(
    rebuilt: dict,
    source_node: dict,
    mapped: dict[tuple[str, str], list[PlacedBlock]],
    fake_counter: list[int],
    nodes_by_id: dict[str, dict],
    cache: dict[str, dict | None],
    plans: TranslationBundle | None = None,
) -> None:
    for target in _as_list(source_node.get("link_to")):
        if not isinstance(target, dict) or not target.get("id"):
            raise ValueError(
                f"link_to on node {source_node.get('id', '?')!r} is missing target id"
            )
        target_id = str(target["id"])
        target_node = nodes_by_id.get(target_id)
        if target_node is None:
            raise ValueError(
                f"cannot rebuild link target {target_id!r} from node "
                f"{source_node.get('id', '?')!r}"
            )
        rebuilt_target = _rebuild_node(
            target_node,
            mapped,
            fake_counter,
            nodes_by_id,
            cache,
            plans,
        )
        if rebuilt_target is None:
            rebuilt_target = _rebuild_node(
                target_node,
                mapped,
                fake_counter,
                nodes_by_id,
                cache,
                plans,
                force_relation_only=True,
            )
            if rebuilt_target is None:
                raise ValueError(
                    f"cannot materialize link target {target_id!r} from node "
                    f"{source_node.get('id', '?')!r}"
                )
        rebuilt["link_to"].append(deepcopy(rebuilt_target))


def _build_label(placed: list[PlacedBlock]) -> dict:
    pages: dict[int, list[dict]] = defaultdict(list)
    ordered = sorted(
        placed,
        key=lambda block: (block.page, int(str(block.__dict__["new_id"]).split("-b")[1])),
    )
    for block in ordered:
        new_id = str(block.__dict__["new_id"])
        block_id = int(new_id.split("-b")[1])
        pages[block.page].append(
            {
                "block_id": block_id,
                "bbox": [float(v) for v in block.bbox],
                "category": block.category,
                "page_index": block.page,
            }
        )
    return {
        "pages": [
            {"page_index": page, "blocks": pages[page]} for page in sorted(pages)
        ],
        "annotator_id": "synth",
    }


def build_gt(
    material: Material,
    placed: list[PlacedBlock],
    out_dir: Path,
    cfg: SynthConfig,
    seq: int,
    origin_metadata: Mapping[str, Any] | None = None,
    plans: TranslationBundle | None = None,
) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir = (out_dir / "images_path").resolve()

    placed_source_ids = {str(block.node_id) for block in placed}
    reserved_ids = {
        str(member_id)
        for source_node in material.nodes_by_id.values()
        for member_id in _as_list(source_node.get("member"))
        if str(member_id) not in placed_source_ids
    }
    mapped = _assign_ids(
        placed,
        page_width=cfg.page.width,
        reserved_ids=reserved_ids,
    )
    fake_counter = [0]
    cache: dict[str, dict | None] = {}
    doc: list[dict] = []
    for root in material.tree:
        rebuilt = _rebuild_node(
            root,
            mapped,
            fake_counter,
            material.nodes_by_id,
            cache,
            plans,
        )
        if rebuilt is not None:
            doc.append(rebuilt)
    # Trainer's merge strategy consumes pages in the order in which they first
    # appear while traversing the main tree. Keep top-level roots in document
    # page order so a source tree with an out-of-order root cannot create a
    # false merge boundary such as p9 -> p0.
    doc = _split_noncontiguous_nodes(doc)
    _lift_cross_page_children(doc)

    origin = {
        "doc_id": f"synth_bilingual_{seq:03d}_{material.doc_id}",
        "task_id": f"synth_task_{seq:03d}",
        "pdf_path": "",
        "images_path": str(images_dir),
        "reading_direction": "horizontal",
        "document_type": material.document_type,
    }
    if origin_metadata:
        origin.update(dict(origin_metadata))
    (out_dir / "origin.json").write_text(
        json.dumps(origin, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "label.json").write_text(
        json.dumps(_build_label(placed), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "multi-page-final.json").write_text(
        json.dumps({"doc": doc}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
