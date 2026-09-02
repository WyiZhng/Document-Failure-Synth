from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.synth.batch_state import (
    append_progress,
    find_recoverable_output,
    find_recoverable_outputs,
    load_latest_progress,
    load_or_create_manifest,
    output_is_complete,
    write_json_atomic,
    write_text_atomic,
)


def _write_complete_output(path: Path, seq: int, metadata: dict | None = None) -> None:
    images = path / "images_path"
    images.mkdir(parents=True, exist_ok=True)
    (images / "raw-page-1.png").write_bytes(b"png")
    (path / "origin.json").write_text(
        json.dumps(
            {
                "doc_id": f"doc_{seq}",
                "task_id": f"synth_task_{seq:03d}",
                "images_path": str(images.resolve()),
                **(metadata or {}),
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


def test_manifest_is_created_and_same_payload_is_reused(tmp_path: Path):
    path = tmp_path / "batch_manifest.json"
    jobs = [{"job_id": "000001", "seq": 1, "case": "/case/a", "copy_index": 0}]
    first, created = load_or_create_manifest(
        path, fingerprint_payload={"seed": 42}, jobs=jobs
    )
    second, reused = load_or_create_manifest(
        path, fingerprint_payload={"seed": 42}, jobs=jobs
    )
    assert created is True
    assert reused is False
    assert first == second


def test_manifest_mismatch_fails_fast(tmp_path: Path):
    path = tmp_path / "batch_manifest.json"
    load_or_create_manifest(path, fingerprint_payload={"seed": 42}, jobs=[])
    with pytest.raises(ValueError, match="manifest"):
        load_or_create_manifest(path, fingerprint_payload={"seed": 43}, jobs=[])


def test_progress_keeps_latest_event_and_ignores_partial_last_line(tmp_path: Path):
    path = tmp_path / "progress.jsonl"
    append_progress(path, {"job_id": "000001", "status": "running"})
    append_progress(path, {"job_id": "000001", "status": "ok"})
    path.write_text(path.read_text(encoding="utf-8") + '{"job_id":', encoding="utf-8")
    assert load_latest_progress(path)["000001"]["status"] == "ok"


def test_progress_rejects_malformed_non_final_line(tmp_path: Path):
    path = tmp_path / "progress.jsonl"
    path.write_text('{"job_id":\n{"job_id": "000001", "status": "ok"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="progress"):
        load_latest_progress(path)


@pytest.mark.parametrize(
    "missing",
    [
        "origin.json",
        "prelabel.json",
        "label.json",
        "multi-page-final.json",
        "images_path/raw-page-1.png",
    ],
)
def test_output_is_complete_requires_phase0_files(tmp_path: Path, missing: str):
    output = tmp_path / "synth_001_doc"
    _write_complete_output(output, seq=1)
    (output / missing).unlink()
    assert output_is_complete(output, seq=1) is False


def test_output_is_complete_checks_task_id(tmp_path: Path):
    output = tmp_path / "synth_001_doc"
    _write_complete_output(output, seq=1)
    origin_path = output / "origin.json"
    origin = json.loads(origin_path.read_text(encoding="utf-8"))
    origin["task_id"] = "synth_task_002"
    origin_path.write_text(json.dumps(origin), encoding="utf-8")
    assert output_is_complete(output, seq=1) is False


def test_find_recoverable_output_requires_one_valid_candidate(tmp_path: Path):
    output_root = tmp_path / "out"
    valid = output_root / "synth_001_doc_a"
    _write_complete_output(valid, seq=1)
    assert find_recoverable_output(output_root, seq=1) == valid

    second = output_root / "synth_001_doc_b"
    _write_complete_output(second, seq=1)
    assert find_recoverable_output(output_root, seq=1) is None


def test_find_recoverable_outputs_requires_a_complete_variant_set(tmp_path: Path):
    output_root = tmp_path / "out"
    first = output_root / "synth_001_doc_zh-en"
    second = output_root / "synth_002_doc_en-zh"
    _write_complete_output(first, seq=1)
    _write_complete_output(second, seq=2)

    assert find_recoverable_outputs(output_root, seq=1, variant_count=2) == [
        first,
        second,
    ]

    (second / "label.json").unlink()
    assert find_recoverable_outputs(output_root, seq=1, variant_count=2) is None


def test_find_recoverable_outputs_matches_explicit_variant_metadata(tmp_path: Path):
    output_root = tmp_path / "out"
    specs = [
        {
            "name": "zh-en_no-cross",
            "column_layout": "zh-en",
            "pagination_mode": "no-cross",
            "synchronize_pairs": True,
        },
        {
            "name": "zh-en_cross",
            "column_layout": "zh-en",
            "pagination_mode": "cross",
            "synchronize_pairs": False,
        },
    ]
    first = output_root / "synth_001_doc_zh-en_no-cross"
    second = output_root / "synth_002_doc_zh-en_cross"
    _write_complete_output(
        first,
        seq=1,
        metadata={
            "sample_seq": 1,
            "variant_name": specs[0]["name"],
            "column_layout": specs[0]["column_layout"],
            "pagination_mode": specs[0]["pagination_mode"],
            "synchronize_pairs": specs[0]["synchronize_pairs"],
        },
    )
    _write_complete_output(
        second,
        seq=2,
        metadata={
            "sample_seq": 2,
            "variant_name": specs[1]["name"],
            "column_layout": specs[1]["column_layout"],
            "pagination_mode": specs[1]["pagination_mode"],
            "synchronize_pairs": specs[1]["synchronize_pairs"],
        },
    )
    assert find_recoverable_outputs(output_root, 1, variant_specs=specs) == [
        first,
        second,
    ]

    origin_path = second / "origin.json"
    origin = json.loads(origin_path.read_text(encoding="utf-8"))
    origin["pagination_mode"] = "no-cross"
    origin_path.write_text(json.dumps(origin), encoding="utf-8")
    assert find_recoverable_outputs(output_root, 1, variant_specs=specs) is None


def test_atomic_writes_leave_target_readable_without_temp_files(tmp_path: Path):
    json_path = tmp_path / "report.json"
    text_path = tmp_path / "paths.txt"
    write_json_atomic(json_path, {"ok": True})
    write_text_atomic(text_path, "/tmp/output\n")
    assert json.loads(json_path.read_text(encoding="utf-8")) == {"ok": True}
    assert text_path.read_text(encoding="utf-8") == "/tmp/output\n"
    assert list(tmp_path.glob(".*.tmp")) == []
