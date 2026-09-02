from src.synth.translation_types import BlockPlan, TranslationBundle


def test_block_plan_distinguishes_actions() -> None:
    translate = BlockPlan("a", "text", "你好", "zh", "en", "translate", "hello")
    copy = BlockPlan("b", "text", "2024", "zh", "en", "copy", "2024")
    source_only = BlockPlan("c", "image", "", "zh", None, "source_only", None)

    assert translate.action == "translate"
    assert copy.action == "copy"
    assert source_only.target_lang is None
    assert source_only.target_text is None


def test_translation_bundle_lookup_and_serialization_are_deterministic() -> None:
    plan = BlockPlan("p0-b1", "text", "你好", "zh", "en", "translate", "hello")
    bundle = TranslationBundle(
        plans={"p0-b1": plan},
        dropped={"p0-b2": "empty leaf"},
        warnings=[{"node_id": "p0-b2", "reason": "empty leaf"}],
    )

    assert bundle.plan_for("p0-b1") == plan
    assert bundle.plan_for("missing") is None
    assert bundle.to_dict() == {
        "plans": {"p0-b1": plan.to_dict()},
        "dropped": {"p0-b2": "empty leaf"},
        "warnings": [{"node_id": "p0-b2", "reason": "empty leaf"}],
    }
