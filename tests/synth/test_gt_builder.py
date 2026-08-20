from __future__ import annotations

import json
from pathlib import Path

from src.synth.gt_builder import build_gt
from src.synth.material import load_material
from src.synth.render import PlacedBlock


def _left(y1: float, y2: float) -> tuple[float, float, float, float]:
    return (40.0, y1, 450.0, y2)


def _right(y1: float, y2: float) -> tuple[float, float, float, float]:
    return (500.0, y1, 960.0, y2)


def _same_page_placed() -> list[PlacedBlock]:
    return [
        PlacedBlock("p0-b1", "zh", "paragraph_title", 0, _left(100, 150), "标题一", 0),
        PlacedBlock("p0-b1", "en", "paragraph_title", 0, _right(100, 150), "Title One", 1),
        PlacedBlock("p0-b2", "zh", "text", 0, _left(200, 250), "正文内容", 2),
        PlacedBlock("p0-b2", "en", "text", 0, _right(200, 250), "Body content", 3),
    ]


def _walk(nodes: list[dict]):
    for node in nodes:
        yield node
        yield from _walk(node.get("children") or [])


def _find_text_pair(tree: list[dict]) -> dict:
    for node in _walk(tree):
        if node.get("category") == ["text", "text"]:
            return node
    raise AssertionError("no bilingual text node")


def test_bilingual_node_member_order(material_fixture, cfg, tmp_path):
    build_gt(material_fixture, _same_page_placed(), tmp_path, cfg, seq=1)
    tree = json.loads((tmp_path / "multi-page-final.json").read_text())["doc"]
    node = _find_text_pair(tree)
    assert len(node["member"]) == 2
    zh_new, en_new = node["member"]
    assert node["id"] == zh_new
    assert node["text"] == ["", ""]
    assert zh_new.startswith("p0-b")
    assert en_new.startswith("p0-b")
    assert node["member"][0] != node["member"][1]


def test_cross_page_translation_makes_multipage_node(material_fixture, cfg, tmp_path):
    placed = [
        PlacedBlock("p0-b1", "zh", "paragraph_title", 0, _left(100, 150), "标题一", 0),
        PlacedBlock("p0-b1", "en", "paragraph_title", 0, _right(100, 150), "Title One", 1),
        PlacedBlock("p0-b2", "zh", "text", 0, _left(200, 250), "正文内容", 2),
        PlacedBlock("p0-b2", "en", "text", 1, _right(40, 120), "Body content", 3),
    ]
    build_gt(material_fixture, placed, tmp_path, cfg, seq=2)
    tree = json.loads((tmp_path / "multi-page-final.json").read_text())["doc"]
    node = _find_text_pair(tree)
    assert node["page_index"] == [0, 1]


def test_label_ids_match_tree_members(material_fixture, cfg, tmp_path):
    build_gt(material_fixture, _same_page_placed(), tmp_path, cfg, seq=1)
    label = json.loads((tmp_path / "label.json").read_text())
    tree = json.loads((tmp_path / "multi-page-final.json").read_text())["doc"]
    members = {m for node in _walk(tree) for m in node.get("member") or []}
    label_ids = {
        f"p{page['page_index']}-b{block['block_id']}"
        for page in label["pages"]
        for block in page["blocks"]
    }
    assert members == label_ids
    assert label["annotator_id"] == "synth"
    for page in label["pages"]:
        for block in page["blocks"]:
            x1, y1, x2, y2 = block["bbox"]
            assert x2 > x1 and y2 > y1


def test_untranslated_image_node_single_member(tiny_case_with_image, cfg, tmp_path):
    material = load_material(tiny_case_with_image, cfg, tmp_path / "assets")
    placed = _same_page_placed() + [
        PlacedBlock("p0-b3", "zh", "image", 0, _left(300, 400), "", 4),
    ]
    build_gt(material, placed, tmp_path / "out", cfg, seq=3)
    tree = json.loads((tmp_path / "out" / "multi-page-final.json").read_text())["doc"]
    image_nodes = [
        node for node in _walk(tree) if node.get("category") == ["image"]
    ]
    assert len(image_nodes) == 1
    assert len(image_nodes[0]["member"]) == 1


def test_origin_doc_id_format(material_fixture, cfg, tmp_path):
    build_gt(material_fixture, _same_page_placed(), tmp_path, cfg, seq=7)
    origin = json.loads((tmp_path / "origin.json").read_text())
    assert origin["doc_id"] == f"synth_bilingual_007_{material_fixture.doc_id}"
    assert origin["task_id"] == "synth_task_007"
    assert origin["document_type"] == material_fixture.document_type
    assert origin["images_path"] == str((tmp_path / "images_path").resolve())
    assert origin["pdf_path"] == ""
    assert origin["reading_direction"] == "horizontal"
