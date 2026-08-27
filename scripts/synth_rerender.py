#!/usr/bin/env python3
"""用已有 rewritten.html 按当前 paginate 规则重渲染五件套,不再调用 LLM。"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.synth.config import expand_source_cases, load_config
from src.synth.material import load_material
from src.synth.runner import ensure_runtime_env, materialize_document

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("synth_rerender")


def main() -> int:
    ensure_runtime_env(ROOT)
    cfg = load_config(ROOT / "src/synth/config/synth.yaml")
    output_root = (ROOT / cfg.output_root).resolve()
    case_dir = expand_source_cases(cfg.source_cases, ROOT)[0]
    dirs = sorted(
        path for path in output_root.glob("synth_*") if path.is_dir() and (path / "rewritten.html").is_file()
    )
    if not dirs:
        raise SystemExit(f"no rewritten.html under {output_root}")

    report_records = []
    n_ok = 0
    for out_dir in dirs:
        seq = int(out_dir.name.split("_")[1])
        seed = cfg.seed + seq * 10
        html = (out_dir / "rewritten.html").read_text(encoding="utf-8")
        material = load_material(case_dir, cfg, output_root / f"_assets_rerender_{seq}")
        logger.info("rerender %s seq=%s", out_dir.name, seq)
        try:
            produced = materialize_document(
                material, html, seq, seed, cfg, output_root, out_dir
            )
            n_ok += 1
            report_records.append(
                {
                    "seq": seq,
                    "status": "ok",
                    "path": produced["path"],
                    "stats": produced["stats"],
                }
            )
            logger.info("ok %s stats=%s", out_dir.name, produced["stats"])
        except Exception as exc:
            logger.exception("failed %s", out_dir.name)
            report_records.append({"seq": seq, "status": "failed", "error": str(exc)[:500]})

    report = {"n_ok": n_ok, "n_failed": len(dirs) - n_ok, "records": report_records}
    (output_root / "rerender_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"rerender ok={n_ok} failed={len(dirs) - n_ok}")
    return 0 if n_ok == len(dirs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
