from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

ALLOWED_COLUMN_LAYOUTS = ("zh-en", "en-zh")
ALLOWED_PAGINATION_MODES = ("no-cross", "cross")

_ORIGIN = "origin.json"
_FILLIN = "multi-page-final-fillin.json"

logger = logging.getLogger(__name__)


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
    batch_max_chars: int = 12000


@dataclass
class OcrConfig:
    url: str
    timeout: int


@dataclass(frozen=True)
class VariantSpec:
    """One physical bilingual sample produced from a semantic rewrite."""

    name: str
    column_layout: str
    pagination_mode: str

    @property
    def synchronize_pairs(self) -> bool:
        # ``no-cross`` is the paired-pagination profile. ``cross`` keeps the
        # two language columns independent so natural cross-page fragments can
        # be represented without changing their logical node ids.
        return self.pagination_mode == "no-cross"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "column_layout": self.column_layout,
            "pagination_mode": self.pagination_mode,
            "synchronize_pairs": self.synchronize_pairs,
        }


@dataclass
class SynthConfig:
    source_cases: list[str]
    copies_per_case: int
    max_source_pages: int | None
    seed: int
    output_root: str
    translate_categories: list[str]
    page: PageConfig
    llm: LlmConfig
    ocr: OcrConfig
    column_layouts: list[str] = field(default_factory=lambda: list(ALLOWED_COLUMN_LAYOUTS))
    max_workers: int = 4
    # Production generation keeps each zh/en block pair on the same page
    # whenever both blocks fit. The renderer API keeps its historical
    # independent-column default unless this flag is enabled by the runner.
    synchronize_bilingual_pairs: bool = False
    # None keeps compatibility with older configs, which generated one
    # variant per column_layout and used the global synchronization flag.
    variant_specs: list[VariantSpec] | None = None

    def get_variant_specs(self) -> list[VariantSpec]:
        if self.variant_specs is not None:
            return list(self.variant_specs)
        mode = "no-cross" if self.synchronize_bilingual_pairs else "cross"
        return [
            VariantSpec(name=layout, column_layout=layout, pagination_mode=mode)
            for layout in self.column_layouts
        ]


def load_config(path: str | Path) -> SynthConfig:
    with open(path) as f:
        raw = yaml.safe_load(f)

    page_raw = raw["page"]
    llm_raw = raw["llm"]
    ocr_raw = raw.get("ocr") or {}
    raw_cases = raw["source_cases"]
    if isinstance(raw_cases, str):
        raw_cases = [raw_cases]
    elif not raw_cases:
        raw_cases = []

    max_pages_raw = raw.get("max_source_pages")
    max_source_pages = None if max_pages_raw is None else int(max_pages_raw)
    max_workers = int(raw.get("max_workers", 4))
    if max_workers <= 0:
        raise ValueError("max_workers must be greater than 0")
    batch_max_chars = int(llm_raw.get("batch_max_chars", 12000))
    if batch_max_chars <= 0:
        raise ValueError("batch_max_chars must be greater than 0")

    column_layouts = _parse_column_layouts(raw.get("column_layouts"))
    variant_specs = _parse_variant_specs(
        raw.get("variants"),
        column_layouts=column_layouts,
        synchronize_pairs=bool(raw.get("synchronize_bilingual_pairs", False)),
    )
    if variant_specs is not None:
        column_layouts = list(dict.fromkeys(spec.column_layout for spec in variant_specs))

    return SynthConfig(
        source_cases=list(raw_cases),
        copies_per_case=int(raw["copies_per_case"]),
        max_source_pages=max_source_pages,
        seed=int(raw["seed"]),
        output_root=str(raw["output_root"]),
        translate_categories=list(raw["translate_categories"]),
        column_layouts=column_layouts,
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
            batch_max_chars=batch_max_chars,
        ),
        ocr=OcrConfig(
            url=str(ocr_raw.get("url") or "").strip(),
            timeout=int(ocr_raw.get("timeout") or 300),
        ),
        max_workers=max_workers,
        synchronize_bilingual_pairs=bool(
            raw.get("synchronize_bilingual_pairs", False)
        ),
        variant_specs=variant_specs,
    )


def _parse_column_layouts(raw: object) -> list[str]:
    if raw is None:
        values = list(ALLOWED_COLUMN_LAYOUTS)
    elif isinstance(raw, str):
        values = [raw]
    else:
        values = list(raw)
    layouts: list[str] = []
    for item in values:
        layout = str(item).strip()
        if not layout:
            continue
        if layout not in ALLOWED_COLUMN_LAYOUTS:
            raise ValueError(
                f"unknown column_layout {layout!r}; allowed: {ALLOWED_COLUMN_LAYOUTS}"
            )
        if layout not in layouts:
            layouts.append(layout)
    if not layouts:
        raise ValueError("column_layouts is empty")
    return layouts


def _parse_variant_specs(
    raw: object,
    *,
    column_layouts: Sequence[str],
    synchronize_pairs: bool,
) -> list[VariantSpec] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise ValueError("variants must be a non-empty list")

    specs: list[VariantSpec] = []
    names: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"variants[{index}] must be an object")
        layout = str(item.get("column_layout", "")).strip()
        if layout not in ALLOWED_COLUMN_LAYOUTS:
            raise ValueError(
                f"variants[{index}].column_layout {layout!r} is invalid; "
                f"allowed: {ALLOWED_COLUMN_LAYOUTS}"
            )
        if layout not in column_layouts:
            raise ValueError(
                f"variants[{index}].column_layout {layout!r} is not present "
                "in column_layouts"
            )

        raw_mode = item.get("pagination_mode")
        raw_sync = item.get("synchronize_pairs")
        if raw_mode is None:
            if raw_sync is None:
                mode = "no-cross" if synchronize_pairs else "cross"
            elif isinstance(raw_sync, bool):
                mode = "no-cross" if raw_sync else "cross"
            else:
                raise ValueError(
                    f"variants[{index}].synchronize_pairs must be boolean"
                )
        else:
            mode = str(raw_mode).strip()
            if mode not in ALLOWED_PAGINATION_MODES:
                raise ValueError(
                    f"variants[{index}].pagination_mode {mode!r} is invalid; "
                    f"allowed: {ALLOWED_PAGINATION_MODES}"
                )
            if raw_sync is not None:
                if not isinstance(raw_sync, bool):
                    raise ValueError(
                        f"variants[{index}].synchronize_pairs must be boolean"
                    )
                if raw_sync != (mode == "no-cross"):
                    raise ValueError(
                        f"variants[{index}] has conflicting pagination_mode and "
                        "synchronize_pairs"
                    )

        name = str(item.get("name") or f"{layout}_{mode}").strip()
        if not name or name in names or "/" in name or "\\" in name:
            raise ValueError(f"variants[{index}].name must be unique and path-safe")
        names.add(name)
        specs.append(
            VariantSpec(
                name=name,
                column_layout=layout,
                pagination_mode=mode,
            )
        )
    return specs


def choose_column_layout(layouts: Sequence[str], seed: int) -> str:
    options = [item for item in layouts if item in ALLOWED_COLUMN_LAYOUTS]
    if not options:
        options = list(ALLOWED_COLUMN_LAYOUTS)
    if len(options) == 1:
        return options[0]
    return random.Random(seed).choice(options)


def _is_source_case(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / _ORIGIN).is_file()
        and (path / _FILLIN).is_file()
    )


def _as_path(item: str, workspace: Path) -> Path:
    path = Path(item)
    return path if path.is_absolute() else workspace / path


def _lines_from_list_file(path: Path) -> list[str]:
    entries: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


def _flatten_source_entries(patterns: Sequence[str], workspace: Path) -> list[str]:
    """Expand list files (one case path per line) into concrete entries."""
    entries: list[str] = []
    for raw in patterns:
        item = str(raw).strip()
        if not item:
            continue
        path = _as_path(item, workspace)
        if path.is_file():
            entries.extend(_lines_from_list_file(path))
        else:
            entries.append(item)
    return entries


def expand_source_cases(patterns: Sequence[str], workspace: Path) -> list[Path]:
    """Resolve yaml `source_cases` entries to case directories.

    Each entry may be a glob, a case directory, or a text file listing
    absolute (or workspace-relative) case directories, one per line.
    """
    workspace = Path(workspace)
    found: list[Path] = []
    seen: set[Path] = set()
    for raw in _flatten_source_entries(patterns, workspace):
        pattern = str(raw).strip()
        if not pattern:
            continue
        is_glob = any(ch in pattern for ch in "*?[")
        if is_glob:
            matches = sorted(workspace.glob(pattern))
        else:
            matches = [_as_path(pattern, workspace)]
        for match in matches:
            if not _is_source_case(match):
                if not is_glob:
                    logger.warning("skip invalid or missing source case: %s", match)
                continue
            resolved = match.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(match)
    if not found:
        raise ValueError(f"no source cases matched {list(patterns)} under {workspace}")
    return found
