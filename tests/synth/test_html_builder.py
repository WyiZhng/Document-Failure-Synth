from bs4 import BeautifulSoup

from src.synth.html_builder import build_source_html


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
