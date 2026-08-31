from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class LinkRelation:
    anchor_id: str
    target_id: str


@dataclass
class LinkGraph:
    nodes_by_id: dict[str, dict]
    relations: list[LinkRelation]
    source_blocks: list[dict[str, Any]]


@dataclass
class Material:
    doc_id: str
    document_type: str
    blocks: list[SourceBlock]
    tree: list[dict]
    assets_dir: Path
    nodes_by_id: dict[str, dict] = field(default_factory=dict)
    relations: list[LinkRelation] = field(default_factory=list)


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


_NODE_LAYOUT_FIELDS = ("page_index", "member", "category", "bbox", "is_virtual")


def _node_id(node: Any, *, context: str) -> str:
    if not isinstance(node, dict):
        raise ValueError(f"{context} must be an object")
    raw_id = node.get("id")
    if raw_id is None or not str(raw_id).strip():
        raise ValueError(f"{context} is missing id")
    return str(raw_id)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return value


def _field_value(field: str, value: Any) -> Any:
    if field in {"page_index", "member"}:
        return tuple(str(item) for item in _as_list(value))
    if field == "category":
        return tuple(str(item) for item in _as_list(value))
    if field == "is_virtual":
        return bool(value)
    return _freeze(value)


def _check_layout_compatibility(
    existing: dict,
    incoming: dict,
    *,
    node_id: str,
    context: str,
) -> None:
    for field in _NODE_LAYOUT_FIELDS:
        if field not in existing or field not in incoming:
            continue
        if existing[field] is None or incoming[field] is None:
            continue
        if _field_value(field, existing[field]) != _field_value(field, incoming[field]):
            raise ValueError(
                f"conflicting {context} node {node_id!r}: field {field} differs"
            )


def _merge_link_snapshots(existing: dict, incoming: dict, *, node_id: str) -> bool:
    changed = False
    existing_links = _as_list(existing.get("link_to"))
    incoming_links = _as_list(incoming.get("link_to"))
    by_target: dict[str, dict] = {}
    for link in existing_links:
        target_id = _node_id(link, context=f"link_to on node {node_id!r}")
        by_target[target_id] = link

    for link in incoming_links:
        target_id = _node_id(link, context=f"link_to on node {node_id!r}")
        previous = by_target.get(target_id)
        if previous is not None:
            _check_layout_compatibility(
                previous,
                link,
                node_id=target_id,
                context=f"link_to target from {node_id!r}",
            )
            continue
        existing_links.append(deepcopy(link))
        by_target[target_id] = existing_links[-1]
        changed = True

    existing["link_to"] = existing_links
    return changed


def collect_link_graph(tree: list[dict]) -> LinkGraph:
    """Normalize children and link_to snapshots into one logical node graph.

    ``children`` remains the source document hierarchy.  Nodes referenced only
    from ``link_to`` are added to the graph and render block list, but are never
    inserted into that hierarchy.
    """

    nodes_by_id: dict[str, dict] = {}
    pending: list[str] = []
    pending_ids: set[str] = set()
    processed: set[str] = set()

    def enqueue(node_id: str) -> None:
        if node_id not in pending_ids:
            pending.append(node_id)
            pending_ids.add(node_id)

    def register_node(node: Any) -> tuple[dict, bool]:
        node_id = _node_id(node, context="source node")
        existing = nodes_by_id.get(node_id)
        if existing is None:
            existing = deepcopy(node)
            existing["id"] = node_id
            existing["children"] = []
            existing["link_to"] = []
            nodes_by_id[node_id] = existing
            changed = True
        else:
            _check_layout_compatibility(
                existing,
                node,
                node_id=node_id,
                context="source",
            )
            changed = False

        for field in _NODE_LAYOUT_FIELDS:
            if field not in existing and field in node:
                existing[field] = deepcopy(node[field])
                changed = True
        if (
            (not existing.get("text"))
            and node.get("text")
        ):
            existing["text"] = deepcopy(node["text"])
            changed = True
        if node.get("link") and not existing.get("link"):
            existing["link"] = True
            changed = True
        elif "link" not in existing and "link" in node:
            existing["link"] = bool(node["link"])
            changed = True

        if _merge_link_snapshots(existing, node, node_id=node_id):
            changed = True

        existing_children = {
            str(child.get("id"))
            for child in existing.get("children") or []
            if isinstance(child, dict) and child.get("id") is not None
        }
        for child in _as_list(node.get("children")):
            child_node, _ = register_node(child)
            child_id = str(child_node["id"])
            if child_id not in existing_children:
                existing["children"].append(child_node)
                existing_children.add(child_id)
                changed = True

        enqueue(node_id)
        if changed:
            processed.discard(node_id)
        return existing, changed

    for root in tree:
        register_node(root)

    relations: list[LinkRelation] = []
    relation_keys: set[tuple[str, str]] = set()
    target_root_ids: list[str] = []

    while pending:
        node_id = pending.pop(0)
        pending_ids.discard(node_id)
        if node_id in processed:
            continue
        node = nodes_by_id[node_id]
        link_to = _as_list(node.get("link_to"))
        if node.get("link") and not link_to:
            raise ValueError(f"link is true but link_to is empty on node {node_id!r}")

        for target in link_to:
            target_id = _node_id(target, context=f"link_to on node {node_id!r}")
            target_node, changed = register_node(target)
            del target_node
            relation_key = (node_id, target_id)
            if relation_key not in relation_keys:
                relation_keys.add(relation_key)
                relations.append(LinkRelation(node_id, target_id))
                target_root_ids.append(target_id)
            if changed:
                processed.discard(target_id)
                enqueue(target_id)

        processed.add(node_id)

    adjacency: dict[str, list[str]] = {}
    for relation in relations:
        adjacency.setdefault(relation.anchor_id, []).append(relation.target_id)

    def detect_cycle(node_id: str, path: tuple[str, ...], active: set[str]) -> None:
        if node_id in active:
            cycle = " -> ".join((*path, node_id))
            raise ValueError(f"link_to cycle detected: {cycle}")
        if node_id not in nodes_by_id:
            return
        active.add(node_id)
        for target_id in adjacency.get(node_id, []):
            detect_cycle(target_id, (*path, node_id), active)
        active.remove(node_id)

    for node_id in nodes_by_id:
        detect_cycle(node_id, (), set())

    def has_real_member(node: dict, seen: set[str]) -> bool:
        node_id = str(node["id"])
        if node_id in seen:
            return False
        seen.add(node_id)
        if not node.get("is_virtual") and _as_list(node.get("member")):
            return True
        return any(
            has_real_member(child, seen)
            for child in node.get("children") or []
            if isinstance(child, dict)
        )

    for target_id in target_root_ids:
        target = nodes_by_id.get(target_id)
        if target is None:
            raise ValueError(f"unresolved link_to target {target_id!r}")
        if not has_real_member(target, set()):
            raise ValueError(f"link_to target {target_id!r} has no materialized node")

    def block_signature(block: dict[str, Any]) -> tuple[Any, ...]:
        return (
            int(block["page"]),
            str(block["category"]),
            str(block["text"]),
            _freeze(block.get("bbox")),
        )

    def dedupe_blocks(
        blocks: Iterator[dict[str, Any]],
        *,
        seen: dict[str, tuple[Any, ...]],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for block in blocks:
            block_id = str(block["id"])
            signature = block_signature(block)
            previous = seen.get(block_id)
            if previous is not None:
                if previous != signature:
                    raise ValueError(
                        f"conflicting source member {block_id!r} in link_to material"
                    )
                continue
            seen[block_id] = signature
            result.append(block)
        return result

    seen_blocks: dict[str, tuple[Any, ...]] = {}
    main_blocks = dedupe_blocks(iter_source_blocks(tree), seen=seen_blocks)
    main_member_ids = set(seen_blocks)
    extra_blocks: list[dict[str, Any]] = []
    extra_seen: dict[str, tuple[Any, ...]] = {}
    for target_id in target_root_ids:
        target_blocks = dedupe_blocks(
            iter_source_blocks([nodes_by_id[target_id]]),
            seen=extra_seen,
        )
        for block in target_blocks:
            if block["id"] not in main_member_ids:
                extra_blocks.append(block)
            elif seen_blocks[block["id"]] != block_signature(block):
                raise ValueError(
                    f"conflicting source member {block['id']!r} in main/link_to trees"
                )

    def position(block: dict[str, Any]) -> tuple[int, float, float]:
        bbox = block.get("bbox")
        try:
            return int(block["page"]), float(bbox[1]), float(bbox[0])
        except (TypeError, IndexError, ValueError):
            return int(block["page"]), float("inf"), float("inf")

    extra_blocks.sort(key=position)
    source_blocks = list(main_blocks)
    for extra in extra_blocks:
        insert_at = len(source_blocks)
        extra_position = position(extra)
        for index, current in enumerate(source_blocks):
            if position(current) > extra_position:
                insert_at = index
                break
        source_blocks.insert(insert_at, extra)

    return LinkGraph(
        nodes_by_id=nodes_by_id,
        relations=relations,
        source_blocks=source_blocks,
    )


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
    graph = collect_link_graph(tree)

    origin = json.loads((case_dir / ORIGIN_FILENAME).read_text(encoding="utf-8"))
    doc_id = str(origin["doc_id"])
    document_type = str(origin["document_type"])

    blocks: list[SourceBlock] = []
    for raw in graph.source_blocks:
        page = int(raw["page"])
        if cfg.max_source_pages is not None and page >= cfg.max_source_pages:
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
        nodes_by_id=graph.nodes_by_id,
        relations=graph.relations,
    )
