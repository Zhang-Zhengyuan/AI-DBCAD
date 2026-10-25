# ResumaCAD

**面向持续人机 CAD 编辑的可靠续作。**

*Version-Aware Geometric Evidence for Reliable Continuation in Human–AI CAD Editing*

ResumaCAD 研究共享 CAD 项目经历人工编辑、需求更新和暂停/恢复后，AI 如何识别仍然有效的几何证据、重新获取失效观测，并以可控成本正确继续设计。名称取意于 **resume**，项目原名 InferraCAD；几何与协作底座沿用 **DBCAD**。

## 研究问题与边界

历史上正确的测量不一定能支持当前行动。例如，一个孔没有变化，但后来移入的支架可能使插入区域发生干涉；反过来，距离数值虽然改变，带有可靠几何界的“间隙至少 3 mm”判断仍可能成立。

我们关注跨版本的**几何证据有效性与可靠续作**，区分几何观测、仍生效的设计要求和依赖它们的计划。合法的新要求应取代旧要求，不能把所有历史状态当作永久约束。版本数量、需求引用跨度、外部变化、几何规模和模型上下文长度需要分别评测。

拟开展两项工作，**目前均处于选题与预实验准备阶段**：

1. **持续编辑 benchmark**：提供初始模型、逐步开放的编辑请求、人工/agent 变更事件、版本与要求更新记录，以及独立几何验收。测阶段和整链完成率、非目标损伤、失效证据误用、恢复代价与总成本。
2. **版本感知的证据维护方法**：将可执行查询绑定到来源版本、实体实例、单位、坐标系和依赖范围；模型变化后选择性保留、撤销或重验观测，并更新受影响计划。先固定模型与工具能力，和全量重验、普通摘要/检索、空间依赖及通用上下文一致性方法公平对照。

不宣称“首次多轮 CAD”“首次长时程 CAD”或已经验证了新方法。已有 [mrCAD](https://aclanthology.org/2025.findings-emnlp.1248/) 的多轮 2D 修改、[CADWorld](https://arxiv.org/abs/2609.16251) 的长时程工程操作、[HistCAD](https://arxiv.org/abs/2602.19171) 的编辑约束保持，以及 [Concord](https://arxiv.org/abs/2610.05281) / [TRACE](https://arxiv.org/abs/2609.33517) 的观测失效与记忆准入研究，均是需要明确比较的近邻。新颖性和收益仍需实验确认。

## 当前可用内容

| 内容 | 当前状态与入口 |
| --- | --- |
| DBCAD 几何、存储与协作底座 | ACIS worker、不可变 SAT 资产、版本清单、实体图投影、浏览器编辑；Apple Silicon 原生运行，见 [macOS 指南](docs/MACOS.md) |
| 原生 AI 编辑入口 | 自然语言 → inspect / command / render / undo → 隔离预览 → 用户应用；当前工具覆盖和限制见 [研究入口](research/harness_study/README.md) |
| neuralCAD-Edit 复现 | 固定上游代码、数据与离线评价环境，见 [复现 README](research/neuralcad_edit/README.md) |
| AgenticCADedit 复现 | 官方工具和双 harness 的环境、API 运行与记录，见 [复现 README](research/agenticcad_edit/README.md) |
| 已完成三 harness 实验 | 24 请求 × 3 方法的初步对照；方法适配与评分局限见 [完整报告](research/harness_study/REPORT.md)、[PDF](research/harness_study/output/inferracad-study-report.pdf) |
| 持续编辑 benchmark 与新方法 | **尚未实现或验证**；上述既有实验不是长期续作评测，也不证明候选方法有效 |

现有实验和报告保留当时的 InferraCAD 名称、文件名与评分口径，便于追溯。SDK、许可、原始数据、凭据、模型调用记录与运行资产留在本机忽略目录，不随源码分发。公开持续编辑评测计划提供 STEP 和开放验收后端，避免将商业 ACIS SDK 作为唯一复现前提。

## 快速进入

- 已配置的 Mac：在 **ResumaCAD** 目录双击 `DBCAD.command`，或运行 `python3 scripts/macos.py start --open`。迁移后的兼容路径规则见 [macOS 指南](docs/MACOS.md#工作区更名与兼容路径)。
- 研究与复现：从上表的研究 README 开始；未配置提供方时先做离线检查，不需要模型 API Key。
- 开题汇报准备：[选题覆盖与价值复核（2026-10-26）](docs/research/NOVELTY_AUDIT_20261026.md)、[12 页内容与讲解底稿](docs/research/PROPOSAL_SLIDES.md)、[四套视觉预览](output/slides/previews/README.md)；最终 PPT 待选定风格后制作。
- 代码导航与后续 agent 接手：[项目地图](docs/ai/PROJECT_CONTEXT.md) · [Mac 接手说明](docs/ai/MAC_HANDOFF.md)。
- 仓库：[Zhang-Zhengyuan/ResumaCAD](https://github.com/Zhang-Zhengyuan/ResumaCAD)。

## DBCAD 底座

采用服务端权威架构：C++ ACIS worker 执行几何计算，FastAPI 网关管理命令与版本，Neo4j 保存版本清单和实体图投影，内容寻址资产库保存 SAT/GLB/graph；React + three.js 浏览器消费网格和查询结果。**SAT + 版本清单是权威状态，JSON 实体图是派生查询索引。** 底层 DBCAD 协议、命令与启动入口保持兼容。

## 新成员上手

使用 coding agent 前先读 [AGENTS.md](AGENTS.md)；团队按 [Issue → 分支 → 验证 → PR → 人工审核](docs/ai/WORKFLOW.md) 协作。项目按需导航见 [AI 项目地图](docs/ai/PROJECT_CONTEXT.md)。

完整步骤见 [复现与部署指南](docs/GETTING_STARTED.md)。worker 构建需要授权 ACIS SDK 与配套依赖；源码不包含 SDK。依赖布局见 [依赖说明](docs/DEPENDENCIES.md)；SDK 已迁移到官方 ACIS 2026.1.0.1（纯公有 API，无定制头文件），编译契约与行为差异见 [ACIS 2026 迁移指南](docs/ACIS_2026_MIGRATION.md)。

Apple Silicon Mac 原生构建与启停见 [macOS 运行指南](docs/MACOS.md)。首次运行 `python3 scripts/macos.py setup --team-kit /path/to/DBCAD-Team-Kit.zip`，之后双击根目录 `DBCAD.command`。

准备依赖和本机配置后，在仓库根目录执行：

```powershell
.\scripts\check-environment.ps1
.\scripts\build.ps1 -Configuration Release
Push-Location web
npm ci
npm run build
Pop-Location
.\CAD_DB\deploy\start_dbcad_fullstack.cmd
```

只验证后端，不需要 CAD SDK 或数据库：

```powershell
cd CAD_DB/backend
uv sync --frozen --extra dev
uv run --frozen pytest -q -m 'not integration'
```

## 功能与模式（v3，2026-10-19 定稿）

- **服务端权威命令**：建模（曲线/曲面/布尔/抽壳/变换）、撤销/重做全部在 ACIS worker 执行，浏览器只提交命令与消费网格（ADR-2：每项目一 worker 进程，命令串行队列即版本链）。
- **版本链与协作**：commit 带 base_version 乐观锁（过期 409 并返回实体级冲突清单）；组件不可变、按 `hash_sat` 全局去重；WebSocket 推送版本事件与在场感知（edit_intent）。
- **三级存储（ADR-3）**：SAT 资产 = 几何真相（内容寻址，还原字节级无损）；GLB = 渲染网格；实体图投影 = 可查询细档索引——原生标签 + 扁平字段，`DERIVED_FROM` 血缘链、`/entities` 与 `/diff` 实体级查询，内容寻址实体跨组件去重（ABC 压测实测 60.5%）。
- **导入导出**：SAT 与 STEP（Spatial InterOp）导入，SAT/GLB/STL 导出；STEP 装配体按组件树拆分入库。
- **渲染全覆盖**：可视化网格 + 边折线；无面体输出纯线框 GLB，退化面计入 `faces_skipped` 诚实上报（ABC 200/200 零失败；CNC 车床装配 248 组件 / 48.8 万三角形实测流畅渲染）。
- Neo4j 管理 WebUI（项目/版本/图/原始提交审计）。
- Qt 桌面客户端、C++ Bridge、客户端 Bolt 直连与 PostgreSQL 实验均已移除（历史见 `docs/history/` 与 tag `legacy/pre-architecture-v2`）。

## 几何内核隔离

ACIS 只存在于 worker 进程：许可初始化与 SDK 调用集中在 `CAD_DB/acis.cpp` 与 `worker/`、`kernel/` 的适配实现；`kernel/model_types.h` 定义中立值类型，`glb_writer`/`mesh_extractor` 输出与内核无关的网格资产。网关与浏览器不接触任何内核类型。

当前为编译时适配：接入其他内核需要实现 worker 命令层与网格提取，并处理已有 SAT 数据的兼容读取。尚未接入第二内核，不承诺无损读取 SAT，也没有运行时插件加载。设计取舍见 [内核隔离与替换指南](docs/KERNEL_ISOLATION.md)。

## 目录

```text
CAD_DB.sln                唯一 Visual Studio 解决方案（默认目标 dbcad_worker）
CAD_DB/
  acis.cpp/common.cpp     ACIS 许可初始化与错误处理
  worker/                 ACIS worker：命令执行、会话、实体图序列化
  kernel/                 网格提取、GLB 写入、中立类型
  backend/app/
    api/v1/               HTTP/WS 入口（项目/版本/命令/资产/实体/事件）
    v1_store.py           版本链 + 实体投影存储（Memory/Neo4j 双实现）
    neo4j_webui/          管理 API；static/webui/ 管理台前端
  backend/tests/          回归与集成检查（API 契约 / 真内核 / STEP / Neo4j 投影）
  backend/scripts/        abc 压测、投影重建工具
  deploy/                 一键启停、Docker、打包
web/                      React + three.js 客户端（Vite）
scripts/                  环境检查与便携构建
docs/                     架构、计划、基准、上手指南；docs/history/ 为 v2 归档
```

## 启停

```powershell
.\CAD_DB\deploy\start_dbcad_fullstack.cmd
.\CAD_DB\deploy\stop_dbcad_fullstack.cmd
```

启动链路：Neo4j → FastAPI → 浏览器 `/web/`，worker 由后端按需启动。默认端口 8000，以启动器输出地址为准；登录使用 `CAD_DB/deploy/dbcad.local.env` 中的 `DBCAD_PASSWORD`。重建与同机双客户端测试见[上手指南](docs/GETTING_STARTED.md#修改代码后重新运行)，参数见[部署说明](CAD_DB/DEPLOYMENT.md)。

[架构](docs/ARCHITECTURE.md) · [v3 计划](docs/V3_PLAN.md) · [压测基准](docs/BENCHMARK_v3.md) · [贡献说明](CONTRIBUTING.md) · [后端说明](CAD_DB/backend/README.md)
