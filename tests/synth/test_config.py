from pathlib import Path
import pytest

from src.synth.config import expand_source_cases, load_config


def test_load_default_config():
    cfg = load_config(Path("src/synth/config/synth.yaml"))
    assert cfg.page.width == 1000
    assert cfg.copies_per_case == 1
    assert cfg.max_workers == 4
    assert "text" in cfg.translate_categories
    assert cfg.source_cases == ["task/source.txt"]
    assert cfg.output_root == "data/output/0714_0827"
    assert cfg.max_source_pages is None
    assert cfg.ocr.timeout == 300
    assert cfg.column_layouts == ["zh-en", "en-zh"]
    assert cfg.llm.batch_max_chars == 12000
    assert cfg.synchronize_bilingual_pairs is True
    specs = cfg.get_variant_specs()
    assert [spec.name for spec in specs] == [
        "zh-en_no-cross",
        "zh-en_cross",
        "en-zh_no-cross",
        "en-zh_cross",
    ]
    assert [spec.pagination_mode for spec in specs] == [
        "no-cross", "cross", "no-cross", "cross"
    ]
    assert [spec.synchronize_pairs for spec in specs] == [True, False, True, False]


def test_load_config_keeps_legacy_global_pagination_compatibility(tmp_path: Path) -> None:
    path = tmp_path / "legacy.yaml"
    path.write_text(
        """\
source_cases: [data/source/*]
copies_per_case: 1
seed: 1
output_root: data/output
translate_categories: [text]
column_layouts: [zh-en, en-zh]
synchronize_bilingual_pairs: true
page: {width: 1000, height: 1414, margin: 40, column_gap: 24}
llm: {model_env: MODEL, base_url_env: URL, api_key_env: KEY, temperature: 0.0, max_retries: 1}
ocr: {url: '', timeout: 1}
""",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.variant_specs is None
    assert [spec.name for spec in cfg.get_variant_specs()] == ["zh-en", "en-zh"]
    assert all(spec.pagination_mode == "no-cross" for spec in cfg.get_variant_specs())


def test_load_config_rejects_conflicting_variant_pagination_fields(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(
        """\
source_cases: [data/source/*]
copies_per_case: 1
seed: 1
output_root: data/output
translate_categories: [text]
column_layouts: [zh-en]
variants:
  - name: bad
    column_layout: zh-en
    pagination_mode: cross
    synchronize_pairs: true
page: {width: 1000, height: 1414, margin: 40, column_gap: 24}
llm: {model_env: MODEL, base_url_env: URL, api_key_env: KEY, temperature: 0.0, max_retries: 1}
ocr: {url: '', timeout: 1}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="conflicting"):
        load_config(path)


def test_load_config_rejects_non_positive_max_workers(tmp_path: Path) -> None:
    source = Path("src/synth/config/synth.yaml").read_text(encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(source.replace("max_workers: 4", "max_workers: 0"), encoding="utf-8")
    with pytest.raises(ValueError, match="max_workers"):
        load_config(path)


@pytest.mark.parametrize("value", [0, -1])
def test_load_config_rejects_non_positive_batch_max_chars(
    tmp_path: Path, value: int
) -> None:
    source = Path("src/synth/config/synth.yaml").read_text(encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(
        source.replace("batch_max_chars: 12000", f"batch_max_chars: {value}"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="batch_max_chars"):
        load_config(path)


def test_load_config_accepts_explicit_page_limit(tmp_path: Path) -> None:
    source = Path("src/synth/config/synth.yaml").read_text(encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(
        source.replace("max_source_pages: null", "max_source_pages: 2"),
        encoding="utf-8",
    )
    assert load_config(path).max_source_pages == 2


def test_load_config_can_disable_synchronized_pagination(tmp_path: Path) -> None:
    source = Path("src/synth/config/synth.yaml").read_text(encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(
        source.replace("synchronize_bilingual_pairs: true", "synchronize_bilingual_pairs: false"),
        encoding="utf-8",
    )

    assert load_config(path).synchronize_bilingual_pairs is False


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
