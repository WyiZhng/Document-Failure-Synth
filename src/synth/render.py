from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.synth.config import SynthConfig

_PAGINATE_JS = (Path(__file__).parent / "paginate.js").read_text(encoding="utf-8")
_BBOX_TOLERANCE = 1.0


@dataclass
class PlacedBlock:
    node_id: str
    lang: str
    category: str
    page: int
    bbox: tuple[float, float, float, float]
    text: str
    order: int
    fragment_index: int = 0


def _raw_to_placed(raw: dict) -> PlacedBlock:
    return PlacedBlock(
        node_id=str(raw["node_id"]),
        lang=str(raw["lang"]),
        category=str(raw["category"]),
        page=int(raw["page"]),
        bbox=(float(raw["x1"]), float(raw["y1"]), float(raw["x2"]), float(raw["y2"])),
        text=str(raw.get("text", "")),
        order=int(raw["order"]),
        fragment_index=int(raw.get("fragment_index", 0)),
    )


def _verify_bbox_against_dom(page, placed: PlacedBlock) -> None:
    selector = (
        f'.synth-page[data-page-index="{placed.page}"] '
        f'[data-node-id="{placed.node_id}"][data-lang="{placed.lang}"]'
        f'[data-fragment-index="{placed.fragment_index}"][data-placed="true"]'
    )
    handle = page.query_selector(selector)
    if handle is None:
        raise RuntimeError(f"placed block not found in DOM: {placed.node_id} ({placed.lang})")

    rect = handle.evaluate(
        """el => {
            const pageEl = el.closest('.synth-page');
            const r = el.getBoundingClientRect();
            const p = pageEl.getBoundingClientRect();
            return {
                x1: r.left - p.left,
                y1: r.top - p.top,
                x2: r.right - p.left,
                y2: r.bottom - p.top,
            };
        }"""
    )
    for key, idx in (("x1", 0), ("y1", 1), ("x2", 2), ("y2", 3)):
        if abs(float(rect[key]) - placed.bbox[idx]) > _BBOX_TOLERANCE:
            raise RuntimeError(
                f"bbox mismatch for {placed.node_id} ({placed.lang}): "
                f"paginate={placed.bbox}, dom=({rect['x1']}, {rect['y1']}, {rect['x2']}, {rect['y2']})"
            )


def render_pages(
    html: str,
    out_images_dir: Path,
    cfg: SynthConfig,
    column_layout: str | None = None,
) -> list[PlacedBlock]:
    out_images_dir.mkdir(parents=True, exist_ok=True)

    # set_content 的页面 origin 是 about:blank,Chromium 会拒绝加载 file:// 图像;
    # 必须落盘成文件后用 file:// 打开,页面才有 file origin。
    html_file = out_images_dir / "_render_source.html"
    html_file.write_text(html, encoding="utf-8")

    layout = column_layout or "zh-en"
    paginate_config = {
        "width": cfg.page.width,
        "height": cfg.page.height,
        "margin": cfg.page.margin,
        "columnGap": cfg.page.column_gap,
        "columnLayout": layout,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            viewport={"width": cfg.page.width, "height": cfg.page.height},
            device_scale_factor=1,
        )
        page.goto(html_file.resolve().as_uri(), wait_until="networkidle")
        page.add_script_tag(content=_PAGINATE_JS)
        raw_placements: list[dict] = page.evaluate(
            "(config) => paginateDocument(config)",
            paginate_config,
        )

        page.evaluate(
            """() => {
                document.querySelectorAll('.synth-page').forEach((el, idx) => {
                    el.setAttribute('data-page-index', String(idx));
                });
            }"""
        )

        placed = [_raw_to_placed(raw) for raw in raw_placements]
        for block in placed:
            _verify_bbox_against_dom(page, block)

        page_handles = page.query_selector_all(".synth-page")
        for idx, handle in enumerate(page_handles):
            screenshot_path = out_images_dir / f"raw-page-{idx + 1}.png"
            handle.screenshot(path=str(screenshot_path))

        browser.close()

    html_file.unlink(missing_ok=True)
    return sorted(placed, key=lambda p: (p.order, p.lang, p.fragment_index, p.page))
