# Pipeline Explainer Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `explain/` 下放一份可 `file://` 打开的静态讲解页，用 `synth_001` 讲清输入、六步处理、五件套输出。

**Architecture:** 无构建。`data.js` 内嵌 JSON 兼容的 `var EXPLAIN = {...};`。`index.html` 用经典 `<script src="data.js">` 再跑一段内联脚本，以 `textContent` 填 `<pre>` 和对照表。源第 1 页图拷进 `explain/assets/`，不 fetch、不用 ES module、无 CDN。

**Tech Stack:** 静态 HTML/CSS/JS；契约测试用现有 pytest。

**Spec:** `docs/superpowers/specs/2026-08-20-pipeline-explainer-page-design.md`

## Global Constraints

- 无构建、无 React、无 npm、无 CDN。
- 不用 `fetch`；不用 ES module（`<script>` 不得带 `type="module"`）。
- `data.js` 必须是 `var EXPLAIN = { ... };`，对象体必须是可 `json.loads` 的 JSON（双引号、无尾逗号）。
- 图片相对路径 `assets/raw-page-1.png`，从 `data/source/task_001_002_raw/images_path/raw-page-1.png` **拷贝**，不得 symlink、不得指回 `data/`。
- 不改 `src/synth/`，不改 `data/examples/` 与源 JSON。
- 浅色、扁平、无渐变、无阴影、无装饰性表情符号。
- HTML 切片用 `textContent` 填 `<pre>`，禁止 `innerHTML` 注入这些切片。
- 本仓库若还不是 git 仓，跳过所有 Commit 步骤，不要 `git init`。
- 每个任务的测试命令：`python3 -m pytest tests/explain/test_explain_page.py -q`

## File map

| 路径 | 职责 |
|------|------|
| `explain/data.js` | 内嵌样例摘录；页面运行时唯一数据源 |
| `explain/assets/raw-page-1.png` | 源第 1 页图副本 |
| `explain/index.html` | 结构、文案、代码切片、内联填充脚本 |
| `explain/styles.css` | 浅色单栏排版与双栏示意图 |
| `README.md` | 加一行讲解页入口 |
| `tests/explain/test_explain_page.py` | 锁定文件、EXPLAIN 形状、HTML 约束、README |

DOM id（后任务必须沿用）：`data-error`、`input`、`step-1`…`step-6`、`output`、`origin-input`、`source-nodes`、`source-blocks`、`html-zh`、`html-pair`、`stats`、`id-map-body`、`artifact-origin`、`artifact-label`、`artifact-tree`、`artifact-prelabel`。

`EXPLAIN` 字段（后任务必须沿用）：`originInput`、`sourceNodes`、`sourceBlocks`、`htmlZh`、`htmlPair`、`stats`、`idMap`、`artifacts.origin`、`artifacts.label`、`artifacts.treeNode`、`artifacts.prelabel`。`idMap` 每项为 `{sourceId, role, newId, bboxX}`。

---

### Task 1: EXPLAIN 数据与源页图

**Files:**
- Create: `tests/explain/test_explain_page.py`
- Create: `explain/data.js`
- Create: `explain/assets/raw-page-1.png`（拷贝，非新建像素）

**Interfaces:**
- Consumes: 源图 `data/source/task_001_002_raw/images_path/raw-page-1.png`；样例摘录见本任务 Step 3 的完整 `data.js`
- Produces: `load_explain()` 可读的 `EXPLAIN` 对象；`explain/assets/raw-page-1.png` 为普通文件且 `st_size > 100000`

- [ ] **Step 1: 写失败测试**

创建 `tests/explain/test_explain_page.py`，全文如下：

```python
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPLAIN_JS = ROOT / "explain" / "data.js"
EXPLAIN_PNG = ROOT / "explain" / "assets" / "raw-page-1.png"
EXPLAIN_HTML = ROOT / "explain" / "index.html"
EXPLAIN_CSS = ROOT / "explain" / "styles.css"
README = ROOT / "README.md"


def load_explain() -> dict:
    text = EXPLAIN_JS.read_text(encoding="utf-8")
    assert text.startswith("var EXPLAIN = ")
    body = text[len("var EXPLAIN = ") :].strip()
    if body.endswith(";"):
        body = body[:-1]
    return json.loads(body)


def test_data_js_contract() -> None:
    data = load_explain()
    assert data["originInput"] == {
        "doc_id": "doc_21f4dc879ffd",
        "document_type": "policy_document",
        "reading_direction": "horizontal",
    }
    nodes = {n["id"]: n for n in data["sourceNodes"]}
    assert nodes["p0-b2"]["category"] == "doc_title"
    assert nodes["p0-b2"]["text"] == "山东省工业和信息化厅文件"
    assert nodes["p0-b2"]["link_to"] == []
    assert nodes["p0-b3"]["text"] == "鲁工信消〔2021〕77号"
    assert nodes["p0-b8"]["category"] == "seal"
    assert nodes["p0-b8"]["text"] == ""
    blocks = {b["id"]: b for b in data["sourceBlocks"]}
    assert blocks["p0-b3"]["page"] == 0
    assert blocks["p0-b3"]["image_path"] is None
    assert blocks["p0-b8"]["image_path"].endswith("img_p0-b8.png")
    assert "鲁工信消〔2021〕77号" in data["htmlZh"]
    assert 'data-lang="zh"' in data["htmlZh"]
    assert "Lu Gongxin Xiao [2021] No. 77" in data["htmlPair"]
    assert 'data-lang="en"' in data["htmlPair"]
    assert data["stats"] == {
        "n_zh": 22,
        "n_en": 21,
        "en_cross_page": 0,
        "n_errors": 0,
    }
    assert data["idMap"] == [
        {
            "sourceId": "p0-b3",
            "role": "中文文号",
            "newId": "p0-b2",
            "bboxX": "40–488",
        },
        {
            "sourceId": "p0-b3",
            "role": "英文文号",
            "newId": "p0-b16",
            "bboxX": "512–960",
        },
    ]
    origin = data["artifacts"]["origin"]
    assert origin["doc_id"] == "synth_bilingual_001_doc_21f4dc879ffd"
    assert origin["task_id"] == "synth_task_001"
    assert origin["document_type"] == "policy_document"
    label_blocks = data["artifacts"]["label"]["pages"][0]["blocks"]
    assert label_blocks[0]["block_id"] == 1
    assert label_blocks[0]["bbox"] == [40.0, 40.0, 488.0, 75.1875]
    assert label_blocks[1]["block_id"] == 2
    tree = data["artifacts"]["treeNode"]
    assert tree["member"] == ["p0-b2", "p0-b16"]
    assert tree["text"] == ["", ""]
    pre = data["artifacts"]["prelabel"]
    assert pre["text"] == "山东省工业和信息化厅文件"
    assert pre["source"] == "paddle_ocr"


def test_source_page_image_is_copied_file() -> None:
    assert EXPLAIN_PNG.is_file()
    assert not EXPLAIN_PNG.is_symlink()
    assert EXPLAIN_PNG.stat().st_size > 100000
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/explain/test_explain_page.py::test_data_js_contract tests/explain/test_explain_page.py::test_source_page_image_is_copied_file -q`

Expected: FAIL，因为 `explain/data.js` 或 PNG 不存在（`FileNotFoundError` 或 assertion）。

- [ ] **Step 3: 拷贝页图并写入 `data.js`**

```bash
mkdir -p explain/assets
cp data/source/task_001_002_raw/images_path/raw-page-1.png explain/assets/raw-page-1.png
```

创建 `explain/data.js`，全文必须等于：

```javascript
var EXPLAIN = {
  "originInput": {
    "doc_id": "doc_21f4dc879ffd",
    "document_type": "policy_document",
    "reading_direction": "horizontal"
  },
  "sourceNodes": [
    {
      "id": "p0-b2",
      "category": "doc_title",
      "text": "山东省工业和信息化厅文件",
      "link_to": []
    },
    {
      "id": "p0-b3",
      "category": "text",
      "text": "鲁工信消〔2021〕77号",
      "link_to": []
    },
    {
      "id": "p0-b8",
      "category": "seal",
      "text": "",
      "link_to": []
    }
  ],
  "sourceBlocks": [
    {
      "id": "p0-b3",
      "page": 0,
      "category": "text",
      "text": "鲁工信消〔2021〕77号",
      "image_path": null
    },
    {
      "id": "p0-b8",
      "page": 0,
      "category": "seal",
      "text": "",
      "image_path": "_assets_1_52/img_p0-b8.png"
    }
  ],
  "htmlZh": "<div class=\"block\" data-category=\"text\" data-lang=\"zh\" data-node-id=\"p0-b3\">鲁工信消〔2021〕77号</div>\n<img class=\"block\" data-category=\"seal\" data-lang=\"zh\" data-node-id=\"p0-b8\" src=\"file://...\" alt=\"\" />",
  "htmlPair": "<div class=\"block\" data-category=\"text\" data-lang=\"zh\" data-node-id=\"p0-b3\">鲁工信消〔2021〕77号</div>\n<div class=\"block\" data-category=\"text\" data-lang=\"en\" data-node-id=\"p0-b3\">Lu Gongxin Xiao [2021] No. 77</div>\n<img class=\"block\" data-category=\"seal\" data-lang=\"zh\" data-node-id=\"p0-b8\" src=\"file://...\" alt=\"\" />",
  "stats": {
    "n_zh": 22,
    "n_en": 21,
    "en_cross_page": 0,
    "n_errors": 0
  },
  "idMap": [
    {
      "sourceId": "p0-b3",
      "role": "中文文号",
      "newId": "p0-b2",
      "bboxX": "40–488"
    },
    {
      "sourceId": "p0-b3",
      "role": "英文文号",
      "newId": "p0-b16",
      "bboxX": "512–960"
    }
  ],
  "artifacts": {
    "origin": {
      "doc_id": "synth_bilingual_001_doc_21f4dc879ffd",
      "task_id": "synth_task_001",
      "pdf_path": "",
      "images_path": "/home/wanyi/projects/Document_analyst_Trainer/复刻失效数据/合成数据/bilingual_v1/synth_001_doc_21f4dc879ffd/images_path",
      "reading_direction": "horizontal",
      "document_type": "policy_document"
    },
    "label": {
      "pages": [
        {
          "page_index": 0,
          "blocks": [
            {
              "block_id": 1,
              "bbox": [40.0, 40.0, 488.0, 75.1875],
              "category": "doc_title",
              "page_index": 0
            },
            {
              "block_id": 2,
              "bbox": [40.0, 83.1875, 488.0, 108.78125],
              "category": "text",
              "page_index": 0
            }
          ]
        }
      ]
    },
    "treeNode": {
      "id": "p0-b2",
      "page_index": [0, 0],
      "member": ["p0-b2", "p0-b16"],
      "category": ["text", "text"],
      "bbox": [
        [40.0, 83.1875, 488.0, 108.78125],
        [512.0, 86.0625, 960.0, 105.09375]
      ],
      "text": ["", ""]
    },
    "prelabel": {
      "block_id": 1,
      "bbox": [129.0, 43.0, 399.0, 71.0],
      "text": "山东省工业和信息化厅文件",
      "category": "doc_title",
      "score": 1.0,
      "source": "paddle_ocr"
    }
  }
};
```

注意 bboxX 字符串里的连接符是 Unicode en dash `–`（U+2013），与测试字面量一致，不要写成 ASCII 连字符 `-`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/explain/test_explain_page.py::test_data_js_contract tests/explain/test_explain_page.py::test_source_page_image_is_copied_file -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/explain/test_explain_page.py explain/data.js explain/assets/raw-page-1.png
git commit -m "$(cat <<'EOF'
Add explainer sample data and source page image.

EOF
)"
```

若不是 git 仓：跳过本步。

---

### Task 2: 讲解页 HTML/CSS 与填充脚本

**Files:**
- Modify: `tests/explain/test_explain_page.py`（追加页面测试）
- Create: `explain/index.html`
- Create: `explain/styles.css`

**Interfaces:**
- Consumes: `EXPLAIN` 字段名与 Task 1 的 `load_explain()`；DOM id 见本计划 File map
- Produces: `explain/index.html` 可 `file://` 打开；脚本用 `textContent` 填 JSON/HTML 切片；`styles.css` 最大内容宽 880px

- [ ] **Step 1: 写失败测试（追加到同一测试文件末尾）**

```python
REQUIRED_IDS = [
    "data-error",
    "input",
    "step-1",
    "step-2",
    "step-3",
    "step-4",
    "step-5",
    "step-6",
    "output",
    "origin-input",
    "source-nodes",
    "source-blocks",
    "html-zh",
    "html-pair",
    "stats",
    "id-map-body",
    "artifact-origin",
    "artifact-label",
    "artifact-tree",
    "artifact-prelabel",
]


def test_html_is_offline_static_page() -> None:
    html = EXPLAIN_HTML.read_text(encoding="utf-8")
    css = EXPLAIN_CSS.read_text(encoding="utf-8")
    assert 'href="styles.css"' in html
    assert "<script src=\"data.js\"></script>" in html
    assert "type=\"module\"" not in html
    assert "fetch(" not in html
    assert "innerHTML" not in html
    assert "https://" not in html
    assert "http://" not in html
    assert "document-failure-synth 在做什么" in html
    assert 'src="assets/raw-page-1.png"' in html
    assert 'alt="源文档第 1 页"' in html
    assert "textContent" in html
    assert "请打开整个 `explain/` 目录下的 `index.html`，不要只拷走这一个文件" in html
    for section_id in REQUIRED_IDS:
        assert f'id="{section_id}"' in html
    for href in ["#input", "#step-1", "#step-2", "#step-3", "#step-4", "#step-5", "#step-6", "#output"]:
        assert f'href="{href}"' in html
    assert "src/synth/material.py" in html
    assert "src/synth/html_builder.py" in html
    assert "src/synth/rewrite.py" in html
    assert "src/synth/paginate.js" in html
    assert "src/synth/validate.py" in html
    assert "src/synth/gt_builder.py" in html
    assert "Keep every original element with data-node-id" in html
    assert "zhNeedsPage || enNeedsPage" in html
    assert "missing en block" in html
    assert 'f"p{page}-b{index}"' in html
    assert "落盘为" in html and "rewritten.html" in html
    assert "少 1 个 en" in html
    assert "(1000 − 2×40 − 24) / 2 = 448" in html
    assert "40–488" in html and "512–960" in html
    assert "本讲解页用步骤 4 示意图代替" in html
    assert "visualize" not in html.lower()
    assert "ocr_url.txt" not in html
    assert "max-width: 880px" in css
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/explain/test_explain_page.py::test_html_is_offline_static_page -q`

Expected: FAIL，`explain/index.html` 不存在。

- [ ] **Step 3: 写 `explain/styles.css`**

全文：

```css
:root {
  --bg: #f6f5f1;
  --fg: #1c1c1c;
  --muted: #5c5c5c;
  --line: #d9d6ce;
  --code-bg: #eceae3;
  --accent: #1f4e79;
}

* { box-sizing: border-box; }

html { scroll-behavior: smooth; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--fg);
  font-family: "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", sans-serif;
  line-height: 1.6;
}

nav {
  position: sticky;
  top: 0;
  background: var(--bg);
  border-bottom: 1px solid var(--line);
  padding: 10px 16px;
  z-index: 1;
}

nav a {
  color: var(--accent);
  text-decoration: none;
  margin-right: 12px;
  font-size: 14px;
}

main {
  max-width: 880px;
  margin: 0 auto;
  padding: 32px 20px 80px;
}

h1 { font-size: 24px; font-weight: 650; margin: 0 0 12px; }
h2 { font-size: 18px; margin: 36px 0 12px; }
h3 { font-size: 16px; margin: 0 0 8px; }

p, li { color: var(--fg); }
.muted { color: var(--muted); font-size: 14px; }

.banner {
  background: #f3e6e6;
  border: 1px solid #d7b6b6;
  padding: 10px 12px;
  margin-bottom: 20px;
}

.split {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

@media (max-width: 720px) {
  .split { grid-template-columns: 1fr; }
}

.split img {
  width: 100%;
  height: auto;
  display: block;
  border: 1px solid var(--line);
}

.step {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 12px;
  margin: 28px 0;
}

.num {
  width: 28px;
  height: 28px;
  border-radius: 14px;
  background: var(--accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 650;
}

pre {
  background: var(--code-bg);
  border: 1px solid var(--line);
  padding: 10px 12px;
  overflow-x: auto;
  font-size: 12px;
  line-height: 1.45;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

code { font-family: inherit; }

.module {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  color: var(--muted);
}

.arrow {
  color: var(--muted);
  font-size: 14px;
  margin: 0 0 0 48px;
}

.spread-wrap { margin: 12px 0 8px; }

.spread-canvas {
  width: 100%;
  max-width: 1000px;
  height: 360px;
  border: 1px solid var(--line);
  background: #fff;
  display: flex;
  padding: 16px;
  gap: 8px;
}

.spread-canvas .col {
  flex: 1;
  border: 1px dashed var(--line);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  color: var(--muted);
}

.spread-canvas .gap { width: 12px; flex: 0 0 12px; }

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

th, td {
  text-align: left;
  border-bottom: 1px solid var(--line);
  padding: 6px 8px;
}

.cards {
  display: grid;
  gap: 16px;
}

.card {
  border: 1px solid var(--line);
  padding: 12px 14px;
  background: #fff;
}

.card h3 { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
```

- [ ] **Step 4: 写 `explain/index.html`**

全文如下。内联脚本必须用 `textContent` / `createElement`，不得出现 `innerHTML`、`fetch(`、`https://`、`http://`、`type="module"`。

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>document-failure-synth 在做什么</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <nav>
    <a href="#input">输入</a>
    <a href="#step-1">1</a>
    <a href="#step-2">2</a>
    <a href="#step-3">3</a>
    <a href="#step-4">4</a>
    <a href="#step-5">5</a>
    <a href="#step-6">6</a>
    <a href="#output">输出</a>
  </nav>
  <main>
    <p id="data-error" class="banner" hidden>
      请打开整个 `explain/` 目录下的 `index.html`，不要只拷走这一个文件
    </p>
    <h1>document-failure-synth 在做什么</h1>
    <p>从已有中文 fillin 树合成左栏中文、右栏英文的双栏文档，产出与真实标注目录同构的五件套：origin / prelabel / label / multi-page-final / images_path。</p>
    <p>用途：给下游 Trainer 提供双栏双语失效样本。本仓止于标注产物，不再生成 train.jsonl。</p>
    <p class="muted">本页只看一份样例：源 doc_21f4dc879ffd（policy_document）→ synth_001，seed 52。</p>

    <h2 id="input">输入</h2>
    <div class="split">
      <div>
        <img src="assets/raw-page-1.png" alt="源文档第 1 页" />
        <p class="muted">源文件拷贝自 data/source/task_001_002_raw/images_path/raw-page-1.png</p>
      </div>
      <div>
        <h3>origin.json</h3>
        <pre id="origin-input"></pre>
        <h3>fillin 节点摘录</h3>
        <pre id="source-nodes"></pre>
      </div>
    </div>
    <ul class="muted">
      <li>源树共 8 页；配置 max_source_pages: 4，只用页 0–3。</li>
      <li>任何非空 link_to 的 case 直接拒绝。</li>
      <li>image / chart / seal / table 要从源页图按 bbox 裁切；缺页图会在这一步失败。</li>
    </ul>

    <h2>处理过程</h2>

    <div class="step" id="step-1">
      <div class="num">1</div>
      <div>
        <h3>加载素材</h3>
        <p>读 fillin 树和 origin，展平成块。图、章、印、表按 bbox 从源页图裁成小图，供后面 HTML 引用。</p>
        <p class="muted">本步输入：上面三个节点。</p>
        <p class="muted">本步输出：SourceBlock 列表。</p>
        <pre id="source-blocks"></pre>
        <p class="module">src/synth/material.py · load_material</p>
        <pre><code>tree = load_fillin_tree(case_dir)
_assert_no_link_to(tree)
origin = json.loads((case_dir / ORIGIN_FILENAME).read_text(encoding="utf-8"))
blocks: list[SourceBlock] = []
for raw in iter_source_blocks(tree):
    page = int(raw["page"])
    if page >= cfg.max_source_pages:
        continue
    image_path = _crop_image_block(...) if raw["category"] in IMAGE_CATEGORIES else None
    blocks.append(SourceBlock(id=raw["id"], page=page, category=raw["category"], text=raw["text"], image_path=image_path))</code></pre>
      </div>
    </div>
    <p class="arrow">下一块把这些块变成带标记的 HTML。</p>

    <div class="step" id="step-2">
      <div class="num">2</div>
      <div>
        <h3>拼源 HTML</h3>
        <p>还不管双栏。每个块打上 data-node-id / data-category / data-lang="zh"，方便后面校对：一个都没丢。</p>
        <p class="muted">本步输入：块列表。本步输出：只有中文的 HTML。</p>
        <pre id="html-zh"></pre>
        <p class="module">src/synth/html_builder.py · build_source_html</p>
        <pre><code>if block.page != current_page:
    parts.append(f'&lt;section class="src-page" data-src-page="{current_page}"&gt;')
attrs = (
    f'data-node-id="{html.escape(block.id, quote=True)}" '
    f'data-category="{html.escape(block.category, quote=True)}" '
    f'data-lang="zh"'
)
if block.image_path:
    parts.append(f'&lt;img class="block" {attrs} src="{src}" alt="" /&gt;')
else:
    parts.append(f'&lt;div class="block" {attrs}&gt;{html.escape(block.text)}&lt;/div&gt;')</code></pre>
      </div>
    </div>
    <p class="arrow">然后才轮到模型：只插英文，不排版。</p>

    <div class="step" id="step-3">
      <div class="num">3</div>
      <div>
        <h3>LLM 插英文</h3>
        <p>这是整条链路里唯一的模型调用。每个源页 section 单独翻译。中文节点不许改；应译类后面插一个相同 data-node-id 的英文兄弟。图章表格页眉页脚不译。</p>
        <p class="muted">本步输入：步骤 2 的中文 HTML。本步输出落盘为 rewritten.html。印章没有 en。</p>
        <pre id="html-pair"></pre>
        <p class="module">src/synth/rewrite.py · rewrite_html</p>
        <pre><code>HARD RULES:
1. Keep every original element with data-node-id exactly as-is. Never drop, rename, or change any attribute (including src, class).
2. Original Chinese blocks have data-lang="zh". Do not modify their text content.
3. For EACH block with data-lang="zh" AND data-category in {translate_categories} (text blocks only, NOT img):
   insert immediately AFTER it one English translation element with the SAME data-node-id.
4. Do NOT add English blocks for image/chart/seal/table/header/footer blocks or any img element.</code></pre>
      </div>
    </div>
    <p class="arrow">几何交给浏览器脚本，不交给模型。</p>

    <div class="step" id="step-4">
      <div class="num">4</div>
      <div>
        <h3>分页截图</h3>
        <p>Playwright 打开 HTML，注入 paginate.js：左栏中文、右栏英文。同一 data-node-id 的 zh/en 必须同页；任一栏放不下就整对翻页。截图像素空间就是后面的 bbox。</p>
        <p class="muted">本步输入：成对 HTML。样例目录没有合成 PNG，用示意图代替。</p>
        <div class="spread-wrap">
          <div class="spread-canvas">
            <div class="col">中文 x=40–488</div>
            <div class="gap"></div>
            <div class="col">英文 x=512–960</div>
          </div>
        </div>
        <p class="muted">栏宽 (1000 − 2×40 − 24) / 2 = 448 · column_gap=24</p>
        <p class="module">src/synth/paginate.js · paginateDocument（由 render.render_pages 注入）</p>
        <pre><code>const zhNeedsPage = Boolean(pair.zh) && !fits(leftY, zhH) && leftY > margin;
const enNeedsPage = Boolean(pair.en) && !fits(rightY, enH) && rightY > margin;
if (zhNeedsPage || enNeedsPage) newPage();</code></pre>
      </div>
    </div>
    <p class="arrow">排完必须过校验，不过就整份丢掉。</p>

    <div class="step" id="step-5">
      <div class="num">5</div>
      <div>
        <h3>校验</h3>
        <p>中文必须还是源树（id / 顺序 / 类别 / 原文）；该译的恰好一个英文；同 id 的 zh/en 同页且 x 不重叠。不过就整份丢掉重试。</p>
        <p class="muted">本步输入：源树 + PlacedBlock。本份统计：少 1 个 en 是因为印章不译。</p>
        <pre id="stats"></pre>
        <p class="module">src/synth/validate.py · validate_doc</p>
        <pre><code>if _is_translatable(zh.category, cfg):
    if len(en_list) == 0:
        errors.append(f"missing en block: {node_id}")
    elif len(en_list) &gt; 1:
        errors.append(f"duplicate en block: {node_id}")
elif en_list:
    errors.append(f"unexpected en block for non-translatable: {node_id}")
if en.page != zh.page:
    errors.append(
        f"zh/en page split for {zh.node_id}: zh={zh.page} en={en.page}"
    )</code></pre>
      </div>
    </div>
    <p class="arrow">通过后按阅读序重编号，写成下游认得的树。</p>

    <div class="step" id="step-6">
      <div class="num">6</div>
      <div>
        <h3>写标注</h3>
        <p>按阅读序重编号：页内先左栏中文，再右栏英文。双语树节点 member=[中文新id, 英文新id]，text 全空。然后对截图跑 PaddleOCR 得到 prelabel（探测器，不与 GT 对齐）。</p>
        <p class="muted">本步输入：PlacedBlock。源文号 p0-b3 在合成树里拆成左右两个新 id。</p>
        <table>
          <thead>
            <tr><th>源 id</th><th>角色</th><th>新 id</th><th>bbox x</th></tr>
          </thead>
          <tbody id="id-map-body"></tbody>
        </table>
        <p class="module">src/synth/gt_builder.py · _assign_ids</p>
        <pre><code>for page in sorted(by_page):
    ordered: list[PlacedBlock] = []
    for lang in ("zh", "en"):
        ordered.extend(sorted(by_page[page][lang], key=_sort_key))
    for index, block in enumerate(ordered, start=1):
        block.__dict__["new_id"] = f"p{page}-b{index}"
        mapped[(block.node_id, block.lang)] = block</code></pre>
      </div>
    </div>

    <h2 id="output">输出 · 五件套</h2>
    <div class="cards">
      <div class="card">
        <h3>origin.json</h3>
        <p>合成文档身份。真实跑通后 images_path 指向本份 images_path/。</p>
        <pre id="artifact-origin"></pre>
      </div>
      <div class="card">
        <h3>label.json</h3>
        <p>页内块框（无字）。</p>
        <pre id="artifact-label"></pre>
      </div>
      <div class="card">
        <h3>multi-page-final.json</h3>
        <p>双语树。文号节点 member 原文在前、译文在后，text 留空。</p>
        <pre id="artifact-tree"></pre>
      </div>
      <div class="card">
        <h3>prelabel.json</h3>
        <p>OCR 预标注（有字），与 GT 框不必对齐。</p>
        <pre id="artifact-prelabel"></pre>
      </div>
      <div class="card">
        <h3>images_path/</h3>
        <p>截图像素空间。真实跑通后为 raw-page-N.png（N 从 1），bbox 与图同一坐标系。本讲解页用步骤 4 示意图代替。</p>
      </div>
    </div>
  </main>
  <script src="data.js"></script>
  <script>
    (function () {
      var banner = document.getElementById("data-error");
      if (typeof EXPLAIN === "undefined") {
        banner.hidden = false;
        return;
      }
      function fillJson(id, obj) {
        document.getElementById(id).textContent = JSON.stringify(obj, null, 2);
      }
      function fillText(id, text) {
        document.getElementById(id).textContent = text;
      }
      fillJson("origin-input", EXPLAIN.originInput);
      fillJson("source-nodes", EXPLAIN.sourceNodes);
      fillJson("source-blocks", EXPLAIN.sourceBlocks);
      fillText("html-zh", EXPLAIN.htmlZh);
      fillText("html-pair", EXPLAIN.htmlPair);
      fillJson("stats", EXPLAIN.stats);
      fillJson("artifact-origin", EXPLAIN.artifacts.origin);
      fillJson("artifact-label", EXPLAIN.artifacts.label);
      fillJson("artifact-tree", EXPLAIN.artifacts.treeNode);
      fillJson("artifact-prelabel", EXPLAIN.artifacts.prelabel);
      var tbody = document.getElementById("id-map-body");
      EXPLAIN.idMap.forEach(function (row) {
        var tr = document.createElement("tr");
        [row.sourceId, row.role, row.newId, row.bboxX].forEach(function (cell) {
          var td = document.createElement("td");
          td.textContent = cell;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    })();
  </script>
</body>
</html>
```

步骤 2 代码块里的 `<section>` / `<div>` / `<img>` 必须写成 `&lt;...&gt;`，否则浏览器会当真标签解析，测试里的 `src/synth/html_builder.py` 仍能匹配，但页面会坏掉。

- [ ] **Step 5: 跑测试确认通过**

Run: `python3 -m pytest tests/explain/test_explain_page.py -q`

Expected: PASS（含 Task 1 两条）。

- [ ] **Step 6: 浏览器核对（人工）**

用浏览器直接打开 `explain/index.html`（`file://`，不要起服务器）。确认：源第 1 页图可见；步骤 3 的 `<pre id="html-pair">` 出现 `Lu Gongxin Xiao [2021] No. 77`；步骤 5 数字 22 / 21 / 0；步骤 6 表格两行 `p0-b3` → `p0-b2` / `p0-b16`；顶栏锚点能跳转。

- [ ] **Step 7: Commit**

```bash
git add tests/explain/test_explain_page.py explain/index.html explain/styles.css
git commit -m "$(cat <<'EOF'
Add static pipeline explainer page.

EOF
)"
```

若不是 git 仓：跳过本步。

---

### Task 3: README 入口

**Files:**
- Modify: `README.md`
- Modify: `tests/explain/test_explain_page.py`

**Interfaces:**
- Consumes: `explain/index.html` 已存在
- Produces: README「运行」节后有讲解页入口，测试锁定该行

- [ ] **Step 1: 写失败测试**

追加：

```python
def test_readme_points_to_explainer() -> None:
    text = README.read_text(encoding="utf-8")
    assert "explain/index.html" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/explain/test_explain_page.py::test_readme_points_to_explainer -q`

Expected: FAIL，`assert "explain/index.html" in text`

- [ ] **Step 3: 改 README**

在现有「运行」代码块之后追加（不要删改环境和运行命令）：

```markdown
讲解页（无需安装，浏览器打开即可）：[`explain/index.html`](explain/index.html)
```

完整文件应变为：

```markdown
# document-failure-synth

本项目生成双语双栏**标注五件套**（origin / prelabel / label / multi-page-final / images_path）。

## 环境

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

按需填写 `.env` 中的 API 与模型配置。

## 运行

```bash
python3 -m src.synth.runner --config src/synth/config/synth.yaml
```

合成结果默认写到 `data/output/`。

讲解页（无需安装，浏览器打开即可）：[`explain/index.html`](explain/index.html)
```

注意：上面外层已是 markdown 围栏。实现时按「在运行节后加那一行」操作，不要把 README 嵌进另一层坏掉的围栏。正确的 README 最后两行是：

合成结果默认写到 `data/output/`。

讲解页（无需安装，浏览器打开即可）：[`explain/index.html`](explain/index.html)

- [ ] **Step 4: 跑全部讲解页测试**

Run: `python3 -m pytest tests/explain/test_explain_page.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md tests/explain/test_explain_page.py
git commit -m "$(cat <<'EOF'
Point README at the pipeline explainer page.

EOF
)"
```

若不是 git 仓：跳过本步。

---

## Spec coverage (self-review)

| Spec 节 | 任务 |
|---------|------|
| §3 目录与 file:// 约束 | Task 1 PNG/data.js；Task 2 html 禁 fetch/module/CDN |
| §4 锚点与滚动结构 | Task 2 nav + ids |
| §5.1 开头文案 | Task 2 h1 与三段正文 |
| §5.2 输入图 + origin + 三节点 + 脚注 | Task 1 数据；Task 2 布局 |
| §5.3 六步模板、模块、代码、箭头 | Task 2 |
| §5.3.6 对照表 | Task 1 idMap；Task 2 tbody 填充 |
| §5.4 五件套卡片；rewritten 不单开；无 visualize / ocr_url | Task 2 |
| §6 EXPLAIN 形状与 textContent | Task 1–2 |
| §7 880px 浅色 | Task 2 CSS |
| §8 data.js 缺失横幅 + 图失败说明 | Task 2 |
| §9 浏览器验收 | Task 2 Step 6 |
| §10 README | Task 3 |
| 不改 src/synth、不改样例 JSON | 全局约束 |
