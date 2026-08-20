# 失效数据生成管线独立拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把一期双语失效数据合成管线整包搬到与 Trainer 同级的独立 git 仓 `document-failure-synth`，切断对 Trainer yaml 的读取，然后从 Trainer 删除 synth 代码。

**Architecture:** 先拷代码与数据到新目录并改成本仓配置，用单测锁住「不再读 Trainer yaml」；新仓验收通过后再删 Trainer 里的 synth。产品输出仍是标注五件套，不搬 `src/data` / `src/train`。

**Tech Stack:** Python 3.10+、pytest、PyYAML、Playwright、OpenAI 兼容 API、PaddleOCR HTTP、git。

**Spec:** `docs/superpowers/specs/2026-08-20-extract-failure-synth-pipeline-design.md`

## Global Constraints

- `TRAINER_ROOT=/home/wanyi/projects/Document_analyst_Trainer`
- `NEW_ROOT=/home/wanyi/projects/document-failure-synth`
- 新仓产品是标注五件套，不搬 pipeline/prepare_dataset，README 不写 train.jsonl
- 不把 API key 写入任何被 git 跟踪的文件；`.env` 必须在 `.gitignore`
- Trainer **不是 git 仓**：对 Trainer 的删除不要 `git commit`；只在 `NEW_ROOT` 做 git
- 不拷贝 `.playwright-browsers`、`.playwright-libs`、`.pydeps`、`_m1/_m2/_m3_check`、`_m4_中间结果`、`_m4_prepared`
- 子代理必须使用 `model: cursor-grok-4.6-high-fast`，禁止 `inherit` / Claude
- 先拷后验再删：Task 4 通过前禁止删除 `TRAINER_ROOT/src/synth`

## 文件结构

新仓：

```
document-failure-synth/
  README.md
  requirements.txt
  .env.example
  .gitignore
  pytest.ini
  src/synth/
  tests/synth/
  scripts/synth_rerender.py
  docs/
  data/source/task_001_002_raw/
  data/output/
  data/examples/bilingual_v1/
```

Trainer 删除：`src/synth/`、`tests/synth/`、`scripts/synth_*.py`、`requirements-synth.txt`、synth 相关 docs、`spikes/html_rewrite_retention/`。

---

### Task 1: 创建新仓骨架并拷入代码

**Files:**
- Create: `/home/wanyi/projects/document-failure-synth/`
- Copy: `TRAINER_ROOT/src/synth/` → `NEW_ROOT/src/synth/`
- Copy: `TRAINER_ROOT/tests/synth/` → `NEW_ROOT/tests/synth/`
- Copy: `TRAINER_ROOT/scripts/synth_rerender.py` → `NEW_ROOT/scripts/synth_rerender.py`
- Copy: `TRAINER_ROOT/requirements-synth.txt` → `NEW_ROOT/requirements.txt`
- Copy: bilingual spec/plan + 拆分 spec → `NEW_ROOT/docs/`
- Create: `NEW_ROOT/.gitignore`、`NEW_ROOT/.env.example`、`NEW_ROOT/pytest.ini`、`NEW_ROOT/README.md`、`NEW_ROOT/src/__init__.py`

**Interfaces:**
- Consumes: Trainer 现有 synth 树
- Produces: 新目录中有完整 `src/synth` 与 `tests/synth`

- [ ] **Step 1: 建目录并拷代码**

```bash
TRAINER_ROOT=/home/wanyi/projects/Document_analyst_Trainer
NEW_ROOT=/home/wanyi/projects/document-failure-synth
mkdir -p "$NEW_ROOT/scripts" "$NEW_ROOT/docs" "$NEW_ROOT/data/source" "$NEW_ROOT/data/output" "$NEW_ROOT/data/examples"
cp -a "$TRAINER_ROOT/src/synth" "$NEW_ROOT/src/"
touch "$NEW_ROOT/src/__init__.py"
cp -a "$TRAINER_ROOT/tests/synth" "$NEW_ROOT/tests/"
touch "$NEW_ROOT/tests/__init__.py"
cp "$TRAINER_ROOT/scripts/synth_rerender.py" "$NEW_ROOT/scripts/"
cp "$TRAINER_ROOT/requirements-synth.txt" "$NEW_ROOT/requirements.txt"
cp "$TRAINER_ROOT/docs/superpowers/specs/2026-08-19-synthetic-bilingual-data-pipeline-design.md" "$NEW_ROOT/docs/"
cp "$TRAINER_ROOT/docs/superpowers/plans/2026-08-19-synthetic-bilingual-pipeline.md" "$NEW_ROOT/docs/"
cp "$TRAINER_ROOT/docs/superpowers/specs/2026-08-20-extract-failure-synth-pipeline-design.md" "$NEW_ROOT/docs/"
```

- [ ] **Step 2: 写 `.gitignore`**

`NEW_ROOT/.gitignore` 全文：

```
.env
.playwright-browsers/
.playwright-libs/
.pydeps/
__pycache__/
*.pyc
.pytest_cache/
data/output/
```

- [ ] **Step 3: 写 `.env.example`**

全文（无真实 key）：

```
OPENAI_API_KEY=
OPENAI_BASE_URL=
SYNTH_LLM_MODEL=
PADDLE_OCR_API_URL=
```

- [ ] **Step 4: 写 `pytest.ini`**

```ini
[pytest]
pythonpath = .
testpaths = tests
markers =
    render: Playwright render tests (requires chromium)
    paddle: live PaddleOCR layout-parsing service tests
```

- [ ] **Step 5: 写 `README.md`**

必须包含：一句话说明本项目生成双栏-双语**标注五件套**；`pip install -r requirements.txt`；`playwright install chromium`；复制 `.env.example` 为 `.env`；入口：

```bash
python3 -m src.synth.runner --config src/synth/config/synth.yaml
```

输出在 `data/output/`。不要写 `prepare_dataset` 或 `train.jsonl`。

- [ ] **Step 6: 确认骨架存在**

```bash
test -f /home/wanyi/projects/document-failure-synth/src/synth/runner.py
test -f /home/wanyi/projects/document-failure-synth/tests/synth/test_runner.py
test -f /home/wanyi/projects/document-failure-synth/.gitignore
```

Expected: 三个命令都 exit 0。本任务不 commit。

---

### Task 2: 切断 LLM / Playwright 对 Trainer yaml 的读取

**Files:**
- Modify: `NEW_ROOT/src/synth/runner.py`
- Test: `NEW_ROOT/tests/synth/test_runner.py`

**Interfaces:**
- Consumes: `ensure_runtime_env(workspace: Path) -> None`
- Produces: 同签名；只设置 Playwright 路径，并从 `workspace/.env` `setdefault` 未设置的变量；不读取 `src/inference/config/pipeline.yaml`

- [ ] **Step 1: 写失败测试**

在 `NEW_ROOT/tests/synth/test_runner.py` 顶部补 `import os`，追加：

```python
def test_ensure_runtime_env_does_not_read_trainer_inference_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("SYNTH_LLM_MODEL", raising=False)
    fake = tmp_path / "src/inference/config/pipeline.yaml"
    fake.parent.mkdir(parents=True)
    fake.write_text(
        "api_config:\n  key: sk-SHOULD-NOT-LOAD\n  url: http://trainer.example\n  model: trainer-model\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    runner.ensure_runtime_env(tmp_path)
    assert os.environ.get("OPENAI_API_KEY") != "sk-SHOULD-NOT-LOAD"
    assert os.environ.get("SYNTH_LLM_MODEL") != "trainer-model"


def test_ensure_runtime_env_loads_local_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SYNTH_LLM_MODEL", raising=False)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=sk-from-dotenv\nSYNTH_LLM_MODEL=local-model\n",
        encoding="utf-8",
    )
    runner.ensure_runtime_env(tmp_path)
    assert os.environ.get("OPENAI_API_KEY") == "sk-from-dotenv"
    assert os.environ.get("SYNTH_LLM_MODEL") == "local-model"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /home/wanyi/projects/document-failure-synth
python3 -m pytest tests/synth/test_runner.py::test_ensure_runtime_env_does_not_read_trainer_inference_yaml tests/synth/test_runner.py::test_ensure_runtime_env_loads_local_dotenv -v
```

Expected: FAIL。

- [ ] **Step 3: 改 `ensure_runtime_env`**

删除 `_INFERENCE_YAML` 及读取逻辑。若 `yaml` 不再使用则删除 `import yaml`。加入：

```python
def _load_dotenv(workspace: Path) -> None:
    path = Path(workspace) / ".env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ.setdefault(key, value)


def ensure_runtime_env(workspace: Path) -> None:
    workspace = Path(workspace)
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", str(workspace / ".playwright-browsers")
    )
    libs = workspace / ".playwright-libs/usr/lib/x86_64-linux-gnu"
    if libs.is_dir():
        current = os.environ.get("LD_LIBRARY_PATH", "")
        prefix = str(libs)
        if prefix not in current.split(":"):
            os.environ["LD_LIBRARY_PATH"] = (
                f"{prefix}:{current}" if current else prefix
            )
    _load_dotenv(workspace)
```

- [ ] **Step 4: 再跑测试**

```bash
cd /home/wanyi/projects/document-failure-synth
python3 -m pytest tests/synth/test_runner.py -q
```

Expected: PASS。

---

### Task 3: 切断 OCR 对 Trainer `pipeline.yaml` 的读取

**Files:**
- Modify: `NEW_ROOT/src/synth/config.py`
- Modify: `NEW_ROOT/src/synth/config/synth.yaml`
- Modify: `NEW_ROOT/src/synth/ocr_prelabel.py`
- Modify: `NEW_ROOT/src/synth/runner.py` 的 `_ocr_candidates` / `materialize_document`
- Modify: `NEW_ROOT/tests/synth/test_ocr_prelabel.py`
- Modify: `NEW_ROOT/tests/synth/test_config.py`
- Modify: `NEW_ROOT/tests/synth/test_runner.py` 的 `_cfg`（补 `ocr` 字段）

**Interfaces:**
- Consumes: 现有 `load_ocr_settings() -> OcrSettings`
- Produces:

```python
@dataclass
class OcrConfig:
    url: str
    timeout: int

# SynthConfig 增加字段 ocr: OcrConfig

def load_ocr_settings(cfg: SynthConfig | None = None) -> OcrSettings:
    ...
```

环境变量 `PADDLE_OCR_API_URL` 优先，否则 `cfg.ocr.url`；都空则 `ValueError`，消息含 `PADDLE_OCR_API_URL`。

- [ ] **Step 1: 写失败测试**

`tests/synth/test_config.py`：

```python
from pathlib import Path
from src.synth.config import load_config

def test_load_default_config():
    cfg = load_config(Path("src/synth/config/synth.yaml"))
    assert cfg.page.width == 1000
    assert cfg.copies_per_case == 10
    assert "text" in cfg.translate_categories
    assert cfg.source_cases == ["data/source/task_001_002_raw"]
    assert cfg.output_root == "data/output/bilingual_v1"
    assert cfg.ocr.timeout == 300
```

`tests/synth/test_ocr_prelabel.py` 保留 `test_load_ocr_settings_prefers_env`，追加：

```python
def test_load_ocr_settings_uses_yaml_when_env_missing(monkeypatch):
    monkeypatch.delenv("PADDLE_OCR_API_URL", raising=False)
    from src.synth.config import LlmConfig, OcrConfig, PageConfig, SynthConfig
    from src.synth.ocr_prelabel import load_ocr_settings
    cfg = SynthConfig(
        source_cases=[],
        copies_per_case=1,
        max_source_pages=4,
        seed=1,
        output_root="x",
        translate_categories=["text"],
        page=PageConfig(1000, 1414, 40, 24),
        llm=LlmConfig("SYNTH_LLM_MODEL", "OPENAI_BASE_URL", "OPENAI_API_KEY", 0.7, 1),
        ocr=OcrConfig(url="http://yaml.example:8/", timeout=12),
    )
    settings = load_ocr_settings(cfg)
    assert settings.url == "http://yaml.example:8"
    assert settings.timeout == 12


def test_load_ocr_settings_errors_when_unconfigured(monkeypatch):
    monkeypatch.delenv("PADDLE_OCR_API_URL", raising=False)
    from src.synth.ocr_prelabel import load_ocr_settings
    with pytest.raises(ValueError, match="PADDLE_OCR_API_URL"):
        load_ocr_settings(None)
```

删除对 `src/data/config/pipeline.yaml` 的读取。`LIVE_PAGE` 改为：

```python
LIVE_PAGE = Path("data/examples/bilingual_v1/synth_001_doc_21f4dc879ffd/images_path/raw-page-1.png")
```

`@pytest.mark.paddle` 测试只使用 `PADDLE_OCR_API_URL`；未设置则 `pytest.skip`。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /home/wanyi/projects/document-failure-synth
python3 -m pytest tests/synth/test_config.py tests/synth/test_ocr_prelabel.py -q -m "not paddle"
```

Expected: FAIL。

- [ ] **Step 3: 改 config 与 yaml**

`config.py` 增加 `OcrConfig`，`SynthConfig` 增加 `ocr: OcrConfig`。`load_config`：

```python
ocr_raw = raw.get("ocr") or {}
# ...
ocr=OcrConfig(
    url=str(ocr_raw.get("url") or "").strip(),
    timeout=int(ocr_raw.get("timeout") or 300),
),
```

`synth.yaml` 的 `source_cases` 改为 `data/source/task_001_002_raw`，`output_root` 改为 `data/output/bilingual_v1`，并增加：

```yaml
ocr:
  url: ""
  timeout: 300
```

`ocr.url` 留空，由 `.env` 的 `PADDLE_OCR_API_URL` 提供，避免把内网 IP 写进 git。

- [ ] **Step 4: 改 `load_ocr_settings`**

删除 `_PIPELINE_YAML`、`_load_pipeline_ocr`。实现：

```python
def load_ocr_settings(cfg: SynthConfig | None = None) -> OcrSettings:
    env_url = os.environ.get("PADDLE_OCR_API_URL", "").strip()
    yaml_url = ""
    timeout = 300
    if cfg is not None:
        yaml_url = str(cfg.ocr.url or "").strip()
        timeout = int(cfg.ocr.timeout)
    url = env_url or yaml_url
    if not url:
        raise ValueError(
            "OCR url not configured: set PADDLE_OCR_API_URL or ocr.url in synth.yaml"
        )
    return OcrSettings(url=url.rstrip("/"), timeout=timeout)
```

需要 `from src.synth.config import SynthConfig`（可用 `from typing import TYPE_CHECKING` 避免环引用；`ocr_prelabel` 当前不 import config，直接 import 即可）。

- [ ] **Step 5: 改 runner 候选 URL**

```python
def _ocr_candidates(cfg: SynthConfig) -> list[OcrSettings]:
    urls: list[str] = []
    env_url = os.environ.get("PADDLE_OCR_API_URL", "").strip().rstrip("/")
    if env_url:
        urls.append(env_url)
    yaml_url = str(cfg.ocr.url or "").strip().rstrip("/")
    if yaml_url:
        urls.append(yaml_url)
    seen: set[str] = set()
    out: list[OcrSettings] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            out.append(OcrSettings(url=url, timeout=cfg.ocr.timeout))
    if not out:
        raise RuntimeError(
            "OCR url not configured: set PADDLE_OCR_API_URL or ocr.url in synth.yaml"
        )
    return out
```

`materialize_document` 改为 `_ocr_candidates(cfg)`。删除硬编码 `http://124.174.25.112:40121`。

`tests/synth/test_runner.py` 的 `_cfg` 增加 `ocr=OcrConfig(url="", timeout=300)`，并 import `OcrConfig`。其它手写 `SynthConfig(...)` 的测试同样补 `ocr`。

- [ ] **Step 6: 跑测试确认通过**

```bash
cd /home/wanyi/projects/document-failure-synth
python3 -m pytest tests/synth -q -m "not render and not paddle"
```

Expected: PASS。

---

### Task 4: 拷数据 + 全仓单测 + git 初始提交

**Files:**
- Copy: `TRAINER_ROOT/复刻失效数据/中间结果/dataset/task_001_002_raw` → `NEW_ROOT/data/source/task_001_002_raw`
- Copy: `bilingual_v1/synth_*`、`report.json`、`synth_input_path.txt` → `NEW_ROOT/data/examples/bilingual_v1/`

**Interfaces:**
- Consumes: Task 1–3 的新仓
- Produces: 含源 case 与样例的 git 仓，`HEAD` 有一次提交

- [ ] **Step 1: 拷源 case 与样例**

```bash
TRAINER_ROOT=/home/wanyi/projects/Document_analyst_Trainer
NEW_ROOT=/home/wanyi/projects/document-failure-synth
cp -a "$TRAINER_ROOT/复刻失效数据/中间结果/dataset/task_001_002_raw" "$NEW_ROOT/data/source/"
mkdir -p "$NEW_ROOT/data/examples/bilingual_v1"
cp -a "$TRAINER_ROOT/复刻失效数据/合成数据/bilingual_v1/"synth_* "$NEW_ROOT/data/examples/bilingual_v1/"
cp "$TRAINER_ROOT/复刻失效数据/合成数据/bilingual_v1/report.json" "$NEW_ROOT/data/examples/bilingual_v1/"
cp "$TRAINER_ROOT/复刻失效数据/合成数据/bilingual_v1/synth_input_path.txt" "$NEW_ROOT/data/examples/bilingual_v1/"
rm -rf "$NEW_ROOT/data/examples/bilingual_v1/"_assets*
```

- [ ] **Step 2: 确认切断**

```bash
cd /home/wanyi/projects/document-failure-synth
grep -R "src/inference/config" src/synth tests || true
grep -R "src/data/config/pipeline.yaml" src/synth tests || true
grep -R "Document_analyst_Trainer" src/synth || true
```

Expected: `src/synth` 无命中。

- [ ] **Step 3: 跑非 live 单测**

```bash
cd /home/wanyi/projects/document-failure-synth
python3 -m pytest tests/synth -q -m "not render and not paddle"
```

Expected: PASS。

- [ ] **Step 4: 冒烟 runner 缺 key**

```bash
cd /home/wanyi/projects/document-failure-synth
env -u OPENAI_API_KEY -u SYNTH_LLM_MODEL python3 -m src.synth.runner --config src/synth/config/synth.yaml
```

Expected: 因环境变量或 OCR 未配置失败，**不是** `FileNotFoundError` 打开 `src/inference/config/pipeline.yaml`。

- [ ] **Step 5: git init 并提交**

```bash
cd /home/wanyi/projects/document-failure-synth
git init
git add README.md requirements.txt pytest.ini .gitignore .env.example src tests scripts docs data/source data/examples
git status
git commit -m "$(cat <<'EOF'
Initial import of bilingual failure-data synth pipeline.

Split from Document_analyst_Trainer so the annotator kit generator can be versioned independently.
EOF
)"
```

Expected: `git status` 中没有 `.env`。

---

### Task 5: 从 Trainer 删除 synth 管线

**Files:**
- Delete: `TRAINER_ROOT/src/synth/`
- Delete: `TRAINER_ROOT/tests/synth/`
- Delete: `TRAINER_ROOT/scripts/synth_m1_check.py`
- Delete: `TRAINER_ROOT/scripts/synth_m2_check.py`
- Delete: `TRAINER_ROOT/scripts/synth_m3_check.py`
- Delete: `TRAINER_ROOT/scripts/synth_rerender.py`
- Delete: `TRAINER_ROOT/requirements-synth.txt`
- Delete: 三份 bilingual/extract spec 与两份 plan（含本计划在 Trainer 的副本）
- Delete: `TRAINER_ROOT/spikes/html_rewrite_retention/`

**Interfaces:**
- Consumes: Task 4 已通过
- Produces: Trainer 无 `src/synth`；训练代码与 `复刻失效数据/` 仍在

- [ ] **Step 1: 把本计划拷到新仓 docs**

```bash
cp /home/wanyi/projects/Document_analyst_Trainer/docs/superpowers/plans/2026-08-20-extract-failure-synth-pipeline.md \
   /home/wanyi/projects/document-failure-synth/docs/
cd /home/wanyi/projects/document-failure-synth
git add docs/2026-08-20-extract-failure-synth-pipeline.md
git commit -m "$(cat <<'EOF'
Add extraction implementation plan to the standalone repo.
EOF
)"
```

若已在新仓则跳过。

- [ ] **Step 2: 删除 Trainer 中的 synth 文件**

```bash
TRAINER_ROOT=/home/wanyi/projects/Document_analyst_Trainer
rm -rf "$TRAINER_ROOT/src/synth"
rm -rf "$TRAINER_ROOT/tests/synth"
rm -f "$TRAINER_ROOT/scripts/synth_m1_check.py" \
      "$TRAINER_ROOT/scripts/synth_m2_check.py" \
      "$TRAINER_ROOT/scripts/synth_m3_check.py" \
      "$TRAINER_ROOT/scripts/synth_rerender.py" \
      "$TRAINER_ROOT/requirements-synth.txt"
rm -f "$TRAINER_ROOT/docs/superpowers/specs/2026-08-19-synthetic-bilingual-data-pipeline-design.md" \
      "$TRAINER_ROOT/docs/superpowers/plans/2026-08-19-synthetic-bilingual-pipeline.md" \
      "$TRAINER_ROOT/docs/superpowers/specs/2026-08-20-extract-failure-synth-pipeline-design.md" \
      "$TRAINER_ROOT/docs/superpowers/plans/2026-08-20-extract-failure-synth-pipeline.md"
rm -rf "$TRAINER_ROOT/spikes/html_rewrite_retention"
```

不要删除 `复刻失效数据/`、`scripts/run_data_pipeline.py`、`scripts/run_prepare_dataset.py`。

- [ ] **Step 3: 验收删除**

```bash
test ! -d /home/wanyi/projects/Document_analyst_Trainer/src/synth
test -d /home/wanyi/projects/Document_analyst_Trainer/src/data
test -d /home/wanyi/projects/Document_analyst_Trainer/src/train
test -d /home/wanyi/projects/document-failure-synth/src/synth
```

Expected: 全部 exit 0。Trainer 不做 git commit。

---

## Self-Review

- Spec §2 决策 → Task 1 路径、Task 4 git、Task 5 删除
- Spec §3 非目标 → Global Constraints
- Spec §4 布局 → Task 1
- Spec §5 依赖切断 → Task 2–3
- Spec §6 Git → Task 4 Step 5
- Spec §7 拷贝清单 → Task 1 + Task 4 Step 1
- Spec §8 Trainer 删除 → Task 5
- Spec §9 顺序 → Task 4 通过后才 Task 5
- Spec §10 验收 → Task 4 Step 2–4、Task 5 Step 3
- Spec §11 回退 → Task 5 前置条件
