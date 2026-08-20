from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
import requests

from src.synth.ocr_prelabel import OcrSettings, build_prelabel, load_ocr_settings

LIVE_PAGE = Path(
    "data/examples/bilingual_v1/synth_001_doc_21f4dc879ffd/images_path/raw-page-1.png"
)


def _fake_detections(*_args, **_kwargs):
    return [
        {
            "block_id": 1,
            "bbox": [10.0, 20.0, 100.0, 40.0],
            "text": "标题",
            "category": "title",
        },
        {
            "block_id": 2,
            "bbox": [10.0, 50.0, 200.0, 90.0],
            "text": "正文",
            "category": "text",
        },
        {
            "block_id": 3,
            "bbox": [10.0, 100.0, 180.0, 220.0],
            "text": "",
            "category": "figure",
        },
        {
            "block_id": 4,
            "bbox": [10.0, 230.0, 80.0, 250.0],
            "text": "页脚",
            "category": "footer",
        },
        {
            "block_id": 5,
            "bbox": [10.0, 260.0, 90.0, 280.0],
            "text": "未知块",
            "category": "weird_label",
        },
    ]


def test_build_prelabel_schema_and_category_map(monkeypatch):
    monkeypatch.setattr("src.synth.ocr_prelabel.layout_parse", _fake_detections)
    settings = OcrSettings(url="http://ocr.test", timeout=5)
    result = build_prelabel([Path("page-a.png"), Path("page-b.png")], settings)

    assert list(result) == ["pages"]
    assert [p["page_index"] for p in result["pages"]] == [0, 1]
    blocks = result["pages"][0]["blocks"]
    assert [b["block_id"] for b in blocks] == [1, 2, 3, 4, 5]
    assert [b["category"] for b in blocks] == [
        "paragraph_title",
        "text",
        "image",
        "footer",
        "text",
    ]
    for block in blocks:
        assert block["source"] == "paddle_ocr"
        assert block["score"] == 1.0
        assert set(block) == {
            "block_id",
            "bbox",
            "text",
            "category",
            "score",
            "source",
        }


def test_unknown_category_falls_back_to_text(monkeypatch, caplog):
    monkeypatch.setattr("src.synth.ocr_prelabel.layout_parse", _fake_detections)
    caplog.set_level(logging.WARNING, logger="src.synth.ocr_prelabel")
    result = build_prelabel([Path("page.png")], OcrSettings(url="http://ocr.test"))
    assert result["pages"][0]["blocks"][4]["category"] == "text"
    assert "weird_label" in caplog.text


def test_load_ocr_settings_prefers_env(monkeypatch):
    monkeypatch.setenv("PADDLE_OCR_API_URL", "http://env.example:9/")
    settings = load_ocr_settings()
    assert settings.url == "http://env.example:9"


def test_load_ocr_settings_uses_yaml_when_env_missing(monkeypatch):
    monkeypatch.delenv("PADDLE_OCR_API_URL", raising=False)
    from src.synth.config import LlmConfig, OcrConfig, PageConfig, SynthConfig

    cfg = SynthConfig(
        source_cases=[],
        copies_per_case=1,
        max_source_pages=4,
        seed=1,
        output_root="x",
        translate_categories=["text"],
        page=PageConfig(1000, 1414, 40, 24),
        llm=LlmConfig("SYNTH_LLM_MODEL", "OPENAI_BASE_URL", "OPENAI_API_KEY", 0.7, 1),
        ocr=OcrConfig(url="http://yaml.example:8/", timeout=12),
    )
    settings = load_ocr_settings(cfg)
    assert settings.url == "http://yaml.example:8"
    assert settings.timeout == 12


def test_load_ocr_settings_errors_when_unconfigured(monkeypatch):
    monkeypatch.delenv("PADDLE_OCR_API_URL", raising=False)
    with pytest.raises(ValueError, match="PADDLE_OCR_API_URL"):
        load_ocr_settings(None)


@pytest.mark.paddle
def test_paddle_live_bilingual_page():
    if not LIVE_PAGE.is_file():
        pytest.skip(f"live fixture missing: {LIVE_PAGE}")
    env_url = os.environ.get("PADDLE_OCR_API_URL", "").strip().rstrip("/")
    if not env_url:
        pytest.skip("PADDLE_OCR_API_URL not set")

    last_error: Exception | None = None
    result = None
    try:
        result = build_prelabel([LIVE_PAGE], OcrSettings(url=env_url, timeout=300))
    except (requests.RequestException, OSError) as exc:
        last_error = exc
    if result is None:
        pytest.skip(f"PaddleOCR service unreachable: {last_error}")

    blocks = result["pages"][0]["blocks"]
    assert blocks
    joined = "".join(str(b.get("text") or "") for b in blocks)
    assert ("山东" in joined) or ("Industry" in joined), joined[:500]
