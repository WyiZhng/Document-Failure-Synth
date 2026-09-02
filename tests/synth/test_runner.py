from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.synth.config import LlmConfig, OcrConfig, PageConfig, SynthConfig
from src.synth import runner


def _cfg(
    tmp_path: Path,
    copies: int = 1,
    cases: list[str] | None = None,
    max_workers: int = 1,
) -> SynthConfig:
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
        max_workers=max_workers,
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


def test_parallel_batch_keeps_seq_order(tmp_path: Path):
    import threading
    import time

    cfg = _cfg(tmp_path, copies=3, max_workers=3)
    barrier = threading.Barrier(3)
    lock = threading.Lock()
    active = 0
    max_active = 0

    def generate(case_dir, seq, seed, cfg, output_root):
        nonlocal active, max_active
        del case_dir, cfg, output_root
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            barrier.wait(timeout=2)
            time.sleep((4 - seq) * 0.02)
            path = tmp_path / f"ok_{seq}"
            path.mkdir(exist_ok=True)
            return {
                "path": str(path),
                "doc_id": f"doc_{seq}",
                "seed": seed,
                "stats": {},
            }
        finally:
            with lock:
                active -= 1

    report = runner.run_batch(cfg, workspace=tmp_path, generate_fn=generate)
    assert max_active >= 2
    assert report["n_ok"] == 3
    assert [record["seq"] for record in report["records"]] == [1, 2, 3]


def test_parallel_failure_does_not_cancel_other_jobs(tmp_path: Path):
    cfg = _cfg(tmp_path, copies=3, max_workers=3)
    calls: dict[int, list[int]] = {}

    def generate(case_dir, seq, seed, cfg, output_root):
        del case_dir, cfg, output_root
        calls.setdefault(seq, []).append(seed)
        if seq == 1:
            raise RuntimeError("job 1 failed")
        path = tmp_path / f"ok_{seq}"
        path.mkdir(exist_ok=True)
        return {
            "path": str(path),
            "doc_id": f"doc_{seq}",
            "seed": seed,
            "stats": {},
        }

    report = runner.run_batch(cfg, workspace=tmp_path, generate_fn=generate)
    assert report["n_skip"] == 1
    assert report["n_ok"] == 2
    assert report["n_discard"] == 3
    assert len(calls[1]) == 3
    assert len(set(calls[1])) == 3
    assert {
        record["seq"]
        for record in report["records"]
        if record["status"] == "ok"
    } == {2, 3}


def _write_complete_output(path: Path, seq: int) -> None:
    images = path / "images_path"
    images.mkdir(parents=True, exist_ok=True)
    (images / "raw-page-1.png").write_bytes(b"png")
    (path / "origin.json").write_text(
        json.dumps(
            {
                "doc_id": f"doc_{seq}",
                "task_id": f"synth_task_{seq:03d}",
                "images_path": str(images.resolve()),
            }
        ),
        encoding="utf-8",
    )
    for name, payload in {
        "prelabel.json": {"pages": []},
        "label.json": {"pages": []},
        "multi-page-final.json": {"doc": []},
    }.items():
        (path / name).write_text(json.dumps(payload), encoding="utf-8")


def _write_generated_like_output(path: Path, seq: int, doc: list[dict]) -> None:
    images = path / "images_path"
    images.mkdir(parents=True, exist_ok=True)
    (images / "raw-page-1.png").write_bytes(b"png")
    (path / "origin.json").write_text(
        json.dumps(
            {
                "doc_id": f"doc_{seq}",
                "task_id": f"synth_task_{seq:03d}",
                "images_path": str(images.resolve()),
            }
        ),
        encoding="utf-8",
    )
    (path / "rewritten.html").write_text("<html></html>", encoding="utf-8")
    for name, payload in {
        "prelabel.json": {"pages": []},
        "label.json": {"pages": []},
        "multi-page-final.json": {"doc": doc},
    }.items():
        (path / name).write_text(json.dumps(payload), encoding="utf-8")


def test_generate_one_rewrites_once_and_materializes_all_layouts(
    tmp_path: Path,
    monkeypatch,
):
    from types import SimpleNamespace

    cfg = _cfg(tmp_path)
    rewrite_calls: list[tuple[str, int]] = []
    materialize_calls: list[dict] = []

    def fake_load_material(case_dir, config, assets_dir):
        assets_dir.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(doc_id="source_doc", assets_dir=assets_dir)

    monkeypatch.setattr(
        runner,
        "load_material",
        fake_load_material,
    )
    monkeypatch.setattr(
        runner,
        "build_source_html",
        lambda loaded, config: "source html",
    )

    def fake_rewrite(html, config, seed=None):
        rewrite_calls.append((html, seed))
        return "rewritten html"

    def fake_materialize(
        loaded,
        rewritten,
        sample_seq,
        seed,
        config,
        output_root,
        out_dir,
        column_layout=None,
        **kwargs,
    ):
        materialize_calls.append(
            {
                "rewritten": rewritten,
                "sample_seq": sample_seq,
                "seed": seed,
                "layout": column_layout,
                "out_dir": out_dir,
                "metadata": kwargs["origin_metadata"],
            }
        )
        return {
            "path": str(out_dir.resolve()),
            "doc_id": loaded.doc_id,
            "seed": seed,
            "sample_seq": sample_seq,
            "column_layout": column_layout,
            "stats": {},
        }

    monkeypatch.setattr(runner, "rewrite_html", fake_rewrite)
    monkeypatch.setattr(runner, "materialize_document", fake_materialize)

    result = runner.generate_one(
        tmp_path / "case",
        2,
        17,
        cfg,
        tmp_path / "generated",
    )

    assert rewrite_calls == [("source html", 17)]
    assert [call["layout"] for call in materialize_calls] == ["zh-en", "en-zh"]
    assert [call["sample_seq"] for call in materialize_calls] == [3, 4]
    assert [call["rewritten"] for call in materialize_calls] == [
        "rewritten html",
        "rewritten html",
    ]
    assert [item["column_layout"] for item in result["variants"]] == [
        "zh-en",
        "en-zh",
    ]
    assert [item["sample_seq"] for item in result["variants"]] == [3, 4]
    assert not (tmp_path / "generated" / "_assets_2_17").exists()


def test_batch_lists_all_layout_variants(tmp_path: Path):
    calls: list[int] = []

    def generate(case_dir, seq, seed, cfg, output_root):
        del case_dir
        calls.append(seq)
        variants = []
        for index, layout in enumerate(cfg.column_layouts):
            path = output_root / f"sample_{seq}_{layout}"
            path.mkdir(parents=True)
            variants.append(
                {
                    "path": str(path.resolve()),
                    "doc_id": f"doc_{seq}",
                    "seed": seed,
                    "sample_seq": (seq - 1) * len(cfg.column_layouts) + index + 1,
                    "column_layout": layout,
                    "stats": {"column_layout": layout},
                }
            )
        return {"variants": variants, "seed": seed, "rewrite_seq": seq}

    report = runner.run_batch(
        _cfg(tmp_path),
        workspace=tmp_path,
        generate_fn=generate,
    )

    assert calls == [1]
    assert report["n_ok"] == 1
    assert report["n_samples_ok"] == 2
    assert report["n_samples_planned"] == 2
    record = report["records"][0]
    assert record["variant_count"] == 2
    assert [item["column_layout"] for item in record["outputs"]] == [
        "zh-en",
        "en-zh",
    ]
    listed = (tmp_path / "out" / "synth_input_path.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    assert listed == [
        str(tmp_path / "out" / "sample_1_zh-en"),
        str(tmp_path / "out" / "sample_1_en-zh"),
    ]


def test_resume_skips_complete_success_and_retries_previous_skip(tmp_path: Path):
    first_calls: list[int] = []

    def first_generate(case_dir, seq, seed, cfg, output_root):
        del case_dir, seed, cfg
        first_calls.append(seq)
        if seq == 2:
            raise RuntimeError("temporary failure")
        path = output_root / f"synth_{seq:03d}_doc_{seq}"
        _write_complete_output(path, seq)
        return {"path": str(path), "doc_id": f"doc_{seq}", "seed": 0, "stats": {}}

    cfg = _cfg(tmp_path, copies=2)
    first = runner.run_batch(cfg, workspace=tmp_path, generate_fn=first_generate)
    assert first["n_ok"] == 1
    assert first["n_skip"] == 1

    second_calls: list[int] = []

    def second_generate(case_dir, seq, seed, cfg, output_root):
        del case_dir, seed, cfg
        second_calls.append(seq)
        path = output_root / f"synth_{seq:03d}_doc_{seq}"
        _write_complete_output(path, seq)
        return {"path": str(path), "doc_id": f"doc_{seq}", "seed": 0, "stats": {}}

    second = runner.run_batch(cfg, workspace=tmp_path, generate_fn=second_generate)
    assert second["n_ok"] == 2
    assert second["n_skip"] == 0
    assert second_calls == [2]


def test_resume_skips_only_when_all_layout_variants_are_complete(tmp_path: Path):
    calls: list[int] = []

    def generate(case_dir, seq, seed, cfg, output_root):
        del case_dir
        calls.append(seq)
        variants = []
        for index, layout in enumerate(cfg.column_layouts):
            sample_seq = (seq - 1) * len(cfg.column_layouts) + index + 1
            path = output_root / f"synth_{sample_seq:03d}_doc_{layout}"
            _write_complete_output(path, sample_seq)
            variants.append(
                {
                    "path": str(path),
                    "doc_id": f"doc_{seq}",
                    "seed": seed,
                    "sample_seq": sample_seq,
                    "column_layout": layout,
                    "stats": {},
                }
            )
        return {"variants": variants, "seed": seed}

    cfg = _cfg(tmp_path)
    first = runner.run_batch(cfg, workspace=tmp_path, generate_fn=generate)
    second = runner.run_batch(cfg, workspace=tmp_path, generate_fn=generate)

    assert first["n_samples_ok"] == 2
    assert second["n_samples_ok"] == 2
    assert calls == [1]


def test_resume_requeues_corrupt_success_output(tmp_path: Path):
    calls: list[int] = []

    def generate(case_dir, seq, seed, cfg, output_root):
        del case_dir, seed, cfg
        calls.append(seq)
        path = output_root / f"synth_{seq:03d}_doc_{seq}"
        _write_complete_output(path, seq)
        return {"path": str(path), "doc_id": f"doc_{seq}", "seed": 0, "stats": {}}

    cfg = _cfg(tmp_path)
    runner.run_batch(cfg, workspace=tmp_path, generate_fn=generate)
    output = Path(cfg.output_root)
    (output / "synth_001_doc_1" / "label.json").unlink()
    runner.run_batch(cfg, workspace=tmp_path, generate_fn=generate)
    assert calls == [1, 1]


def test_resume_requeues_final_incompatible_generated_output(tmp_path: Path):
    calls: list[int] = []
    listings_seen_by_retry: list[str] = []

    invalid = {
        "id": "p0-fake1",
        "page_index": [0],
        "member": [],
        "children": [],
        "category": "list_item",
        "bbox": [],
        "text": "",
        "is_virtual": True,
        "link": False,
        "link_to": [],
    }
    valid = {
        "id": "p0-b1",
        "page_index": [0],
        "member": ["p0-b1"],
        "children": [],
        "category": ["text"],
        "bbox": [[40, 40, 400, 120]],
        "text": [""],
        "is_virtual": False,
        "link": False,
        "link_to": [],
    }

    def generate(case_dir, seq, seed, cfg, output_root):
        del case_dir, seed, cfg
        calls.append(seq)
        if len(calls) == 2:
            listings_seen_by_retry.append(
                (output_root / "synth_input_path.txt").read_text(encoding="utf-8")
            )
        path = output_root / f"synth_{seq:03d}_doc_{seq}"
        _write_generated_like_output(path, seq, [invalid if len(calls) == 1 else valid])
        return {"path": str(path), "doc_id": f"doc_{seq}", "seed": 0, "stats": {}}

    cfg = _cfg(tmp_path)
    runner.run_batch(cfg, workspace=tmp_path, generate_fn=generate)
    second = runner.run_batch(cfg, workspace=tmp_path, generate_fn=generate)

    assert calls == [1, 1]
    assert listings_seen_by_retry == [""]
    assert second["n_ok"] == 1
    assert runner._output_is_final_compatible(
        Path(cfg.output_root) / "synth_001_doc_1", cfg
    )


def test_materialize_rejects_empty_virtual_after_gt_build(tmp_path, monkeypatch):
    from types import SimpleNamespace

    cfg = _cfg(tmp_path)
    material = SimpleNamespace(
        doc_id="doc-test",
        blocks=[],
        tree=[],
        assets_dir=tmp_path / "assets",
    )
    out_dir = tmp_path / "out" / "invalid"

    def fake_render(html, images_dir, config, column_layout=None):
        del html, config, column_layout
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "raw-page-1.png").write_bytes(b"png")
        return []

    def fake_gt(*args, **kwargs):
        del args, kwargs
        out_dir.joinpath("multi-page-final.json").write_text(
            json.dumps(
                {
                    "doc": [
                        {
                            "id": "p0-fake1",
                            "page_index": [0],
                            "member": [],
                            "children": [],
                            "category": "list_item",
                            "bbox": [],
                            "text": "",
                            "is_virtual": True,
                            "link": False,
                            "link_to": [],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(runner, "render_pages", fake_render)
    monkeypatch.setattr(runner, "validate_doc", lambda *args, **kwargs: SimpleNamespace(ok=True, errors=[], stats={}))
    monkeypatch.setattr(runner, "build_gt", fake_gt)
    monkeypatch.setattr(runner, "draw_overlays", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_ocr_candidates", lambda config: [SimpleNamespace(url="fake")])
    monkeypatch.setattr(runner, "build_prelabel", lambda *args, **kwargs: {"pages": []})

    with pytest.raises(RuntimeError, match="empty|children"):
        runner.materialize_document(
            material,
            "<html></html>",
            1,
            1,
            cfg,
            tmp_path / "out",
            out_dir,
            column_layout="zh-en",
        )


def test_materialize_rejects_final_tree_page_without_rendered_image(tmp_path, monkeypatch):
    from types import SimpleNamespace

    cfg = _cfg(tmp_path)
    material = SimpleNamespace(
        doc_id="doc-test",
        blocks=[],
        tree=[],
        assets_dir=tmp_path / "assets",
    )
    out_dir = tmp_path / "out" / "invalid-page"

    def fake_render(html, images_dir, config, column_layout=None):
        del html, config, column_layout
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "raw-page-1.png").write_bytes(b"png")
        return []

    def fake_gt(*args, **kwargs):
        del args, kwargs
        out_dir.joinpath("multi-page-final.json").write_text(
            json.dumps(
                {
                    "doc": [
                        {
                            "id": "p2-b1",
                            "page_index": [2],
                            "member": ["p2-b1"],
                            "children": [],
                            "category": ["text"],
                            "bbox": [[40, 40, 400, 120]],
                            "text": [""],
                            "is_virtual": False,
                            "link": False,
                            "link_to": [],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(runner, "render_pages", fake_render)
    monkeypatch.setattr(
        runner,
        "validate_doc",
        lambda *args, **kwargs: SimpleNamespace(ok=True, errors=[], stats={}),
    )
    monkeypatch.setattr(runner, "build_gt", fake_gt)

    with pytest.raises(RuntimeError, match="rendered page|page 2"):
        runner.materialize_document(
            material,
            "<html></html>",
            1,
            1,
            cfg,
            tmp_path / "out",
            out_dir,
            column_layout="zh-en",
        )


def test_materialize_accepts_final_tree_with_rendered_pages(tmp_path, monkeypatch):
    from types import SimpleNamespace

    cfg = _cfg(tmp_path)
    material = SimpleNamespace(
        doc_id="doc-test",
        blocks=[],
        tree=[],
        assets_dir=tmp_path / "assets",
    )
    out_dir = tmp_path / "out" / "valid"

    def fake_render(html, images_dir, config, column_layout=None):
        del html, config, column_layout
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "raw-page-1.png").write_bytes(b"png")
        return []

    def fake_gt(*args, **kwargs):
        del args, kwargs
        out_dir.joinpath("multi-page-final.json").write_text(
            json.dumps(
                {
                    "doc": [
                        {
                            "id": "p0-b1",
                            "page_index": [0],
                            "member": ["p0-b1"],
                            "children": [],
                            "category": ["text"],
                            "bbox": [[40, 40, 400, 120]],
                            "text": [""],
                            "is_virtual": False,
                            "link": False,
                            "link_to": [],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(runner, "render_pages", fake_render)
    monkeypatch.setattr(runner, "validate_doc", lambda *args, **kwargs: SimpleNamespace(ok=True, errors=[], stats={}))
    monkeypatch.setattr(runner, "build_gt", fake_gt)
    monkeypatch.setattr(runner, "draw_overlays", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_ocr_candidates", lambda config: [SimpleNamespace(url="fake")])
    monkeypatch.setattr(runner, "build_prelabel", lambda *args, **kwargs: {"pages": []})

    result = runner.materialize_document(
        material,
        "<html></html>",
        1,
        1,
        cfg,
        tmp_path / "out",
        out_dir,
        column_layout="zh-en",
    )

    assert result["path"] == str(out_dir.resolve())


def test_rendered_page_indices_require_contiguous_zero_based_ledger(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "raw-page-1.png").write_bytes(b"png")
    (images / "raw-page-3.png").write_bytes(b"png")

    with pytest.raises(RuntimeError, match="not contiguous"):
        runner._rendered_page_indices(images)


def test_resume_rejects_changed_semantic_config(tmp_path: Path):
    from dataclasses import replace

    calls: list[int] = []

    def generate(case_dir, seq, seed, cfg, output_root):
        del case_dir, seed, cfg
        calls.append(seq)
        path = output_root / f"synth_{seq:03d}_doc_{seq}"
        _write_complete_output(path, seq)
        return {"path": str(path), "doc_id": f"doc_{seq}", "seed": 0, "stats": {}}

    cfg = _cfg(tmp_path)
    runner.run_batch(cfg, workspace=tmp_path, generate_fn=generate)
    with pytest.raises(ValueError, match="manifest"):
        runner.run_batch(
            replace(cfg, seed=cfg.seed + 1),
            workspace=tmp_path,
            generate_fn=generate,
        )
    assert calls == [1]


@pytest.mark.render
def test_two_real_generation_jobs_do_not_share_render_output(
    tmp_path: Path,
    tiny_case_dir: Path,
    monkeypatch,
):
    from bs4 import BeautifulSoup
    from dataclasses import replace

    def fake_rewrite(html, cfg, seed=None):
        del seed
        soup = BeautifulSoup(html, "lxml")
        for zh in list(soup.select("[data-node-id][data-lang='zh']")):
            if zh.name == "img" or zh.get("data-category") not in cfg.translate_categories:
                continue
            en = soup.new_tag(
                "div",
                attrs={
                    "data-node-id": zh["data-node-id"],
                    "data-category": zh.get("data-category", "text"),
                    "data-lang": "en",
                },
            )
            en.string = "English text"
            zh.insert_after(en)
        return str(soup)

    def fake_prelabel(image_paths, settings=None):
        del settings
        return {
            "pages": [
                {"page_index": index, "blocks": []}
                for index, _ in enumerate(image_paths)
            ]
        }

    monkeypatch.setattr(runner, "rewrite_html", fake_rewrite)
    monkeypatch.setattr(runner, "build_prelabel", fake_prelabel)
    cfg = replace(
        _cfg(tmp_path, copies=2, max_workers=2),
        source_cases=[str(tiny_case_dir)],
        ocr=OcrConfig(url="http://fake-ocr", timeout=1),
    )
    runner.ensure_runtime_env(Path(__file__).resolve().parents[2])

    report = runner.run_batch(cfg, workspace=tmp_path)

    assert report["n_ok"] == 2
    assert report["n_samples_ok"] == 4
    assert [record["seq"] for record in report["records"]] == [1, 2]
    output_paths = [
        Path(output["path"])
        for record in report["records"]
        for output in record["outputs"]
    ]
    assert len(set(output_paths)) == 4
    assert len(output_paths) == 4
    listed = (tmp_path / "out" / "synth_input_path.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    assert [str(path) for path in output_paths] == listed
    for output_record, output in zip(
        [output for record in report["records"] for output in record["outputs"]],
        output_paths,
    ):
        assert output.is_dir()
        assert (output / "origin.json").is_file()
        assert (output / "prelabel.json").is_file()
        assert (output / "label.json").is_file()
        assert (output / "multi-page-final.json").is_file()
        assert list((output / "images_path").glob("raw-page-*.png"))
        origin = json.loads((output / "origin.json").read_text(encoding="utf-8"))
        assert origin["task_id"] == (
            f"synth_task_{output_record['sample_seq']:03d}"
        )
        assert origin["column_layout"] == output_record["column_layout"]
