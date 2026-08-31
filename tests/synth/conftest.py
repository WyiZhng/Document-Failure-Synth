from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from src.synth.config import load_config
from src.synth.material import load_material


def _write_page_image(path: Path, width: int = 1000, height: int = 1000, color=(240, 240, 240)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), color).save(path)


def _base_tree(*extra_children: dict) -> list[dict]:
    children = [
        {
            "id": "p0-b1",
            "page_index": [0],
            "member": ["p0-b1"],
            "children": [],
            "category": ["paragraph_title"],
            "bbox": [[100, 100, 400, 150]],
            "text": ["标题一"],
            "is_virtual": False,
            "link": False,
            "link_to": [],
        },
        {
            "id": "p0-b2",
            "page_index": [0],
            "member": ["p0-b2"],
            "children": [],
            "category": ["text"],
            "bbox": [[100, 200, 400, 250]],
            "text": ["正文内容"],
            "is_virtual": False,
            "link": False,
            "link_to": [],
        },
        *extra_children,
    ]
    return [
        {
            "id": "p0-fake1",
            "page_index": [0],
            "member": [],
            "children": children,
            "category": ["text"],
            "bbox": [],
            "text": [""],
            "is_virtual": True,
            "link": False,
            "link_to": [],
        }
    ]


def _write_case_dir(
    case_dir: Path,
    tree: list[dict],
    *,
    wrap_doc: bool = False,
    page_size: tuple[int, int] = (1000, 1000),
    pages: tuple[int, ...] = (0,),
) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "origin.json").write_text(
        json.dumps(
            {
                "doc_id": "doc_test_001",
                "task_id": "task_test_001",
                "pdf_path": "",
                "images_path": str(case_dir / "images_path"),
                "reading_direction": "horizontal",
                "document_type": "policy_document",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    payload: list[dict] | dict = {"doc": tree} if wrap_doc else tree
    (case_dir / "multi-page-final-fillin.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    for page in pages:
        _write_page_image(
            case_dir / "images_path" / f"raw-page-{page + 1}.png", *page_size
        )


@pytest.fixture
def cfg():
    return load_config(Path("src/synth/config/synth.yaml"))


@pytest.fixture
def tiny_case_dir(tmp_path):
    _write_case_dir(tmp_path / "tiny_case", _base_tree())
    return tmp_path / "tiny_case"


@pytest.fixture
def tiny_case_with_link(tmp_path):
    tree = _base_tree()
    tree[0]["children"][0]["link"] = True
    tree[0]["children"][0]["link_to"] = [_table_link_target()]
    _write_case_dir(tmp_path / "tiny_case_link", tree, pages=(0, 1))
    return tmp_path / "tiny_case_link"


def _table_link_target() -> dict:
    return {
        "id": "p1-fake1",
        "page_index": [1],
        "member": [],
        "children": [
            {
                "id": "p1-b1",
                "page_index": [1],
                "member": ["p1-b1"],
                "children": [],
                "category": ["table"],
                "bbox": [[100, 100, 400, 300]],
                "text": [""],
                "is_virtual": False,
                "link": False,
                "link_to": [],
            }
        ],
        "category": ["table"],
        "bbox": [],
        "text": [""],
        "is_virtual": True,
        "link": False,
        "link_to": [],
    }


@pytest.fixture
def tiny_case_with_shared_link(tmp_path):
    tree = _base_tree()
    for node in tree[0]["children"][:2]:
        node["link"] = True
        node["link_to"] = [_table_link_target()]
    _write_case_dir(tmp_path / "tiny_case_shared_link", tree, pages=(0, 1))
    return tmp_path / "tiny_case_shared_link"


@pytest.fixture
def tiny_case_with_late_link(tmp_path):
    tree = _base_tree()
    tree[0]["children"][0]["link"] = True
    tree[0]["children"][0]["link_to"] = [
        {
            "id": "p5-b1",
            "page_index": [5],
            "member": ["p5-b1"],
            "children": [],
            "category": ["text"],
            "bbox": [[100, 100, 400, 150]],
            "text": ["后页正文"],
            "is_virtual": False,
            "link": False,
            "link_to": [],
        }
    ]
    _write_case_dir(tmp_path / "tiny_case_late_link", tree, pages=(0, 5))
    return tmp_path / "tiny_case_late_link"


@pytest.fixture
def tiny_case_with_image(tmp_path):
    tree = _base_tree(
        {
            "id": "p0-b3",
            "page_index": [0],
            "member": ["p0-b3"],
            "children": [],
            "category": ["image"],
            "bbox": [[100, 300, 300, 500]],
            "text": [""],
            "is_virtual": False,
            "link": False,
            "link_to": [],
        }
    )
    _write_case_dir(tmp_path / "tiny_case_image", tree)
    return tmp_path / "tiny_case_image"


@pytest.fixture
def tiny_case_with_slash_image_id(tmp_path):
    tree = _base_tree(
        {
            "id": "p0/b3",
            "page_index": [0],
            "member": ["p0/b3"],
            "children": [],
            "category": ["image"],
            "bbox": [[100, 300, 300, 500]],
            "text": [""],
            "is_virtual": False,
            "link": False,
            "link_to": [],
        }
    )
    _write_case_dir(tmp_path / "tiny_case_slash_id", tree)
    return tmp_path / "tiny_case_slash_id"


@pytest.fixture
def material_fixture(tiny_case_dir, cfg, tmp_path):
    return load_material(tiny_case_dir, cfg, tmp_path / "assets")
