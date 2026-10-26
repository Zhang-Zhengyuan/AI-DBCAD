# 实验索引

每个科学实验有稳定 ID；一次执行另有 run ID。底座在 `systems/dbcad/`，既有复现与 harness 在 `research/`，这里负责关联问题、协议、代码、结果和原始记录位置。

- [EXP-001 · 三 harness 初步对照](exp-001-three-harness/README.md)：已有 24 请求 × 3 方法运行及后验审计；登记既有成果，不代表新运行。它没有测持续人机编辑的证据失效。
- [EXP-002 · 跨版本证据失效预实验](exp-002-evidence-validity/README.md)：**仅计划，尚未运行**。先检查现有系统在外部修改后是否实际误用旧证据，再决定方法投入。

```bash
python3 scripts/experiments.py list
python3 scripts/experiments.py check
```

新增实验先取未用的下一个编号，将 `_template/` 复制为 `exp-NNN-slug/`，填写 README 和 `experiment.json`。实验专用脚本可放在该目录；已有实现直接引用，不复制 harness 或环境。每次运行将 `_template/run.example.json` 填入忽略的 `runs/<run-id>/run.json`，保留全部失败与中断；模板本身不表示运行已经发生。

实验定义、必要配置和可复用脚本可纳入 Git；原始运行/图片/模型/API 记录留本地。经判断值得共享的脱敏小型摘要放 `results/`，附来源 run ID、生成命令和 SHA-256 清单。运行一次实验不自动创建 Issue、提交或 PR。详细边界见 [收录规则](../docs/REPOSITORY_LAYOUT.md)。

EXP-001 的旧脚本继续输出到 `research/harness_study/.local/` 和忽略的 `output/`，这里保存已审阅的指标快照与路径指针；不移动原始数据、环境或账本。新实验使用本目录的 `runs/`，需要发布原始数据时另做明确的数据发布决定。
