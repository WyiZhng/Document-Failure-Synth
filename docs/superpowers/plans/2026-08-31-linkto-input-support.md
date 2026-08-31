# link_to 输入支持 Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

Goal: 让带有 link/link_to 的源标注进入现有双语双栏合成流水线，目标内容只渲染一次，并在最终 GT 中保留可被 Trainer 消费的引用关系。

Architecture: 在素材阶段把主 children 树和 link_to 目标规范化为去重节点池、关系集合和可渲染块列表。HTML、翻译、分页和截图阶段继续只处理可渲染块；校验和 GT 阶段使用同一份节点索引与源 ID 映射恢复 link/link_to。

Tech Stack: Python 3.10+、dataclasses、PyYAML、Pillow、BeautifulSoup4、Playwright、pytest、现有 OpenAI 兼容 LLM 和 Trainer 阶段 0 数据处理流程。

Spec: docs/superpowers/specs/2026-08-31-linkto-input-support-design.md

## Global Constraints

- 保留原始 link/link_to 关系，不把关系改造成 children 父子关系。
- 目标按源文档阅读顺序和源页位置进入渲染流；合成后的物理页仍由现有双栏分页器决定。
- 同一逻辑节点按 id、同一版面块按 member ID 去重；多个锚点可以共享一个目标。
- 虚拟节点不生成独立 HTML 块，但必须递归处理真实子节点。
- max_source_pages: null 表示读取全部源页面；页码仍为 0-based。
- 单元测试使用 fixture 和 mock，不调用真实 LLM/OCR；Trainer 源码不修改。
- 默认 pytest 捕获模式在当前 Python 3.14 环境会触发 tempfile FileNotFoundError，验证命令使用 pytest -s。

---

## File Map

- src/synth/config.py：允许 max_source_pages 为 null。
- src/synth/config/synth.yaml：默认使用全部源页面。
- src/synth/material.py：收集主树和 link_to 目标，去重、排序、裁图并构造 Material。
- src/synth/validate.py：使用规范化块并校验关系和统计信息。
- src/synth/runner.py：将 Material 上下文传给校验。
- src/synth/gt_builder.py：把源 link/link_to 映射为输出 ID 并恢复到 GT。
- tests/synth/conftest.py：提供跨页、虚拟目标和共享目标 fixture。
- tests/synth/test_config.py、test_material.py、test_html_builder.py、test_validate.py、test_gt_builder.py：覆盖各阶段。
- README.md：说明 link_to 支持和全量页面行为。

HTML builder、paginate.js、render.py 继续只处理去重后的普通渲染块，不新增引用图逻辑。

---

### Task 1: 支持全量源页面配置

Files:
- Modify: src/synth/config.py:42-94
- Modify: src/synth/config/synth.yaml:1-4
- Test: tests/synth/test_config.py:7-15

Interfaces:
- SynthConfig.max_source_pages: int | None
- load_config(path: str | Path) -> SynthConfig
- 显式整数仍表示只保留 page < max_source_pages 的块。

- [ ] Step 1: 写失败测试。

把默认配置测试改为：

    def test_load_default_config():
        cfg = load_config(Path("src/synth/config/synth.yaml"))
        assert cfg.copies_per_case == 1
        assert cfg.source_cases == ["task/source.txt"]
        assert cfg.output_root == "data/output/0714_0827"
        assert cfg.max_source_pages is None

    def test_load_config_accepts_explicit_page_limit(tmp_path):
        source = Path("src/synth/config/synth.yaml").read_text(encoding="utf-8")
        path = tmp_path / "config.yaml"
        path.write_text(
            source.replace("max_source_pages: null", "max_source_pages: 2"),
            encoding="utf-8",
        )
        assert load_config(path).max_source_pages == 2

- [ ] Step 2: 运行 pytest -s -q tests/synth/test_config.py，确认当前 int(None) 或旧配置断言失败。

- [ ] Step 3: 将字段类型改为 int | None；在 load_config 中使用：

    max_pages_raw = raw.get("max_source_pages")
    max_pages = None if max_pages_raw is None else int(max_pages_raw)

将 yaml 中 max_source_pages 改为 null。

- [ ] Step 4: 重新运行 pytest -s -q tests/synth/test_config.py，确认配置测试通过。显式页数过滤在 Task 3 的校验兼容测试中验证。

- [ ] Step 5: 提交：

    git add src/synth/config.py src/synth/config/synth.yaml tests/synth/test_config.py
    git commit -m "feat: allow processing all source pages"

---

### Task 2: 建立 link-aware 素材规范化层

Files:
- Modify: src/synth/material.py:13-179
- Modify: tests/synth/conftest.py:18-150
- Modify: tests/synth/test_material.py:9-46

Interfaces:

    @dataclass(frozen=True)
    class LinkRelation:
        anchor_id: str
        target_id: str

    @dataclass
    class LinkGraph:
        nodes_by_id: dict[str, dict]
        relations: list[LinkRelation]
        source_blocks: list[dict[str, Any]]

    def collect_link_graph(tree: list[dict]) -> LinkGraph

Material 新增：

    nodes_by_id: dict[str, dict]
    relations: list[LinkRelation]

- [ ] Step 1: 构造三个 fixture。

  tiny_case_with_link：p0-b1 的 link_to 指向虚拟 p1-fake1，p1-fake1 的 child 为 page_index [1]、category table、bbox 非空的 p1-b1；写入 raw-page-1.png 和 raw-page-2.png。

  tiny_case_with_shared_link：p0-b1 和 p0-b2 都指向 p1-fake1。

  tiny_case_with_late_link：目标真实节点位于 page_index [5]，并写入 raw-page-6.png。

- [ ] Step 2: 写失败测试：

在 test_material.py 增加 from src.synth.material import LinkRelation。

    def test_load_material_accepts_and_materializes_link_target(
        tiny_case_with_link, cfg, tmp_path
    ):
        material = load_material(
            tiny_case_with_link, cfg, tmp_path / "assets"
        )
        assert material.relations == [
            LinkRelation("p0-b1", "p1-fake1")
        ]
        assert material.nodes_by_id["p1-fake1"]["is_virtual"] is True
        assert [block.id for block in material.blocks] == [
            "p0-b1", "p0-b2", "p1-b1"
        ]
        assert material.blocks[-1].image_path is not None

    def test_shared_link_target_is_materialized_once(
        tiny_case_with_shared_link, cfg, tmp_path
    ):
        material = load_material(
            tiny_case_with_shared_link, cfg, tmp_path / "assets"
        )
        assert len(material.relations) == 2
        assert [b.id for b in material.blocks].count("p1-b1") == 1

    def test_link_target_beyond_old_page_limit_is_kept(
        tiny_case_with_late_link, cfg, tmp_path
    ):
        material = load_material(
            tiny_case_with_late_link, cfg, tmp_path / "assets"
        )
        assert any(b.id == "p5-b1" and b.page == 5 for b in material.blocks)

- [ ] Step 3: 运行 pytest -s -q tests/synth/test_material.py，确认现有 _assert_no_link_to 使新测试失败。

- [ ] Step 4: 实现 collect_link_graph。

  先序注册主 children 树和所有 link_to 目标；非空 link_to 生成 LinkRelation；目标子树继续递归收集嵌套关系。

  主树版本优先提供版面字段，link_to 快照只补充缺失子节点。比较 page_index、member、category、bbox、is_virtual；冲突时抛出包含 anchor_id、target_id、node_id 的 ValueError。

  用递归路径集合检测 link_to 循环。虚拟目标必须至少有真实后代。逻辑 id 和 member id 都去重。

  source_blocks 的顺序保留主树块相对顺序；只存在于 link_to 的块按 page、bbox y、bbox x 插入对应页。每条记录包含 id、page、category、text、bbox。

- [ ] Step 5: 删除 load_material 对 _assert_no_link_to 的调用，改为使用 collect_link_graph；当 max_source_pages 为 None 时不做页面过滤；对 target 的 image、chart、seal、table 继续调用 _crop_image_block。

- [ ] Step 6: 增加缺失目标、空虚拟目标、重复节点冲突和循环关系测试，分别断言 ValueError 消息包含目标身份或错误类型。

- [ ] Step 7: 运行 pytest -s -q tests/synth/test_material.py，确认素材测试通过。

- [ ] Step 8: 提交：

    git add src/synth/material.py tests/synth/conftest.py tests/synth/test_material.py
    git commit -m "feat: normalize link_to targets into material"

---

### Task 3: 让 HTML 和校验消费规范化块

Files:
- Modify: src/synth/validate.py:38-145
- Modify: src/synth/runner.py:111
- Modify: tests/synth/test_html_builder.py:1-40
- Modify: tests/synth/test_validate.py:1-226

Interfaces:

    def validate_doc(
        source_tree: list[dict],
        placed: list[PlacedBlock],
        cfg: SynthConfig,
        *,
        material: Material | None = None,
    ) -> ValidationResult

material 非空时使用 material.blocks、material.nodes_by_id 和 material.relations；为空时保留旧的 source_tree 兼容路径。

- [ ] Step 1: 写失败测试。

在 test_html_builder.py 增加以下导入，并将 `test_link_target_is_marked_once` 放在该文件中：

    from bs4 import BeautifulSoup
    from src.synth.html_builder import build_source_html
    from src.synth.material import load_material

在 test_validate.py 增加以下导入，并将关系统计 helper/test 放在该文件中：

    from dataclasses import replace
    from src.synth.material import IMAGE_CATEGORIES, load_material
    from src.synth.render import PlacedBlock

    def test_link_target_is_marked_once(
        tiny_case_with_shared_link, cfg, tmp_path
    ):
        material = load_material(
            tiny_case_with_shared_link, cfg, tmp_path / "assets"
        )
        soup = BeautifulSoup(build_source_html(material, cfg), "lxml")
        ids = [
            element["data-node-id"]
            for element in soup.select("[data-node-id]")
        ]
        assert ids.count("p1-b1") == 1
        assert ids.index("p0-b1") < ids.index("p1-b1")

    def _placed_for_material(material, cfg):
        placed = []
        for index, block in enumerate(material.blocks):
            y1 = 40 + index * 30
            y2 = y1 + 20
            placed.append(
                PlacedBlock(
                    block.id,
                    "zh",
                    block.category,
                    block.page,
                    (40, y1, 450, y2),
                    block.text,
                    index * 2,
                )
            )
            if (
                block.category in cfg.translate_categories
                and block.category not in IMAGE_CATEGORIES
            ):
                placed.append(
                    PlacedBlock(
                        block.id,
                        "en",
                        block.category,
                        block.page,
                        (500, y1, 960, y2),
                        "English translation",
                        index * 2 + 1,
                    )
                )
        return placed

    def test_link_stats_are_reported(tiny_case_with_shared_link, cfg, tmp_path):
        material = load_material(
            tiny_case_with_shared_link, cfg, tmp_path / "assets"
        )
        result = validate_doc(
            material.tree,
            _placed_for_material(material, cfg),
            cfg,
            material=material,
        )
        assert result.stats["link_count"] == 2
        assert result.stats["unique_target_count"] == 1

将现有 test_later_source_pages_are_ignored 改为使用
limited_cfg = replace(cfg, max_source_pages=5)，继续验证显式页数限制；另增：

    def test_later_source_pages_are_included_when_unlimited(cfg):
        tree = _base_tree(
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
        )
        placed = _valid_placed() + [
            PlacedBlock("p5-b1", "zh", "text", 5, _left_bbox(300, 350), "后页正文", 4),
            PlacedBlock("p5-b1", "en", "text", 5, _right_bbox(300, 350), "Later body", 5),
        ]
        result = validate_doc(tree, placed, cfg)
        assert result.ok

- [ ] Step 2: 运行 pytest -s -q tests/synth/test_html_builder.py tests/synth/test_validate.py，确认 validate_doc 当前不接受 material 且没有关系统计。

- [ ] Step 3: 在 runner.py 中将校验调用改为：

    result = validate_doc(
        material.tree,
        placed,
        cfg,
        material=material,
    )

- [ ] Step 4: 实现 material 分支。

  source_by_id 直接由 material.blocks 构造，不重新遍历 link_to，也不重复按页面截断。保留现有中文块、英文配对、类别、文字、bbox、同页和跨页校验。material 为空时仍使用 cfg.max_source_pages is None or page < cfg.max_source_pages，保证旧调用在默认全量和显式限制下都有效。

  递归收集每个关系目标的真实 member ID；每个真实目标 member 必须有且只有一个中文 placed block。虚拟目标只检查真实后代。

  增加 link_count、unique_target_count、materialized_target_block_count、virtual_target_count、unresolved_link_count；缺失或重复目标错误必须包含 anchor_id 和 target_id。

- [ ] Step 5: 运行目标测试：

    pytest -s -q tests/synth/test_html_builder.py tests/synth/test_validate.py

  预期新旧测试全部通过，且 HTML 中共享目标只出现一个 data-node-id。

- [ ] Step 6: 提交：

    git add src/synth/validate.py src/synth/runner.py tests/synth/test_html_builder.py tests/synth/test_validate.py
    git commit -m "feat: validate materialized link targets"

---

### Task 4: 在 GT 中恢复 link/link_to 和输出 ID

Files:
- Modify: src/synth/gt_builder.py:16-203
- Modify: tests/synth/test_gt_builder.py:28-110

Interfaces:
- 保留 build_gt(material: Material, placed: list[PlacedBlock], out_dir: Path, cfg: SynthConfig, seq: int) -> None。
- 内部重建器按源逻辑 id 缓存输出节点，保证共享目标使用一致的输出 ID。

- [ ] Step 1: 写失败测试。

  新增 _walk_with_links，同时遍历 children 和每个 link_to 目标快照。更新 label_ids_match_tree_members 使用该遍历。

  在 test_gt_builder.py 增加 from src.synth.material import IMAGE_CATEGORIES，用于构造与真实素材规则一致的 placed 列表。

  在测试文件中定义 _placed_blocks_for_material，按每个 material block 生成左栏中文 placed；对非 image/chart/seal/table 且在 cfg.translate_categories 中的块额外生成右栏英文 placed：

      def _placed_blocks_for_material(material, cfg):
          placed = []
          for index, block in enumerate(material.blocks):
              y1 = 40 + index * 30
              y2 = y1 + 20
              placed.append(
                  PlacedBlock(
                      block.id, "zh", block.category, block.page,
                      (40, y1, 450, y2), block.text, index * 2,
                  )
              )
              if (
                  block.category in cfg.translate_categories
                  and block.category not in IMAGE_CATEGORIES
              ):
                  placed.append(
                      PlacedBlock(
                          block.id, "en", block.category, block.page,
                          (500, y1, 960, y2), "English translation", index * 2 + 1,
                      )
                  )
          return placed

  新增 test_gt_preserves_virtual_link_target：使用 tiny_case_with_link 和 _placed_blocks_for_material，断言输出锚点 link 为 true，link_to 非空，目标为虚拟节点，目标 child 的 category 为 table。

  新增 test_gt_reuses_shared_target_output_id：使用 tiny_case_with_shared_link，断言两个锚点的 link_to 根 ID 和 child ID 相同。

- [ ] Step 2: 运行 pytest -s -q tests/synth/test_gt_builder.py，确认当前 _rebuild_node 固定输出 link false/link_to 空列表，导致新测试失败。

- [ ] Step 3: 实现带缓存的 GT 重建。

  真实节点按 source member ID 映射中文和英文新 ID；虚拟节点继续输出 member 为空的包装节点。缓存键为源逻辑 node id，缓存值为重建节点或 None。

  主树节点的 link 字段取源节点的布尔值。每个 link_to 目标通过 material.nodes_by_id 递归重建，link_to 中保存重建后的完整快照。

  目标已在主树中时不再次渲染，只复用其重建 ID；目标只存在于 link_to 时不插入主 children，其真实块已经在 label.json 中，目标树通过 link_to 保存。

  虚拟目标必须生成稳定的输出包装 ID；嵌套 link_to 使用相同缓存。无法映射的 member ID 直接报告 GT 错误，不保留旧源 ID。

- [ ] Step 4: 运行 pytest -s -q tests/synth/test_gt_builder.py，确认旧测试和关系测试通过。

- [ ] Step 5: 提交：

    git add src/synth/gt_builder.py tests/synth/test_gt_builder.py
    git commit -m "feat: preserve link_to in generated ground truth"

---

### Task 5: 更新说明并完成真实数据 smoke 验收

Files:
- Modify: README.md:1-25
- Test: tests/synth/test_material.py
- Test: tests/synth/test_html_builder.py
- Test: tests/synth/test_gt_builder.py

- [ ] Step 1: 更新 README，说明默认配置从 task/source.txt 读取、max_source_pages: null 表示整份文档、link_to 目标只渲染一次、multi-page-final.json 保留引用关系。

- [ ] Step 2: 对当前真实 source 只做素材和 HTML smoke，不调用 LLM：

    python3 - <<'PY'
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from src.synth.config import load_config
    from src.synth.html_builder import build_source_html
    from src.synth.material import load_material

    cfg = load_config(Path("src/synth/config/synth.yaml"))
    cases = [
        Path("data/source/task_001_015_raw"),
        Path("data/source/task_001_017_raw"),
    ]

    with TemporaryDirectory() as temp:
        for case in cases:
            material = load_material(case, cfg, Path(temp) / case.name)
            html = build_source_html(material, cfg)
            assert material.relations
            assert "data-node-id" in html
            print(
                case.name,
                len(material.relations),
                len(material.blocks),
                len({r.target_id for r in material.relations}),
            )
    PY

  预期 task_001_015_raw 为 3 条关系、1 个唯一目标，并包含 page_index 5 的 p5-b4/p5-b5；task_001_017_raw 为 1 条关系，并包含 p1-b1。

- [ ] Step 3: 运行完整单元测试：

    pytest -s -q tests/synth

- [ ] Step 4: Chromium 可用时运行：

    pytest -s -q -m render tests/synth/test_render.py

  确认目标块可以完成分页、截图和 bbox 回读，且不会因共享引用重复出现。

- [ ] Step 5: 在 LLM、OCR 和 Chromium 均可用时运行现有 CLI：

    python3 -m src.synth.runner --config src/synth/config/synth.yaml

  检查生成目录的 label.json 含目标块，multi-page-final.json 含 link true 和非空 link_to，report stats 含五个 link 指标。

- [ ] Step 6: 使用 Document_analyst_Trainer 的现有阶段 0 配置处理一个生成目录，确认跨页目标进入长距离目标处理，reference_link 数据可以生成，multi-page-final-fillin.json 文字补全不丢失关系。

- [ ] Step 7: 提交：

    git add README.md tests/synth
    git commit -m "docs: document link_to source support"

---

## 最终验收清单

- [ ] task_001_015_raw 的三个锚点都保留，p5-b5/p5-b4 只渲染一次。
- [ ] task_001_017_raw 的虚拟目标不单独渲染，p1-b1 表格只渲染一次。
- [ ] max_source_pages: null 读取第 6 页等全部源页面。
- [ ] 目标只存在于 link_to 时不会从渲染流丢失。
- [ ] 目标已在主树中时不会重复渲染。
- [ ] 输出 GT 的 link/link_to 使用新的输出 ID，关系目标快照可解析。
- [ ] 无 link_to 样本没有块顺序、类别或 GT 结构回归。
- [ ] Trainer 能继续生成引用关系任务数据。
