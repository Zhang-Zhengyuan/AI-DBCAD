# inferraCAD：Mac Agent 接手说明

更新：2026-10-24。

## 目标

Zhang Zhengyuan 从 DBCAD（数据库 CAD 存储）转向 AI-native CAD 研究，目标是通过复现和实验找到实用、可验证的研究缺口并发表论文。创新点尚未确定；当前先复现 neuralCAD-Edit，优先使用模型 API，不训练基础模型。

## 工作区与迁移边界

- 仓库：[Zhang-Zhengyuan/inferraCAD](https://github.com/Zhang-Zhengyuan/inferraCAD)，私有；维护者已明确后续研究统一使用当前 InferraCAD 工作区，研究文件放在本仓库 `research/`。
- Mac：M5、24 GB 统一内存、macOS 26、约 500 GB 空闲磁盘。
- DBCAD 底座为 React + FastAPI + Neo4j + C++ ACIS worker。维护者在 2026-10-24 提供了 ACIS/InterOp 2026.1.0.1 Mac ARM64 SDK，并明确将当前优先事项调整为先运行 Mac 原生 DBCAD 底座，关联 [Issue #4](https://github.com/Zhang-Zhengyuan/inferraCAD/issues/4)。
- Mac 构建与启停入口、实际验证和剩余覆盖范围见 [macOS 运行指南](../MACOS.md)。后端仍通过本机子进程、管道和文件调用原生 worker，协议和存储架构保持不变。
- 先读仓库 `AGENTS.md` 和 [项目地图](PROJECT_CONTEXT.md)，遵守 Issue、分支、PR 流程。

## 当前进度

- 接手文档的 Issue #2 已关闭；Mac 环境与离线复现续作关联 [Issue #6](https://github.com/Zhang-Zhengyuan/inferraCAD/issues/6)。
- Mac 已在 `research/neuralcad_edit/.local/upstream` 克隆[官方代码](https://github.com/AutodeskAILab/neuralCAD-Edit)，固定提交 `68d1acceda0743e364f45fb7823f7cf39c530ba5`，保持未修改。
- [官方论文](https://arxiv.org/abs/2604.16170)；[官方数据](https://huggingface.co/datasets/autodesk/neuralCAD-Edit)，压缩约 6 GB、解压约 13 GB。
- 完整数据已下载并校验；ARM64 环境、真实样本 STEP/七视图/视频/自动指标、DINO 重提取及发布评分汇总已验证。入口与实际边界见 [研究 README](../../research/neuralcad_edit/README.md) 和 [验收记录](../../research/neuralcad_edit/VALIDATION.md)。未运行付费模型或新的人工评价。

## Mac 上下一步

先使用 [论文与代码阅读指南](../../research/neuralcad_edit/PAPER_AND_CODE.md) 和 `reading.ipynb` 研究真实样本与官方实现。用户本轮要求先完成研究准备，研究清楚后再开展实验；目前仅准备了默认不执行的单任务 API 运行计划。新的模型调用、方法开发和全量付费实验留待下一步明确安排。

环境、数据、缓存、结果放在研究目录的 `.local/` 内；凭据仅存本机。只提交脚本、依赖锁和说明。暂不开发新方法或运行全量付费实验。
