# 失效数据生成管线独立拆分设计

日期: 2026-08-20  
状态: 待需求方审阅 spec  
范围: 把一期已落地的双栏-双语合成管线，从 `Document_analyst_Trainer` 整包搬到同级独立项目，并建立单独 git 仓

## 1. 目标

`Document_analyst_Trainer` 继续只负责训练数据链路（标注 → pipeline → jsonl → 训练）。失效数据生成（素材 → html → LLM 翻译 → 分页渲染 → 五件套标注）单独成仓，与 Trainer **同级、独立版本管理**，互不 import。

成功标准：

- 新仓不依赖 Trainer 的 Python 包、yaml、脚本即可合成标注五件套
- Trainer 中不再包含 synth 管线代码
- 新仓有独立 git 历史；Trainer 保持原状（它目前不是 git 仓，本工作不给 Trainer 建仓）

## 2. 已拍板决策

| 项 | 选择 |
|---|---|
| 与 Trainer 关系 | 整包搬走，Trainer 删除全部 synth 代码 |
| 数据 | 新仓自带一份源 case + 自己的输出目录 + 已跑通的 bilingual_v1 样例 |
| 落地形态 | 同级独立 Python 项目 + 当天 `git init` |
| 目录名 | `/home/wanyi/projects/document-failure-synth` |
| 产品输出 | 与阶段 0 同构的标注目录（origin / prelabel / label / multi-page-final / images_path），**不是** train.jsonl |
| 搬迁策略 | 先拷到新仓并验收，最后才删 Trainer 里的 synth，避免中途两头都不能跑 |

## 3. 非目标

- 不重写合成算法、不改五件套 schema、不加新的失效模式
- 不把 `src/data`、`src/train`、`scripts/run_data_pipeline.py`、`scripts/run_prepare_dataset.py` 搬进新仓
- 不删除 Trainer 的 `复刻失效数据/`（含历史 `bilingual_v1`、`_m4_*`）；那是 Trainer 侧留存，新仓另有拷贝
- 不拷贝 `.playwright-browsers`、`.playwright-libs`、`.pydeps`
- 不把 API key 写入新仓任何已跟踪文件
- 不把 Trainer 改成 git 仓，不建 submodule
- 不强制在拆分当天用 LLM 重跑 10 份合成（费时费钱）；以单测 + 配置切断 + 目录可运行为准
- 不迁 `spikes/html_rewrite_retention/`（一期 throwaway，Trainer 侧一并删除）

## 4. 新仓布局

```
/home/wanyi/projects/document-failure-synth/
  README.md
  requirements.txt
  .env.example
  .gitignore
  src/synth/                 # 现有模块原样迁入，入口不变
  tests/synth/
  scripts/synth_rerender.py  # 保留；m1/m2/m3 一次性验收脚本不迁
  docs/
    2026-08-19-synthetic-bilingual-data-pipeline-design.md
    2026-08-19-synthetic-bilingual-pipeline.md
    2026-08-20-extract-failure-synth-pipeline-design.md
  data/
    source/task_001_002_raw/     # 从 Trainer 中间结果拷一份 fillin 源 case
    output/                      # 默认合成输出根，gitignore
    examples/bilingual_v1/       # 已验收的 10 份五件套样例
```

CLI 保持：

```bash
python -m src.synth.runner --config src/synth/config/synth.yaml
```

默认 `synth.yaml`：

- `source_cases`: `data/source/task_001_002_raw`
- `output_root`: `data/output/bilingual_v1`

## 5. 依赖切断

当前 `src/synth` 不 import `src.data` / `src.train`。仅有两处路径耦合，必须改掉：

| 文件 | 现状 | 新仓行为 |
|---|---|---|
| `src/synth/runner.py` `ensure_runtime_env` | 回退读取 Trainer `src/inference/config/pipeline.yaml` 的 key/url/model | 只读环境变量；可选加载本仓 `.env`（若存在）。Playwright 路径只指向本仓 `.playwright-browsers` |
| `src/synth/ocr_prelabel.py` | 回退读取 Trainer `src/data/config/pipeline.yaml` 的 `ocr.url` | 只读 `PADDLE_OCR_API_URL`，或本仓 `synth.yaml` 新增的 `ocr.url`（无则报明确错误） |

`.env.example` 只列变量名：

```
OPENAI_API_KEY=
OPENAI_BASE_URL=
SYNTH_LLM_MODEL=
PADDLE_OCR_API_URL=
```

`scripts/synth_rerender.py` 随模块迁入，工作目录改为新仓根。不迁 `synth_m1_check.py` / `synth_m2_check.py` / `synth_m3_check.py`（绑定 Trainer 里的 `_m1/_m2/_m3_check` 产物）。

## 6. Git

- 在新仓根目录 `git init`
- `.gitignore` 至少包含：`.env`、`.playwright-browsers/`、`.playwright-libs/`、`.pydeps/`、`__pycache__/`、`*.pyc`、`data/output/`、`.pytest_cache/`
- 提交：代码、测试、文档、`requirements.txt`、`.env.example`、`data/source/`、`data/examples/bilingual_v1/`
- 不提交：密钥、浏览器二进制、合成跑批的 `data/output/`
- 第一次提交信息说明这是从 Trainer 拆出的失效数据生成管线；不把 Trainer 历史 squash 进来（Trainer 无 git）

## 7. 从 Trainer 拷贝的清单

拷贝：

- `src/synth/` → 新仓 `src/synth/`
- `tests/synth/` → 新仓 `tests/synth/`
- `scripts/synth_rerender.py` → 新仓 `scripts/`
- `requirements-synth.txt` → 新仓 `requirements.txt`
- `docs/superpowers/specs/2026-08-19-synthetic-bilingual-data-pipeline-design.md`
- `docs/superpowers/plans/2026-08-19-synthetic-bilingual-pipeline.md`
- 本 spec
- `复刻失效数据/中间结果/dataset/task_001_002_raw/` → `data/source/task_001_002_raw/`
- `复刻失效数据/合成数据/bilingual_v1/`（仅 `synth_*` 目录、`report.json`、`synth_input_path.txt`；不要 `_assets*`）→ `data/examples/bilingual_v1/`

不拷贝：`_m1_check`、`_m2_check`、`_m3_check`、`_m4_中间结果`、`_m4_prepared`、`_m4_artifacts`、Playwright 浏览器、`.pydeps`。

## 8. Trainer 删除清单（新仓验收通过之后）

删除：

- `src/synth/`
- `tests/synth/`
- `scripts/synth_m1_check.py`
- `scripts/synth_m2_check.py`
- `scripts/synth_m3_check.py`
- `scripts/synth_rerender.py`
- `requirements-synth.txt`
- `docs/superpowers/specs/2026-08-19-synthetic-bilingual-data-pipeline-design.md`
- `docs/superpowers/plans/2026-08-19-synthetic-bilingual-pipeline.md`
- `docs/superpowers/specs/2026-08-20-extract-failure-synth-pipeline-design.md`（原文迁到新仓 docs；Trainer 不再保留拆分 spec）
- `spikes/html_rewrite_retention/`

保留：`复刻失效数据/`、`src/data/`、`src/train/`、`scripts/run_data_pipeline.py`、`scripts/run_prepare_dataset.py`、其余训练/推理代码。

删除后 Trainer 内不应再有可运行的 synth 管线；`grep` 命中仅可能出现在与本拆分无关的历史数据路径名中。

## 9. 搬迁步骤

1. 创建 `/home/wanyi/projects/document-failure-synth`，按第 7 节拷贝。
2. 改新仓 yaml 路径；切断第 5 节两处 Trainer yaml 读取；补 `.env.example`、`.gitignore`、`README.md`（用法只写到五件套为止，不写 prepare_dataset）。
3. `git init`，确认 `git status` 看不到 `.env`，再做第一次提交。
4. 在新仓跑 `pytest tests/synth -m "not render and not paddle"`；有浏览器再跑 `@pytest.mark.render`。
5. 冒烟：无 key 时 runner 应因环境变量失败，而不是 `FileNotFoundError` 去打开 Trainer 的 yaml。
6. 第 4–5 步通过后，按第 8 节删除 Trainer 中的 synth 文件。

## 10. 验收

- 新仓单测通过（至少非 live 标记）
- 新仓 `src/synth` 文本中不再出现 `Document_analyst_Trainer`、`src/inference/config`、`src/data/config/pipeline.yaml` 作为读取路径
- 缺密钥时错误信息指向环境变量
- Trainer 不再有 `src/synth/` 目录
- 新仓 `git log` 至少一条初始提交；`.env` 未被跟踪

## 11. 风险与回退

- 源 case 含页面图，目录会偏大：接受，不引入 git-lfs。
- Playwright 需在新仓按 README 重装。
- 若新仓验收失败：不执行第 8 节删除，Trainer 仍可运行原管线。
- 若删除 Trainer 代码后发现漏拷：从本 spec 第 7 节清单补拷；Trainer 侧数据目录仍在，源 case 可再拷一次。
