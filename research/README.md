# 研究适配与复用实现

本目录保存固定上游的复现入口和已有 harness 实现。实验问题、状态和结果统一从 [experiments](../experiments/README.md) 登记查找；不要再直接添加一个未登记的实验文件夹。

- [neuralCAD-Edit](neuralcad_edit/README.md)：固定代码、数据、环境和离线评价。
- [AgenticCADedit](agenticcad_edit/README.md)：官方工具、双 harness、预算和评价适配。
- [三 harness](harness_study/README.md)：EXP-001 的已有实现及历史报告，不是长期续作评测。

`.local/` 保存上游 checkout、数据、环境、账本和原始运行，`output/` 保存本地导出，均忽略。现有路径保持稳定以复用环境及历史记录；新实验不复制这些数据，通过相对位置、固定提交和校验值引用。共享的源码、锁文件与必要说明可入库，付费运行仍需其原有预算授权。
