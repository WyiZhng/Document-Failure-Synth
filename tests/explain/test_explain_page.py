from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPLAIN_JS = ROOT / "explain" / "data.js"
EXPLAIN_PNG = ROOT / "explain" / "assets" / "raw-page-1.png"
EXPLAIN_HTML = ROOT / "explain" / "index.html"
EXPLAIN_CSS = ROOT / "explain" / "styles.css"
README = ROOT / "README.md"


def load_explain() -> dict:
    text = EXPLAIN_JS.read_text(encoding="utf-8")
    assert text.startswith("var EXPLAIN = ")
    body = text[len("var EXPLAIN = ") :].strip()
    if body.endswith(";"):
        body = body[:-1]
    return json.loads(body)


def test_data_js_contract() -> None:
    data = load_explain()
    assert data["originInput"] == {
        "doc_id": "doc_21f4dc879ffd",
        "document_type": "policy_document",
        "reading_direction": "horizontal",
    }
    nodes = {n["id"]: n for n in data["sourceNodes"]}
    assert nodes["p0-b2"]["category"] == "doc_title"
    assert nodes["p0-b2"]["text"] == "山东省工业和信息化厅文件"
    assert nodes["p0-b2"]["link_to"] == []
    assert nodes["p0-b3"]["text"] == "鲁工信消〔2021〕77号"
    assert nodes["p0-b8"]["category"] == "seal"
    assert nodes["p0-b8"]["text"] == ""
    blocks = {b["id"]: b for b in data["sourceBlocks"]}
    assert blocks["p0-b3"]["page"] == 0
    assert blocks["p0-b3"]["image_path"] is None
    assert blocks["p0-b8"]["image_path"].endswith("img_p0-b8.png")
    assert "鲁工信消〔2021〕77号" in data["htmlZh"]
    assert 'data-lang="zh"' in data["htmlZh"]
    assert "Lu Gongxin Xiao [2021] No. 77" in data["htmlPair"]
    assert 'data-lang="en"' in data["htmlPair"]
    assert data["stats"] == {
        "n_zh": 22,
        "n_en": 21,
        "en_cross_page": 0,
        "n_errors": 0,
    }
    assert data["idMap"] == [
        {
            "sourceId": "p0-b3",
            "role": "中文文号",
            "newId": "p0-b2",
            "bboxX": "40–488",
        },
        {
            "sourceId": "p0-b3",
            "role": "英文文号",
            "newId": "p0-b16",
            "bboxX": "512–960",
        },
    ]
    origin = data["artifacts"]["origin"]
    assert origin["doc_id"] == "synth_bilingual_001_doc_21f4dc879ffd"
    assert origin["task_id"] == "synth_task_001"
    assert origin["document_type"] == "policy_document"
    label_blocks = data["artifacts"]["label"]["pages"][0]["blocks"]
    assert label_blocks[0]["block_id"] == 1
    assert label_blocks[0]["bbox"] == [40.0, 40.0, 488.0, 75.1875]
    assert label_blocks[1]["block_id"] == 2
    tree = data["artifacts"]["treeNode"]
    assert tree["member"] == ["p0-b2", "p0-b16"]
    assert tree["text"] == ["", ""]
    pre = data["artifacts"]["prelabel"]
    assert pre["text"] == "山东省工业和信息化厅文件"
    assert pre["source"] == "paddle_ocr"


def test_source_page_image_is_copied_file() -> None:
    assert EXPLAIN_PNG.is_file()
    assert not EXPLAIN_PNG.is_symlink()
    assert EXPLAIN_PNG.stat().st_size > 100000


REQUIRED_IDS = [
    "data-error",
    "input",
    "step-1",
    "step-2",
    "step-3",
    "step-4",
    "step-5",
    "step-6",
    "output",
    "origin-input",
    "source-nodes",
    "source-blocks",
    "html-zh",
    "html-pair",
    "stats",
    "id-map-body",
    "artifact-origin",
    "artifact-label",
    "artifact-tree",
    "artifact-prelabel",
]


def test_html_is_offline_static_page() -> None:
    html = EXPLAIN_HTML.read_text(encoding="utf-8")
    css = EXPLAIN_CSS.read_text(encoding="utf-8")
    assert 'href="styles.css"' in html
    assert "<script src=\"data.js\"></script>" in html
    assert "type=\"module\"" not in html
    assert "fetch(" not in html
    assert "innerHTML" not in html
    assert "https://" not in html
    assert "http://" not in html
    assert "document-failure-synth 在做什么" in html
    assert 'src="assets/raw-page-1.png"' in html
    assert 'alt="源文档第 1 页"' in html
    assert "textContent" in html
    assert "请打开整个 `explain/` 目录下的 `index.html`，不要只拷走这一个文件" in html
    for section_id in REQUIRED_IDS:
        assert f'id="{section_id}"' in html
    for href in ["#input", "#step-1", "#step-2", "#step-3", "#step-4", "#step-5", "#step-6", "#output"]:
        assert f'href="{href}"' in html
    assert "src/synth/material.py" in html
    assert "src/synth/html_builder.py" in html
    assert "src/synth/rewrite.py" in html
    assert "src/synth/paginate.js" in html
    assert "src/synth/validate.py" in html
    assert "src/synth/gt_builder.py" in html
    assert "Keep every original element with data-node-id" in html
    assert "zhNeedsPage || enNeedsPage" in html
    assert "missing en block" in html
    assert 'f"p{page}-b{index}"' in html
    assert "落盘为" in html and "rewritten.html" in html
    assert "少 1 个 en" in html
    assert "(1000 − 2×40 − 24) / 2 = 448" in html
    assert "40–488" in html and "512–960" in html
    assert "本讲解页用步骤 4 示意图代替" in html
    assert "visualize" not in html.lower()
    assert "ocr_url.txt" not in html
    assert "max-width: 880px" in css


def test_readme_points_to_explainer() -> None:
    text = README.read_text(encoding="utf-8")
    assert "explain/index.html" in text
