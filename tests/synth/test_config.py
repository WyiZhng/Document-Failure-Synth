from pathlib import Path
import pytest

from src.synth.config import expand_source_cases, load_config


def test_load_default_config():
    cfg = load_config(Path("src/synth/config/synth.yaml"))
    assert cfg.page.width == 1000
    assert cfg.copies_per_case == 1
    assert "text" in cfg.translate_categories
    assert cfg.source_cases == ["task/source.txt"]
    assert cfg.output_root == "data/output/0714_0827"
    assert cfg.max_source_pages is None
    assert cfg.ocr.timeout == 300


def test_load_config_accepts_explicit_page_limit(tmp_path: Path) -> None:
    source = Path("src/synth/config/synth.yaml").read_text(encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(
        source.replace("max_source_pages: null", "max_source_pages: 2"),
        encoding="utf-8",
    )
    assert load_config(path).max_source_pages == 2


def test_expand_source_cases_glob(tmp_path: Path) -> None:
    good_a = tmp_path / "data/source/task_a"
    good_b = tmp_path / "data/source/task_b"
    junk = tmp_path / "data/source/not_a_case"
    for path in (good_a, good_b):
        path.mkdir(parents=True)
        (path / "origin.json").write_text("{}", encoding="utf-8")
        (path / "multi-page-final-fillin.json").write_text("[]", encoding="utf-8")
    junk.mkdir(parents=True)
    (junk / "readme.txt").write_text("skip", encoding="utf-8")

    found = expand_source_cases(["data/source/*"], tmp_path)
    assert [p.name for p in found] == ["task_a", "task_b"]


def test_expand_source_cases_raises_when_empty(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no source cases"):
        expand_source_cases(["data/source/*"], tmp_path)
