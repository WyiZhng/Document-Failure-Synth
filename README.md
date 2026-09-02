# document-failure-synth

本项目生成双语双栏标注五件套（`origin` / `prelabel` / `label` / `multi-page-final` / `images_path`）。

## 推荐方式：Docker

前置条件：安装 Docker Engine 或 Docker Desktop，并确保 `docker compose` 可用。

```bash
git clone <项目地址>
cd document-failure-synth
cp .env.example .env
# 编辑 .env，填写 LLM 和 OCR 服务配置
mkdir -p data/source
# 将待处理的 case 目录放入 data/source/
./scripts/run.sh
```

`./scripts/run.sh` 会首次构建镜像，后续复用 Docker 缓存。镜像内已经包含固定版本的 Python 依赖、Playwright Chromium 以及浏览器所需的系统库；`.env` 和 `data/` 不会被写入镜像。

容器默认读取 [config/synth.yaml](config/synth.yaml)，输入使用 `data/source/*`，输出写入 `data/output/`。每个 case 目录至少需要包含 `origin.json` 和 `multi-page-final-fillin.json`。可以通过参数使用其他配置：

```bash
./scripts/run.sh --config config/my-synth.yaml
```

运行测试：

```bash
docker compose run --rm --entrypoint python synth -m pytest -q -s -m 'not paddle'
```

## 依赖和配置

- `requirements.txt`：直接依赖，已固定版本。
- `requirements.lock.txt`：Docker 使用的完整 Linux/Python 3.14 依赖锁定文件。
- `config/synth.yaml`：可移植的容器配置模板。
- `src/synth/config/synth.yaml`：保留现有服务器任务配置，适用于已有服务器路径的运行方式。
- `.env`：API 密钥和服务地址，只保存在本地，不提交到 Git。

OCR 和 LLM 服务仍然需要在运行时可访问；Docker 只封装本项目自身的运行环境。

## 不使用 Docker

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" \
  .venv/bin/python -m playwright install chromium
.venv/bin/python -m src.synth.runner --config config/synth.yaml
```

Linux 本地运行时仍可能需要额外的 Chromium 系统库，因此优先使用 Docker。

讲解页（无需安装，浏览器打开即可）：[`explain/index.html`](explain/index.html)
