from pathlib import Path
from src.synth.config import load_config


def test_load_default_config():
    cfg = load_config(Path("src/synth/config/synth.yaml"))
    assert cfg.page.width == 1000
    assert cfg.copies_per_case == 10
    assert "text" in cfg.translate_categories
    assert cfg.source_cases == ["data/source/task_001_002_raw"]
    assert cfg.output_root == "data/output/bilingual_v1"
    assert cfg.ocr.timeout == 300
