# 双栏-双语失效数据合成 Pipeline 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 合成 10~20 份"双栏-双语"(左栏中文、右栏英文)文档,输出与真实标注目录同构的五件套,零改造跑通 `start_prepare_dataset.sh`。

**Architecture:** 从已有标注 fillin 树抽素材 → 生成带 `data-node-id` 标记的源 html → LLM 仅做逐块翻译(插入 `data-lang="en"` 兄弟块)→ 确定性分页脚本做双栏排版并回读 bbox → PaddleOCR 真实跑出 prelabel → gt_builder 产出五件套 → validate 做结构不变性校验,不合格整份丢弃。

**Tech Stack:** Python 3.10+、Playwright(Chromium)、PaddleOCR(PP-StructureV3)、OpenAI 兼容 API、Pillow、BeautifulSoup4、pytest。

**Spec:** `docs/superpowers/specs/2026-08-19-synthetic-bilingual-data-pipeline-design.md`(执行者必读)

## Global Constraints

- 新代码全部在 `src/synth/`,**不改动** `src/data/`、`src/train/` 及现有配置。
- 输出接口与阶段 0 标注目录同构:`origin.json` + `prelabel.json` + `label.json` + `multi-page-final.json` + `images_path/raw-page-{N}.png`(N 从 1 起,页内 `page_index` 从 0 起)。
- gt 的 bbox 一律为**截图像素坐标**,不做归一化。
- `prelabel.json` **必须来自真实运行的 PaddleOCR**,严禁从 DOM/gt 派生或模拟。
- 双语节点约定:原文与译文合为一个节点,`member` 顺序固定**原文在前、译文在后**;树层级、兄弟顺序、category 完全继承源树。
- 一期不做:非失效元素注入、渲染增强、其他失效模式;源文档只选**无 `link_to`** 的 case。
- 本目录**不是 git 仓库**,所有任务跳过 commit 步骤;以"测试通过"作为任务完成标志。
- LLM 密钥/地址走环境变量:`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`SYNTH_LLM_MODEL`;单元测试一律 mock LLM,真实调用只发生在里程碑验收。
- 翻译范围:category ∈ {text, paragraph_title, doc_title} 的块;image/chart/seal/table/header/footer 原样保留不翻译。

## 文件结构(全景)

```
src/synth/
├── __init__.py
├── config.py            # SynthConfig dataclass + load_config(path)
├── config/synth.yaml    # 默认配置
├── material.py          # 素材抽取:fillin 树 → SourceBlock 列表 + 图像裁切
├── html_builder.py      # SourceBlock → 带标记源 html
├── rewrite.py           # LLM 逐 section 翻译改写 + 标记后置校验
├── render.py            # Playwright 分页渲染 + 截图 + bbox 回读
├── paginate.js          # 确定性双栏分页脚本(render 注入)
├── ocr_prelabel.py      # PaddleOCR → prelabel.json
├── gt_builder.py        # placements + 源树 → origin/label/multi-page-final
├── validate.py          # 结构不变性校验 + 英文节点核查 + bbox 校验
├── visualize.py         # gt 叠框可视化图
└── runner.py            # 批量编排 CLI:python -m src.synth.runner
tests/synth/
├── conftest.py          # 小型 fixture 树/素材
├── test_config.py
├── test_material.py
├── test_html_builder.py
├── test_rewrite.py
├── test_render.py       # 需 playwright,标记 @pytest.mark.render
├── test_gt_builder.py
└── test_validate.py
requirements-synth.txt
```

## 核心数据结构(所有任务共用,签名以此为准)

```python
# src/synth/material.py
@dataclass
class SourceBlock:
    id: str            # 源块 id,如 "p0-b1"
    page: int          # 源页码(0 起)
    category: str
    text: str          # fillin 文字;图像块为 ""
    image_path: str | None  # 图像块裁切后的 png 路径,文本块为 None

@dataclass
class Material:
    doc_id: str
    document_type: str
    blocks: list[SourceBlock]   # 阅读顺序(树的先序遍历序)
    tree: list[dict]            # 源 fillin 树(原样)
    assets_dir: Path

# src/synth/render.py
@dataclass
class PlacedBlock:
    node_id: str       # 源块 id(中英共用,靠 lang 区分)
    lang: str          # "zh" | "en"
    category: str
    page: int          # 合成页码(0 起)
    bbox: tuple[float, float, float, float]  # 页内截图像素坐标
    text: str
    order: int         # 全文档 DOM 顺序

# src/synth/validate.py
@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    stats: dict        # {"n_zh":…, "n_en":…, "en_cross_page":…, ...}
```

---

# 里程碑 M1:html → 渲染 → bbox 回读(补 spike 未验证环节)

### Task 1: 脚手架 + 配置

**Files:**
- Create: `src/synth/__init__.py`(空)、`src/synth/config.py`、`src/synth/config/synth.yaml`、`requirements-synth.txt`、`tests/synth/__init__.py`、`tests/synth/test_config.py`

**Interfaces:**
- Produces: `load_config(path: str | Path) -> SynthConfig`;`SynthConfig` 字段见下方 yaml,嵌套用 dataclass(`PageConfig`, `LlmConfig`)。

- [ ] **Step 1: 写 `src/synth/config/synth.yaml`**

```yaml
source_cases:
  - 复刻失效数据/中间结果/dataset/task_001_002_raw
copies_per_case: 10
max_source_pages: 4          # 每份合成文档最多取源文档前 N 页素材
seed: 42
output_root: 复刻失效数据/合成数据/bilingual_v1
translate_categories: [text, paragraph_title, doc_title]
page:
  width: 1000                # CSS px,亦即截图像素宽
  height: 1414               # A4 比例
  margin: 40
  column_gap: 24
llm:
  model_env: SYNTH_LLM_MODEL
  base_url_env: OPENAI_BASE_URL
  api_key_env: OPENAI_API_KEY
  temperature: 0.7
  max_retries: 1
```

- [ ] **Step 2: 写失败测试 `tests/synth/test_config.py`**

```python
from pathlib import Path
from src.synth.config import load_config

def test_load_default_config():
    cfg = load_config(Path("src/synth/config/synth.yaml"))
    assert cfg.page.width == 1000
    assert cfg.copies_per_case == 10
    assert "text" in cfg.translate_categories
```

- [ ] **Step 3: 运行确认失败**(`pytest tests/synth/test_config.py -v`,预期 ModuleNotFoundError)
- [ ] **Step 4: 实现 `config.py`**(dataclass + `yaml.safe_load`,路径字段保持字符串、由使用方 resolve)
- [ ] **Step 5: 运行测试通过;写 `requirements-synth.txt`**

```
pyyaml
pillow
beautifulsoup4
lxml
playwright
openai
pytest
# PaddleOCR 相关(M3 安装):paddlepaddle、paddleocr
```

### Task 2: material — 素材抽取与图像裁切

**Files:**
- Create: `src/synth/material.py`、`tests/synth/test_material.py`、`tests/synth/conftest.py`

**Interfaces:**
- Consumes: `SynthConfig`
- Produces: `load_material(case_dir: Path, cfg: SynthConfig, assets_dir: Path) -> Material`

**要点:**
- fillin 树顶层兼容 `{"doc":[...]}` 与裸数组;先序遍历展平真实节点的 members(逻辑参考 `spikes/html_rewrite_retention/html_from_tree.py` 的 `iter_marked_blocks`,但**不跳过图像块**)。
- 源 case 含 `link_to` 非空节点时抛 `ValueError`(一期约束)。
- 只取 `page < cfg.max_source_pages` 的块。
- 图像块(category ∈ {image, chart, seal, table}):fillin 的 bbox 是 qwen_0_1000 归一化坐标,裁切时须按源页图尺寸反归一化:`px = bbox/1000 * (W 或 H)`;从 `{case_dir}/images_path/raw-page-{page+1}.png` 裁出,存 `assets_dir/img_{id}.png`。
- `doc_id`/`document_type` 读自 case 的 `origin.json`。

- [ ] **Step 1: conftest 里构造 3 节点小树 fixture(1 虚拟父 + 2 文本子)与假 origin.json、纯色页图**
- [ ] **Step 2: 写失败测试**

```python
def test_load_material_flattens_in_reading_order(tiny_case_dir, cfg, tmp_path):
    m = load_material(tiny_case_dir, cfg, tmp_path / "assets")
    assert [b.id for b in m.blocks] == ["p0-b1", "p0-b2"]
    assert m.blocks[0].category == "paragraph_title"

def test_load_material_rejects_link_to(tiny_case_with_link, cfg, tmp_path):
    with pytest.raises(ValueError):
        load_material(tiny_case_with_link, cfg, tmp_path / "assets")

def test_image_block_cropped(tiny_case_with_image, cfg, tmp_path):
    m = load_material(tiny_case_with_image, cfg, tmp_path / "assets")
    img_blocks = [b for b in m.blocks if b.image_path]
    assert img_blocks and Path(img_blocks[0].image_path).exists()
```

- [ ] **Step 3: 确认失败 → 实现 → 通过**

### Task 3: html_builder — 带标记源 html

**Files:**
- Create: `src/synth/html_builder.py`、`tests/synth/test_html_builder.py`

**Interfaces:**
- Consumes: `Material`
- Produces: `build_source_html(material: Material, cfg: SynthConfig) -> str`

**要点(标记契约,rewrite/render/gt_builder 都依赖):**
- 每块一个元素:文本块 `<div class="block" data-node-id="{id}" data-category="{cat}" data-lang="zh">{转义文本}</div>`;图像块 `<img class="block" data-node-id="{id}" data-category="{cat}" data-lang="zh" src="{相对 assets 路径}">`。
- 按源页分组:`<section class="src-page" data-src-page="{page}">…</section>`(rewrite 按 section 分块调用 LLM)。
- 基础样式内联 `<style>`:各 category 的字号/字重(参考 spike 脚本),不含任何分栏/分页 CSS(那是 render 的职责)。

- [ ] **Step 1: 写失败测试**

```python
from bs4 import BeautifulSoup

def test_every_block_marked(material_fixture, cfg):
    soup = BeautifulSoup(build_source_html(material_fixture, cfg), "lxml")
    els = soup.select("[data-node-id]")
    assert {e["data-node-id"] for e in els} == {b.id for b in material_fixture.blocks}
    assert all(e["data-lang"] == "zh" for e in els)

def test_sections_by_source_page(material_fixture, cfg):
    soup = BeautifulSoup(build_source_html(material_fixture, cfg), "lxml")
    assert [s["data-src-page"] for s in soup.select("section.src-page")] == ["0"]
```

- [ ] **Step 2: 确认失败 → 实现 → 通过**

### Task 4: render — 确定性双栏分页 + 截图 + bbox 回读

**Files:**
- Create: `src/synth/render.py`、`src/synth/paginate.js`、`tests/synth/test_render.py`

**Interfaces:**
- Consumes: rewritten html 字符串(M1 阶段可直接喂纯中文源 html)
- Produces: `render_pages(html: str, out_images_dir: Path, cfg: SynthConfig) -> list[PlacedBlock]`;副作用:写 `out_images_dir/raw-page-{N}.png`

**paginate.js 算法(确定性,这是本计划对 spec 的关键细化——分栏分页几何不交给 LLM):**
1. 按 DOM 顺序收集全部 `[data-node-id]`,拆成 zh 列表与 en 列表(en 列表可为空,M1 即此情形;en 为空时中文占整页宽单栏排)。
2. 隐藏容器中以栏宽(`(width - 2*margin - column_gap)/2`)测量每块高度。
3. 生成固定尺寸页容器(`position:relative; width×height`),游标式填充:zh 依次进各页**左栏**(放不下即开新页左栏),en 依次进各页**右栏**;块内不跨页切割。页数 = 两栏所需页数取大。
4. 每块绝对定位落位,返回 `[{node_id, lang, category, page, x1, y1, x2, y2, text}]`(相对页容器左上角,即截图像素坐标)。
- Python 侧:Playwright 固定 `viewport={"width": cfg.page.width}`、`device_scale_factor=1`,`page.evaluate(paginate.js)` 拿落位表,再对每个页容器 `element_handle.screenshot()` 出 `raw-page-{N}.png`,并用 `getBoundingClientRect` 抽查落位表与 DOM 一致。

- [ ] **Step 1: 写失败测试(标记 `@pytest.mark.render`,CI 无浏览器可跳过)**

```python
def test_render_zh_only_single_column(cfg, tmp_path):
    html = make_test_html(n_blocks=8, lang="zh")   # 辅助函数:8 个高 150px 的标记块
    placed = render_pages(html, tmp_path, cfg)
    assert len(placed) == 8 and all(p.lang == "zh" for p in placed)
    assert (tmp_path / "raw-page-1.png").exists()
    for p in placed:  # bbox 在页内且非零
        assert 0 <= p.bbox[0] < p.bbox[2] <= cfg.page.width
        assert 0 <= p.bbox[1] < p.bbox[3] <= cfg.page.height

def test_render_bilingual_two_columns_overflow(cfg, tmp_path):
    # 3 个 zh 块(矮)+ 3 个 en 块(高到右栏必须溢出到第 2 页)
    html = make_bilingual_test_html()
    placed = render_pages(html, tmp_path, cfg)
    en_pages = {p.page for p in placed if p.lang == "en"}
    assert max(en_pages) == 1                      # 复现"英文段落3在第二页"
    zh = [p for p in placed if p.lang == "zh"]
    en = [p for p in placed if p.lang == "en" and p.page == 0]
    assert max(b.bbox[2] for b in zh) <= min(b.bbox[0] for b in en)  # 左右不重叠

def test_screenshot_pixel_matches_bbox(cfg, tmp_path):
    # 块背景涂红,按回读 bbox 裁截图,中心像素应为红色 —— 验证坐标与像素对齐
    ...
```

- [ ] **Step 2: `playwright install chromium` 后确认测试失败 → 实现 → 通过**

### Task 5: visualize + M1 端到端验收物

**Files:**
- Create: `src/synth/visualize.py`、`scripts/synth_m1_check.py`(一次性验收脚本可放 `src/synth/` 外)

**Interfaces:**
- Produces: `draw_overlays(images_dir: Path, placed: list[PlacedBlock], out_dir: Path) -> list[Path]`(每页一张叠框图:红框 zh、蓝框 en、角标 node_id)

- [ ] **Step 1: 实现 draw_overlays(Pillow),含最小单测(生成图存在、尺寸与页图一致)**
- [ ] **Step 2: `synth_m1_check.py`:material(真实 task_001_002_raw)→ html_builder → render(不接 LLM)→ draw_overlays,输出到 `复刻失效数据/合成数据/_m1_check/`**
- [ ] **Step 3: 【验收点 · 需求方】人工看叠框图:框与文字像素对齐、无零框 → M1 通过**

---

# 里程碑 M2:LLM 翻译改写 + 校验

### Task 6: rewrite — LLM 逐 section 翻译

**Files:**
- Create: `src/synth/rewrite.py`、`tests/synth/test_rewrite.py`

**Interfaces:**
- Consumes: 源 html(Task 3 产出)
- Produces: `rewrite_html(source_html: str, cfg: SynthConfig, client=None) -> str`(client 为 None 时按 env 建 OpenAI 客户端;测试注入 mock)

**要点:**
- 按 `<section class="src-page">` 逐段调 LLM。prompt 硬约束(沿用 spike 验证过的写法):对每个 `data-lang="zh"` 且 category ∈ `cfg.translate_categories` 的块,在其**紧后**插入同 `data-node-id`、`data-category`、`data-lang="en"` 的英文翻译块;不得修改/删除任何既有元素与属性,不得新增其他元素,只输出该 section 的 html。
- 后置校验(每 section):zh 集合不变、中文文本逐块未被篡改、应译块的 en 兄弟齐全且非空、无无标记文本节点;不过则重试 `cfg.llm.max_retries` 次,仍不过抛 `RewriteError`。

- [ ] **Step 1: 写失败测试(mock client 返回构造好的合法/非法响应)**

```python
def test_rewrite_inserts_en_sibling(source_html, cfg):
    out = rewrite_html(source_html, cfg, client=FakeLLM(valid=True))
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
```

- [ ] **Step 2: 确认失败 → 实现 → 通过**

### Task 7: validate — 结构不变性 + 英文节点核查

**Files:**
- Create: `src/synth/validate.py`、`tests/synth/test_validate.py`

**Interfaces:**
- Consumes: `Material.tree`(源树)、`list[PlacedBlock]`、`SynthConfig`
- Produces: `validate_doc(source_tree: list[dict], placed: list[PlacedBlock], cfg: SynthConfig) -> ValidationResult`

**校验规则(spec 第 5 节,gt 可靠性 = 与源结构对比):**
1. 结构不变性:placed 中 zh 块的 (id 集合、DOM 顺序、category、文本) 与源树先序展平结果完全一致——无缺失/新增/换序/篡改。
2. 英文节点核查:每个应译块恰有 1 个同 id 的 en 块;en 文本非空且英文占比达标(启发式:去空白后 ASCII 字母占比 > 0.5);不应译块(图像等)无 en 块。
3. bbox 合法:页内、面积 > 0;同页 zh 与 en 无水平重叠(x 区间不相交)。
4. stats 记录 `en_cross_page`(en 页码 > 对应 zh 页码的节点数)等指标。

- [ ] **Step 1: 写失败测试(构造合法 placed + 逐一注入 6 类破坏:缺 zh、多野块、换序、改文本、en 缺失、en 是中文,断言各自被检出且 error 信息含 node_id)**
- [ ] **Step 2: 确认失败 → 实现 → 通过**

### Task 8: M2 端到端验收物

**Files:**
- Create: `scripts/synth_m2_check.py`

- [ ] **Step 1: 串联 material → html_builder → rewrite(真实 LLM,读 env)→ render → validate → draw_overlays,对 task_001_002_raw 跑 3 次(不同 seed),输出校验报告 json + 叠框图**
- [ ] **Step 2: 【验收点 · 需求方】校验通过率(目标 ≥ 2/3)、叠框图确认双栏版面与配对正确 → M2 通过。需要:`OPENAI_API_KEY`/`OPENAI_BASE_URL`/`SYNTH_LLM_MODEL` 已配置**

---

# 里程碑 M3:PaddleOCR prelabel + gt 五件套 + 跑通第一步

### Task 9: ocr_prelabel — 真实 PaddleOCR

**Files:**
- Create: `src/synth/ocr_prelabel.py`、`tests/synth/test_ocr_prelabel.py`(标记 `@pytest.mark.paddle`)

**Interfaces:**
- Consumes: `raw-page-{N}.png` 列表
- Produces: `build_prelabel(image_paths: list[Path]) -> dict`(即 `prelabel.json` 内容)

**要点:**
- 用 `paddleocr` 的 PP-StructureV3 版面解析(检测+识别+区域分类)对整页图**真实推理**;严禁任何模拟路径。
- PaddleOCR 版面类别 → 本仓库类别映射表(模块级常量,可按需补充):`title→paragraph_title`, `doc_title→doc_title`, `text→text`, `figure/image→image`, `table→table`, `header→header`, `footer→footer`,未知类别落 `text` 并记 warning。
- 输出 schema 对齐真实 prelabel:`{"pages":[{"page_index", "blocks":[{block_id(页内 1 起), bbox(像素), text, category, score, source:"paddle_ocr"}]}]}`。

- [ ] **Step 1: `pip install paddlepaddle paddleocr`(CPU 版即可)**
- [ ] **Step 2: 冒烟测试:对 M1 产出的 1 张真实截图跑,断言 blocks 非空、schema 字段齐全、text 命中页面上已知词**
- [ ] **Step 3: 实现 → 通过**

### Task 10: gt_builder — 五件套落盘

**Files:**
- Create: `src/synth/gt_builder.py`、`tests/synth/test_gt_builder.py`

**Interfaces:**
- Consumes: `Material`(源树 + doc 元信息)、`list[PlacedBlock]`、`SynthConfig`
- Produces: `build_gt(material: Material, placed: list[PlacedBlock], out_dir: Path, cfg: SynthConfig, seq: int) -> None`;写出 `origin.json`、`label.json`、`multi-page-final.json`(`prelabel.json` 由 Task 9 另写;页面图由 render 已写)

**算法(spec 第 4 节):**
1. **id 重映射**:每合成页内按阅读顺序(左栏上→下,再右栏上→下,即先 zh 后 en、各按 y 排序)分配 `block_id` 1..n,得 `新id = p{page}-b{n}`;建 `(源id, lang) → 新id` 映射。
2. **label.json**:全部 placed 块 → `{block_id, bbox, category, page_index}`,`annotator_id: "synth"`。
3. **multi-page-final.json**:先序重建源树——真实节点:members = [各 zh 成员新 id] + [对应 en 成员新 id](原文在前译文在后),`page_index`/`bbox`/`category` 平行数组,`text` 全 `[""]`,节点 `id` = 首个 zh 成员新 id,`link:false, link_to:[]`;虚拟节点:`id = p{首个后代成员页}-fake{全局递增}`,`member:[] bbox:[] text:""`,category 保持字符串;顶层包 `{"doc":[...]}`。
4. **origin.json**:`doc_id = synth_bilingual_{seq:03d}_{源doc_id}`、`task_id = synth_task_{seq:03d}`、`pdf_path: ""`、`images_path` 指向 `out_dir/images_path`(render 输出移入)、`reading_direction: horizontal`、`document_type` 继承源。

- [ ] **Step 1: 写失败测试**

```python
def test_bilingual_node_member_order(material_fixture, placed_bilingual, cfg, tmp_path):
    build_gt(material_fixture, placed_bilingual, tmp_path, cfg, seq=1)
    tree = json.loads((tmp_path / "multi-page-final.json").read_text())["doc"]
    node = find_node(tree, category=["text", "text"])
    assert len(node["member"]) == 2
    zh_new, en_new = node["member"]
    assert node["id"] == zh_new
    assert node["text"] == ["", ""]

def test_cross_page_translation_makes_multipage_node(...):
    # en 落在下一页的节点:page_index == [0, 1]
    ...

def test_label_ids_match_tree_members(...):
    # label.json 全部 block_id 与树 member 一一对应,bbox 为像素且非零
    ...

def test_untranslated_image_node_single_member(...):
    ...
```

- [ ] **Step 2: 确认失败 → 实现 → 通过**

### Task 11: M3 端到端 + 跑通 pipeline.py 第一步

**Files:**
- Create: `scripts/synth_m3_check.py`

- [ ] **Step 1: 对 task_001_002_raw 全链路生成 2 份完整合成目录(五件套齐全)**
- [ ] **Step 2: 写临时 `annotation_path_list` 指向合成目录,跑 `python3 src/data/pipeline.py annotation_path_list=… output_path=复刻失效数据/合成数据/_m3_中间结果`(OCR 服务 `ocr.url` 需在线,不在线则记录哪些块 fillin 失败)**
- [ ] **Step 3: 【验收点 · 需求方】中间结果目录齐全,`multi-page-final-fillin.json` 中英文字补全正确、bbox 已归一化;5 个 prepared.jsonl 非空 → M3 通过**

---

# 里程碑 M4:批量 runner + 跑通全链路

### Task 12: runner — 批量编排与报告

**Files:**
- Create: `src/synth/runner.py`(CLI:`python -m src.synth.runner --config src/synth/config/synth.yaml`)、`tests/synth/test_runner.py`(mock 各模块,只测编排/丢弃/报告逻辑)

**Interfaces:**
- Consumes: 前述全部模块
- Produces: `{output_root}/synth_{seq:03d}_{源doc_id}/` × N、`{output_root}/synth_input_path.txt`(每行绝对路径)、`{output_root}/report.json`

**编排逻辑:**
- 遍历 `source_cases × copies_per_case`,每份换 seed(`cfg.seed + seq`,传给 LLM temperature 采样与素材页选取);
- validate 不过 → 删目录、换 seed 重试,同一 (case, copy) 连续 3 次失败则跳过并记入 report;
- report.json:成功/丢弃/跳过计数、每份 ValidationResult.stats 汇总(含 en_cross_page 份额)、耗时。

- [ ] **Step 1: 写失败测试(mock:validate 第一次 fail 第二次 ok → 断言重试且最终成功;连续 3 fail → 跳过且 report 记录)**
- [ ] **Step 2: 确认失败 → 实现 → 通过**
- [ ] **Step 3: 真实批量跑 10~20 份(LLM + PaddleOCR 真实调用)**
- [ ] **Step 4: 跑 `start_prepare_dataset.sh` 两步(参数指向合成清单),生成 train.jsonl**
- [ ] **Step 5: 【验收点 · 需求方】train.jsonl 中合成 doc 的样本数与任务分布(semantic/single_tree/deduplicate/merge 均有,双语跨页节点产出 merge 样本)、人工抽检叠框图与若干条样本 → M4 通过,项目一期完成**

---

## Self-Review 记录

- Spec 覆盖:第 2 节接口契约 → Task 9/10;第 3 节八模块 → Task 1-12(`config`=T1, `material`=T2, `html_builder`=T3, `render`=T4, `rewrite`=T6, `ocr_prelabel`=T9, `gt_builder`=T10, `validate`=T7, `runner`=T12, 可视化=T5);第 4 节 gt 规则 → Task 10;第 5 节校验 → Task 7;第 6 节里程碑 → Task 5/8/11/12 的验收点。无遗漏。
- 与 spec 的两处显式细化(非偏离):① 分栏/分页几何由 `paginate.js` 确定性完成,LLM 只做翻译+块级样式;② en 块以"紧邻 zh 块之后的兄弟"为插入约定,便于校验与回读。
- 类型一致性:`PlacedBlock`/`Material`/`SourceBlock`/`ValidationResult` 在"核心数据结构"节统一定义,各任务签名引用同一定义。
- 本仓库无 git,全部任务以测试通过为完成标志,无 commit 步骤。
