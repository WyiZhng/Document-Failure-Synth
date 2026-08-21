# 管线讲解页设计

日期: 2026-08-20
状态: 待需求方审阅
范围: 给不了解本项目的人看的静态讲解页（输入 → 处理 → 输出）

---

## 1. 目标

让没读过代码的人，打开一个 HTML 文件就能回答三件事：

1. 这个项目干什么：从已有中文标注树，合成「左栏中文 / 右栏英文」双栏文档。
2. 做了什么：一份真实样例从源标注变成五件套。
3. 怎么实现的：每步对应哪个模块，外加约 10 行关键代码。

讲解只走 **一份文档**：源 case `data/source/task_001_002_raw/`（山东工信厅政策文件 `doc_21f4dc879ffd`）→ 样例 `data/examples/bilingual_v1/synth_001_doc_21f4dc879ffd/`。

## 2. 非目标

- 不跑管线、不调 LLM、不调 OCR、不起本地服务器。
- 不浏览 10 份合成结果。
- 不展示合成后的 `raw-page-N.png`（样例目录里没有；输出用 JSON 摘录 + 几何示意图）。
- 不把完整 fillin 树（约 1400 行）塞进页面。
- 不做 Cursor Canvas、不做深色主题、不做英文版。
- 不解释 Playwright 启动、`.env`、重试计数等运行细节。

## 3. 交付形态

自包含目录 `explain/`，用浏览器直接打开 `explain/index.html`（`file://` 必须可用）。

```
explain/
  index.html
  styles.css
  data.js
  assets/raw-page-1.png
```

约束（不可破）：

- 无构建、无 React、无 npm、无 CDN。
- 不用 `fetch`（`file://` 会失败）。
- 不用 ES module（部分浏览器在 `file://` 下拦截）。
- `data.js` 必须是 `var EXPLAIN = { ... };`，由经典脚本标签加载。
- 图片用相对路径 `assets/raw-page-1.png`，从源目录 **拷贝**，不符号链接、不指回 `data/`。源文件：`data/source/task_001_002_raw/images_path/raw-page-1.png`（约 465KB）。

`README.md` 在「运行」节后加一行：讲解页打开 `explain/index.html`。

## 4. 页面信息架构

单栏纵向滚动，内容最大宽度 880px，浅色背景（投屏）。顶栏锚点：`输入` `1` `2` `3` `4` `5` `6` `输出`。点了滚到对应 `id`，不做别的交互。

顺序固定：

1. **开头**：项目一句话 + 本页看哪份样例。
2. **输入**：源页图 + origin 字段 + 三个 fillin 节点。
3. **处理 1–6**：素材 → 源 HTML → 插英文 → 分页 → 校验 → 写标注。
4. **输出**：五件套卡片。

## 5. 各段内容

### 5.1 开头

标题：`document-failure-synth 在做什么`

正文必须包含：

- 从已有中文 fillin 树合成左栏中文、右栏英文的双栏文档。
- 产出与真实标注目录同构的五件套：`origin` / `prelabel` / `label` / `multi-page-final` / `images_path`。
- 用途：给下游 Trainer 提供双栏双语失效样本；本仓止于标注产物。
- 样例：源 `doc_21f4dc879ffd`（`policy_document`）→ `synth_001`，seed 52。

### 5.2 输入

左：`assets/raw-page-1.png`（源第 1 页，含红章）。`img` 的 `alt` 为「源文档第 1 页」。图加载失败时，旁边仍显示路径说明，不白屏。

右：摘录，不要全文。

`origin.json` 只展示：

```json
{
  "doc_id": "doc_21f4dc879ffd",
  "document_type": "policy_document",
  "reading_direction": "horizontal"
}
```

fillin 三个节点（可再压缩字段，但 id / category / text / link_to 必须在）：

| id | category | text |
|----|----------|------|
| `p0-b2` | `doc_title` | 山东省工业和信息化厅文件 |
| `p0-b3` | `text` | 鲁工信消〔2021〕77号 |
| `p0-b8` | `seal` | `""`（印章，无字） |

脚注固定三句：

- 源树共 8 页；配置 `max_source_pages: 4`，只用页 0–3。
- 任何非空 `link_to` 的 case 直接拒绝。
- `image` / `chart` / `seal` / `table` 要从源页图按 bbox 裁切；缺页图会在这一步失败。

### 5.3 处理步骤（统一模板）

每步一块，结构相同：

1. 序号 + 短标题
2. 2–4 句白话（外人能懂）
3. 「本步输入」切片
4. 「本步输出」切片
5. 模块路径（等宽）
6. 约 10 行关键代码（校验步最多 15 行）。从下列指定片段拷贝，可微裁但不得改语义。

步骤之间用短箭头文案连接，例如「下一块把这些块变成带标记的 HTML」。

#### 步骤 1 · 加载素材

- 白话：读 fillin 树和 origin；展平成块；图章印表裁成小图。
- 输入：上面三个节点。
- 输出：示意 `SourceBlock` 列表，至少含：
  - `{ id: "p0-b3", page: 0, category: "text", text: "鲁工信消〔2021〕77号", image_path: null }`
  - `{ id: "p0-b8", page: 0, category: "seal", text: "", image_path: ".../img_p0-b8.png" }`
- 模块：`src/synth/material.py` · `load_material`
- 代码：`load_material` 中读树、拒 `link_to`、按 `max_source_pages` 截断、对 `IMAGE_CATEGORIES` 裁图、组装 `SourceBlock` 那段（约 141–171 行）。

#### 步骤 2 · 拼源 HTML

- 白话：还不管双栏。每个块打上 `data-node-id` / `data-category` / `data-lang="zh"`，方便后面校对「一个都没丢」。
- 输入：块列表。
- 输出：仅中文的 HTML 两行（文号 + 印章），例如：

```html
<div class="block" data-category="text" data-lang="zh" data-node-id="p0-b3">鲁工信消〔2021〕77号</div>
<img class="block" data-category="seal" data-lang="zh" data-node-id="p0-b8" src="file://..." alt="" />
```

- 模块：`src/synth/html_builder.py` · `build_source_html`
- 代码：按页写 `<section class="src-page">` 以及 div/img 打标那段（约 44–61 行）。

#### 步骤 3 · LLM 插英文

- 白话：这是整条链路里 **唯一** 的模型调用。每个源页 section 单独翻译。中文节点不许改；应译类后面插一个 **相同 `data-node-id`** 的英文兄弟。图章表格页眉页脚不译。
- 输入：步骤 2 的中文 HTML。
- 输出：用 `synth_001` 真实译文：

```html
<div class="block" data-category="text" data-lang="zh" data-node-id="p0-b3">鲁工信消〔2021〕77号</div>
<div class="block" data-category="text" data-lang="en" data-node-id="p0-b3">Lu Gongxin Xiao [2021] No. 77</div>
<img class="block" data-category="seal" data-lang="zh" data-node-id="p0-b8" ...>
```

明确写：印章没有 en。中间物会写成该份目录下的 `rewritten.html`。
- 模块：`src/synth/rewrite.py` · `rewrite_html`
- 代码：`REWRITE_PROMPT` 的 HARD RULES 1–4（文件第 13–19 行附近）。不要贴整段 prompt 后的 input 说明。

#### 步骤 4 · 分页截图

- 白话：几何不交给模型。Playwright 打开 HTML，注入 `paginate.js`：左栏中文、右栏英文；同一 `data-node-id` 的 zh/en **必须同页**，任一栏放不下就整对翻页。截图像素空间就是后面 bbox。
- 输入：成对 HTML。
- 输出：没有合成 PNG。用 CSS 画一张示意图：画布 1000×约 360px；左栏 x=40–488 标「中文」；右栏 x=512–960 标「英文」；中间 24px 为 `column_gap`。旁注栏宽公式 `(1000 − 2×40 − 24) / 2 = 448`。
- 模块：`src/synth/paginate.js` · `paginateDocument`（由 `render.render_pages` 注入）
- 代码：成对翻页那段（`zhNeedsPage || enNeedsPage` 则 `newPage`，约 91–96 行）。

#### 步骤 5 · 校验

- 白话：中文必须还是源树（id / 顺序 / 类别 / 原文）；该译的恰好一个英文；同 id 的 zh/en 同页且 x 不重叠。不过就整份丢掉重试。
- 输入：源树 + 排版后的 `PlacedBlock`。
- 输出：本份真实统计（来自 `data/examples/bilingual_v1/report.json` 的 seq=1）：`n_zh=22`，`n_en=21`，`en_cross_page=0`，`n_errors=0`。解释：少 1 个 en 是因为印章不译。
- 模块：`src/synth/validate.py` · `validate_doc`
- 代码：应译必须有 en、以及 zh/en 不得跨页那两段（约 97–109 行与 119–127 行，可拼成一块展示，总行数仍控制在约 15 行以内）。

#### 步骤 6 · 写标注

- 白话：按阅读序重编号——页内先左栏中文、再右栏英文。双语树节点 `member=[中文新id, 英文新id]`，`text` 全空。然后对截图跑 PaddleOCR 得到 `prelabel`（探测器，不与 GT 对齐）。
- 输入：`PlacedBlock`。
- 输出：对照表（必须做成两列表格，不要只写在正文里）：

| 源 id | 角色 | 新 id | bbox x |
|-------|------|-------|--------|
| `p0-b3` | 中文文号 | `p0-b2` | 40–488 |
| `p0-b3` | 英文文号 | `p0-b16` | 512–960 |

数字来自 `synth_001` 的 `multi-page-final.json` 中 id 为 `p0-b2` 的节点（`member: ["p0-b2","p0-b16"]`）。
- 模块：`src/synth/gt_builder.py` · `_assign_ids`；OCR 为 `src/synth/ocr_prelabel.py` · `build_prelabel`
- 代码：`_assign_ids` 里按页先 zh 后 en 赋 `p{page}-b{n}`（约 16–32 行）。OCR 只在白话里提，不单独成步、不另贴代码。

### 5.4 输出

五张卡片，每张：文件名、一句话职责、约 15 行真实 JSON。数据来自 `data/examples/bilingual_v1/synth_001_doc_21f4dc879ffd/`。

| 文件 | 一句话 | 摘录要求 |
|------|--------|----------|
| `origin.json` | 合成文档身份 | `doc_id=synth_bilingual_001_doc_21f4dc879ffd`，`task_id=synth_task_001`，`document_type=policy_document`。`images_path` 可保留样例里的绝对路径，并加注「真实跑通后指向本份 `images_path/`」。 |
| `label.json` | 页内块框（无字） | 第一页前两个 block 的 `block_id` / `bbox` / `category`。 |
| `multi-page-final.json` | 双语树 | 文号节点：`member`、两个 bbox、`text: ["",""]`。 |
| `prelabel.json` | OCR 预标注（有字） | 第一个 block：含 `text: "山东省工业和信息化厅文件"`、`source: "paddle_ocr"`。 |
| `images_path/` | 截图像素空间 | 无 PNG。卡片正文写：真实跑通后为 `raw-page-N.png`（N 从 1），bbox 与图同一坐标系。本讲解页用步骤 4 示意图代替。 |

`rewritten.html` 不单开卡片，在步骤 3 输出区写「落盘为 `rewritten.html`」。

五件套定义就是上表前五项；`visualize/` 与 `ocr_url.txt` 不展示。

## 6. `data.js` 形状

`EXPLAIN` 只放页面要渲染的字符串和对象，页面不读仓库其它文件。字段固定为：

```js
var EXPLAIN = {
  originInput: { /* 5.2 的 origin 三字段 */ },
  sourceNodes: [ /* p0-b2, p0-b3, p0-b8 */ ],
  sourceBlocks: [ /* 步骤 1 两个 SourceBlock */ ],
  htmlZh: "/* 步骤 2 两行 HTML */",
  htmlPair: "/* 步骤 3 三行 HTML */",
  stats: { n_zh: 22, n_en: 21, en_cross_page: 0, n_errors: 0 },
  idMap: [ /* 步骤 6 两行对照 */ ],
  artifacts: {
    origin: { /* synth_001 origin 关键字段 */ },
    label: { /* 前两块 */ },
    treeNode: { /* 文号双语节点 */ },
    prelabel: { /* OCR 第一块 */ }
  }
};
```

`index.html` 用 `document.getElementById` + `textContent` 填 `<pre>`；HTML 切片用 `textContent` 显示源码，不要 `innerHTML` 进文档（避免执行未知标记）。示意图用静态 HTML/CSS，不进 `EXPLAIN`。

代码片段可以直接写在 `index.html` 的 `<pre><code>` 里，不必放进 `EXPLAIN`。

## 7. 视觉

- 浅色、扁平、无渐变、无阴影、无装饰性表情符号。
- 步骤序号用圆形数字，不要卡片墙（开头、输入、输出可以和步骤块有区分：输入左右分栏，步骤为编号条，输出为五张文件卡片）。
- 代码块：等宽、浅底、可横向滚动；字号小于正文。
- 源页图：最大宽度 100%，高度自适应。
- 双栏示意图：明确标出 40 / 488 / 512 / 960，让人能对上 JSON 里的 bbox。

## 8. 错误处理

静态页无运行时后端。仅两类失败：

- `data.js` 未加载：正文顶部显示「请打开整个 `explain/` 目录下的 `index.html`，不要只拷走这一个文件」。
- 图片 404：保留 alt 与「源文件拷贝自 `data/source/task_001_002_raw/images_path/raw-page-1.png`」。

## 9. 测试与验收

无自动化测试。实现者用浏览器以 `file://` 打开 `explain/index.html`，下列全部成立才算完成：

- 不联网、不起服务能看完整页。
- 源第 1 页图可见。
- 锚点能跳到输入、六步、输出。
- 步骤 3 看得到真实译文 `Lu Gongxin Xiao [2021] No. 77`。
- 步骤 5 数字为 22 / 21 / 0。
- 步骤 6 对照表源 id `p0-b3` → `p0-b2` / `p0-b16`。
- 输出五张卡片都有 JSON 摘录；`images_path` 卡片说明本页无合成截图。
- `README.md` 有讲解页入口。

## 10. 实现时改动的文件

| 路径 | 动作 |
|------|------|
| `explain/index.html` | 新建 |
| `explain/styles.css` | 新建 |
| `explain/data.js` | 新建 |
| `explain/assets/raw-page-1.png` | 从源 images_path 拷贝 |
| `README.md` | 加一行入口 |

不改 `src/synth/`、不改样例 JSON。
