from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from src.synth.config import SynthConfig
from src.synth.material import Material, _as_list
from src.synth.render import PlacedBlock


def _sort_key(block: PlacedBlock) -> tuple[float, float, int]:
    return (block.bbox[1], block.bbox[0], block.order)


def _assign_ids(placed: list[PlacedBlock]) -> dict[tuple[str, str], PlacedBlock]:
    by_page: dict[int, dict[str, list[PlacedBlock]]] = defaultdict(
        lambda: {"zh": [], "en": []}
    )
    for block in placed:
        lang = block.lang if block.lang in {"zh", "en"} else "zh"
        by_page[block.page][lang].append(block)

    mapped: dict[tuple[str, str], PlacedBlock] = {}
    for page in sorted(by_page):
        ordered: list[PlacedBlock] = []
        for lang in ("zh", "en"):
            ordered.extend(sorted(by_page[page][lang], key=_sort_key))
        for index, block in enumerate(ordered, start=1):
            block.__dict__["new_id"] = f"p{page}-b{index}"
            mapped[(block.node_id, block.lang)] = block
    return mapped


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
    mapped: dict[tuple[str, str], PlacedBlock],
    fake_counter: list[int],
) -> dict | None:
    rebuilt_children: list[dict] = []
    for child in node.get("children") or []:
        if not isinstance(child, dict):
            continue
        rebuilt = _rebuild_node(child, mapped, fake_counter)
        if rebuilt is not None:
            rebuilt_children.append(rebuilt)

    if node.get("is_virtual"):
        if not rebuilt_children:
            return None
        page = _first_page(rebuilt_children[0])
        fake_counter[0] += 1
        return {
            "id": f"p{page}-fake{fake_counter[0]}",
            "page_index": [page],
            "member": [],
            "children": rebuilt_children,
            "category": _virtual_category(node),
            "bbox": [],
            "text": "",
            "is_virtual": True,
            "link": False,
            "link_to": [],
        }

    members: list[str] = []
    pages: list[int] = []
    bboxes: list[list[float]] = []
    categories: list[str] = []
    texts: list[str] = []

    for member_id in _as_list(node.get("member")):
        zh = mapped.get((str(member_id), "zh"))
        if zh is None:
            continue
        members.append(str(zh.__dict__["new_id"]))
        pages.append(zh.page)
        bboxes.append([float(v) for v in zh.bbox])
        categories.append(zh.category)
        texts.append("")
        en = mapped.get((str(member_id), "en"))
        if en is not None:
            members.append(str(en.__dict__["new_id"]))
            pages.append(en.page)
            bboxes.append([float(v) for v in en.bbox])
            categories.append(en.category)
            texts.append("")

    if not members:
        if rebuilt_children:
            page = _first_page(rebuilt_children[0])
            fake_counter[0] += 1
            return {
                "id": f"p{page}-fake{fake_counter[0]}",
                "page_index": [page],
                "member": [],
                "children": rebuilt_children,
                "category": _virtual_category(node),
                "bbox": [],
                "text": "",
                "is_virtual": True,
                "link": False,
                "link_to": [],
            }
        return None

    return {
        "id": members[0],
        "page_index": pages,
        "member": members,
        "children": rebuilt_children,
        "category": categories,
        "bbox": bboxes,
        "text": texts,
        "is_virtual": False,
        "link": False,
        "link_to": [],
    }


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
) -> None:
    del cfg
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir = (out_dir / "images_path").resolve()

    mapped = _assign_ids(placed)
    fake_counter = [0]
    doc: list[dict] = []
    for root in material.tree:
        rebuilt = _rebuild_node(root, mapped, fake_counter)
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
