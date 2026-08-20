from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class PageConfig:
    width: int
    height: int
    margin: int
    column_gap: int


@dataclass
class LlmConfig:
    model_env: str
    base_url_env: str
    api_key_env: str
    temperature: float
    max_retries: int


@dataclass
class OcrConfig:
    url: str
    timeout: int


@dataclass
class SynthConfig:
    source_cases: list[str]
    copies_per_case: int
    max_source_pages: int
    seed: int
    output_root: str
    translate_categories: list[str]
    page: PageConfig
    llm: LlmConfig
    ocr: OcrConfig


def load_config(path: str | Path) -> SynthConfig:
    with open(path) as f:
        raw = yaml.safe_load(f)

    page_raw = raw["page"]
    llm_raw = raw["llm"]
    ocr_raw = raw.get("ocr") or {}

    return SynthConfig(
        source_cases=list(raw["source_cases"]),
        copies_per_case=int(raw["copies_per_case"]),
        max_source_pages=int(raw["max_source_pages"]),
        seed=int(raw["seed"]),
        output_root=str(raw["output_root"]),
        translate_categories=list(raw["translate_categories"]),
        page=PageConfig(
            width=int(page_raw["width"]),
            height=int(page_raw["height"]),
            margin=int(page_raw["margin"]),
            column_gap=int(page_raw["column_gap"]),
        ),
        llm=LlmConfig(
            model_env=str(llm_raw["model_env"]),
            base_url_env=str(llm_raw["base_url_env"]),
            api_key_env=str(llm_raw["api_key_env"]),
            temperature=float(llm_raw["temperature"]),
            max_retries=int(llm_raw["max_retries"]),
        ),
        ocr=OcrConfig(
            url=str(ocr_raw.get("url") or "").strip(),
            timeout=int(ocr_raw.get("timeout") or 300),
        ),
    )
