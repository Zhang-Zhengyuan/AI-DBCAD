# 项目上下文与按需导航

核对日期：2026-10-26。科研项目现名 ResumaCAD，底座沿用 DBCAD v3。本文是定位索引，实际源码、锁文件、构建文件与当前 CI 是事实来源；历史设计稿描述的目标不等于已实现。模块路径或命令变化时同 PR 更新本索引。

先按 [目录与收录规则](../REPOSITORY_LAYOUT.md) 判断本地/入库/远端范围。DBCAD 源码已集中到 `systems/dbcad/`；[实验索引](../../experiments/README.md) 关联稳定实验 ID、代码和结果，EXP-001 为旧单请求对照，EXP-002 已完成首轮 24 个受控恢复分支（非自然长期链）。开题写作和汇报导出默认仅本地，不自动创建 Issue 或 PR。

AI-native CAD 研究与 Mac 接手入口：[MAC_HANDOFF.md](MAC_HANDOFF.md)。下文描述继承的 DBCAD 底座。

后续研究统一在本 ResumaCAD 仓库进行。维护者已选定“持续人机 CAD 编辑中的版本感知几何证据维护与可靠续作”，拟研究独立的持续编辑 benchmark 和证据维护方法，候选方法与真正持续链 benchmark 尚未实现或验证；EXP-002 的工具适配、独立真值与初步结果已落地，当前定位以[根 README](../../README.md) 为准。neuralCAD-Edit 的固定代码、完整数据、ARM64 环境和离线评测入口见 [research/neuralcad_edit/README.md](../../research/neuralcad_edit/README.md)，论文与代码导读见 [PAPER_AND_CODE.md](../../research/neuralcad_edit/PAPER_AND_CODE.md)。

AgenticCADedit 官方 MCP/完整脚本双 harness 的 Mac 复现与有预算 API 对照入口见 [research/agenticcad_edit/README.md](../../research/agenticcad_edit/README.md)，原实现局限和评分口径见其 [PAPER_AND_CODE.md](../../research/agenticcad_edit/PAPER_AND_CODE.md)。

原生 AI 编辑与三 harness 对照研究入口：[research/harness_study/README.md](../../research/harness_study/README.md)。AI 预览支持分段暂停/同任务继续，有 trace_dir 时保存私有检查点；原任务累计费用与版本检查不重置。AI 路由与隔离预览循环位于 `systems/dbcad/CAD_DB/backend/app/api/v1/ai.py`、`app/ai_editor.py`，浏览器面板为 `systems/dbcad/web/src/AIPanel.tsx`；费用控制模块 `app/ai_budget.py` 与研究 API 共用。

AI-CAD-agent 底层框架（EXP-003 已选定）：官方 AgenticCADedit 循环（固定上游提交）经 `agenticcadedit-acis/1` 适配器运行在 ACIS 上——接入层 `app/ai_agentic.py`（桥接与提示）、`app/ai_agentic_runner.py`/`app/ai_agentic_mcp.py`（上游循环与 MCP 子进程）、`app/ai_cad_facade.py` + `app/cad_contract.py`（受限原生语言与命令/查询契约）、`app/ai_render_views.py`（固定相机多视图 + 真实选区高亮）；worker 侧新命令在 `CAD_DB/worker/agent_geometry.inc`。配置字段 `framework=agenticcadedit-acis` + `agentic_upstream` + `render_python`。用户交互层支持：网页填写自定义 provider（base_url+key，只存本机 0600 文件）、拉取模型列表并按“图片+工具”探针筛选可用 VLM（纯文本模型不可选）、填写单价后模型与探针共同受授权总账限制，provider 另有跨模型子上限、随指令上传图纸图片进入官方循环上下文（看图建模）；实现于 `app/ai_provider_store.py` 与 `app/api/v1/ai.py` 的 /ai/providers 端点组。选型证据与边界见 [experiments/exp-003-cad-agent-selection/DECISION.md](../../experiments/exp-003-cad-agent-selection/DECISION.md)。

Mac 原生环境与验收：[../MACOS.md](../MACOS.md)。构建入口 `scripts/build-macos.sh`，环境/进程入口 `scripts/macos.py`，用户启动入口 `DBCAD.command`。SDK、原生构建及服务状态分别在根目录提供的授权包、`build/macos/`、`.local/macos/`，均不入库。

Issue #30 修复说明：网页下一阶段通过 `parent_job_id` 显式继承已终结任务；`mode=inspect` 是禁止写操作的只读阶段，完成状态为 completed、不发布空版本。运行中也保存检查点，中断检查点仅供审阅并拒绝自动重放。`SharedBudget` 在既有授权账本原位保留费用与未知预留，默认/自定义模型和视觉工具探针共同受总上限约束，provider 另有跨模型累计子上限；VLM 资格要求真实图片答案通过工具返回。连续编辑研究入口：[EXP-004](../../experiments/exp-004-native-continuation/README.md)。

后续交互修复：无工具输出保留模型公开文本，`no_tool_call` 暂停仅在用户填写回答/补充要求后续作，不自动重试或应用；`render_views(views=['overview'])` 可查看当前完整模型，普通视图仍保留初始世界相机。EXP-004 首轮完整分支最终几何 48/48 正确，预览违规与错误报告另记，未证实旧记忆误用；副模型预算截断，详见实验结果，不等同于长期研究假设已验证。

## 运行链路（v3 服务端权威）

`浏览器（React + three.js 薄客户端）→ FastAPI 网关 → ACIS worker（C++，每项目一进程，命令串行队列即版本链，ADR-2）`。

几何真相 = SAT 资产（内容寻址，sha256）；GLB 网格与实体图投影是派生索引（ADR-3）。后端不解释几何，ACIS 只存在于 worker 进程。浏览器只消费 JSON + GLB，撤销/重做/布尔等命令全部回传服务端执行。Qt 客户端已移除（tag `v3` 前的架构见 `docs/history/`）。

## 从需求到源码

| 需求 | 先定位 | 再读 / 验证 |
|---|---|---|
| 前端渲染、视图、组件树 | `systems/dbcad/web/src/Viewer.tsx`、`App.tsx`、`store.ts`、`api.ts` | `systems/dbcad/web/package.json`；`node scripts/showcase.cjs` 真浏览器截图 |
| HTTP API（项目/版本/命令/资产/实体） | `systems/dbcad/CAD_DB/backend/app/api/v1/` | `backend/tests/test_api_v1_*.py` |
| 几何命令执行（ACIS worker） | `systems/dbcad/CAD_DB/worker/main.cpp`、`session.cpp` | `docs/ARCHITECTURE.md`；`tests/test_v1_real_commands_integration.py` |
| STEP/SAT 导入、导出 | `worker/main.cpp`（loadModel 分发）、`backend/app/worker_client.py` | `tests/test_v1_step_import_integration.py` |
| 网格提取与 GLB | `systems/dbcad/CAD_DB/kernel/mesh_extractor.cpp`、`glb_writer.cpp` | `docs/P0_MESH_BENCH.md`；`systems/dbcad/web/scripts/validate-assets.mjs` |
| 实体图序列化（细档） | `systems/dbcad/CAD_DB/worker/entity_graph_codec.cpp` | `docs/ARCHITECTURE.md` ADR-3；`tests/test_v1_projection_neo4j.py` |
| 版本链/组件/实体存储 | `backend/app/v1_store.py`、`entity_projection.py`、`component_store.py` | `tests/test_api_v1_entities.py`（/diff、409 冲突、压实） |
| 细档 Neo4j 物理表示 | `backend/app/json_graph_native.py` | `docs/history/STORAGE_CONSOLIDATION_PLAN.md`（出处） |
| 事件推送/在场感知 | `backend/app/events_hub.py`、`api/v1/events.py` | `docs/ARCHITECTURE.md` §协同 |
| Neo4j 管理 WebUI | `backend/app/neo4j_webui/`、`static/webui/` | 鉴权与页面人工验证 |
| 压测与基准 | `backend/scripts/benchmark_v3.py` | `docs/BENCHMARK_v3.md`（ABC 200 样本） |
| 本机环境、构建依赖 | `scripts/check-environment.ps1`、`build.ps1`、`Directory.Build.props` | `docs/GETTING_STARTED.md`、`DEPENDENCIES.md` |
| 部署和打包 | `systems/dbcad/CAD_DB/deploy/`、`deploy/lib/` | `systems/dbcad/CAD_DB/DEPLOYMENT.md`；`scripts/tests/test-fullstack-launcher.ps1`；`systems/dbcad/web/e2e/` |

表内 backend 路径相对 `systems/dbcad/CAD_DB/`。不确定文件名先限定目录用 `rg --files` 查找。

## 不要默认读取的内容

`_deps/`、`.vs/`、`x64/`、`dist/`、`artifacts/`、`neo4j-runtime/`、虚拟环境和参考文献不在日常源码搜索范围。需要 SDK 声明时只定位相关头文件；不把授权 SDK 复制进仓库。外层同名目录和 clean 副本不是当前代码事实来源。

## 技术参考包

提供的《重要文档（给AI用的）》含 00 索引及 01 保存恢复、02 历史、03 错误、04 Bulletin、05 属性、06 应用接口、07 API 参考，整理自 18 份 ACIS PDF。它是几何内核参考集，不是 Git 协作规范。

使用原则：保存恢复要验证拓扑/属性/版本兼容；历史状态配对与回滚要覆盖失败路径；检查 ACIS outcome 及恢复后对象有效性；Bulletin/内核历史不能直接等同于多人协作提交；UUID 不能依赖进程地址或一次会话的句柄。具体内存所有权、宏和 API 签名必须查本项目当前适配实现及授权 SDK 文档，PDF 提取示例可能缺行。

本次刷新远端后确认，维护者已将这组整理稿收录到 `参考文献/00_索引.md` 至 `07_API参考.md`。涉及相应 ACIS 主题时按需读取，不默认加载全文；不要重复导入附件，也不要进一步复制 SDK、许可或完整厂商文档。新增资料公开前由维护者确认授权范围。
