from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

import requests
from PIL import Image

from src.synth.config import SynthConfig

logger = logging.getLogger(__name__)

CATEGORY_MAP = {
    "title": "paragraph_title",
    "paragraph_title": "paragraph_title",
    "doc_title": "doc_title",
    "text": "text",
    "figure": "image",
    "image": "image",
    "table": "table",
    "header": "header",
    "footer": "footer",
    "seal": "seal",
    "chart": "chart",
}


@dataclass
class OcrSettings:
    url: str
    timeout: int = 300


def _map_category(raw: str) -> str:
    key = str(raw or "").strip().lower()
    mapped = CATEGORY_MAP.get(key)
    if mapped is None:
        logger.warning("unknown OCR layout category %r; falling back to 'text'", raw)
        return "text"
    return mapped


def load_ocr_settings(cfg: SynthConfig | None = None) -> OcrSettings:
    env_url = os.environ.get("PADDLE_OCR_API_URL", "").strip()
    yaml_url = ""
    timeout = 300
    if cfg is not None:
        yaml_url = str(cfg.ocr.url or "").strip()
        timeout = int(cfg.ocr.timeout)
    url = env_url or yaml_url
    if not url:
        raise ValueError(
            "OCR url not configured: set PADDLE_OCR_API_URL or ocr.url in synth.yaml"
        )
    return OcrSettings(url=url.rstrip("/"), timeout=timeout)


def _polygon_to_bbox(points: Any) -> list[float] | None:
    if isinstance(points, (list, tuple)) and len(points) == 4 and all(
        isinstance(v, (int, float)) for v in points
    ):
        x1, y1, x2, y2 = (float(v) for v in points)
        return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
    if not isinstance(points, list) or not points:
        return None
    xs: list[float] = []
    ys: list[float] = []
    if isinstance(points[0], (list, tuple)):
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                return None
            xs.append(float(point[0]))
            ys.append(float(point[1]))
    elif all(isinstance(value, (int, float)) for value in points):
        if len(points) < 4 or len(points) % 2 != 0:
            return None
        xs = [float(points[index]) for index in range(0, len(points), 2)]
        ys = [float(points[index]) for index in range(1, len(points), 2)]
    else:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def layout_parse(image_path: Path, settings: OcrSettings) -> list[dict[str, Any]]:
    """Full-page PP-Structure layout-parsing. Same protocol as src.utils.ocr.ocr."""
    resolved = Path(image_path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Paddle OCR input image not found: {resolved}")
    with Image.open(resolved) as source:
        image = source.convert("RGB")
        width, height = image.size
        buffer = BytesIO()
        image.save(buffer, format="PNG")
    encoded_file = base64.b64encode(buffer.getvalue()).decode("ascii")
    endpoint = settings.url.rstrip("/")
    if not endpoint.endswith("/layout-parsing"):
        endpoint += "/layout-parsing"
    response = requests.post(
        endpoint,
        json={"file": encoded_file, "fileType": 1},
        timeout=settings.timeout,
    )
    response.raise_for_status()
    pages = response.json()["result"]["layoutParsingResults"]

    detections: list[dict[str, Any]] = []
    for page in pages:
        pruned = page.get("prunedResult") if isinstance(page, Mapping) else None
        if not isinstance(pruned, Mapping):
            continue
        blocks = pruned.get("parsing_res_list")
        if not isinstance(blocks, list):
            continue
        for index, block in enumerate(blocks):
            if not isinstance(block, Mapping):
                continue
            bbox = _polygon_to_bbox(
                block.get("block_bbox") or block.get("block_polygon_points")
            )
            if bbox is None:
                continue
            x1, y1, x2, y2 = bbox
            detections.append(
                {
                    "block_id": index + 1,
                    "bbox": [
                        max(0.0, min(float(width), x1)),
                        max(0.0, min(float(height), y1)),
                        max(0.0, min(float(width), x2)),
                        max(0.0, min(float(height), y2)),
                    ],
                    "text": str(block.get("block_content", "") or ""),
                    "category": str(block.get("block_label", "") or "").lower(),
                }
            )
    return detections


def _detection_to_block(det: dict[str, Any]) -> dict[str, Any]:
    score = det.get("score")
    if score is None:
        score = 1.0
    return {
        "block_id": int(det["block_id"]),
        "bbox": [float(v) for v in det["bbox"]],
        "text": str(det.get("text") or ""),
        "category": _map_category(str(det.get("category") or "")),
        "score": float(score),
        "source": "paddle_ocr",
    }


def build_prelabel(
    image_paths: list[Path],
    settings: OcrSettings | None = None,
) -> dict:
    if settings is None:
        settings = load_ocr_settings()
    pages: list[dict[str, Any]] = []
    for page_index, image_path in enumerate(image_paths):
        detections = layout_parse(Path(image_path), settings)
        blocks = [_detection_to_block(det) for det in detections]
        pages.append({"page_index": page_index, "blocks": blocks})
    return {"pages": pages}
