from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from bs4 import BeautifulSoup

from src.synth.config import LlmConfig, SynthConfig
from src.synth.html_builder import build_source_html
from src.synth.material import load_material
from src.synth.rewrite import RewriteError, rewrite_html

_PROMPT_MARKER = "Input section HTML:\n"


def _extract_section_html(user_message: str) -> str:
    if _PROMPT_MARKER not in user_message:
        raise ValueError("prompt missing section marker")
    return user_message.split(_PROMPT_MARKER, 1)[1].strip()


def _insert_en_siblings(section_html: str, translate_categories: list[str]) -> str:
    soup = BeautifulSoup(section_html, "lxml")
    section = soup.select_one("section.src-page") or soup
    for el in list(section.select("[data-node-id][data-lang='zh']")):
        if el.name == "img":
            continue
        if el.get("data-category") not in translate_categories:
            continue
        en = soup.new_tag(
            "div",
            attrs={
                "class": el.get("class") or ["block"],
                "data-node-id": el["data-node-id"],
                "data-category": el["data-category"],
                "data-lang": "en",
            },
        )
        en.string = f"[EN] {el.get_text(strip=True)}"
        el.insert_after(en)
    return str(section)


def _drop_first_zh_marker(section_html: str) -> str:
    soup = BeautifulSoup(section_html, "lxml")
    section = soup.select_one("section.src-page") or soup
    zh = section.select_one("[data-node-id][data-lang='zh']")
    if zh:
        zh.decompose()
    return str(section)


def _mutate_first_zh_text(section_html: str) -> str:
    soup = BeautifulSoup(section_html, "lxml")
    section = soup.select_one("section.src-page") or soup
    zh = section.select_one("[data-node-id][data-lang='zh']")
    if zh:
        zh.string = "篡改后的文本"
    return str(section)


@dataclass
class FakeLLM:
    translate_categories: list[str] = field(
        default_factory=lambda: ["text", "paragraph_title", "doc_title"]
    )
    valid: bool = False
    drop_marker: bool = False
    mutate_zh: bool = False
    always_invalid: bool = False
    call_count: int = 0

    def __post_init__(self) -> None:
        self.chat = self._Chat(self)

    class _Chat:
        def __init__(self, parent: FakeLLM) -> None:
            self.completions = FakeLLM._Completions(parent)

    class _Completions:
        def __init__(self, parent: FakeLLM) -> None:
            self._parent = parent

        def create(self, **kwargs):
            parent = self._parent
            parent.call_count += 1
            user_message = kwargs["messages"][-1]["content"]
            section_html = _extract_section_html(user_message)

            if parent.always_invalid:
                content = section_html
            elif parent.drop_marker:
                content = _drop_first_zh_marker(section_html)
            elif parent.mutate_zh:
                content = _mutate_first_zh_text(section_html)
            elif parent.valid:
                content = _insert_en_siblings(section_html, parent.translate_categories)
            else:
                content = section_html

            return FakeLLM._Response(content)

    @dataclass
    class _Choice:
        message: FakeLLM._Message

    @dataclass
    class _Message:
        content: str

    @dataclass
    class _Response:
        content: str

        def __post_init__(self) -> None:
            self.choices = [FakeLLM._Choice(FakeLLM._Message(self.content))]


@pytest.fixture
def source_html(material_fixture, cfg):
    return build_source_html(material_fixture, cfg)


@pytest.fixture
def source_html_with_image(tiny_case_with_image, cfg, tmp_path):
    material = load_material(tiny_case_with_image, cfg, tmp_path / "assets")
    return build_source_html(material, cfg)


@pytest.fixture
def cfg_one_retry(cfg):
    return SynthConfig(
        source_cases=cfg.source_cases,
        copies_per_case=cfg.copies_per_case,
        max_source_pages=cfg.max_source_pages,
        seed=cfg.seed,
        output_root=cfg.output_root,
        translate_categories=list(cfg.translate_categories),
        page=cfg.page,
        llm=LlmConfig(
            model_env=cfg.llm.model_env,
            base_url_env=cfg.llm.base_url_env,
            api_key_env=cfg.llm.api_key_env,
            temperature=cfg.llm.temperature,
            max_retries=1,
        ),
        ocr=cfg.ocr,
    )


def test_rewrite_inserts_en_sibling(source_html, cfg):
    client = FakeLLM(valid=True)
    out = rewrite_html(source_html, cfg, client=client)
    soup = BeautifulSoup(out, "lxml")
    zh = soup.select('[data-lang="zh"][data-category="text"]')
    for el in zh:
        sib = el.find_next_sibling(attrs={"data-node-id": el["data-node-id"]})
        assert sib and sib["data-lang"] == "en" and sib.get_text(strip=True)


def test_rewrite_rejects_marker_loss(source_html, cfg):
    with pytest.raises(RewriteError):
        rewrite_html(source_html, cfg, client=FakeLLM(drop_marker=True))


def test_rewrite_rejects_zh_text_mutation(source_html, cfg):
    with pytest.raises(RewriteError):
        rewrite_html(source_html, cfg, client=FakeLLM(mutate_zh=True))


def test_rewrite_skips_image_blocks(source_html_with_image, cfg):
    client = FakeLLM(valid=True)
    out = rewrite_html(source_html_with_image, cfg, client=client)
    soup = BeautifulSoup(out, "lxml")
    img = soup.select_one('img[data-node-id="p0-b3"]')
    assert img is not None
    sib = img.find_next_sibling(attrs={"data-node-id": "p0-b3", "data-lang": "en"})
    assert sib is None


def test_rewrite_retries_once_then_raises(source_html, cfg_one_retry):
    client = FakeLLM(always_invalid=True)
    with pytest.raises(RewriteError):
        rewrite_html(source_html, cfg_one_retry, client=client)
    assert client.call_count == 2
