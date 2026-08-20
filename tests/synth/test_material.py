import json
from pathlib import Path

import pytest

from src.synth.material import load_material


def test_load_material_flattens_in_reading_order(tiny_case_dir, cfg, tmp_path):
    m = load_material(tiny_case_dir, cfg, tmp_path / "assets")
    assert [b.id for b in m.blocks] == ["p0-b1", "p0-b2"]
    assert m.blocks[0].category == "paragraph_title"
    assert m.doc_id == "doc_test_001"
    assert m.document_type == "policy_document"


def test_load_material_rejects_link_to(tiny_case_with_link, cfg, tmp_path):
    with pytest.raises(ValueError):
        load_material(tiny_case_with_link, cfg, tmp_path / "assets")


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
