# AI-DBCAD

**面向持续人机 CAD 编辑的可靠续作。**

*Version-Aware Geometric Evidence for Reliable Continuation in Human–AI CAD Editing*

AI-DBCAD 研究共享 CAD 项目经历人工编辑、需求更新和暂停/恢复后，AI 如何识别仍有效的几何证据、重新获取失效观测，并以可控成本正确继续设计。项目与产品统一命名为 **AI-DBCAD**，几何与协作能力由 ACIS、FastAPI 与 Neo4j 支撑。

## 当前进度

已选定“持续人机 CAD 编辑中的版本感知几何证据维护与可靠续作”。已完成首轮 AgenticCADedit 受控检查点预实验；真正连续的编辑 benchmark 和候选几何证据维护方法尚未实现或验证。

AI-DBCAD v3 底座、neuralCAD-Edit/AgenticCADedit 复现环境和三 harness 单请求对照已有结果；它们不能证明长期证据失效的普遍性，也不能证明候选方法有效。EXP-002 已用独立几何真值和成对外部变化观察到旧数值误用案例；按需重查也是有效且廉价的强基线，后续方法必须正面比较。

AI-CAD-agent 底层框架已选定（EXP-003）：官方 AgenticCADedit 循环经 `agenticcadedit-acis/1` 适配器接入 AI-DBCAD/ACIS，含精确面边查询、选区高亮、拉伸/旋转/扫掠、圆角/倒角与阶段化连续对话；IterCAD 经审计不接入，保留为外部对照。AI 面板支持自定义 provider/模型选择（仅 VLM 探针通过可选）与图纸图片输入（Issue #28）。

不宣称“首次多轮 CAD”或“首次长时程 CAD”。已有 mrCAD、CADWorld、HistCAD、Concord/TRACE 等近邻工作需要明确比较；合法需求更新与误用旧观测须分别评价。新颖性、实际错误率和完整成本仍需核实。

## 从哪里开始

- [实验索引](experiments/README.md)：每个实验的稳定 ID、协议、代码、结果和本地原始记录位置。
- [EXP-001 · 已完成三 harness 对照](experiments/exp-001-three-harness/README.md)：精选文本指标、代理评分的后验局限和来源清单。
- [EXP-002 · 证据失效预实验](experiments/exp-002-evidence-validity/README.md)：已完成 24 个正式恢复分支；N/F/A 最终成功 5/8、8/8、8/8，不能外推工业发生率。
- [EXP-003 · 框架选型与 ACIS 接入](experiments/exp-003-cad-agent-selection/README.md)：官方 AgenticCADedit 循环接入 AI-DBCAD/ACIS，付费端到端验收与选型决策见 [DECISION.md](experiments/exp-003-cad-agent-selection/DECISION.md)。
- [研究适配与复现](research/README.md)：固定上游、依赖环境、数据与 harness 入口。
- [AI-DBCAD 底座](systems/dbcad/README.md)：worker、后端、前端、构建与部署。
- [代码导航](docs/ai/PROJECT_CONTEXT.md) · [Mac 接手说明](docs/ai/MAC_HANDOFF.md)。

## 仓库组织与 Git 收录

```text
systems/dbcad/    AI-DBCAD 底座源码与模块测试
research/        固定上游复现适配和既有 harness 实现
experiments/     实验定义、稳定 ID、精选文本结果；每次 runs/ 留本地
scripts/         仓库级构建、生命周期和实验登记检查
docs/           共享架构、运行和协作规则
```

完整目录、结果追溯与判断依据见 [收录与目录规则](docs/REPOSITORY_LAYOUT.md)。底座不再直接散落于根 `CAD_DB/`、`web/`；SDK、本机数据和构建缓存仍由忽略目录管理。

**每次任务先判断“仅本地 / 拟纳入 Git / 需要远端协作”，不默认 push 或提 PR。** 可复用代码、依赖锁、实验定义和必要的脱敏小型指标可以入库；一次实验运行、一次写作或一次导出不各建一个 PR。

开题报告、文献工作笔记和讲稿在本地 `docs/research/`；PPT、预览图、PDF 在本地 `output/`；原始请求/响应、几何模型、截图、账本、数据与缓存在忽略的 `.local/`、`runs/` 或既有 `research/*/output/`。这些内容默认不提交、不推送。`.gitignore` 只是防误收录，不等于数据备份；大型结果以后需要共享时，另用明确的数据存储与校验清单。

团队先读 [AGENTS.md](AGENTS.md)。只有应共享的变更才走 [Issue → 专属分支 → 验证 → 按需 PR → 人工审核](docs/ai/WORKFLOW.md)，agent 不自行合并。

## 运行与验证

已配置的 Mac 在仓库根双击 `AI-DBCAD.command`，或运行 `python3 scripts/macos.py start --open`。底座源码工作目录为 `systems/dbcad/`。首次安装和构建见 [macOS 指南](docs/MACOS.md)；Windows 见 [复现与部署](docs/GETTING_STARTED.md)。SDK 与许可不随源码分发，详见 [依赖说明](docs/DEPENDENCIES.md)。

```bash
# 查阅登记，不运行 CAD 或模型 API
python3 scripts/experiments.py list
python3 scripts/experiments.py check

# 仅验证后端，无需 CAD SDK 或数据库
cd systems/dbcad/CAD_DB/backend
uv sync --frozen --extra dev
uv run --frozen pytest -q -m 'not integration'
```

AI-DBCAD 是服务端权威架构：C++ ACIS worker 执行几何计算，FastAPI 管理命令与版本，Neo4j 保存版本清单和实体图投影，内容寻址资产库存 SAT/GLB/graph；React + three.js 浏览器只消费网格和查询。**SAT + 版本清单是权威状态，JSON 实体图是派生索引。** `base_version` 乐观锁、组件不可变与实体血缘保持既有契约。

[系统架构](docs/ARCHITECTURE.md) · [内核隔离](docs/KERNEL_ISOLATION.md) · [验证矩阵](docs/ai/VALIDATION.md) · [贡献说明](CONTRIBUTING.md) · [GitHub](https://github.com/Zhang-Zhengyuan/AI-DBCAD)

## 名称与兼容

仓库、网页、管理台与启动入口统一为 **AI-DBCAD**（Issue #38）。内部 `systems/dbcad/`、`CAD_DB.sln`、`dbcad_worker`、`CAD_DB_*` / `DBCAD_*` 配置、协议 schema 和浏览器存储键保持兼容，避免影响已有模型、预算账本和登录/续作状态。历史报告与实验来源保留当时名称。Mac 本地目录改名时保留旧路径符号链接，以兼容虚拟环境、容器挂载与构建缓存。
