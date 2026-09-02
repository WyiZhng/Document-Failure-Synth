from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from src.synth.batch_state import (
    append_progress,
    find_recoverable_outputs,
    load_latest_progress,
    load_or_create_manifest,
    output_is_complete,
    write_json_atomic,
    write_text_atomic,
)
from src.synth.config import (
    SynthConfig,
    VariantSpec,
    choose_column_layout,
    expand_source_cases,
    load_config,
)
from src.synth.gt_builder import build_gt
from src.synth.html_builder import build_bilingual_html, build_source_html
from src.synth.material import load_material
from src.synth.ocr_prelabel import OcrSettings, build_prelabel
from src.synth.render import render_pages
from src.synth.rewrite import rewrite_html, translate_material
from src.synth.translation_types import TranslationBundle
from src.synth.validate import validate_doc, validate_final_case
from src.synth.visualize import draw_overlays

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
_ORIGINAL_REWRITE_HTML = rewrite_html
_RAW_PAGE_RE = re.compile(r"^raw-page-(\d+)\.png$")


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
    produced: Any = None
    error: str = ""


def _produced_outputs(produced: Any) -> tuple[list[dict[str, Any]], bool]:
    """Normalize one-output and multi-variant generator results.

    The single-output form remains supported for injected generators and for
    callers that use ``run_batch`` as a library.  The built-in generator uses
    ``{"variants": [...]}`` so one rewrite can be rendered into several
    physical samples.
    """

    if isinstance(produced, dict) and "variants" in produced:
        raw_variants = produced.get("variants")
        if not isinstance(raw_variants, list):
            raise ValueError("generated variants must be a list")
        variants = [item for item in raw_variants if isinstance(item, dict)]
        if len(variants) != len(raw_variants):
            raise ValueError("each generated variant must be an object")
        if not variants:
            raise ValueError("generated variants cannot be empty")
        if any(not str(item.get("path", "")).strip() for item in variants):
            raise ValueError("each generated variant is missing path")
        return variants, True

    if isinstance(produced, list):
        variants = [item for item in produced if isinstance(item, dict)]
        if len(variants) != len(produced):
            raise ValueError("each generated variant must be an object")
        if not variants:
            raise ValueError("generated variants cannot be empty")
        if any(not str(item.get("path", "")).strip() for item in variants):
            raise ValueError("each generated variant is missing path")
        return variants, True

    if not isinstance(produced, dict):
        raise ValueError("generator must return an output object or variants list")
    if not str(produced.get("path", "")).strip():
        raise ValueError("generated output is missing path")
    return [produced], False


def _record_outputs(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw_outputs = record.get("outputs")
    if isinstance(raw_outputs, list):
        return [item for item in raw_outputs if isinstance(item, dict)]
    if record.get("path"):
        return [
            {
                "path": record.get("path"),
                "doc_id": record.get("doc_id"),
                "seed": record.get("seed"),
                "stats": record.get("stats") or {},
                "sample_seq": record.get("seq"),
            }
        ]
    return []


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
    generate: Callable[[Path, int, int, SynthConfig, Path], Any],
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
            _produced_outputs(produced)
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
            "variant_count": 0,
            "outputs": [],
            "error": result.error[:500],
        }

    produced = result.produced
    outputs, is_variant_payload = _produced_outputs(produced)
    record = {
        "seq": job.seq,
        "case": str(job.case_dir),
        "copy_index": job.copy_index,
        "status": "ok",
        "attempts": result.attempts,
        "discard_count": result.discard_count,
        "variant_count": len(outputs),
        "outputs": outputs,
    }
    if is_variant_payload:
        if isinstance(produced, dict):
            record["seed"] = produced.get("seed")
            record["rewrite_seq"] = produced.get("rewrite_seq", job.seq)
        else:
            record["rewrite_seq"] = job.seq
    else:
        output = outputs[0]
        # Keep the original top-level fields for callers that consume a
        # single-output report, while ``outputs`` is the canonical form.
        record.update(
            {
                "path": output.get("path"),
                "doc_id": output.get("doc_id"),
                "seed": output.get("seed"),
                "stats": output.get("stats") or {},
            }
        )
    return record


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
    "variant_count",
    "outputs",
    "rewrite_seq",
    "variant_name",
    "pagination_mode",
    "synchronize_pairs",
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
        "synchronize_bilingual_pairs": cfg.synchronize_bilingual_pairs,
        "variants": [spec.to_dict() for spec in cfg.get_variant_specs()],
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
            "batch_max_chars": cfg.llm.batch_max_chars,
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


def _origin_value(origin: dict[str, Any], key: str, fallback: Any = None) -> Any:
    value = origin.get(key)
    return fallback if value is None else value


def _recovered_event(
    job: JobSpec,
    output_paths: Path | list[Path],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    previous = previous or {}
    paths = [output_paths] if isinstance(output_paths, Path) else list(output_paths)
    previous_outputs = {
        str(item.get("path")): item
        for item in _record_outputs(previous)
        if item.get("path")
    }
    outputs: list[dict[str, Any]] = []
    for output_path in paths:
        origin = json.loads((output_path / "origin.json").read_text(encoding="utf-8"))
        previous_output = previous_outputs.get(str(output_path.resolve()), {})
        output = {
            "path": str(output_path.resolve()),
            "doc_id": origin.get("doc_id"),
            "seed": _origin_value(
                origin,
                "seed",
                previous_output.get("seed", previous.get("seed")),
            ),
            "stats": previous_output.get(
                "stats", previous.get("stats") or {}
            ),
        }
        for key in (
            "sample_seq",
            "column_layout",
            "rewrite_seq",
            "variant_name",
            "pagination_mode",
            "synchronize_pairs",
        ):
            if key in origin:
                output[key] = origin[key]
        outputs.append(output)

    event = {
        "job_id": job.job_id,
        "seq": job.seq,
        "case": str(job.case_dir),
        "copy_index": job.copy_index,
        "status": "ok",
        "attempts": int(previous.get("attempts", 0)),
        "discard_count": int(previous.get("discard_count", 0)),
        "variant_count": len(outputs),
        "outputs": outputs,
        "recovered": True,
        "updated_at": time.time(),
    }
    if len(outputs) == 1:
        event.update(
            {
                "path": outputs[0].get("path"),
                "doc_id": outputs[0].get("doc_id"),
                "seed": outputs[0].get("seed"),
                "stats": outputs[0].get("stats") or {},
            }
        )
    return event


def _output_is_final_compatible(out_dir: Path, cfg: SynthConfig) -> bool:
    """Reject a phase-0-complete directory that predates final validation.

    Library callers can provide their own lightweight output objects, so the
    additional check is applied to directories carrying the synthesizer's
    ``rewritten.html`` marker. Generated cases always carry that marker.
    """

    out_dir = Path(out_dir)
    if not (out_dir / "rewritten.html").is_file():
        return True
    try:
        origin = json.loads((out_dir / "origin.json").read_text(encoding="utf-8"))
        images_dir = Path(str(origin["images_path"]))
        if not images_dir.is_absolute():
            images_dir = out_dir / images_dir
        payload = json.loads(
            (out_dir / "multi-page-final.json").read_text(encoding="utf-8")
        )
        doc = payload.get("doc") if isinstance(payload, dict) else None
        rendered_pages = _rendered_page_indices(images_dir)
        return validate_final_case(
            doc,
            rendered_pages=rendered_pages,
            cfg=cfg,
        ).ok
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _variant_for_sample(
    sample_seq: int,
    job: JobSpec,
    specs: list[VariantSpec],
) -> VariantSpec | None:
    first_sample_seq = (job.seq - 1) * len(specs) + 1
    offset = sample_seq - first_sample_seq
    if 0 <= offset < len(specs):
        return specs[offset]
    return None


def _output_matches_config_variant(
    out_dir: Path,
    sample_seq: int,
    job: JobSpec,
    cfg: SynthConfig,
) -> bool:
    """Require explicit output metadata for configs using named variants."""

    # Configs constructed by older callers have no explicit ``variants``
    # field. Their two-layout recovery behavior remains intentionally loose.
    if cfg.variant_specs is None:
        return True
    spec = _variant_for_sample(sample_seq, job, cfg.get_variant_specs())
    if spec is None:
        return False
    try:
        origin = json.loads((Path(out_dir) / "origin.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    return isinstance(origin, dict) and all(
        origin.get(key) == expected
        for key, expected in {
            "variant_name": spec.name,
            "column_layout": spec.column_layout,
            "pagination_mode": spec.pagination_mode,
            "synchronize_pairs": spec.synchronize_pairs,
            "sample_seq": sample_seq,
        }.items()
    )


def _event_outputs_complete(
    event: dict[str, Any], job: JobSpec, cfg: SynthConfig | None = None
) -> bool:
    """Check every physical output recorded for a successful job."""

    outputs = _record_outputs(event)
    if not outputs:
        return False
    recorded_count = event.get("variant_count")
    if recorded_count is not None:
        try:
            if int(recorded_count) != len(outputs):
                return False
        except (TypeError, ValueError):
            return False

    for output in outputs:
        raw_path = output.get("path")
        if not raw_path:
            return False
        try:
            sample_seq = int(output.get("sample_seq", job.seq))
        except (TypeError, ValueError):
            return False
        if not output_is_complete(Path(raw_path), sample_seq):
            return False
        if cfg is not None and not _output_matches_config_variant(
            Path(raw_path), sample_seq, job, cfg
        ):
            return False
        if cfg is not None and not _output_is_final_compatible(Path(raw_path), cfg):
            return False
    return True


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
    variants_per_job: int = 1,
    variant_specs: list[dict[str, Any]] | None = None,
) -> dict:
    records = _terminal_records(jobs, latest)
    records.sort(key=lambda record: int(record["seq"]))
    successes: list[str] = []
    for record in records:
        if record.get("status") != "ok":
            continue
        successes.extend(
            str(output["path"])
            for output in _record_outputs(record)
            if output.get("path")
        )
    n_ok = sum(record.get("status") == "ok" for record in records)
    n_samples_ok = sum(
        len(_record_outputs(record))
        for record in records
        if record.get("status") == "ok"
    )
    report = {
        # n_ok/n_skip remain semantic-job counters for compatibility.
        "n_ok": n_ok,
        "n_skip": sum(record.get("status") == "skipped" for record in records),
        "n_discard": sum(int(record.get("discard_count", 0)) for record in records),
        "n_planned": len(jobs),
        "n_samples_ok": n_samples_ok,
        "n_samples_planned": len(jobs) * variants_per_job,
        "variants": variant_specs or [],
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
    *,
    cleanup_assets: bool = True,
    origin_metadata: dict[str, Any] | None = None,
    plans: TranslationBundle | None = None,
    synchronize_pairs: bool | None = None,
    variant_name: str | None = None,
    pagination_mode: str | None = None,
) -> dict:
    rewritten = _relink_images(rewritten, material)
    images_dir = out_dir / "images_path"
    if images_dir.exists():
        shutil.rmtree(images_dir)
    layout = column_layout or choose_column_layout(cfg.column_layouts, seed)
    effective_sync = (
        getattr(cfg, "synchronize_bilingual_pairs", False)
        if synchronize_pairs is None
        else bool(synchronize_pairs)
    )
    effective_mode = pagination_mode or ("no-cross" if effective_sync else "cross")
    logger.info(
        "layout=%s pagination_mode=%s synchronize_pairs=%s seq=%s seed=%s",
        layout,
        effective_mode,
        effective_sync,
        seq,
        seed,
    )
    render_kwargs: dict[str, Any] = {"column_layout": layout}
    if synchronize_pairs is not None or getattr(
        cfg, "synchronize_bilingual_pairs", False
    ):
        render_kwargs["synchronize_pairs"] = effective_sync
    placed = render_pages(rewritten, images_dir, cfg, **render_kwargs)
    result = validate_doc(
        material.tree,
        placed,
        cfg,
        material=material,
        plans=plans,
    )
    if not result.ok:
        raise RuntimeError("; ".join(result.errors[:8]))

    build_gt(
        material,
        placed,
        out_dir,
        cfg,
        seq=seq,
        origin_metadata=origin_metadata,
        plans=plans,
    )
    final_payload = json.loads(
        (out_dir / "multi-page-final.json").read_text(encoding="utf-8")
    )
    doc = final_payload.get("doc") if isinstance(final_payload, dict) else None
    rendered_pages = _rendered_page_indices(images_dir)
    final_result = validate_final_case(
        doc,
        rendered_pages=rendered_pages,
        cfg=cfg,
    )
    if not final_result.ok:
        raise RuntimeError(
            "final Trainer compatibility validation failed: "
            + "; ".join(final_result.errors[:8])
        )

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

    if cleanup_assets and Path(material.assets_dir).exists():
        shutil.rmtree(material.assets_dir, ignore_errors=True)
    stats = dict(result.stats)
    stats.update(
        {
            "final_rendered_page_count": final_result.stats["rendered_page_count"],
            "final_projection_error_count": final_result.stats[
                "projection_error_count"
            ],
            "final_tree_error_count": final_result.stats["final_tree_error_count"],
            "final_merge_error_count": final_result.stats["merge_error_count"],
            "final_n_errors": final_result.stats["n_errors"],
        }
    )
    if plans is not None:
        stats.update(
            {
                "dropped_node_count": len(plans.dropped),
                "translation_warning_count": len(plans.warnings),
                "dropped_node_ids": list(plans.dropped),
                "translation_warnings": list(plans.warnings),
            }
        )
    stats["column_layout"] = layout
    stats["pagination_mode"] = effective_mode
    stats["synchronize_pairs"] = effective_sync
    if variant_name:
        stats["variant_name"] = variant_name
    return {
        "path": str(out_dir.resolve()),
        "doc_id": material.doc_id,
        "seed": seed,
        "sample_seq": seq,
        "column_layout": layout,
        "variant_name": variant_name,
        "pagination_mode": effective_mode,
        "synchronize_pairs": effective_sync,
        "stats": stats,
    }


def _rendered_page_indices(images_dir: Path) -> list[int]:
    """Return the contiguous zero-based page ledger produced by rendering."""

    pages: list[int] = []
    for path in Path(images_dir).glob("raw-page-*.png"):
        match = _RAW_PAGE_RE.fullmatch(path.name)
        if match is None:
            raise RuntimeError(f"invalid rendered page filename: {path.name}")
        page_number = int(match.group(1))
        if page_number <= 0:
            raise RuntimeError(f"invalid rendered page number: {path.name}")
        pages.append(page_number - 1)
    pages.sort()
    if not pages:
        raise RuntimeError(f"rendered page ledger is empty: {images_dir}")
    expected = list(range(pages[-1] + 1))
    if pages != expected:
        raise RuntimeError(
            f"rendered page ledger is not contiguous: {pages}"
        )
    return pages


def generate_one(
    case_dir: Path,
    seq: int,
    seed: int,
    cfg: SynthConfig,
    output_root: Path,
) -> dict[str, Any]:
    assets = output_root / f"_assets_{seq}_{seed}"
    output_dirs: list[Path] = []
    try:
        material = load_material(case_dir, cfg, assets)
        logger.info("generate seq=%s seed=%s doc_id=%s", seq, seed, material.doc_id)
        bundle: TranslationBundle | None = None
        if (
            hasattr(material, "blocks")
            and hasattr(material, "tree")
            and rewrite_html is _ORIGINAL_REWRITE_HTML
        ):
            bundle = translate_material(material, cfg, seed=seed)
            rewritten = build_bilingual_html(material, bundle, cfg)
            logger.info(
                "structured translation done seq=%s dropped=%s warnings=%s",
                seq,
                len(bundle.dropped),
                len(bundle.warnings),
            )
        else:
            # Compatibility for injected legacy generators that only provide a
            # doc_id/assets_dir stub. Real production Material always follows
            # the structured path above.
            html = build_source_html(material, cfg)
            rewritten = rewrite_html(html, cfg, seed=seed)
            logger.info("legacy rewrite done seq=%s", seq)
        variants: list[dict[str, Any]] = []
        variant_specs = cfg.get_variant_specs()
        variant_count = len(variant_specs)
        for variant_index, spec in enumerate(variant_specs):
            sample_seq = (seq - 1) * variant_count + variant_index + 1
            out_dir = output_root / (
                f"synth_{sample_seq:03d}_{material.doc_id}_{spec.name}"
            )
            if out_dir.exists():
                shutil.rmtree(out_dir)
            out_dir.mkdir(parents=True)
            output_dirs.append(out_dir)
            (out_dir / "rewritten.html").write_text(rewritten, encoding="utf-8")
            materialize_kwargs = {
                "column_layout": spec.column_layout,
                "synchronize_pairs": spec.synchronize_pairs,
                "variant_name": spec.name,
                "pagination_mode": spec.pagination_mode,
                "cleanup_assets": False,
                "origin_metadata": {
                    "source_doc_id": material.doc_id,
                    "rewrite_seq": seq,
                    "sample_seq": sample_seq,
                    "variant_name": spec.name,
                    "column_layout": spec.column_layout,
                    "pagination_mode": spec.pagination_mode,
                    "synchronize_pairs": spec.synchronize_pairs,
                    "seed": seed,
                },
            }
            if bundle is not None:
                materialize_kwargs["plans"] = bundle
            variant = materialize_document(
                material,
                rewritten,
                sample_seq,
                seed,
                cfg,
                output_root,
                out_dir,
                **materialize_kwargs,
            )
            variant["rewrite_seq"] = seq
            variant.setdefault("variant_name", spec.name)
            variant.setdefault("pagination_mode", spec.pagination_mode)
            variant.setdefault("synchronize_pairs", spec.synchronize_pairs)
            variants.append(variant)
        return {"variants": variants, "seed": seed, "rewrite_seq": seq}
    except Exception:
        for out_dir in output_dirs:
            if out_dir.exists():
                shutil.rmtree(out_dir)
        raise
    finally:
        if assets.exists():
            shutil.rmtree(assets, ignore_errors=True)


def run_batch(
    cfg: SynthConfig,
    *,
    workspace: Path,
    generate_fn: Callable[..., Any] | None = None,
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
    variant_specs = cfg.get_variant_specs()
    variant_payload = [spec.to_dict() for spec in variant_specs]
    logger.info(
        "batch cases=%s copies_per_case=%s variants=%s",
        [str(path) for path in case_dirs],
        cfg.copies_per_case,
        variant_payload,
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
        if (
            previous
            and previous.get("status") == "ok"
            and _event_outputs_complete(previous, job, cfg)
        ):
            continue

        if not manifest_created:
            recovered = find_recoverable_outputs(
                output_root,
                job.seq,
                variant_specs=variant_payload
                if cfg.variant_specs is not None
                else None,
                variant_count=None
                if cfg.variant_specs is not None
                else len(variant_specs),
            )
            if recovered and all(
                _output_is_final_compatible(path, cfg) for path in recovered
            ):
                event = _recovered_event(job, recovered, previous)
                append_progress(progress_path, event)
                latest[job.job_id] = event
                continue
        pending.append(job)

    if not pending:
        return _write_batch_checkpoint(
            output_root,
            jobs,
            latest,
            started,
            variants_per_job=len(variant_specs),
            variant_specs=variant_payload,
        )

    # Remove stale successful records from the checkpoint before any pending
    # job is regenerated. In particular, a phase-0-complete but final-invalid
    # directory must not remain in synth_input_path.txt if the process is
    # interrupted between resume discovery and the next terminal event.
    for job in pending:
        running = _running_event(job, latest.get(job.job_id))
        append_progress(progress_path, running)
        latest[job.job_id] = running
    _write_batch_checkpoint(
        output_root,
        jobs,
        latest,
        started,
        variants_per_job=len(variant_specs),
        variant_specs=variant_payload,
    )

    executor = ThreadPoolExecutor(
        max_workers=cfg.max_workers,
        thread_name_prefix="synth",
    )
    futures = {}
    try:
        for job in pending:
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
            _write_batch_checkpoint(
                output_root,
                jobs,
                latest,
                started,
                variants_per_job=len(variant_specs),
                variant_specs=variant_payload,
            )
    except KeyboardInterrupt:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    return _write_batch_checkpoint(
        output_root,
        jobs,
        latest,
        started,
        variants_per_job=len(variant_specs),
        variant_specs=variant_payload,
    )


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
        f"samples_ok={report['n_samples_ok']}/{report['n_samples_planned']} "
        f"elapsed={report['elapsed_sec']}s"
    )
    return 0 if report["n_samples_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
