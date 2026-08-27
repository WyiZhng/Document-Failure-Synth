from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Callable

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
    result = validate_doc(material.tree, placed, cfg)
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
    records: list[dict] = []
    successes: list[str] = []
    n_ok = 0
    n_skip = 0
    n_discard = 0
    seq = 0
    case_dirs = expand_source_cases(cfg.source_cases, workspace)
    logger.info(
        "batch cases=%s copies_per_case=%s",
        [str(path) for path in case_dirs],
        cfg.copies_per_case,
    )

    for case_dir in case_dirs:
        for copy_index in range(cfg.copies_per_case):
            seq += 1
            last_error = ""
            produced = None
            for attempt in range(MAX_ATTEMPTS):
                seed = cfg.seed + seq * 10 + attempt
                try:
                    produced = generate(case_dir, seq, seed, cfg, output_root)
                    break
                except Exception as exc:
                    n_discard += 1
                    last_error = str(exc) or traceback.format_exc(limit=3)
                    logger.warning(
                        "seq=%s attempt=%s failed: %s", seq, attempt + 1, last_error[:300]
                    )
            if produced is None:
                n_skip += 1
                records.append(
                    {
                        "seq": seq,
                        "case": str(case_dir),
                        "copy_index": copy_index,
                        "status": "skipped",
                        "attempts": MAX_ATTEMPTS,
                        "error": last_error[:500],
                    }
                )
                continue
            n_ok += 1
            successes.append(produced["path"])
            records.append(
                {
                    "seq": seq,
                    "case": str(case_dir),
                    "copy_index": copy_index,
                    "status": "ok",
                    "path": produced["path"],
                    "doc_id": produced.get("doc_id"),
                    "seed": produced.get("seed"),
                    "stats": produced.get("stats") or {},
                }
            )

    report = {
        "n_ok": n_ok,
        "n_skip": n_skip,
        "n_discard": n_discard,
        "n_planned": len(case_dirs) * cfg.copies_per_case,
        "elapsed_sec": round(time.time() - started, 2),
        "records": records,
    }
    (output_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_root / "synth_input_path.txt").write_text(
        "\n".join(successes) + ("\n" if successes else ""), encoding="utf-8"
    )
    return report


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
