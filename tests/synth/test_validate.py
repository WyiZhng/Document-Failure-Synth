from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from src.synth.material import IMAGE_CATEGORIES, load_material
from src.synth.merge_compat import validate_merge_projection
from src.synth.render import PlacedBlock
from src.synth.translation_types import BlockPlan, TranslationBundle
from src.synth.validate import (
    validate_doc,
    validate_final_case,
    validate_final_tree,
)
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


def test_bundle_validates_english_source_to_chinese_target(material_fixture, cfg):
    material = replace(
        material_fixture,
        blocks=[
            replace(material_fixture.blocks[0], text="English title"),
            replace(material_fixture.blocks[1], text="English body"),
        ],
    )
    plans = TranslationBundle(
        plans={
            "p0-b1": BlockPlan(
                "p0-b1", "paragraph_title", "English title", "en", "zh", "translate", "中文标题"
            ),
            "p0-b2": BlockPlan(
                "p0-b2", "text", "English body", "en", "zh", "translate", "中文正文"
            ),
        },
        dropped={},
        warnings=[],
    )
    placed = [
        PlacedBlock("p0-b1", "en", "paragraph_title", 0, _left_bbox(100, 150), "English title", 0),
        PlacedBlock("p0-b1", "zh", "paragraph_title", 0, _right_bbox(100, 150), "中文标题", 1),
        PlacedBlock("p0-b2", "en", "text", 0, _left_bbox(200, 250), "English body", 2),
        PlacedBlock("p0-b2", "zh", "text", 0, _right_bbox(200, 250), "中文正文", 3),
    ]

    result = validate_doc(material.tree, placed, cfg, material=material, plans=plans)

    assert result.ok
    assert result.stats["translation_cross_page"] == 0
    assert result.stats["en_cross_page"] == 0


def test_bundle_accepts_copy_and_dropped_target(material_fixture, cfg):
    plans = TranslationBundle(
        plans={
            "p0-b1": BlockPlan(
                "p0-b1", "paragraph_title", "标题一", "zh", "en", "copy", "标题一"
            ),
            "p0-b2": BlockPlan(
                "p0-b2", "text", "正文内容", "zh", "en", "translate", None
            ),
        },
        dropped={"p0-b2": "translation failed"},
        warnings=[{"node_id": "p0-b2", "attempts": 2}],
    )
    placed = [
        PlacedBlock("p0-b1", "zh", "paragraph_title", 0, _left_bbox(100, 150), "标题一", 0),
        PlacedBlock("p0-b1", "en", "paragraph_title", 0, _right_bbox(100, 150), "标题一", 1),
        PlacedBlock("p0-b2", "zh", "text", 0, _left_bbox(200, 250), "正文内容", 2),
    ]

    result = validate_doc(
        material_fixture.tree,
        placed,
        cfg,
        material=material_fixture,
        plans=plans,
    )

    assert result.ok
    assert result.stats["dropped_node_ids"] == ["p0-b2"]
    assert result.stats["translation_warning_count"] == 1


def test_bundle_wrong_target_language_fails(material_fixture, cfg):
    plans = TranslationBundle(
        plans={
            "p0-b1": BlockPlan(
                "p0-b1", "paragraph_title", "标题一", "zh", "en", "translate", "Title One"
            ),
            "p0-b2": BlockPlan(
                "p0-b2", "text", "正文内容", "zh", "en", "translate", "Body content"
            ),
        },
        dropped={},
        warnings=[],
    )
    placed = _valid_placed()
    placed[1] = PlacedBlock(
        "p0-b1", "en", "paragraph_title", 0, _right_bbox(100, 150), "这是中文", 1
    )

    result = validate_doc(
        material_fixture.tree,
        placed,
        cfg,
        material=material_fixture,
        plans=plans,
    )

    assert not result.ok
    assert any("target text mismatch" in error or "target-language" in error for error in result.errors)


def _final_real(
    node_id: str = "p0-b1",
    *,
    page: int = 0,
    category: str = "text",
    link: bool = False,
    link_to: list[dict] | None = None,
) -> dict:
    return {
        "id": node_id,
        "page_index": [page],
        "member": [node_id],
        "children": [],
        "category": [category],
        "bbox": [[40, 40, 400, 120]],
        "text": [""],
        "is_virtual": False,
        "link": link,
        "link_to": link_to or [],
    }


def _final_virtual(node_id: str = "p0-fake1", child: dict | None = None) -> dict:
    return {
        "id": node_id,
        "page_index": [0],
        "member": [],
        "children": [] if child is None else [child],
        "category": "list_item",
        "bbox": [],
        "text": "",
        "is_virtual": True,
        "link": False,
        "link_to": [],
    }


def test_final_tree_accepts_materialized_virtual_and_empty_text(cfg):
    doc = [_final_virtual(child=_final_real("p0-b2", category="table"))]

    errors = validate_final_tree(
        doc,
        rendered_pages={0},
        page_width=cfg.page.width,
        page_height=cfg.page.height,
    )

    assert errors == []


def test_final_tree_rejects_empty_virtual(cfg):
    errors = validate_final_tree(
        [_final_virtual()],
        rendered_pages={0},
        page_width=cfg.page.width,
        page_height=cfg.page.height,
    )

    assert errors and any("p0-fake1" in error and "children" in error for error in errors)


def test_final_tree_rejects_page_not_in_rendered_page_ledger(cfg):
    errors = validate_final_tree(
        [_final_real(page=2)],
        rendered_pages={0, 1},
        page_width=cfg.page.width,
        page_height=cfg.page.height,
    )

    assert errors and any("page 2" in error and "rendered" in error for error in errors)


def test_final_tree_rejects_malformed_aligned_arrays(cfg):
    node = _final_real()
    node["bbox"] = [[40, 40, 400, 120], [40, 130, 400, 200]]

    errors = validate_final_tree(
        [node],
        rendered_pages={0},
        page_width=cfg.page.width,
        page_height=cfg.page.height,
    )

    assert errors and any("aligned" in error and "p0-b1" in error for error in errors)


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda node: node.update({"link": True, "link_to": []}), "link_to"),
        (
            lambda node: node.update(
                {
                    "link": True,
                    "link_to": [{"id": "target", "children": []}],
                }
            ),
            "target",
        ),
    ],
    ids=["anchor_without_target", "empty_target_snapshot"],
)
def test_final_tree_rejects_bad_link(mutate, needle, cfg):
    node = _final_real(link=False)
    mutate(node)

    errors = validate_final_tree(
        [node],
        rendered_pages={0},
        page_width=cfg.page.width,
        page_height=cfg.page.height,
    )

    assert errors and any(needle in error for error in errors)


def test_final_tree_accepts_geometry_bearing_reference_link(cfg):
    target = _final_real("p1-reference", page=1, category="reference")
    anchor = _final_real("p0-anchor", link=True, link_to=[target])

    errors = validate_final_tree(
        [anchor],
        rendered_pages={0, 1},
        page_width=cfg.page.width,
        page_height=cfg.page.height,
    )

    assert errors == []


def test_final_case_composes_projection_validation(cfg):
    parent = _final_real("p0-b1", page=0)
    parent["page_index"] = [0, 2]
    parent["member"] = ["p0-b1", "p2-b1"]
    parent["category"] = ["text", "text"]
    parent["bbox"] = [[40, 40, 400, 120], [40, 40, 400, 120]]
    parent["text"] = ["", ""]
    middle = _final_virtual("p1-fake1", _final_real("p1-b1", page=1))
    middle["children"][0]["page_index"] = [1, 2]
    middle["children"][0]["member"] = ["p1-b1", "p2-b2"]
    middle["children"][0]["category"] = ["text", "text"]
    middle["children"][0]["bbox"] = [[40, 40, 400, 120], [40, 40, 400, 120]]
    sibling = _final_real("p1-b2", page=1)
    sibling["page_index"] = [1, 2]
    sibling["member"] = ["p1-b2", "p2-b3"]
    sibling["category"] = ["text", "text"]
    sibling["bbox"] = [[40, 40, 400, 120], [40, 40, 400, 120]]
    sibling["text"] = ["", ""]
    parent["children"] = [middle, sibling]

    result = validate_final_case(
        [parent],
        rendered_pages={0, 1, 2},
        cfg=cfg,
    )

    assert not result.ok
    assert any("parent leaves preorder" in error for error in result.errors)


def test_merge_compat_accepts_trainer_merge_id():
    node = _final_real("merge-p0-b1", page=0)
    node["page_index"] = [0, 1]
    node["member"] = ["p0-b1", "p1-b1"]
    node["category"] = ["text", "text"]
    node["bbox"] = [
        [40, 40, 400, 120],
        [40, 40, 400, 120],
    ]
    node["text"] = ["", ""]

    assert validate_merge_projection([node]) == []


def test_merge_compat_rejects_non_merge_id_for_cross_page_node():
    node = _final_real("p0-b1", page=0)
    node["page_index"] = [0, 1]
    node["member"] = ["p0-b1", "p1-b1"]
    node["category"] = ["text", "text"]
    node["bbox"] = [
        [40, 40, 400, 120],
        [40, 40, 400, 120],
    ]
    node["text"] = ["", ""]

    errors = validate_merge_projection([node])

    assert errors and any("merged tree" in error for error in errors)
