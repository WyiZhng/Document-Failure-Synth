"""Read-only mirror of the Trainer merge reconstruction contract.

The synthesizer must not import the Trainer project at runtime.  This module
keeps the small part of ``src.train.merge_strategy`` that determines whether a
final tree can be replayed by ``strategy._merge``.  If that Trainer contract
changes, this mirror should be updated together with its compatibility tests.
"""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


ROOT_ID = "__document_root__"
TITLE_CATEGORIES = {"figure_title", "table_title", "form_title"}
ALIGNED_FIELDS = {"member", "page_index", "category", "bbox", "text", "is_virtual"}


class MergeCompatibilityError(Exception):
    """Raised when the Trainer merge contract cannot replay a final tree."""


@dataclass
class FinalIndex:
    roots: list[dict[str, Any]]
    nodes: dict[str, dict[str, Any]]
    parent: dict[str, str | None]
    identity_to_node: dict[tuple[str, str], str]
    identity_to_parent: dict[tuple[str, str], str | None]
    page_index_set: list[int]


@dataclass
class ActiveNode:
    target: dict[str, Any] | None
    level: int
    children: list["ActiveNode"] = field(default_factory=list)
    idx: int = -1


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _category(node: Mapping[str, Any]) -> str:
    categories = _as_list(node.get("category"))
    return str(categories[0]) if categories else ""


def _identity_tokens(node: Mapping[str, Any]) -> list[tuple[str, str]]:
    members = [str(value) for value in _as_list(node.get("member"))]
    if members:
        return [("member", member) for member in members]
    node_id = node.get("id")
    if node_id is None:
        raise MergeCompatibilityError("node without members requires an id")
    return [("virtual", str(node_id))]


def index_final_tree(tree_list: list[dict[str, Any]]) -> FinalIndex:
    nodes: dict[str, dict[str, Any]] = {}
    parent: dict[str, str | None] = {}
    identity_to_node: dict[tuple[str, str], str] = {}
    identity_to_parent: dict[tuple[str, str], str | None] = {}
    counter = 0
    page_index_set: list[int] = []

    def visit(raw: Mapping[str, Any], parent_key: str | None) -> dict[str, Any]:
        nonlocal counter
        key = f"node:{counter}"
        counter += 1

        node = deepcopy(dict(raw))
        raw_children = node.pop("children", []) or []
        node["_node_key"] = key
        node["children"] = []
        nodes[key] = node
        parent[key] = parent_key
        for page_index in node.get("page_index", []):
            if page_index not in page_index_set:
                page_index_set.append(page_index)
        for token in _identity_tokens(node):
            previous = identity_to_node.get(token)
            if previous is not None and previous != key:
                raise MergeCompatibilityError(
                    f"identity {token[1]!r} maps to multiple final nodes"
                )
            identity_to_node[token] = key
            identity_to_parent[token] = parent_key

        for child in raw_children:
            if isinstance(child, Mapping):
                node["children"].append(visit(child, key))
        return node

    roots = [visit(root, None) for root in tree_list if isinstance(root, Mapping)]
    return FinalIndex(
        roots=roots,
        nodes=nodes,
        parent=parent,
        identity_to_node=identity_to_node,
        identity_to_parent=identity_to_parent,
        page_index_set=page_index_set,
    )


def _node_key(node: Mapping[str, Any], index: FinalIndex) -> str:
    keys = {
        index.identity_to_node[token]
        for token in _identity_tokens(node)
        if token in index.identity_to_node
    }
    if len(keys) != 1:
        raise MergeCompatibilityError(
            f"cannot uniquely resolve node {node.get('id')!r} from member identity"
        )
    return next(iter(keys))


def _parent_key(node: Mapping[str, Any], index: FinalIndex) -> str | None:
    parents = {
        index.identity_to_parent[token]
        for token in _identity_tokens(node)
        if token in index.identity_to_parent
    }
    if len(parents) != 1:
        raise MergeCompatibilityError(f"cannot uniquely resolve parent for {node.get('id')!r}")
    return next(iter(parents))


def projected_shallow(node: Mapping[str, Any], pages: set[int]) -> dict[str, Any]:
    page_values = [int(value) for value in node.get("page_index")]
    positions = [i for i, page in enumerate(page_values) if page in pages]
    projected = {
        key: deepcopy(value) for key, value in node.items() if key != "children"
    }
    for key in ALIGNED_FIELDS:
        value = node.get(key)
        if isinstance(value, list) and len(value) == len(page_values):
            projected[key] = [deepcopy(value[i]) for i in positions]
    return projected


def project_tree(nodes: list[dict[str, Any]], pages: set[int]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for node in nodes:
        children = project_tree(
            [child for child in node.get("children", []) if isinstance(child, dict)],
            pages,
        )
        node_pages = {int(value) for value in node.get("page_index")}
        if not node_pages.intersection(pages):
            output.extend(children)
            continue
        projected = projected_shallow(node, pages)
        projected["children"] = children
        output.append(projected)
    return output


def selected_branches(nodes: list[dict[str, Any]]) -> list[int]:
    if not nodes:
        return []
    last_category = _category(nodes[-1])
    if last_category == "reference":
        index = len(nodes) - 1
        while index >= 0 and _category(nodes[index]) == "reference":
            index -= 1
        return [index] if index >= 0 else []
    if last_category in TITLE_CATEGORIES:
        index = len(nodes) - 1
        while index >= 0 and _category(nodes[index]) in TITLE_CATEGORIES:
            index -= 1
        selected = list(range(index + 1, len(nodes)))
        if index >= 0:
            selected.insert(0, index)
        return selected
    return [len(nodes) - 1]


def make_active_tree(doc: list[dict[str, Any]]) -> ActiveNode:
    def build_level(nodes: list[dict[str, Any]], level: int) -> list[ActiveNode]:
        active_nodes = [ActiveNode(target=node, level=level) for node in nodes]
        for index in selected_branches(nodes):
            children = [
                child
                for child in nodes[index].get("children", [])
                if isinstance(child, dict)
            ]
            active_nodes[index].children = build_level(children, level + 1)
        return active_nodes

    root = ActiveNode(target=None, level=0, children=build_level(doc, 1))
    next_index = 0

    def assign(node: ActiveNode) -> None:
        nonlocal next_index
        node.idx = next_index
        next_index += 1
        for child in node.children:
            assign(child)

    assign(root)
    return root


def walk_active(root: ActiveNode) -> list[ActiveNode]:
    output: list[ActiveNode] = []

    def visit(node: ActiveNode) -> None:
        output.append(node)
        for child in node.children:
            visit(child)

    visit(root)
    return output


def public_shallow(node: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in node.items()
        if key != "children" and not key.startswith("_")
    }


def active_payload(node: ActiveNode) -> dict[str, Any]:
    if node.target is None:
        payload: dict[str, Any] = {
            "idx": node.idx,
            "id": ROOT_ID,
            "category": "root",
            "level": 0,
        }
    else:
        payload = public_shallow(node.target)
        payload["idx"] = node.idx
        payload["level"] = node.level
    payload["children"] = [active_payload(child) for child in node.children]
    return payload


def public_tree(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for node in nodes:
        public = public_shallow(node)
        public["children"] = public_tree(
            [child for child in node.get("children", []) if isinstance(child, dict)]
        )
        output.append(public)
    return output


def infer_actions(
    left_doc: list[dict[str, Any]],
    right_doc: list[dict[str, Any]],
    index: FinalIndex,
) -> tuple[list[dict[str, Any]], ActiveNode]:
    active_root = make_active_tree(left_doc)
    active_nodes = walk_active(active_root)
    active_by_key: dict[str, ActiveNode] = {}
    for active in active_nodes:
        if active.target is None:
            continue
        key = _node_key(active.target, index)
        if key in active_by_key:
            raise MergeCompatibilityError(f"final node {key} appears twice in active tree")
        active_by_key[key] = active

    actions: list[dict[str, Any]] = []
    for node_idx, incoming in enumerate(right_doc):
        key = _node_key(incoming, index)
        merge_target = active_by_key.get(key)
        incoming_id = str(incoming.get("id", ""))
        if (
            incoming_id.startswith("merge")
            and merge_target is not None
            and str(merge_target.target.get("id")) == incoming_id
        ):
            actions.append(
                {
                    "node_idx": node_idx,
                    "action": "merge",
                    "merge_idx": merge_target.idx,
                }
            )
            continue

        parent_key = _parent_key(incoming, index)
        parent_target = active_by_key.get(parent_key) if parent_key else None
        actions.append(
            {
                "node_idx": node_idx,
                "action": "attach",
                "parent_path_idx": parent_target.idx if parent_target else 0,
            }
        )
    return actions, active_root


def _merge_nodes(
    target: dict[str, Any],
    incoming: dict[str, Any],
    pages: set[int],
    index: FinalIndex,
) -> None:
    key = _node_key(target, index)
    if _node_key(incoming, index) != key:
        raise MergeCompatibilityError("merge target and incoming node identities differ")

    expected_nodes = project_tree([index.nodes[key]], pages)
    if len(expected_nodes) != 1:
        raise MergeCompatibilityError(f"cannot project merged node {target.get('id')!r}")
    expected = expected_nodes[0]

    target_children = {
        _node_key(child, index): child
        for child in target.get("children", [])
        if isinstance(child, dict)
    }
    incoming_children = {
        _node_key(child, index): child
        for child in incoming.get("children", [])
        if isinstance(child, dict)
    }
    merged_children: list[dict[str, Any]] = []
    for expected_child in expected.get("children", []):
        child_key = _node_key(expected_child, index)
        left_child = target_children.get(child_key)
        right_child = incoming_children.get(child_key)
        if left_child is not None and right_child is not None:
            _merge_nodes(left_child, right_child, pages, index)
            merged_children.append(left_child)
        elif left_child is not None:
            merged_children.append(left_child)
        elif right_child is not None:
            merged_children.append(deepcopy(right_child))
        else:
            merged_children.append(deepcopy(expected_child))

    replacement = projected_shallow(index.nodes[key], pages)
    target.clear()
    target.update(replacement)
    target["children"] = merged_children


def apply_actions(
    left_doc: list[dict[str, Any]],
    right_doc: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    pages: set[int],
    index: FinalIndex,
) -> list[dict[str, Any]]:
    merged = deepcopy(left_doc)
    incoming_nodes = deepcopy(right_doc)
    active_nodes = {node.idx: node for node in walk_active(make_active_tree(merged))}
    expected_coverage = list(range(len(incoming_nodes)))
    actual_coverage = sorted(int(action["node_idx"]) for action in actions)
    if actual_coverage != expected_coverage:
        raise MergeCompatibilityError(
            f"action coverage mismatch: {actual_coverage} != {expected_coverage}"
        )

    for action in sorted(actions, key=lambda item: int(item["node_idx"])):
        incoming = incoming_nodes[int(action["node_idx"])]
        if action.get("action") == "merge":
            target = active_nodes.get(int(action.get("merge_idx", -1)))
            if target is None or target.target is None:
                raise MergeCompatibilityError(f"invalid merge_idx: {action.get('merge_idx')}")
            _merge_nodes(target.target, incoming, pages, index)
        elif action.get("action") == "attach":
            target = active_nodes.get(int(action.get("parent_path_idx", -1)))
            if target is None:
                raise MergeCompatibilityError(
                    f"invalid parent_path_idx: {action.get('parent_path_idx')}"
                )
            if target.target is None:
                merged.append(incoming)
            else:
                target.target.setdefault("children", []).append(incoming)
        else:
            raise MergeCompatibilityError(f"unsupported action: {action!r}")
    return merged


def validate_merge_projection(tree: Any) -> list[str]:
    """Replay Trainer's page-wise merge and return deterministic errors."""

    if not isinstance(tree, list) or len(tree) == 0:
        return []
    try:
        final_index = index_final_tree(tree)
        if len(final_index.page_index_set) <= 1:
            return []
        groups = [
            {"pages": [page], "doc": project_tree(final_index.roots, {page})}
            for page in final_index.page_index_set
        ]
        round_index = 1
        while len(groups) > 1:
            next_groups: list[dict[str, Any]] = []
            for pair_index in range(0, len(groups), 2):
                if pair_index + 1 >= len(groups):
                    next_groups.append(groups[pair_index])
                    continue
                left = groups[pair_index]
                right = groups[pair_index + 1]
                left_boundary = max(int(page) for page in left["pages"])
                right_boundary = min(int(page) for page in right["pages"])
                if left_boundary >= right_boundary:
                    raise MergeCompatibilityError(
                        "merge groups must be ordered and non-overlapping: "
                        f"left boundary p{left_boundary}, right boundary p{right_boundary}"
                    )
                merged_pages = sorted({*left["pages"], *right["pages"]})
                expected = project_tree(final_index.roots, set(merged_pages))
                actions, _active_root = infer_actions(
                    left["doc"], right["doc"], final_index
                )
                merged = apply_actions(
                    left["doc"], right["doc"], actions, set(merged_pages), final_index
                )
                if public_tree(merged) != public_tree(expected):
                    raise MergeCompatibilityError(
                        "merged tree does not match final-tree page projection "
                        f"(round={round_index}, left={left['pages']}, right={right['pages']})"
                    )
                next_groups.append({"pages": merged_pages, "doc": merged})
            groups = next_groups
            round_index += 1
    except Exception as exc:
        return [f"merge compatibility replay failed: {exc}"]
    return []
