from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from src.synth.config import SynthConfig
from src.synth.material import Material, _as_list
from src.synth.render import PlacedBlock


def _sort_key(block: PlacedBlock) -> tuple[float, float, int]:
    return (block.bbox[1], block.bbox[0], block.order)


def _assign_ids(
    placed: list[PlacedBlock],
    page_width: float,
) -> dict[tuple[str, str], list[PlacedBlock]]:
    by_page: dict[int, list[PlacedBlock]] = defaultdict(list)
    for block in placed:
        by_page[block.page].append(block)

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
            block.__dict__["new_id"] = f"p{page}-b{index}"
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


def _first_page(node: dict) -> int:
    pages = _as_list(node.get("page_index"))
    if pages:
        return int(pages[0])
    return 0


def _virtual_category(node: dict) -> str:
    cat = node.get("category")
    if isinstance(cat, list):
        return str(cat[0]) if cat else "text"
    if cat:
        return str(cat)
    return "text"


def _rebuild_node(
    node: dict,
    mapped: dict[tuple[str, str], list[PlacedBlock]],
    fake_counter: list[int],
    nodes_by_id: dict[str, dict],
    cache: dict[str, dict | None],
) -> dict | None:
    source_id = str(node.get("id", ""))
    if source_id in cache:
        return cache[source_id]

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
        )
        if rebuilt is not None:
            rebuilt_children.append(rebuilt)

    if source_node.get("is_virtual"):
        if not rebuilt_children:
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
        )
        return rebuilt

    members: list[str] = []
    pages: list[int] = []
    bboxes: list[list[float]] = []
    categories: list[str] = []
    texts: list[str] = []

    for member_id in _as_list(source_node.get("member")):
        zh_parts = _mapped_parts(mapped, str(member_id), "zh")
        if not zh_parts:
            continue
        for zh in zh_parts:
            members.append(str(zh.__dict__["new_id"]))
            pages.append(zh.page)
            bboxes.append([float(v) for v in zh.bbox])
            categories.append(zh.category)
            texts.append("")

        for en in _mapped_parts(mapped, str(member_id), "en"):
            members.append(str(en.__dict__["new_id"]))
            pages.append(en.page)
            bboxes.append([float(v) for v in en.bbox])
            categories.append(en.category)
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
            )
            return rebuilt
        cache[source_id] = None
        return None

    rebuilt = {
        "id": members[0],
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
    )
    return rebuilt


def _rebuild_link_targets(
    rebuilt: dict,
    source_node: dict,
    mapped: dict[tuple[str, str], list[PlacedBlock]],
    fake_counter: list[int],
    nodes_by_id: dict[str, dict],
    cache: dict[str, dict | None],
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
        )
        if rebuilt_target is None:
            raise ValueError(
                f"cannot rebuild empty link target {target_id!r} from node "
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
) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir = (out_dir / "images_path").resolve()

    mapped = _assign_ids(placed, page_width=cfg.page.width)
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
        )
        if rebuilt is not None:
            doc.append(rebuilt)

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
