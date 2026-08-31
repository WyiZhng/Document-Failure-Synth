from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
import logging
import os
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from src.synth.batch_state import (
    append_progress,
    find_recoverable_output,
    load_latest_progress,
    load_or_create_manifest,
    output_is_complete,
    write_json_atomic,
    write_text_atomic,
)
from src.synth.config import SynthConfig, choose_column_layout, expand_source_cases, load_config
from src.synth.gt_builder import build_gt
from src.synth.html_builder import build_source_html
from src.synth.material import load_material
from src.synth.ocr_prelabel import OcrSettings, build_prelabel
from src.synth.render import render_pages
from src.synth.rewrite import rewrite_html
from src.synth.validate import validate_doc, validate_page_projection
from src.synth.visualize import draw_overlays

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    seq: int
    case_dir: Path
    copy_index: int
    base_seed: int


@dataclass
class JobResult:
    job: JobSpec
    status: str
    attempts: int
    discard_count: int
    produced: dict[str, Any] | None = None
    error: str = ""


def _build_jobs(
    case_dirs: list[Path],
    copies_per_case: int,
    base_seed: int,
) -> list[JobSpec]:
    jobs: list[JobSpec] = []
    seq = 0
    for case_dir in case_dirs:
        for copy_index in range(copies_per_case):
            seq += 1
            jobs.append(
                JobSpec(
                    job_id=f"{seq:06d}",
                    seq=seq,
                    case_dir=case_dir,
                    copy_index=copy_index,
                    base_seed=base_seed + seq * 10,
                )
            )
    return jobs


def _run_job_with_retries(
    job: JobSpec,
    cfg: SynthConfig,
    output_root: Path,
    generate: Callable[[Path, int, int, SynthConfig, Path], dict],
) -> JobResult:
    last_error = ""
    for attempt in range(MAX_ATTEMPTS):
        seed = job.base_seed + attempt
        try:
            produced = generate(
                job.case_dir,
                job.seq,
                seed,
                cfg,
                output_root,
            )
            return JobResult(
                job=job,
                status="ok",
                attempts=attempt + 1,
                discard_count=attempt,
                produced=produced,
            )
        except Exception as exc:
            last_error = str(exc) or traceback.format_exc(limit=3)
            logger.warning(
                "seq=%s attempt=%s failed: %s",
                job.seq,
                attempt + 1,
                last_error[:300],
            )
    return JobResult(
        job=job,
        status="skipped",
        attempts=MAX_ATTEMPTS,
        discard_count=MAX_ATTEMPTS,
        error=last_error[:500],
    )


def _record_from_result(result: JobResult) -> dict:
    job = result.job
    if result.status == "skipped":
        return {
            "seq": job.seq,
            "case": str(job.case_dir),
            "copy_index": job.copy_index,
            "status": "skipped",
            "attempts": result.attempts,
            "discard_count": result.discard_count,
            "error": result.error[:500],
        }

    produced = result.produced or {}
    return {
        "seq": job.seq,
        "case": str(job.case_dir),
        "copy_index": job.copy_index,
        "status": "ok",
        "attempts": result.attempts,
        "discard_count": result.discard_count,
        "path": produced.get("path"),
        "doc_id": produced.get("doc_id"),
        "seed": produced.get("seed"),
        "stats": produced.get("stats") or {},
    }


_REPORT_FIELDS = (
    "seq",
    "case",
    "copy_index",
    "status",
    "attempts",
    "discard_count",
    "path",
    "doc_id",
    "seed",
    "stats",
    "error",
)


def _source_case_signature(case_dir: Path) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for path in sorted(item for item in case_dir.rglob("*") if item.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append(
            {
                "path": path.relative_to(case_dir).as_posix(),
                "sha256": digest,
            }
        )
    return files


def _semantic_fingerprint_payload(
    cfg: SynthConfig,
    case_dirs: list[Path],
) -> dict[str, Any]:
    return {
        "source_cases": [str(path.resolve()) for path in case_dirs],
        "source_files": {
            str(path.resolve()): _source_case_signature(path) for path in case_dirs
        },
        "copies_per_case": cfg.copies_per_case,
        "max_source_pages": cfg.max_source_pages,
        "seed": cfg.seed,
        "translate_categories": list(cfg.translate_categories),
        "column_layouts": list(cfg.column_layouts),
        "page": {
            "width": cfg.page.width,
            "height": cfg.page.height,
            "margin": cfg.page.margin,
            "column_gap": cfg.page.column_gap,
        },
        "llm": {
            "model": os.environ.get(cfg.llm.model_env, ""),
            "base_url": os.environ.get(cfg.llm.base_url_env, ""),
            "temperature": cfg.llm.temperature,
            "max_retries": cfg.llm.max_retries,
        },
        "ocr": {
            "env_url": os.environ.get("PADDLE_OCR_API_URL", "").strip().rstrip("/"),
            "config_url": str(cfg.ocr.url or "").strip().rstrip("/"),
            "timeout": cfg.ocr.timeout,
        },
    }


def _job_manifest_payload(jobs: list[JobSpec]) -> list[dict[str, Any]]:
    return [
        {
            "job_id": job.job_id,
            "seq": job.seq,
            "case": str(job.case_dir.resolve()),
            "copy_index": job.copy_index,
            "base_seed": job.base_seed,
        }
        for job in jobs
    ]


def _record_from_event(event: dict[str, Any]) -> dict[str, Any]:
    return {key: event[key] for key in _REPORT_FIELDS if key in event}


def _running_event(job: JobSpec, previous: dict[str, Any] | None) -> dict[str, Any]:
    previous = previous or {}
    return {
        "job_id": job.job_id,
        "seq": job.seq,
        "case": str(job.case_dir),
        "copy_index": job.copy_index,
        "status": "running",
        "attempts": int(previous.get("attempts", 0)),
        "discard_count": int(previous.get("discard_count", 0)),
        "updated_at": time.time(),
    }


def _terminal_event(
    job: JobSpec,
    record: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    previous = previous or {}
    event = dict(record)
    event.update(
        {
            "job_id": job.job_id,
            "updated_at": time.time(),
            "attempts": int(previous.get("attempts", 0))
            + int(record.get("attempts", 0)),
            "discard_count": int(previous.get("discard_count", 0))
            + int(record.get("discard_count", 0)),
        }
    )
    return event


def _recovered_event(
    job: JobSpec,
    output_path: Path,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    origin = json.loads((output_path / "origin.json").read_text(encoding="utf-8"))
    previous = previous or {}
    return {
        "job_id": job.job_id,
        "seq": job.seq,
        "case": str(job.case_dir),
        "copy_index": job.copy_index,
        "status": "ok",
        "attempts": int(previous.get("attempts", 0)),
        "discard_count": int(previous.get("discard_count", 0)),
        "path": str(output_path.resolve()),
        "doc_id": origin.get("doc_id"),
        "seed": previous.get("seed"),
        "stats": previous.get("stats") or {},
        "recovered": True,
        "updated_at": time.time(),
    }


def _terminal_records(
    jobs: list[JobSpec],
    latest: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for job in jobs:
        event = latest.get(job.job_id)
        if event is None or event.get("status") not in {"ok", "skipped"}:
            continue
        records.append(_record_from_event(event))
    return records


def _write_batch_checkpoint(
    output_root: Path,
    jobs: list[JobSpec],
    latest: dict[str, dict[str, Any]],
    started: float,
) -> dict:
    records = _terminal_records(jobs, latest)
    records.sort(key=lambda record: int(record["seq"]))
    successes = [
        str(record["path"])
        for record in records
        if record.get("status") == "ok" and record.get("path")
    ]
    report = {
        "n_ok": sum(record.get("status") == "ok" for record in records),
        "n_skip": sum(record.get("status") == "skipped" for record in records),
        "n_discard": sum(int(record.get("discard_count", 0)) for record in records),
        "n_planned": len(jobs),
        "elapsed_sec": round(time.time() - started, 2),
        "records": records,
    }
    write_json_atomic(output_root / "report.json", report)
    write_text_atomic(
        output_root / "synth_input_path.txt",
        "\n".join(successes) + ("\n" if successes else ""),
    )
    return report


def _load_dotenv(workspace: Path) -> None:
    path = Path(workspace) / ".env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ.setdefault(key, value)


def ensure_runtime_env(workspace: Path) -> None:
    workspace = Path(workspace)
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", str(workspace / ".playwright-browsers")
    )
    libs = workspace / ".playwright-libs/usr/lib/x86_64-linux-gnu"
    if libs.is_dir():
        current = os.environ.get("LD_LIBRARY_PATH", "")
        prefix = str(libs)
        if prefix not in current.split(":"):
            os.environ["LD_LIBRARY_PATH"] = (
                f"{prefix}:{current}" if current else prefix
            )
    _load_dotenv(workspace)


def _ocr_candidates(cfg: SynthConfig) -> list[OcrSettings]:
    urls: list[str] = []
    env_url = os.environ.get("PADDLE_OCR_API_URL", "").strip().rstrip("/")
    if env_url:
        urls.append(env_url)
    yaml_url = str(cfg.ocr.url or "").strip().rstrip("/")
    if yaml_url:
        urls.append(yaml_url)
    seen: set[str] = set()
    out: list[OcrSettings] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            out.append(OcrSettings(url=url, timeout=cfg.ocr.timeout))
    if not out:
        raise RuntimeError(
            "OCR url not configured: set PADDLE_OCR_API_URL or ocr.url in synth.yaml"
        )
    return out


def _relink_images(html: str, material) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    by_id = {block.id: block for block in material.blocks if block.image_path}
    for img in soup.select("img[data-node-id]"):
        block = by_id.get(img.get("data-node-id"))
        if block is None:
            continue
        img["src"] = Path(block.image_path).resolve().as_uri()
    return str(soup)


def materialize_document(
    material,
    rewritten: str,
    seq: int,
    seed: int,
    cfg: SynthConfig,
    output_root: Path,
    out_dir: Path,
    column_layout: str | None = None,
) -> dict:
    rewritten = _relink_images(rewritten, material)
    images_dir = out_dir / "images_path"
    if images_dir.exists():
        shutil.rmtree(images_dir)
    layout = column_layout or choose_column_layout(cfg.column_layouts, seed)
    logger.info("layout=%s seq=%s seed=%s", layout, seq, seed)
    placed = render_pages(rewritten, images_dir, cfg, column_layout=layout)
    result = validate_doc(material.tree, placed, cfg, material=material)
    if not result.ok:
        raise RuntimeError("; ".join(result.errors[:8]))

    build_gt(material, placed, out_dir, cfg, seq=seq)
    doc = json.loads((out_dir / "multi-page-final.json").read_text(encoding="utf-8"))["doc"]
    projection_errors = validate_page_projection(doc)
    if projection_errors:
        raise RuntimeError("; ".join(projection_errors[:4]))

    visualize_dir = out_dir / "visualize"
    if visualize_dir.exists():
        shutil.rmtree(visualize_dir)
    draw_overlays(images_dir, placed, visualize_dir)

    image_paths = sorted(
        images_dir.glob("raw-page-*.png"),
        key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
    )
    last_error: Exception | None = None
    prelabel = None
    used: OcrSettings | None = None
    for settings in _ocr_candidates(cfg):
        try:
            prelabel = build_prelabel(image_paths, settings)
            used = settings
            break
        except Exception as exc:
            last_error = exc
    if prelabel is None:
        raise RuntimeError(f"OCR failed: {last_error}")
    (out_dir / "prelabel.json").write_text(
        json.dumps(prelabel, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if used is not None:
        (out_dir / "ocr_url.txt").write_text(used.url, encoding="utf-8")

    if Path(material.assets_dir).exists():
        shutil.rmtree(material.assets_dir, ignore_errors=True)
    stats = dict(result.stats)
    stats["column_layout"] = layout
    return {
        "path": str(out_dir.resolve()),
        "doc_id": material.doc_id,
        "seed": seed,
        "stats": stats,
    }


def generate_one(
    case_dir: Path,
    seq: int,
    seed: int,
    cfg: SynthConfig,
    output_root: Path,
) -> dict:
    material = load_material(case_dir, cfg, output_root / f"_assets_{seq}_{seed}")
    out_dir = output_root / f"synth_{seq:03d}_{material.doc_id}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    logger.info("generate seq=%s seed=%s doc_id=%s", seq, seed, material.doc_id)

    try:
        html = build_source_html(material, cfg)
        rewritten = rewrite_html(html, cfg, seed=seed)
        (out_dir / "rewritten.html").write_text(rewritten, encoding="utf-8")
        logger.info("rewrite done seq=%s", seq)
        layout = choose_column_layout(cfg.column_layouts, seed)
        return materialize_document(
            material,
            rewritten,
            seq,
            seed,
            cfg,
            output_root,
            out_dir,
            column_layout=layout,
        )
    except Exception:
        if out_dir.exists():
            shutil.rmtree(out_dir)
        assets = output_root / f"_assets_{seq}_{seed}"
        if assets.exists():
            shutil.rmtree(assets, ignore_errors=True)
        raise


def run_batch(
    cfg: SynthConfig,
    *,
    workspace: Path,
    generate_fn: Callable[..., dict] | None = None,
) -> dict:
    generate = generate_fn or generate_one
    workspace = Path(workspace)
    output_root = Path(cfg.output_root)
    if not output_root.is_absolute():
        output_root = (workspace / output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    started = time.time()
    case_dirs = expand_source_cases(cfg.source_cases, workspace)
    jobs = _build_jobs(case_dirs, cfg.copies_per_case, cfg.seed)
    logger.info(
        "batch cases=%s copies_per_case=%s",
        [str(path) for path in case_dirs],
        cfg.copies_per_case,
    )

    manifest_path = output_root / "batch_manifest.json"
    progress_path = output_root / "progress.jsonl"
    _manifest, manifest_created = load_or_create_manifest(
        manifest_path,
        fingerprint_payload=_semantic_fingerprint_payload(cfg, case_dirs),
        jobs=_job_manifest_payload(jobs),
    )
    latest = load_latest_progress(progress_path)

    pending: list[JobSpec] = []
    for job in jobs:
        previous = latest.get(job.job_id)
        previous_path = previous.get("path") if previous else None
        if (
            previous
            and previous.get("status") == "ok"
            and previous_path
            and output_is_complete(Path(previous_path), job.seq)
        ):
            continue

        if not manifest_created:
            recovered = find_recoverable_output(output_root, job.seq)
            if recovered is not None:
                event = _recovered_event(job, recovered, previous)
                append_progress(progress_path, event)
                latest[job.job_id] = event
                continue
        pending.append(job)

    if not pending:
        return _write_batch_checkpoint(output_root, jobs, latest, started)
    _write_batch_checkpoint(output_root, jobs, latest, started)

    executor = ThreadPoolExecutor(
        max_workers=cfg.max_workers,
        thread_name_prefix="synth",
    )
    futures = {}
    try:
        for job in pending:
            running = _running_event(job, latest.get(job.job_id))
            append_progress(progress_path, running)
            latest[job.job_id] = running
            futures[
                executor.submit(
                    _run_job_with_retries,
                    job,
                    cfg,
                    output_root,
                    generate,
                )
            ] = job

        for future in as_completed(futures):
            job = futures[future]
            previous = latest.get(job.job_id)
            try:
                result = future.result()
            except Exception as exc:
                error = str(exc) or traceback.format_exc(limit=3)
                result = JobResult(
                    job=job,
                    status="skipped",
                    attempts=1,
                    discard_count=1,
                    error=error[:500],
                )
            record = _record_from_result(result)
            event = _terminal_event(job, record, previous)
            append_progress(progress_path, event)
            latest[job.job_id] = event
            _write_batch_checkpoint(output_root, jobs, latest, started)
    except KeyboardInterrupt:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    return _write_batch_checkpoint(output_root, jobs, latest, started)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Synthesize bilingual failure documents")
    parser.add_argument(
        "--config",
        default="src/synth/config/synth.yaml",
        help="Synth YAML config",
    )
    args = parser.parse_args(argv)
    workspace = Path(__file__).resolve().parents[2]
    ensure_runtime_env(workspace)
    cfg = load_config(workspace / args.config if not Path(args.config).is_absolute() else args.config)
    report = run_batch(cfg, workspace=workspace)
    print(
        f"ok={report['n_ok']} skip={report['n_skip']} discard={report['n_discard']} "
        f"elapsed={report['elapsed_sec']}s"
    )
    return 0 if report["n_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
