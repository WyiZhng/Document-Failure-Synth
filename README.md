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

默认扫描 `data/source/*`，每个源 case 生成 1 份，写到 `data/output/bilingual_v2/`。`copies_per_case` 改回大于 1 可对同一源出多份译文。

生成成功前会检查最终 `multi-page-final.json` 与实际 `images_path` 的页码、virtual 节点、对齐数组、link 目标、页面投影和跨页 merge 重放；不通过的 variant 会删除并重试，不会写入 `synth_input_path.txt`。默认开启 `synchronize_bilingual_pairs`，让中英文块在普通分页情况下成对换页，减少下游页面投影冲突。

讲解页（无需安装，浏览器打开即可）：[`explain/index.html`](explain/index.html)
