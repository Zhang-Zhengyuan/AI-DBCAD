# EXP-003 决策：AI-CAD-agent 底层框架选型

日期：2026-10-27。归属 Issue #26 / `feat/26-cad-agent-acis`。本文记录选型结论、证据与边界；付费运行原始记录在本机 `.local`（账本、trace、检查点），不入库。

## 决策

**主框架：官方 AgenticCADedit 完整决策循环（固定提交 `c084034cdcf462025a60ee70c5ba94577428e5ca`），经 `agenticcadedit-acis/1` 适配器接入 DBCAD/ACIS，作为 ResumaCAD 长期人机协同实验的 AI-CAD-agent 底层。** IterCAD 不作为接入对象，保留为 CadQuery 侧外部对照（本目录基础设施就绪，本轮付费调用 0）。DeciCAD/PhysCAD 无公开代码，CADIR 为 OCCT SDK，均不满足接入条件。

## 依据

### 1. IterCAD 结构性不匹配（源码审计，未运行其权重）

- 公开编辑入口 `process_itercad_edit` 消费**原始 CadQuery 建模程序**；DBCAD 的场景是导入/编辑 B-Rep（不可变 SAT 资产 + 版本链），没有也不应要求 CadQuery 程序历史。
- 其价值集中在 SFT/RL 权重：公开权重 `KnowledgeXLab/IterCAD-RL` 为 Qwen3_5 架构、BF16 共 10,349,929,472 字节，训练环境要求 transformers 5.2.0（本机研究环境 4.57.1 实测 `AutoConfig` 不支持）；未下载、未加载、未推理。
- 权重学习的是 CadQuery 代码语言；换成 ACIS 原生工具语言后权重作废，剩下的循环不优于现有适配器。

### 2. AgenticCADedit 与目标形态一致

持久会话、持续操作**当前几何**、面/边查询、选区高亮、小步执行、undo——正是"人可随时改模型、AI 在同一后端接着做"所需的交互面；且模型无关（本轮回 Qwen3.6-27B API）。

### 3. 端到端付费验证（2026-10-27，真实 ACIS worker + 真实付费模型）

| 运行 | 结果 | 调用 | 费用 |
|---|---|---:|---:|
| 首跑（provider usage 嵌套 dict 触发上游循环 `int+dict` 崩溃；已修复为扁平 usage） | failed，费用如实保留 | 1 | ¥0.0105 |
| T1 从零生成 60×40×8 底板 + Φ12 通孔 | ready → 应用 v1 | 7 | ¥0.1541 |
| T2 孔口上下两边 r=0.5 fillet + 高亮确认 | ready → 应用 v2 | 12 | ¥0.2926 |
| T3 阶段链（`parent_job_id` 继承 T2 完整对话，在 v2 上续作） | ready（未应用，见 T3 案例） | 4 | ¥0.2148 |

账本合计 **¥0.6720 / ¥300**，24 次调用全部 completed。任务链：inspect → list_edges → render_views（含高亮）→ run_step → validate → export_step → finish。

### 4. 独立几何审计（不采信模型自述）

- **v1**：trimesh 水密=True、euler=0（贯通孔拓扑）、bbox 精确 60×40×8、体积 18299.808 vs 解析 18295.221（0.0251% 曲面离散误差）。
- **v2**：水密=True、bbox 不变、体积较 v1 降 6.786 mm³（r=0.5 双孔口圆角的解析量级，含离散噪声）；ACIS 精确面积 6475.398→6459.440；面数 7→9、边数 14→16（每条圆角边净增一边），与 fillet 拓扑一致。
- **T3 案例**：指令"上表面中心加凸台"与中心孔几何矛盾；模型把 r=4 圆柱放进 r=6 孔内，形成不接触的双实体 BODY，validate() 通过且模型如实说明了这一状态。**valid ≠ 语义正确**——这是 ResumaCAD 课题要量化的验收问题，不是执行缺陷；该预览未应用。

## 已验证的链路（组件级）

- 官方 `BaseVLM._run_mcp` 循环原样运行（runner 子进程校验上游 HEAD 与 `base_vlm.py`/`vllm_openai.py` 无工作区改动）；MCP 八工具经 0700 临时目录内的 0600 Unix socket 回父进程执行，子进程不接触凭据。
- 受限原生语言（无 Python exec/import/文件/网络/任意属性访问）覆盖图元、变换、布尔、extrude/revolve/sweep、fillet/chamfer、面边查询与精确 distance/interference/validate。
- 真实选区高亮：`render_selection` 返回 FACE/EDGE 三角/折线 → VTK 红色叠加渲染；模型 3 次传错格式（索引）后按错误提示改传中心坐标成功——防索引跨拓扑误用是设计选择。
- 固定世界相机（原始场景 ∪ 当前预览的包围盒锚定），延续三 harness 研究观测探针的结论：自动 FitAll 会抹掉尺度/位置变化信号。
- run_step 失败原子回滚（几何+变量绑定+投影+undo 栈）、纯查询不消耗 undo、检查点持久化、跨进程跨段恢复（tests 24+6 项）。
- 预算：每调用先按完整上下文+输出预留再结算，缺 usage 保留预留；跨进程文件锁与准入平滑。
- 阶段链：`parent_job_id` 继承完整对话并显式声明版本切换（旧组件引用不保证有效）。

## 诚实边界（未验证/已知限制）

- 浏览器 UI 未在新框架下人工走查（后端契约测试通过；前端消费字段为超集兼容）。
- 工具批次中途崩溃的 exactly-once 执行未验证；正常 round_limit 的跨进程恢复有测试覆盖。恢复入口会拒绝把"带待执行 tool_calls 的消息"伪装成可续作检查点。
- `export_step` 输出在段私有临时目录，段结束即清理；需要交付用户下载时应先落持久资产（待办）。
- 纯检查类指令若模型 finish(success=true) 而几何未变，会被 no-op 守卫判 failed（已知语义限制，编辑任务下是正确防伪）。
- IterCAD/DeciCAD/PhysCAD/CADIR 的否定结论基于源码与公开元数据审计，未运行其权重；若后续出现可用代码或 ACIS 侧需求变化，重新评估。
- 本轮 3+1 个任务只证明链路与能力面，不构成对框架编辑质量的统计结论。

## 附录 A：官方 harness 逐组件对照（回应"到底接入了多少"）

**架构原则：官方循环代码一行不抄、原样执行。** `ai_agentic_runner.py`（全文件 114 行）在无凭据子进程里 import 固定上游 checkout，直接调用 `BaseVLM._run_mcp(...)`；每次运行前 `verify_upstream` 校验 HEAD 为 `c084034...` 且 `base_vlm.py`/`vllm_openai.py` 无工作区/暂存改动。上游循环本体（`base_vlm.py:838` 起，约 150 行核心：迭代、工具解析、图片回填、finish 拦截、轮数/退出、usage 累计）**全部来自上游文件**——我们代码里"循环部分"小，是集成方式的证据，不是缺失。

| 官方组件 | 上游位置 | ACIS 侧承载 | 保真度 |
|---|---|---|---|
| Agent 决策循环 `_run_mcp` | `src/vlms/base_vlm.py:838-`（文件共 1029 行） | 原样执行（runner 子进程直接调用） | 100% 上游代码，运行时强制固定提交 |
| VLM transport | `src/vlms/vllm_openai.py` | `NativeVLM(OfficialVLM)` 只覆写 `request_tool_turn`/`create_messages` 走 socket 回父进程统一计费 | 继承 + ~40 行胶水 |
| MCP server 八工具 | `cadquery_mcp/server.py`（8 个 `@mcp.tool`） | `app/ai_agentic_mcp.py` 同名同签名（含 `highlight_faces/edges: list[list[float]]` 中心坐标语义） | 契约保留；实现指向 ACIS |
| 持久建模会话 | `cad_session.py`（268 行：namespace、浅快照历史、run_step/undo/reset/inspect/list_faces/list_edges/render/export_step） | `app/ai_cad_facade.py` CadSession（647 行）：变量绑定、不可变快照 undo、原子回滚、跨段检查点；并修复上游浅拷贝在原地修改时不恢复的缺陷 | 语义 1:1 重写并加强 |
| 几何执行面 | CadQuery/OCP | `app/cad_contract.py`（162 行契约）+ `CAD_DB/worker/agent_geometry.inc`（496 行 ACIS：extrude/revolve/sweep/fillet/chamfer、distance/interference/validate、render_selection、STEP 导出） | 后端适配本体 |
| 渲染与高亮 | `cadquery_rendering.py`（OCP/Cocoa，`view.FitAll()`） | `app/ai_render_views.py` + worker 真实 FACE/EDGE 选择网格；**固定世界相机**（修正上游 FitAll 抹掉尺度变化信号的观测缺陷，依据三 harness 观测探针） | 等价+改进 |
| finish 客户端拦截 | 上游客户端约定 | runner `FINISH_TOOL` + `exit_reason`（finish/round_limit/no_tool_call） | 上游语义 |

**与"之前"（PR #11/#25 的 ~600 行原生适配器）的区别**：旧入口是自写 JSON 工具循环，8 个白名单动作（图元/变换/布尔/删除）、无面边查询、无高亮、无 fillet/sweep/extrude、无变量绑定与程序语言、自动 FitAll 三视图、2048 token。现入口运行上游官方循环，工具面为官方八工具 + 受限原生语言（覆盖官方演示节奏 inspect→定位→高亮→小步编辑→验证→finish），精确面/边分页查询、真实选区高亮、fillet/chamfer/extrude/revolve/sweep、distance/interference/validate、含变量绑定的 undo、跨段检查点、8192 token、阶段化连续对话。付费 T2 任务完整走了官方论文的节奏（list_edges→高亮渲染→fillet→validate→渲染→export→finish）。

**有意不做/尚未做到的（与"完整 CadQuery harness"的差距）**：run_step 是受限语言子集而非全量 CadQuery Python API（模型生成代码不经 Python exec，命令经校验走服务端权威路径——这是 DBCAD 架构红线）；官方 OCP 渲染器换为 VTK（多视图+高亮语义等价，风格不同）；worker 已有 shell 命令尚未暴露给 AI；浏览器 UI 未在新框架下人工走查。表达面扩展按任务需要继续，不宣称已覆盖 ACIS 全部能力。

## 复现入口

- 私有配置：`framework="agenticcadedit-acis"`、`agentic_upstream`（固定 checkout）、`render_python`（含 VTK/MCP 的研究 Python）；完整 schema 见本目录 [README.md](README.md)。
- API：`POST /api/v1/projects/{id}/ai/jobs`（instruction / base_version / selected / parent_job_id）；只读精确查询 `POST /api/v1/projects/{id}/geometry/query`。
- 测试：`tests/test_ai_agentic.py`（24 项）+ `tests/test_ai_official.py`（6 项）；设 `CAD_AGENT_UPSTREAM`/`CAD_AGENT_PYTHON` 后含官方源码真实子进程集成项。
- 审计：worker CLI `export --stl-out` + trimesh 独立核验（本目录 `geometry.py` 亦有离线评分）。
