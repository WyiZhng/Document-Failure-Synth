from __future__ import annotations

import copy
import json
from dataclasses import replace
from types import SimpleNamespace

from src.synth.material import Material, SourceBlock
from src.synth.rewrite import (
    infer_default_language,
    is_neutral_text,
    split_translation_batches,
    translate_material,
)
from src.synth.translation_types import TranslationBundle


class StructuredLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []
        self.prompts: list[str] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create),
        )

    def create(self, **kwargs):
        prompt = kwargs["messages"][-1]["content"]
        self.prompts.append(prompt)
        raw = prompt.split("Records:\n", 1)[1]
        records = json.loads(raw)
        self.calls.append(records)
        payload = self.responses.pop(0) if self.responses else []
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        )


def _material(blocks: list[SourceBlock], nodes_by_id=None) -> Material:
    return Material(
        doc_id="doc",
        document_type="test",
        blocks=blocks,
        tree=[],
        assets_dir=None,
        nodes_by_id=nodes_by_id or {},
    )


def test_language_helpers_and_page_batches():
    blocks = [
        SourceBlock("zh", 0, "text", "中文", None),
        SourceBlock("en", 0, "text", "English", None),
        SourceBlock("later", 1, "text", "later", None),
    ]
    assert infer_default_language(blocks) == "en"
    assert is_neutral_text("2024-01-01", "date")
    assert is_neutral_text("EQ_001", "identifier")
    assert not is_neutral_text("日期：2024-01-01", "date")
    batches = split_translation_batches(blocks, max_chars=8)
    assert [[block.id for block in batch] for batch in batches] == [
        ["zh"],
        ["en"],
        ["later"],
    ]


def test_mixed_language_records_translate_in_opposite_directions_and_retry_only_failures(
    material_fixture, cfg
):
    material = replace(
        material_fixture,
        blocks=[
            SourceBlock("zh", 0, "text", "中文内容", None),
            SourceBlock("en", 0, "text", "English content", None),
        ],
    )
    first = [
        {
            "node_id": "zh",
            "source_lang": "zh",
            "target_lang": "en",
            "translation": "Chinese content",
        }
    ]
    second = [
        {
            "node_id": "en",
            "source_lang": "en",
            "target_lang": "zh",
            "translation": "中文内容",
        }
    ]
    client = StructuredLLM([first, second])

    bundle = translate_material(material, cfg, client=client, seed=3)

    assert isinstance(bundle, TranslationBundle)
    assert bundle.plan_for("zh").target_lang == "en"
    assert bundle.plan_for("zh").target_text == "Chinese content"
    assert bundle.plan_for("en").source_lang == "en"
    assert bundle.plan_for("en").target_lang == "zh"
    assert bundle.plan_for("en").target_text == "中文内容"
    assert [record["node_id"] for record in client.calls[0]] == ["zh", "en"]
    assert [record["node_id"] for record in client.calls[1]] == ["en"]
    assert bundle.dropped == {}


def test_copy_and_empty_relation_only_plans_do_not_call_model(material_fixture, cfg):
    relation_node = {
        "id": "anchor",
        "member": ["anchor"],
        "children": [],
        "link": True,
        "link_to": [{"id": "target"}],
    }
    material = _material(
        [
            SourceBlock("number", 0, "text", "2013-002", None),
            SourceBlock("empty-leaf", 0, "text", "", None),
            SourceBlock("anchor", 0, "text", "", None),
        ],
        {"anchor": relation_node},
    )
    client = StructuredLLM([])

    bundle = translate_material(material, cfg, client=client)

    assert client.calls == []
    assert bundle.plan_for("number").action == "copy"
    assert bundle.plan_for("number").target_text == "2013-002"
    assert bundle.plan_for("anchor").action == "source_only"
    assert "empty-leaf" in bundle.dropped


def test_malformed_response_drops_nodes_after_exactly_one_retry(material_fixture, cfg):
    material = replace(
        material_fixture,
        blocks=[SourceBlock("zh", 0, "text", "中文", None)],
    )
    client = StructuredLLM(["not json", "still not json"])

    bundle = translate_material(material, cfg, client=client)

    assert len(client.calls) == 2
    assert [record["node_id"] for record in client.calls[0]] == ["zh"]
    assert [record["node_id"] for record in client.calls[1]] == ["zh"]
    assert "invalid JSON response" in client.prompts[1]
    assert "zh" in bundle.dropped
    assert bundle.plan_for("zh").target_text is None
    assert bundle.warnings[0]["attempts"] == 2


def test_client_initialization_error_is_node_level_and_bounded(
    material_fixture, cfg, monkeypatch
):
    material = replace(
        material_fixture,
        blocks=[SourceBlock("zh", 0, "text", "中文", None)],
    )
    calls = []

    def fail_to_create_client(config):
        calls.append(config)
        raise RuntimeError("missing API key")

    monkeypatch.setattr("src.synth.rewrite._openai_client", fail_to_create_client)
    bundle = translate_material(material, cfg)

    assert len(calls) == 2
    assert bundle.dropped["zh"] == "translation API error: missing API key; translation API error: missing API key"
    assert bundle.warnings[0]["attempts"] == 2


def test_changed_category_in_response_is_retried_and_dropped(material_fixture, cfg):
    material = replace(
        material_fixture,
        blocks=[SourceBlock("zh", 0, "text", "中文", None)],
    )
    response = [
        {
            "node_id": "zh",
            "category": "paragraph_title",
            "source_lang": "zh",
            "target_lang": "en",
            "translation": "Chinese",
        }
    ]
    client = StructuredLLM([response, response])

    bundle = translate_material(material, cfg, client=client)

    assert len(client.calls) == 2
    assert bundle.plan_for("zh").target_text is None
    assert "source category changed" in bundle.dropped["zh"]


def test_translation_never_mutates_source_material(material_fixture, cfg):
    before = copy.deepcopy(material_fixture.tree)
    client = StructuredLLM(
        [[
            {
                "node_id": "p0-b1",
                "source_lang": "zh",
                "target_lang": "en",
                "source_text": "标题一",
                "translation": "Title One",
            },
            {
                "node_id": "p0-b2",
                "source_lang": "zh",
                "target_lang": "en",
                "source_text": "正文内容",
                "translation": "Body content",
            },
        ]]
    )

    translate_material(material_fixture, cfg, client=client)

    assert material_fixture.tree == before
    assert [block.text for block in material_fixture.blocks] == ["标题一", "正文内容"]
