import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.synth.material import LinkRelation, load_material
from tests.synth.conftest import _base_tree, _table_link_target, _write_case_dir


def test_load_material_flattens_in_reading_order(tiny_case_dir, cfg, tmp_path):
    m = load_material(tiny_case_dir, cfg, tmp_path / "assets")
    assert [b.id for b in m.blocks] == ["p0-b1", "p0-b2"]
    assert m.blocks[0].category == "paragraph_title"
    assert m.doc_id == "doc_test_001"
    assert m.document_type == "policy_document"


def test_load_material_accepts_and_materializes_link_target(
    tiny_case_with_link, cfg, tmp_path
):
    material = load_material(tiny_case_with_link, cfg, tmp_path / "assets")
    assert material.relations == [LinkRelation("p0-b1", "p1-fake1")]
    assert material.nodes_by_id["p1-fake1"]["is_virtual"] is True
    assert [block.id for block in material.blocks] == ["p0-b1", "p0-b2", "p1-b1"]
    assert material.blocks[-1].image_path is not None


def test_shared_link_target_is_materialized_once(
    tiny_case_with_shared_link, cfg, tmp_path
):
    material = load_material(tiny_case_with_shared_link, cfg, tmp_path / "assets")
    assert len(material.relations) == 2
    assert [block.id for block in material.blocks].count("p1-b1") == 1


def test_link_target_beyond_old_page_limit_is_kept(
    tiny_case_with_late_link, cfg, tmp_path
):
    material = load_material(tiny_case_with_late_link, cfg, tmp_path / "assets")
    assert any(block.id == "p5-b1" and block.page == 5 for block in material.blocks)


def test_image_block_cropped(tiny_case_with_image, cfg, tmp_path):
    m = load_material(tiny_case_with_image, cfg, tmp_path / "assets")
    img_blocks = [b for b in m.blocks if b.image_path]
    assert img_blocks and Path(img_blocks[0].image_path).exists()
    assert img_blocks[0].text == ""
    assert img_blocks[0].id == "p0-b3"


def test_image_block_sanitizes_id_in_filename(tiny_case_with_slash_image_id, cfg, tmp_path):
    m = load_material(tiny_case_with_slash_image_id, cfg, tmp_path / "assets")
    img_blocks = [b for b in m.blocks if b.image_path]
    assert len(img_blocks) == 1
    assert img_blocks[0].id == "p0/b3"
    assert Path(img_blocks[0].image_path).name == "img_p0_b3.png"
    assert Path(img_blocks[0].image_path).exists()


def test_load_material_wraps_doc_key(tiny_case_dir, cfg, tmp_path):
    tree = json.loads((tiny_case_dir / "multi-page-final-fillin.json").read_text())
    (tiny_case_dir / "multi-page-final-fillin.json").write_text(
        json.dumps({"doc": tree}, ensure_ascii=False),
        encoding="utf-8",
    )
    m = load_material(tiny_case_dir, cfg, tmp_path / "assets")
    assert [b.id for b in m.blocks] == ["p0-b1", "p0-b2"]


def test_missing_link_target_is_rejected(tmp_path, cfg):
    tree = _base_tree()
    tree[0]["children"][0]["link"] = True
    tree[0]["children"][0]["link_to"] = [{"id": "missing-target"}]
    case_dir = tmp_path / "missing_target"
    _write_case_dir(case_dir, tree)

    with pytest.raises(ValueError, match="missing-target"):
        load_material(case_dir, cfg, tmp_path / "assets")


def test_empty_virtual_link_target_is_retained_as_relation_only(tmp_path, cfg):
    tree = _base_tree()
    tree[0]["children"][0]["link"] = True
    target = _table_link_target()
    target["children"] = []
    tree[0]["children"][0]["link_to"] = [target]
    case_dir = tmp_path / "empty_virtual_target"
    _write_case_dir(case_dir, tree)

    material = load_material(case_dir, cfg, tmp_path / "assets")
    assert material.relations == [LinkRelation("p0-b1", "p1-fake1")]
    assert material.nodes_by_id["p1-fake1"]["children"] == []
    assert "p1-fake1" not in [block.id for block in material.blocks]


def test_conflicting_link_target_layout_is_rejected(tmp_path, cfg):
    tree = _base_tree()
    first = _table_link_target()
    second = deepcopy(first)
    second["page_index"] = [2]
    second["children"][0]["page_index"] = [2]
    tree[0]["children"][0]["link"] = True
    tree[0]["children"][0]["link_to"] = [first]
    tree[0]["children"][1]["link"] = True
    tree[0]["children"][1]["link_to"] = [second]
    case_dir = tmp_path / "conflicting_target"
    _write_case_dir(case_dir, tree, pages=(0, 1, 2))

    with pytest.raises(ValueError, match="p1-fake1.*page_index|page_index.*p1-fake1"):
        load_material(case_dir, cfg, tmp_path / "assets")


def test_link_to_cycle_is_rejected(tmp_path, cfg):
    tree = _base_tree()
    anchor = tree[0]["children"][0]
    back_reference = deepcopy(anchor)
    back_reference["link"] = False
    back_reference["link_to"] = []
    target = {
        "id": "p1-b1",
        "page_index": [1],
        "member": ["p1-b1"],
        "children": [],
        "category": ["text"],
        "bbox": [[100, 100, 400, 150]],
        "text": ["目标"],
        "is_virtual": False,
        "link": True,
        "link_to": [back_reference],
    }
    anchor["link"] = True
    anchor["link_to"] = [target]
    case_dir = tmp_path / "cycle"
    _write_case_dir(case_dir, tree, pages=(0, 1))

    with pytest.raises(ValueError, match="cycle"):
        load_material(case_dir, cfg, tmp_path / "assets")
