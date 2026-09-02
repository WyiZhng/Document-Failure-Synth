from bs4 import BeautifulSoup
from dataclasses import replace

from src.synth.html_builder import build_bilingual_html, build_source_html
from src.synth.material import SourceBlock
from src.synth.translation_types import BlockPlan, TranslationBundle


def test_every_block_marked(material_fixture, cfg):
    soup = BeautifulSoup(build_source_html(material_fixture, cfg), "lxml")
    els = soup.select("[data-node-id]")
    assert {e["data-node-id"] for e in els} == {b.id for b in material_fixture.blocks}
    assert all(e["data-lang"] == "zh" for e in els)


def test_sections_by_source_page(material_fixture, cfg):
    soup = BeautifulSoup(build_source_html(material_fixture, cfg), "lxml")
    assert [s["data-src-page"] for s in soup.select("section.src-page")] == ["0"]


def test_text_blocks_use_div(material_fixture, cfg):
    soup = BeautifulSoup(build_source_html(material_fixture, cfg), "lxml")
    for block in material_fixture.blocks:
        if block.image_path:
            continue
        el = soup.select_one(f'div.block[data-node-id="{block.id}"]')
        assert el is not None
        assert el.name == "div"
        assert block.text in el.get_text()


def test_image_blocks_use_img(tiny_case_with_image, cfg, tmp_path):
    from src.synth.material import load_material

    material = load_material(tiny_case_with_image, cfg, tmp_path / "assets")
    soup = BeautifulSoup(build_source_html(material, cfg), "lxml")
    img = soup.select_one('img.block[data-node-id="p0-b3"]')
    assert img is not None
    assert img["data-category"] == "image"
    assert img["data-lang"] == "zh"
    assert img.get("src")


def test_shared_link_target_is_marked_once(tiny_case_with_shared_link, cfg, tmp_path):
    from src.synth.material import load_material

    material = load_material(tiny_case_with_shared_link, cfg, tmp_path / "assets")
    soup = BeautifulSoup(build_source_html(material, cfg), "lxml")
    ids = [element["data-node-id"] for element in soup.select("[data-node-id]")]
    assert ids.count("p1-b1") == 1
    assert ids.index("p0-b1") < ids.index("p1-b1")


def test_bilingual_html_uses_node_plan_and_keeps_source_once(material_fixture, cfg):
    bundle = TranslationBundle(
        plans={
            "p0-b1": BlockPlan(
                "p0-b1", "paragraph_title", "标题一", "zh", "en", "translate", "Title One"
            ),
            "p0-b2": BlockPlan(
                "p0-b2", "text", "正文内容", "zh", "en", "copy", "正文内容"
            ),
        },
        dropped={},
        warnings=[],
    )

    soup = BeautifulSoup(build_bilingual_html(material_fixture, bundle, cfg), "lxml")

    assert [el.get("data-lang") for el in soup.select('[data-node-id="p0-b1"]')] == [
        "zh",
        "en",
    ]
    assert [el.get_text(strip=True) for el in soup.select('[data-node-id="p0-b2"]')] == [
        "正文内容",
        "正文内容",
    ]
    assert soup.select_one('[data-node-id="p0-b1"][data-lang="zh"]').get_text(
        strip=True
    ) == "标题一"


def test_bilingual_html_supports_english_source_and_dropped_target(material_fixture, cfg):
    material = replace(
        material_fixture,
        blocks=[
            SourceBlock("en", 0, "text", "English source", None),
            SourceBlock("dropped", 0, "text", "中文源文", None),
        ],
    )
    bundle = TranslationBundle(
        plans={
            "en": BlockPlan("en", "text", "English source", "en", "zh", "translate", "中文源文"),
            "dropped": BlockPlan("dropped", "text", "中文源文", "zh", "en", "translate", None),
        },
        dropped={"dropped": "translation failed"},
        warnings=[],
    )

    soup = BeautifulSoup(build_bilingual_html(material, bundle, cfg), "lxml")

    assert [el.get("data-lang") for el in soup.select('[data-node-id="en"]')] == [
        "en",
        "zh",
    ]
    assert [el.get_text(strip=True) for el in soup.select('[data-node-id="dropped"]')] == [
        "中文源文"
    ]


def test_bilingual_html_materializes_empty_relation_plan_as_invisible_block(
    material_fixture, cfg
):
    material = replace(
        material_fixture,
        blocks=[SourceBlock("empty-reference", 4, "reference", "", None)],
    )
    bundle = TranslationBundle(
        plans={
            "empty-reference": BlockPlan(
                "empty-reference",
                "reference",
                "",
                "zh",
                None,
                "source_only",
                None,
            )
        },
        dropped={},
        warnings=[],
    )

    soup = BeautifulSoup(build_bilingual_html(material, bundle, cfg), "lxml")
    element = soup.select_one('[data-node-id="empty-reference"]')

    assert element is not None
    assert element.get("data-relation-only") == "true"
    assert element.get("data-lang") == "zh"
