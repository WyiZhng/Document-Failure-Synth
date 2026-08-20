from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from PIL import Image

from src.synth.config import SynthConfig

FILLIN_FILENAME = "multi-page-final-fillin.json"
ORIGIN_FILENAME = "origin.json"
IMAGE_CATEGORIES = frozenset({"image", "chart", "seal", "table"})


@dataclass
class SourceBlock:
    id: str
    page: int
    category: str
    text: str
    image_path: str | None


@dataclass
class Material:
    doc_id: str
    document_type: str
    blocks: list[SourceBlock]
    tree: list[dict]
    assets_dir: Path


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def load_fillin_tree(case_dir: Path) -> list[dict]:
    path = case_dir / FILLIN_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "doc" in payload:
        payload = payload["doc"]
    if not isinstance(payload, list):
        raise ValueError(f"unexpected fillin tree format: {path}")
    return payload


def _assert_no_link_to(nodes: list[dict]) -> None:
    def walk(node: dict) -> None:
        link_to = node.get("link_to") or []
        if link_to:
            node_id = node.get("id", "?")
            raise ValueError(f"case contains non-empty link_to on node {node_id!r}")
        for child in node.get("children") or []:
            if isinstance(child, dict):
                walk(child)

    for root in nodes:
        if isinstance(root, dict):
            walk(root)


def iter_source_blocks(nodes: list[dict]) -> Iterator[dict[str, Any]]:
    def walk(node: dict) -> Iterator[dict[str, Any]]:
        if not isinstance(node, dict):
            return
        members = _as_list(node.get("member"))
        pages = _as_list(node.get("page_index"))
        cats = _as_list(node.get("category"))
        texts = node.get("text")
        if isinstance(texts, str):
            texts = [texts]
        texts = _as_list(texts)
        bboxes = _as_list(node.get("bbox"))

        if members and not node.get("is_virtual"):
            for index, member_id in enumerate(members):
                category = str(cats[index] if index < len(cats) else cats[-1] if cats else "text")
                text = texts[index] if index < len(texts) else ""
                text = "" if category in IMAGE_CATEGORIES else str(text).strip()
                page = int(pages[index] if index < len(pages) else pages[0] if pages else 0)
                bbox = bboxes[index] if index < len(bboxes) else (bboxes[-1] if bboxes else None)
                yield {
                    "id": str(member_id),
                    "page": page,
                    "category": category,
                    "text": text,
                    "bbox": bbox,
                }

        for child in node.get("children") or []:
            if isinstance(child, dict):
                yield from walk(child)

    for root in nodes:
        if isinstance(root, dict):
            yield from walk(root)


def _safe_image_stem(block_id: str) -> str:
    safe = block_id.replace("/", "_").replace("\\", "_")
    for ch in (":", "*", "?", '"', "<", ">", "|"):
        safe = safe.replace(ch, "_")
    return safe


def _denorm_bbox(bbox: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = (float(v) for v in bbox[:4])
    left = int(round(x1 / 1000 * width))
    top = int(round(y1 / 1000 * height))
    right = int(round(x2 / 1000 * width))
    bottom = int(round(y2 / 1000 * height))
    return left, top, right, bottom


def _crop_image_block(case_dir: Path, assets_dir: Path, block: dict[str, Any]) -> str:
    page = int(block["page"])
    page_path = case_dir / "images_path" / f"raw-page-{page + 1}.png"
    if not page_path.is_file():
        raise FileNotFoundError(f"missing source page image: {page_path}")

    bbox = block.get("bbox")
    if not bbox or len(bbox) < 4:
        raise ValueError(f"missing bbox for image block {block['id']!r}")

    with Image.open(page_path) as img:
        width, height = img.size
        crop_box = _denorm_bbox(bbox, width, height)
        cropped = img.crop(crop_box)
        out_path = assets_dir / f"img_{_safe_image_stem(block['id'])}.png"
        cropped.save(out_path)

    return str(out_path)


def load_material(case_dir: Path, cfg: SynthConfig, assets_dir: Path) -> Material:
    case_dir = Path(case_dir)
    assets_dir = Path(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)

    tree = load_fillin_tree(case_dir)
    _assert_no_link_to(tree)

    origin = json.loads((case_dir / ORIGIN_FILENAME).read_text(encoding="utf-8"))
    doc_id = str(origin["doc_id"])
    document_type = str(origin["document_type"])

    blocks: list[SourceBlock] = []
    for raw in iter_source_blocks(tree):
        page = int(raw["page"])
        if page >= cfg.max_source_pages:
            continue

        image_path: str | None = None
        if raw["category"] in IMAGE_CATEGORIES:
            image_path = _crop_image_block(case_dir, assets_dir, raw)

        blocks.append(
            SourceBlock(
                id=raw["id"],
                page=page,
                category=raw["category"],
                text=raw["text"],
                image_path=image_path,
            )
        )

    return Material(
        doc_id=doc_id,
        document_type=document_type,
        blocks=blocks,
        tree=tree,
        assets_dir=assets_dir,
    )
