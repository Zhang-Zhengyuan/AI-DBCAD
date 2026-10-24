# inferraCAD：Mac Agent 接手说明

更新：2026-10-24。

## 目标

Zhang Zhengyuan 从 DBCAD（数据库 CAD 存储）转向 AI-native CAD 研究，目标是通过复现和实验找到实用、可验证的研究缺口并发表论文。创新点尚未确定；当前先复现 neuralCAD-Edit，优先使用模型 API，不训练基础模型。

## 工作区与迁移边界

- 仓库：[Zhang-Zhengyuan/inferraCAD](https://github.com/Zhang-Zhengyuan/inferraCAD)，私有；Mac 建议使用 `~/THU_Things/inferraCAD`，后续研究都在此开展。
- Mac：M5、24 GB 统一内存、macOS 26、约 500 GB 空闲磁盘。
- DBCAD 底座为 React + FastAPI + Neo4j + C++ ACIS worker。本阶段不移植 worker；现有 Windows EXE/DLL 不能直接在 Mac 原生运行。
- 后端通过本机子进程、管道和文件调用 worker，不能直接填远程地址。需要联调时，建议保留 Windows 后端与 worker，Mac 通过 HTTP 使用；benchmark 实验独立运行。
- 先读仓库 `AGENTS.md` 和 [项目地图](PROJECT_CONTEXT.md)，遵守 Issue、分支、PR 流程。

## 当前进度

- 任务：[Issue #2](https://github.com/Zhang-Zhengyuan/inferraCAD/issues/2)，分支 `chore/2-setup-neuralcad-edit`。
- Windows 已在 `research/neuralcad_edit/.local/upstream` 克隆[官方代码](https://github.com/AutodeskAILab/neuralCAD-Edit)，提交 `68d1acceda0743e364f45fb7823f7cf39c530ba5`；该目录被 Git 忽略，Mac 需要重新克隆。
- [官方论文](https://arxiv.org/abs/2604.16170)；[官方数据](https://huggingface.co/datasets/autodesk/neuralCAD-Edit)，压缩约 6 GB、解压约 13 GB。
- 用户已暂停 Windows 配置工作。完整数据未下载，环境未安装，尚无运行验证或复现成绩。

## Mac 上下一步

1. 拉取任务分支，重新克隆并固定官方代码。阅读论文及 `src/scripts_benchmark_inference/run_harness.py`、`src/vlms/base_vlm.py`、`src/harnesses/cadquery_script.py` 和 `src/utils/evals_feature_geometric.py`。官方同时提供 benchmark 和 CadQuery 迭代编辑 harness。
2. 建立原生 ARM64 独立环境，参考官方 Python 3.12 配置；下载数据、配置路径、锁定依赖。不要复制 Windows 虚拟环境。
3. 用官方小样本验证：数据读取 → STEP 导入 → harness 执行/导出 → 七视图渲染 → 自动评分，然后准备模型 API 单任务运行命令。
4. 交付中文阅读指南、运行命令和实测结果，再讨论后续实验。小样本跑通不等于论文全量复现；自动指标不能替代人工 Acceptance。

环境、数据、缓存、结果放在研究目录的 `.local/` 内；凭据仅存本机。只提交脚本、依赖锁和说明。暂不开发新方法或运行全量付费实验。
