from __future__ import annotations

import json
import os
from pathlib import Path

from src.synth.config import LlmConfig, OcrConfig, PageConfig, SynthConfig
from src.synth import runner


def _cfg(tmp_path: Path, copies: int = 1, cases: list[str] | None = None) -> SynthConfig:
    case_names = cases or ["case_a"]
    if not any(any(ch in name for ch in "*?[") for name in case_names):
        for name in case_names:
            path = tmp_path / name
            path.mkdir(parents=True, exist_ok=True)
            (path / "origin.json").write_text("{}", encoding="utf-8")
            (path / "multi-page-final-fillin.json").write_text("[]", encoding="utf-8")
    return SynthConfig(
        source_cases=case_names,
        copies_per_case=copies,
        max_source_pages=4,
        seed=42,
        output_root=str(tmp_path / "out"),
        translate_categories=["text", "paragraph_title", "doc_title"],
        page=PageConfig(width=1000, height=1414, margin=40, column_gap=24),
        llm=LlmConfig(
            model_env="SYNTH_LLM_MODEL",
            base_url_env="OPENAI_BASE_URL",
            api_key_env="OPENAI_API_KEY",
            temperature=0.7,
            max_retries=1,
        ),
        ocr=OcrConfig(url="", timeout=300),
    )


def test_retry_succeeds_on_second_attempt(tmp_path: Path) -> None:
    calls: list[int] = []

    def generate(case_dir, seq, seed, cfg, output_root):
        del case_dir, cfg, output_root
        calls.append(seed)
        if len(calls) == 1:
            raise RuntimeError("validate failed")
        path = tmp_path / f"ok_{seq}"
        path.mkdir()
        return {
            "path": str(path.resolve()),
            "doc_id": "doc_x",
            "seed": seed,
            "stats": {"n_zh": 2, "n_en": 2, "en_cross_page": 1, "n_errors": 0},
        }

    report = runner.run_batch(_cfg(tmp_path), workspace=tmp_path, generate_fn=generate)
    assert report["n_ok"] == 1
    assert report["n_skip"] == 0
    assert report["n_discard"] == 1
    assert report["n_planned"] == 1
    assert len(calls) == 2
    assert calls[0] != calls[1]
    assert report["records"][0]["status"] == "ok"

    listing = (tmp_path / "out" / "synth_input_path.txt").read_text(encoding="utf-8").strip()
    assert listing.endswith("ok_1")
    saved = json_report(tmp_path)
    assert saved["records"][0]["stats"]["en_cross_page"] == 1


def test_skip_after_three_failures(tmp_path: Path) -> None:
    calls: list[int] = []

    def generate(case_dir, seq, seed, cfg, output_root):
        del case_dir, seq, cfg, output_root
        calls.append(seed)
        raise RuntimeError("always fail")

    report = runner.run_batch(_cfg(tmp_path), workspace=tmp_path, generate_fn=generate)
    assert report["n_ok"] == 0
    assert report["n_skip"] == 1
    assert report["n_discard"] == 3
    assert len(calls) == 3
    assert len(set(calls)) == 3
    assert report["records"][0]["status"] == "skipped"
    assert "always fail" in report["records"][0]["error"]
    listing = (tmp_path / "out" / "synth_input_path.txt").read_text(encoding="utf-8")
    assert listing == ""


def test_second_copy_can_succeed_after_first_skipped(tmp_path: Path) -> None:
    def generate(case_dir, seq, seed, cfg, output_root):
        if seq == 1:
            raise RuntimeError("first copy dead")
        path = tmp_path / "ok_2"
        path.mkdir(exist_ok=True)
        return {"path": str(path.resolve()), "doc_id": "doc_x", "seed": seed, "stats": {}}

    report = runner.run_batch(
        _cfg(tmp_path, copies=2), workspace=tmp_path, generate_fn=generate
    )
    assert report["n_ok"] == 1
    assert report["n_skip"] == 1
    assert report["n_discard"] == 3
    assert [r["status"] for r in report["records"]] == ["skipped", "ok"]


def json_report(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))


def test_ensure_runtime_env_does_not_read_trainer_inference_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("SYNTH_LLM_MODEL", raising=False)
    fake = tmp_path / "src/inference/config/pipeline.yaml"
    fake.parent.mkdir(parents=True)
    fake.write_text(
        "api_config:\n  key: sk-SHOULD-NOT-LOAD\n  url: http://trainer.example\n  model: trainer-model\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    runner.ensure_runtime_env(tmp_path)
    assert os.environ.get("OPENAI_API_KEY") != "sk-SHOULD-NOT-LOAD"
    assert os.environ.get("SYNTH_LLM_MODEL") != "trainer-model"


def test_ensure_runtime_env_loads_local_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SYNTH_LLM_MODEL", raising=False)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=sk-from-dotenv\nSYNTH_LLM_MODEL=local-model\n",
        encoding="utf-8",
    )
    runner.ensure_runtime_env(tmp_path)
    assert os.environ.get("OPENAI_API_KEY") == "sk-from-dotenv"
    assert os.environ.get("SYNTH_LLM_MODEL") == "local-model"


def test_batch_one_copy_per_source_case(tmp_path: Path) -> None:
    seen: list[tuple[str, int]] = []

    def generate(case_dir, seq, seed, cfg, output_root):
        del seed, cfg, output_root
        seen.append((Path(case_dir).name, seq))
        path = tmp_path / f"ok_{seq}"
        path.mkdir()
        return {"path": str(path.resolve()), "doc_id": Path(case_dir).name, "seed": 0, "stats": {}}

    case_a = tmp_path / "data/source/case_a"
    case_b = tmp_path / "data/source/case_b"
    for path in (case_a, case_b):
        path.mkdir(parents=True)
        (path / "origin.json").write_text("{}", encoding="utf-8")
        (path / "multi-page-final-fillin.json").write_text("[]", encoding="utf-8")

    cfg = _cfg(tmp_path, copies=1, cases=["data/source/*"])
    report = runner.run_batch(cfg, workspace=tmp_path, generate_fn=generate)
    assert report["n_ok"] == 2
    assert report["n_planned"] == 2
    assert seen == [("case_a", 1), ("case_b", 2)]
