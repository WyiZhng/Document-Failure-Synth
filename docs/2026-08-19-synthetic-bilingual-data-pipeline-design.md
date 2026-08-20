# 双栏-双语失效数据合成 Pipeline 设计(一期)

日期:2026-08-19
状态:已与需求方对齐,待实现
架构方案:C(受约束生成——LLM 管内容与排版,结构标记贯穿始终)
可行性依据:`spikes/html_rewrite_retention/`(5 次 LLM 改写,标记保持率与中英配对率均 100%,判定 C_feasible;bbox 渲染回读未在 spike 中验证,列为里程碑 M1 首个验收点)

---

## 1. 背景与目标

现有训练数据链路为:标注数据(阶段 0)→ `src/data/pipeline.py` 生成中间结果 → `src/train/prepare_dataset` 生成 `train.jsonl`。模型在"双栏-双语"版面上存在失效。本项目合成该失效模式的训练数据。

**一期目标**:交付可复用的合成 pipeline,生成 10~20 份"双栏-双语"合成文档。每份输出与真实标注目录完全同构,追加到 `annotation_path_list_*.txt` 后,`start_prepare_dataset.sh` 两步零改造跑通。

**非目标(一期不做)**:

- 非失效元素随机组合(跨页分割页码、引用注入等)
- 渲染后处理增强(扫描噪声、模糊、透视)
- LLM 自动从 hard case 总结失效模式
- 除"双栏-双语"外的其他失效模式(仅留扩展点)
- 内部"版面转 html"服务接入(素材直接来自已有标注树,该服务将来作为 `material` 层增强)

## 2. 输出接口契约(与阶段 0 同构)

每份合成文档一个目录 `synth_{seq}_{source_doc_id}/`,包含:

| 文件 | 要求 |
|------|------|
| `origin.json` | `doc_id`(格式 `synth_bilingual_{seq}_{source_doc_id}`,全局唯一)、`task_id`、`pdf_path`(可为空串)、`images_path`(指向本目录页面图子目录)、`reading_direction: horizontal`、`document_type`(继承源文档) |
| `raw-page-{N}.png` | N 从 1 起;Playwright 截图,像素尺寸即 bbox 坐标空间 |
| `prelabel.json` | **必须来自真实运行的 PaddleOCR**(对合成页面图做全页版面检测+文字识别,PP-Structure 版面解析),严禁从 DOM/gt 派生或程序模拟;`{"pages":[{"page_index","blocks":[{block_id,bbox,text,category,score,source:"paddle_ocr"}]}]}`,页从 0 起 |
| `label.json` | `{"pages":[...], "annotator_id":"synth"}`;每块 `block_id`/`bbox`/`category`/`page_index`;bbox 为截图像素坐标 |
| `multi-page-final.json` | `{"doc":[树]}`;节点 schema 与真实标注一致(见第 4 节);`text` 留空占位(`[""]`),由现有 fillin 步骤补 |

坐标约定:全部使用截图像素坐标(浮点或整数均可),**不做归一化**——归一化到 qwen_0_1000 是现有 `reconstruct_tree` 的职责。

批量输出:合成根目录下附 `synth_input_path.txt`(每行一个合成目录绝对路径)与 `report.json`(生成/丢弃统计、校验指标)。

## 3. 架构与模块(新代码在 `src/synth/`,不改动 `src/data`、`src/train`)

```
config.yaml ─► runner ─► material ─► html_builder ─► rewrite(LLM) ─► render(Playwright)
                                                                        │
                       ocr_prelabel(PaddleOCR) ◄── raw-page-N.png ◄─────┤
                                │                                       │ DOM bbox 表
                                ▼                                       ▼
                          prelabel.json                            gt_builder ─► origin/label/multi-page-final
                                                                        │
                                                                    validate ─► 通过:落盘 / 不通过:丢弃重生成
```

| 模块 | 职责 | 输入 → 输出 |
|------|------|------------|
| `config` | 失效模式结构化描述:语言对(一期 zh→en)、双栏参数(左原文右译文)、生成份数、源文档列表、LLM 模型名与并发 | 人工编写 yaml |
| `material` | 从已有标注 case 读 fillin 树,抽取文本内容块;图像块从源页面图按 bbox 裁切复用 | fillin 树 + 页面图 → 素材包 |
| `html_builder` | 素材包 → 带标记源 html:每块 `data-node-id`(源块 id)、`data-category`、`data-lang="zh"`;图像块以 `<img>` 内嵌并同样带标记(spike 脚本 `html_from_tree.py` 思路转正) | 素材包 → source.html |
| `rewrite` | 调公网 LLM(OpenAI 兼容,密钥走环境变量):**仅做两件事**——为每个文本块生成英文翻译块(复制原 `data-node-id`,标 `data-lang="en"`)、将版面排为左栏原文右栏译文的双栏 CSS。prompt 硬约束:不得增删改任何 `data-node-id`,不得产生无标记文本块 | source.html → rewritten.html |
| `render` | Playwright/Chromium:固定视口宽度(CSS 1000px,A4 比例分页,`device_scale_factor=1`),CSS 分页后按页截图;`getBoundingClientRect` 回读每个标记块的页内 bbox。译文是否跨页由排版自然溢出决定,不强控 | rewritten.html → raw-page-N.png + 块级 bbox 表 |
| `ocr_prelabel` | 对每张截图**真实运行 PaddleOCR**(PP-Structure 版面解析:检测+识别+分类),组装 `prelabel.json`;优先本地安装 paddleocr 直接跑,若 `pipeline.yaml` 的 `/layout-parsing` 服务本身即 PaddleOCR 部署则可复用 | png → prelabel.json |
| `gt_builder` | bbox 表 + 源树层级 → `label.json` + `multi-page-final.json` + `origin.json`(映射规则见第 4 节,**独立可替换层**) | bbox 表 → 三件 gt 文件 |
| `validate` | 自动校验 + 叠框可视化(规则见第 5 节);不通过则整份丢弃、换随机种子重生成,连续 3 次失败记入 report 并跳过该源文档 | 全部产物 → 通过/丢弃 |
| `runner` | 批量编排(每份文档独立、可并发)、写 `synth_input_path.txt` 与 `report.json` | config → 合成根目录 |

## 4. gt 映射规则(草案,待与标注团队对齐;实现为独立层,规则变更只改此层重新生成)

**核心约定:原文与译文合为一个节点**(需求方提供的树形图):

```json
{
  "id": "p0-b5",
  "member": ["p0-b5", "p1-b2"],
  "page_index": [0, 1],
  "bbox": [[中文块框], [英文块框]],
  "category": ["text", "text"],
  "text": ["", ""],
  "is_virtual": false,
  "link": false,
  "link_to": []
}
```

- `member` 顺序固定:**原文在前、译文在后**(不按版面位置)。
- 节点 `id` 取中文成员 id;成员 id 由 `gt_builder` 按最终页面重新分配(`p{page}-b{n}`,页内递增)。html 阶段中英块共用源块 `data-node-id`、靠 `data-lang` 区分,到最终 id 的重映射在此层完成。
- 树的层级、兄弟顺序、虚拟节点完全继承源中文树;译文不改变任何父子关系。
- 译文块落在后续页时,该节点自然成为跨页节点(顺带产出 cross_page_merge 训练样本)。
- `label.json` 的 blocks = 所有中文成员 + 所有英文成员,各自带真实 bbox 与 category。
- 图像块(`image`/`chart`/`seal` 类)不翻译,单成员节点原样保留。

**连带影响,须知**:prelabel 来自真实 PaddleOCR,其框与 gt 框不保证 10px 内匹配;现有 fillin 步骤对未命中块会调 OCR 服务。因此**跑 `pipeline.py` 第一步时 OCR 服务必须在线**——与生成 prelabel 共用同一服务,不追求离线。

## 5. 校验规则(gt 可靠性 = 结构对比)

设计依据:输入的中文树结构是已知真值,改写只是"给节点补英文成员",故校验核心是结构不变性,而非从零验证。

**自动校验(每份,任一不过即丢弃):**

1. **结构不变性**:从 rewritten.html DOM 回读的树与源中文树同构——节点集合无缺失/新增、父子关系一致、兄弟顺序一致、各中文成员 `category` 不变、中文文本内容未被篡改(逐块比对)。
2. **英文节点核查**:每个应译节点 member 恰为 `[zh, en]` 两成员;英文文本非空且语言检测为英文;英文块 bbox 合法(非零、页内、与任何中文块无显著重叠)。
3. **bbox 合法性**:所有块框在页面范围内、面积大于 0、与截图像素空间一致。
4. **产物完整性**:五件套齐全、页面图与树引用的 `page_index` 覆盖一致、`label.json` 与 `multi-page-final.json` 的成员 id 一一对应。

**人工抽检**:每份输出叠框可视化图(gt 框 + 节点 id + zh/en 着色画在截图上),存于合成目录 `visualize/` 下。

## 6. 里程碑与验收(需求方只做规划验收,实现由子代理执行)

| 里程碑 | 内容 | 验收标准 |
|--------|------|----------|
| M1 | `html_builder` + `render` + bbox 回读,1 份文档(不接 LLM,直接渲染中文源 html) | 叠框图人工确认 bbox 与截图像素对齐、无零框(补上 spike 未验证环节) |
| M2 | 接入 `rewrite`(翻译+双栏)+ `validate`,3 份文档 | 自动校验通过率报告;叠框图确认双栏版面正确、结构对比逻辑有效 |
| M3 | `ocr_prelabel` + `gt_builder` 五件套,跑通 `pipeline.py` 第一步 | 中间结果目录生成,`multi-page-final-fillin.json` 文字补全正确(OCR 服务在线) |
| M4 | `runner` 批量 10~20 份,跑通第二步出 train.jsonl | 样本数与任务分布符合预期(semantic/single_tree/deduplicate/merge 均有产出),人工抽检 gt |

## 7. 测试策略

- 单元测试:`gt_builder`(id 重映射、跨页节点、member 顺序)、`validate`(结构对比对各类破坏的检出)、`html_builder`(标记完整性)。
- 端到端冒烟:1 份小文档全链路(可用 mock LLM 响应固定化)。
- LLM 调用与 PaddleOCR 均隔离在各自模块后,便于 mock。

## 8. 依赖与开放问题

| 项 | 状态 |
|----|------|
| gt 标注约定(原文译文同节点) | 草案已定,**待需求方与标注/算法团队正式对齐**;变更只影响 `gt_builder` |
| PaddleOCR 环境(生成 prelabel) | 必须真实运行 PaddleOCR(本地安装或其部署服务),不允许模拟 |
| OCR 服务(fillin 回退,`pipeline.yaml` 的 `ocr.url`) | 跑 `pipeline.py` 第一步时需在线 |
| 公网 LLM API(翻译+排版) | 密钥/模型名通过 config 与环境变量提供 |
| 源文档素材 | 一期使用 `复刻失效数据` 下已有 case;更多素材从服务器按需下载 |
| 内部"版面转 html"服务 | 一期不接,二期作为 `material` 增强 |
