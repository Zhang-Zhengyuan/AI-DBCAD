# AI-DBCAD：Mac Agent 接手说明

更新：2026-10-25。

## 目标

维护者已选定“持续人机 CAD 编辑中的版本感知几何证据维护与可靠续作”，项目与产品统一命名为 **AI-DBCAD**。拟形成持续编辑 benchmark 和几何证据维护方法；这两项工作尚未实现或验证。现有 AI-DBCAD 底座、上游复现和单轮对照实验是准备基础，不代表已证明新方法有效。研究问题、近邻与状态以[根 README](../../README.md)为准。

## 工作区与迁移边界

- 仓库：[Zhang-Zhengyuan/AI-DBCAD](https://github.com/Zhang-Zhengyuan/AI-DBCAD)，保留私有可见性；后续工作统一使用 AI-DBCAD 工作区，研究文件放在本仓库 `research/`。旧目录可作为兼容符号链接保留，避免虚拟环境、构建缓存和数据库挂载失效。
- Mac：M5、24 GB 统一内存、macOS 26、约 500 GB 空闲磁盘。
- AI-DBCAD 底座为 React + FastAPI + Neo4j + C++ ACIS worker。Mac 原生运行适配关联 [Issue #4](https://github.com/Zhang-Zhengyuan/AI-DBCAD/issues/4)，SDK 与许可仅留本机。
- Mac 构建与启停入口、实际验证和剩余覆盖范围见 [macOS 运行指南](../MACOS.md)。后端仍通过本机子进程、管道和文件调用原生 worker，协议和存储架构保持不变。
- 先读仓库 `AGENTS.md` 和 [项目地图](PROJECT_CONTEXT.md)，遵守 Issue、分支、PR 流程。

## 当前进度

- Mac 环境与离线复现见 [Issue #6](https://github.com/Zhang-Zhengyuan/AI-DBCAD/issues/6)；原生 AI 与三 harness 实验见 [Issue #10](https://github.com/Zhang-Zhengyuan/AI-DBCAD/issues/10)；项目更名与研究定位见 [Issue #12](https://github.com/Zhang-Zhengyuan/AI-DBCAD/issues/12)。
- Mac 已在 `research/neuralcad_edit/.local/upstream` 克隆[官方代码](https://github.com/AutodeskAILab/neuralCAD-Edit)，固定提交 `68d1acceda0743e364f45fb7823f7cf39c530ba5`，保持未修改。
- [官方论文](https://arxiv.org/abs/2604.16170)；[官方数据](https://huggingface.co/datasets/autodesk/neuralCAD-Edit)，压缩约 6 GB、解压约 13 GB。
- 完整数据、ARM64 环境及离线指标已验证，边界见 [研究 README](../../research/neuralcad_edit/README.md) 和 [验收记录](../../research/neuralcad_edit/VALIDATION.md)。
- 已配置 AgenticCADedit 复现和原生 AI 预览/应用入口；完成 24 请求 × 3 方法的付费初步实验，另有观测诊断。以[完整报告](../../research/harness_study/REPORT.md)记录的方法适配、费用估计和代理评分局限为准。未完成全量专家人工评价，也没有长期续作实验结果。

## Mac 上下一步

下一阶段先形成中立的持续编辑协议与小型强基线诊断，区分版本长度、外部变化、需求更新和几何规模。不能将现有 neuralCAD-Edit 分数称为长期任务成绩；不能预设旧事实/要求永久有效，或为候选方法定制评分器。公开复现计划提供 STEP 和开放验收后端，ACIS 继续只存在于原生 worker。

环境、数据、缓存、结果放在 `.local/` 内；凭据仅存本机。后续付费实验须核对预算余额或取得新预算，不因课题立项自动重复使用已消耗的额度。本轮更名不执行模型调用、不重置数据库；历史报告和兼容存储键允许保留旧名称。
