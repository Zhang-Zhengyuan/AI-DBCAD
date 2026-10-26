# EXP-001 · 三 harness 初步对照

状态：completed（登记既有运行，未重新实验）。原始协议与结果形成于 2026-10-25，历史名称 InferraCAD 保留。代码在 [research/harness_study](../../research/harness_study/README.md)，历史结论与局限见 [REPORT.md](../../research/harness_study/REPORT.md)。

## 问题、协议与结论边界

比较 neuralCAD script、AgenticCADedit、IterCAD STEP adapter 在同一 Qwen3.6-27B 提供方别名和预算下的单请求编辑表现。正式计划为 24 个分层请求 × 3 方法，每 episode 最多 6 次调用、4096 可见输出 token；推理修订未经权重级核实。固定上游提交和种子在 [experiment.json](experiment.json) 中。

CD、表面体素 IoU、DINO、实体可用性与盲 VLM 代理判定分别报告，不能混称专家验收。后验审计发现代理通过的 3 个样例中有 2 个明显非目标范围变化，原代理分数保留；未触发标记不表示正确。

本实验没有外部人工编辑穿插的版本链，不能证明长期续作的旧证据误用，也不能证明候选几何守卫有效。独立观测通道诊断同样不等于端到端改进。

## 入口和原始记录

运行入口仍为仓库根 `research/harness_study/run.sh`。`register` 会改变当前计划指针，不应为了查阅历史而重登记。仅查阅本次登记可以运行 `python3 scripts/experiments.py list`。

原始运行位置：`research/harness_study/.local/primary-20261025-022103/`；诊断、账本及 current 指针也在该模块 `.local/`。本地图片和 PDF 保留于该模块 `output/`，已忽略。没有为新预实验重置任何账本或追加 API 调用。

## 入库结果

[results](results/) 是历史指标的精选文本快照：完整逐例指标 CSV、机器汇总、原协议和原生入口验收摘要；[manifest.json](results/manifest.json) 记录源运行、原快照 Git 提交、生成命令和每份文件 SHA-256。当前源码的路径适配不改变历史 implementation_sha256；哈希中的旧路径表示当时的实现。

这些文本此前已随源码收录，本次仅归档到实验 ID 下；没有新增公开原始请求/回应、STEP、截图或凭据。`protocol.json` 保留旧生成器的 NaN 表示，与旧快照逐字节一致；新实验应使用标准 JSON 的 null。生成图像/PDF仍只在本地。费用区分确认值与失败请求保留费用，不能把保守账本占用直接称为提供商最终账单。
