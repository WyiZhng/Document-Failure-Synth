from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from src.synth.material import IMAGE_CATEGORIES, load_material
from src.synth.render import PlacedBlock
from src.synth.validate import validate_doc
from tests.synth.conftest import _base_tree


def _left_bbox(y1: float, y2: float) -> tuple[float, float, float, float]:
    return (40.0, y1, 450.0, y2)


def _right_bbox(y1: float, y2: float) -> tuple[float, float, float, float]:
    return (500.0, y1, 960.0, y2)


def _valid_placed() -> list[PlacedBlock]:
    return [
        PlacedBlock("p0-b1", "zh", "paragraph_title", 0, _left_bbox(100, 150), "标题一", 0),
        PlacedBlock("p0-b1", "en", "paragraph_title", 0, _right_bbox(100, 150), "Title One", 1),
        PlacedBlock("p0-b2", "zh", "text", 0, _left_bbox(200, 250), "正文内容", 2),
        PlacedBlock("p0-b2", "en", "text", 0, _right_bbox(200, 250), "Body content", 3),
    ]


def _placed_for_material(material, cfg) -> list[PlacedBlock]:
    placed: list[PlacedBlock] = []
    for index, block in enumerate(material.blocks):
        y1 = 40 + index * 30
        y2 = y1 + 20
        placed.append(
            PlacedBlock(
                block.id,
                "zh",
                block.category,
                block.page,
                (40, y1, 450, y2),
                block.text,
                index * 2,
            )
        )
        if (
            block.category in cfg.translate_categories
            and block.category not in IMAGE_CATEGORIES
        ):
            placed.append(
                PlacedBlock(
                    block.id,
                    "en",
                    block.category,
                    block.page,
                    (500, y1, 960, y2),
                    "English translation",
                    index * 2 + 1,
                )
            )
    return placed


def _assert_fails_with_node_id(result, node_id: str) -> None:
    assert not result.ok
    assert any(node_id in err for err in result.errors)
    assert result.stats["n_errors"] == len(result.errors)


def test_valid_placed_passes(cfg):
    result = validate_doc(_base_tree(), _valid_placed(), cfg)
    assert result.ok
    assert result.errors == []
    assert result.stats["n_zh"] == 2
    assert result.stats["n_en"] == 2
    assert result.stats["en_cross_page"] == 0
    assert result.stats["n_errors"] == 0


def test_link_stats_are_reported(tiny_case_with_shared_link, cfg, tmp_path):
    material = load_material(tiny_case_with_shared_link, cfg, tmp_path / "assets")
    result = validate_doc(
        material.tree,
        _placed_for_material(material, cfg),
        cfg,
        material=material,
    )
    assert result.ok
    assert result.stats["link_count"] == 2
    assert result.stats["unique_target_count"] == 1
    assert result.stats["materialized_target_block_count"] == 1
    assert result.stats["virtual_target_count"] == 1
    assert result.stats["unresolved_link_count"] == 0


@pytest.mark.parametrize(
    "mutate,node_id",
    [
        (lambda p: [b for b in p if b.node_id != "p0-b1" or b.lang != "zh"], "p0-b1"),
        (
            lambda p: p
            + [
                PlacedBlock(
                    "p0-wild",
                    "zh",
                    "text",
                    0,
                    _left_bbox(300, 350),
                    "野生块",
                    99,
                )
            ],
            "p0-wild",
        ),
        (
            lambda p: [
                PlacedBlock("p0-b2", "zh", "text", 0, _left_bbox(200, 250), "正文内容", 0),
                PlacedBlock("p0-b1", "en", "paragraph_title", 0, _right_bbox(100, 150), "Title One", 1),
                PlacedBlock("p0-b1", "zh", "paragraph_title", 0, _left_bbox(100, 150), "标题一", 2),
                PlacedBlock("p0-b2", "en", "text", 0, _right_bbox(200, 250), "Body content", 3),
            ],
            "p0-b",
        ),
        (
            lambda p: [
                PlacedBlock("p0-b1", "zh", "paragraph_title", 0, _left_bbox(100, 150), "标题一", 0),
                PlacedBlock("p0-b1", "en", "paragraph_title", 0, _right_bbox(100, 150), "Title One", 1),
                PlacedBlock("p0-b2", "zh", "text", 0, _left_bbox(200, 250), "被篡改正文", 2),
                PlacedBlock("p0-b2", "en", "text", 0, _right_bbox(200, 250), "Body content", 3),
            ],
            "p0-b2",
        ),
        (lambda p: [b for b in p if not (b.node_id == "p0-b1" and b.lang == "en")], "p0-b1"),
        (
            lambda p: [
                PlacedBlock("p0-b1", "zh", "paragraph_title", 0, _left_bbox(100, 150), "标题一", 0),
                PlacedBlock(
                    "p0-b1",
                    "en",
                    "paragraph_title",
                    0,
                    _right_bbox(100, 150),
                    "这是中文翻译",
                    1,
                ),
                PlacedBlock("p0-b2", "zh", "text", 0, _left_bbox(200, 250), "正文内容", 2),
                PlacedBlock("p0-b2", "en", "text", 0, _right_bbox(200, 250), "Body content", 3),
            ],
            "p0-b1",
        ),
    ],
    ids=[
        "missing_zh",
        "extra_wild_zh",
        "zh_order_swapped",
        "zh_text_changed",
        "missing_en",
        "en_is_chinese",
    ],
)
def test_injected_defects_fail(cfg, mutate, node_id):
    placed = mutate(copy.deepcopy(_valid_placed()))
    result = validate_doc(_base_tree(), placed, cfg)
    _assert_fails_with_node_id(result, node_id)


def test_zh_en_x_overlap_fails(cfg):
    placed = [
        PlacedBlock("p0-b1", "zh", "paragraph_title", 0, (40, 100, 500, 150), "标题一", 0),
        PlacedBlock("p0-b1", "en", "paragraph_title", 0, (450, 100, 960, 150), "Title One", 1),
        PlacedBlock("p0-b2", "zh", "text", 0, _left_bbox(200, 250), "正文内容", 2),
        PlacedBlock("p0-b2", "en", "text", 0, _right_bbox(200, 250), "Body content", 3),
    ]
    result = validate_doc(_base_tree(), placed, cfg)
    _assert_fails_with_node_id(result, "p0-b1")


def test_image_block_with_en_fails(cfg):
    tree = _base_tree(
        {
            "id": "p0-b3",
            "page_index": [0],
            "member": ["p0-b3"],
            "children": [],
            "category": ["image"],
            "bbox": [[100, 300, 300, 500]],
            "text": [""],
            "is_virtual": False,
            "link": False,
            "link_to": [],
        }
    )
    placed = _valid_placed() + [
        PlacedBlock("p0-b3", "zh", "image", 0, _left_bbox(300, 400), "", 4),
        PlacedBlock("p0-b3", "en", "image", 0, _right_bbox(300, 400), "Should not exist", 5),
    ]
    result = validate_doc(tree, placed, cfg)
    _assert_fails_with_node_id(result, "p0-b3")


def test_page_projection_detects_sandwich():
    from src.synth.validate import validate_page_projection

    def real(node_id, pages, children=None):
        return {
            "id": node_id,
            "page_index": pages,
            "member": [node_id],
            "children": children or [],
            "category": ["text"] * len(pages),
            "bbox": [[0, 0, 1, 1]] * len(pages),
            "text": [""] * len(pages),
            "is_virtual": False,
            "link": False,
            "link_to": [],
        }

    # 祖先 A 在页 0/2,其子孙 C 也到页 2,但中间夹着父不在页 2 的孤儿 B
    sandwich = [
        real(
            "p0-b1",
            [0, 2],
            children=[
                {
                    "id": "p1-fake1",
                    "page_index": [1],
                    "member": [],
                    "children": [real("p1-b1", [1, 2])],
                    "category": "text",
                    "bbox": [],
                    "text": "",
                    "is_virtual": True,
                    "link": False,
                    "link_to": [],
                },
                real("p1-b2", [1, 2]),
            ],
        )
    ]
    errors = validate_page_projection(sandwich)
    assert errors and "page 2" in errors[0]

    # 无跨页交错时应通过
    clean = [real("p0-b1", [0], children=[real("p0-b2", [0]), real("p1-b1", [1])])]
    assert validate_page_projection(clean) == []


def test_zh_en_page_split_passes(cfg):
    placed = [
        PlacedBlock("p0-b1", "zh", "paragraph_title", 0, _left_bbox(100, 150), "标题一", 0),
        PlacedBlock("p0-b1", "en", "paragraph_title", 1, _right_bbox(100, 150), "Title One", 1),
        PlacedBlock("p0-b2", "zh", "text", 0, _left_bbox(200, 250), "正文内容", 2),
        PlacedBlock("p0-b2", "en", "text", 0, _right_bbox(200, 250), "Body content", 3),
    ]
    result = validate_doc(_base_tree(), placed, cfg)
    assert result.ok
    assert result.errors == []
    assert result.stats["en_cross_page"] == 1


def test_split_zh_en_fragments_pass(cfg):
    placed = [
        PlacedBlock("p0-b1", "zh", "paragraph_title", 0, _left_bbox(100, 150), "标题一", 0),
        PlacedBlock("p0-b1", "en", "paragraph_title", 0, _right_bbox(100, 150), "Title One", 1),
        PlacedBlock(
            "p0-b2",
            "zh",
            "text",
            0,
            _left_bbox(200, 250),
            "正文",
            2,
            fragment_index=0,
        ),
        PlacedBlock(
            "p0-b2",
            "zh",
            "text",
            1,
            _left_bbox(100, 150),
            "内容",
            2,
            fragment_index=1,
        ),
        PlacedBlock(
            "p0-b2",
            "en",
            "text",
            0,
            _right_bbox(200, 250),
            "Body ",
            3,
            fragment_index=0,
        ),
        PlacedBlock(
            "p0-b2",
            "en",
            "text",
            1,
            _right_bbox(100, 150),
            "content",
            3,
            fragment_index=1,
        ),
    ]
    result = validate_doc(_base_tree(), placed, cfg)
    assert result.ok
    assert result.errors == []
    assert result.stats["n_zh"] == 3
    assert result.stats["n_en"] == 3
    assert result.stats["en_cross_page"] == 0


def test_later_source_pages_are_ignored(cfg):
    tree = _base_tree(
        {
            "id": "p5-b1",
            "page_index": [5],
            "member": ["p5-b1"],
            "children": [],
            "category": ["text"],
            "bbox": [[100, 100, 400, 150]],
            "text": ["后页正文"],
            "is_virtual": False,
            "link": False,
            "link_to": [],
        }
    )
    result = validate_doc(tree, _valid_placed(), replace(cfg, max_source_pages=5))
    assert result.ok
    assert result.errors == []


def test_later_source_pages_are_included_when_unlimited(cfg):
    tree = _base_tree(
        {
            "id": "p5-b1",
            "page_index": [5],
            "member": ["p5-b1"],
            "children": [],
            "category": ["text"],
            "bbox": [[100, 100, 400, 150]],
            "text": ["后页正文"],
            "is_virtual": False,
            "link": False,
            "link_to": [],
        }
    )
    placed = _valid_placed() + [
        PlacedBlock("p5-b1", "zh", "text", 5, _left_bbox(100, 150), "后页正文", 4),
        PlacedBlock("p5-b1", "en", "text", 5, _right_bbox(100, 150), "Later body", 5),
    ]
    result = validate_doc(tree, placed, cfg)
    assert result.ok
