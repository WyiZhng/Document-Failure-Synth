from __future__ import annotations

from pathlib import Path

from PIL import Image

from src.synth.render import PlacedBlock
from src.synth.visualize import draw_overlays


def test_draw_overlays_creates_matching_size_output(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    page_size = (400, 300)
    Image.new("RGB", page_size, (250, 250, 250)).save(images_dir / "raw-page-1.png")

    placed = [
        PlacedBlock(
            node_id="p0-b1",
            lang="zh",
            category="text",
            page=0,
            bbox=(20.0, 30.0, 180.0, 90.0),
            text="中文",
            order=0,
        ),
        PlacedBlock(
            node_id="p0-b2",
            lang="en",
            category="text",
            page=0,
            bbox=(210.0, 120.0, 360.0, 180.0),
            text="English",
            order=1,
        ),
    ]

    out_dir = tmp_path / "visualize"
    outputs = draw_overlays(images_dir, placed, out_dir)

    assert len(outputs) == 1
    out_path = out_dir / "page-1.png"
    assert out_path.exists()
    assert outputs[0] == out_path

    with Image.open(out_path) as out_img, Image.open(images_dir / "raw-page-1.png") as src:
        assert out_img.size == src.size == page_size
