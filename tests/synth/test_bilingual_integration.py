from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.synth import runner
from src.synth.config import LlmConfig, OcrConfig, PageConfig, SynthConfig, VariantSpec
from src.synth.translation_types import BlockPlan, TranslationBundle


def test_generate_one_reuses_one_bundle_and_html_for_four_variants(
    tmp_path: Path, monkeypatch
):
    cfg = SynthConfig(
        source_cases=["case"],
        copies_per_case=1,
        max_source_pages=None,
        seed=1,
        output_root=str(tmp_path / "out"),
        translate_categories=["text"],
        page=PageConfig(width=1000, height=1414, margin=40, column_gap=24),
        llm=LlmConfig(
            model_env="SYNTH_LLM_MODEL",
            base_url_env="OPENAI_BASE_URL",
            api_key_env="OPENAI_API_KEY",
            temperature=0.0,
            max_retries=1,
        ),
        ocr=OcrConfig(url="", timeout=1),
        column_layouts=["zh-en", "en-zh"],
        max_workers=1,
        variant_specs=[
            VariantSpec("zh-en_no-cross", "zh-en", "no-cross"),
            VariantSpec("zh-en_cross", "zh-en", "cross"),
            VariantSpec("en-zh_no-cross", "en-zh", "no-cross"),
            VariantSpec("en-zh_cross", "en-zh", "cross"),
        ],
    )
    loaded = SimpleNamespace(
        doc_id="source-doc",
        assets_dir=tmp_path / "assets",
        blocks=[],
        tree=[],
    )
    bundle = TranslationBundle(plans={}, dropped={}, warnings=[])
    bundle_calls = []
    html_calls = []
    materialize_calls = []

    monkeypatch.setattr(runner, "load_material", lambda *args: loaded)

    def fake_translate(material, config, seed=None):
        bundle_calls.append((material, config, seed))
        return bundle

    def fake_build(material, received_bundle, config):
        html_calls.append((material, received_bundle, config))
        return "bilingual html"

    def fake_materialize(material, html, sample_seq, seed, config, output_root, out_dir, **kwargs):
        materialize_calls.append((material, html, kwargs.get("plans"), kwargs["origin_metadata"]))
        return {
            "path": str(out_dir.resolve()),
            "doc_id": material.doc_id,
            "seed": seed,
            "sample_seq": sample_seq,
            "column_layout": kwargs["column_layout"],
            "stats": {},
        }

    monkeypatch.setattr(runner, "translate_material", fake_translate)
    monkeypatch.setattr(runner, "build_bilingual_html", fake_build)
    monkeypatch.setattr(runner, "materialize_document", fake_materialize)

    result = runner.generate_one(
        tmp_path / "case",
        1,
        17,
        cfg,
        tmp_path / "generated",
    )

    assert len(bundle_calls) == 1
    assert len(html_calls) == 1
    assert len(materialize_calls) == 4
    assert all(call[1] == "bilingual html" for call in materialize_calls)
    assert all(call[2] is bundle for call in materialize_calls)
    assert [item["column_layout"] for item in result["variants"]] == [
        "zh-en", "zh-en", "en-zh", "en-zh"
    ]
    assert [item["variant_name"] for item in result["variants"]] == [
        "zh-en_no-cross", "zh-en_cross", "en-zh_no-cross", "en-zh_cross"
    ]
    assert [call[3]["pagination_mode"] for call in materialize_calls] == [
        "no-cross", "cross", "no-cross", "cross"
    ]
    assert [call[3]["synchronize_pairs"] for call in materialize_calls] == [
        True, False, True, False
    ]


def test_translation_failure_metadata_is_sample_level():
    bundle = TranslationBundle(
        plans={
            "p0-b1": BlockPlan(
                "p0-b1", "text", "中文", "zh", "en", "translate", None
            )
        },
        dropped={"p0-b1": "empty translation"},
        warnings=[
            {
                "node_id": "p0-b1",
                "category": "text",
                "reason": "empty translation",
                "attempts": 2,
                "dropped": True,
            }
        ],
    )

    stats = {
        "dropped_node_count": len(bundle.dropped),
        "translation_warning_count": len(bundle.warnings),
        "dropped_node_ids": list(bundle.dropped),
        "translation_warnings": list(bundle.warnings),
    }

    assert stats["dropped_node_count"] == 1
    assert stats["translation_warning_count"] == 1
    assert stats["dropped_node_ids"] == ["p0-b1"]
