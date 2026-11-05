# 三 harness 研究与原生 AI 编辑

所有操作从 AI-DBCAD 根目录运行。本目录复用 `research/agenticcad_edit/.local/venv`，本机数据、模型输出、API 记录和账本在 `.local/`，不入库。本实验已登记为 [EXP-001](../../experiments/exp-001-three-harness/README.md)。主报告为 [REPORT.md](REPORT.md)，精选文本指标在 `experiments/exp-001-three-harness/results/`；PDF、图片和重新生成的输出在本地忽略的 `output/`，不会随克隆分发；研究问题和否证条件见 [RESEARCH_DIRECTIONS.md](RESEARCH_DIRECTIONS.md)。历史报告保留当时的 InferraCAD 名称，当前选题与实现状态以[根 README](../../README.md)为准。

## 环境与第三方代码

先按 [AgenticCADedit 环境说明](../agenticcad_edit/README.md) 安装已锁定的 ARM64 环境与 neuralCAD-Edit 数据。本轮增加 trimesh（只用于独立网格审计），锁文件在该环境目录内。运行 `research/agenticcad_edit/setup.sh` 同步依赖。

第三种方法是 [KnowledgeXLab/IterCAD](https://github.com/KnowledgeXLab/IterCAD)，固定提交 `55998c4a4537573f67ca241dedc52ee2840652ca`（Apache-2.0），克隆至 `research/harness_study/.local/itercad`。本地执行其公开 `eval/evalution.py` 的 `process_itercad_edit`，不重写算法循环；API transport 与 STEP 入口适配在 `experiment.py`。

这里比较 **harness + 同一 Qwen3.6-27B API**，不加载 IterCAD 的 SFT/RL 权重；其源码任务原本要求 CadQuery 源码，适配器以导入输入 STEP 的最小程序开始，并提供同样的多模态请求。无 ground-truth 选择、无 best-of-K、无真值反馈。结果不得冠以原论文模型复现分数。

```bash
git clone https://github.com/KnowledgeXLab/IterCAD.git research/harness_study/.local/itercad
git -C research/harness_study/.local/itercad checkout --detach 55998c4a4537573f67ca241dedc52ee2840652ca
```

已有 checkout 时先检查状态，勿覆盖本地改动。其他两种源码提交固定在 `experiment.py` 的计划中；第三方仓库保持未修改。

## 有预算的运行

先把凭据放在 `research/agenticcad_edit/.local/provider.env`（权限 600），不能提交或打印 Key。把已核实的价格写入本目录 `.local/pricing.json`，格式见 `../agenticcad_edit/pricing.example.json`。每个新的授权预算使用新的账本路径，禁止删除旧账本以“恢复额度”。

本轮代码授权上限为 ¥100，所有生成、打分、观测诊断和原生 AI 共用 `.local/budget.json`。预留后才能请求；调用失败/没有 usage 时保留预留，不自动重试。账本模块在 `systems/dbcad/CAD_DB/backend/app/ai_budget.py`，两个入口共用同一文件锁。

```bash
research/harness_study/run.sh register
research/harness_study/run.sh batch --workers 8
research/harness_study/run.sh evaluate judge-plan
research/harness_study/run.sh evaluate metrics
research/harness_study/run.sh evaluate judge
```

`register` 会创建新的计划和 current 指针；同一账本不会自动清零。`batch` 只运行当前计划，同一 episode 有独占锁，已有结果跳过；存在未完成的付费记录时拒绝悄悄重跑。`--run /绝对路径` 可选择旧计划。`metrics/judge --watch` 可等待正在运行的 episode。结束时停止不再需要的 watch 进程。

正式协议：24 个分层平衡任务、三个方法、每方法每任务最多 6 次调用、4096 输出 token、temperature 0.6、top_p 0.95、0.5 FPS。同样的调用次数不等于同样的推理量或操作数；费用、真实调用与退出原因必须一起报告。脚本基线只补传原执行器已支持的 `input_file`，保留原先的无状态全程序循环及错误/渲染反馈行为。

最初 36 任务/40 轮的运行属于诊断阶段：输入路径遗漏与按费用异常停止会阻断结果导出。修改协议时保留原始输出、停止原因和不确定费用，重新登记正式计划，不把诊断分数与正式分数混在一起。总费用包含诊断阶段。

离线指标使用 10,000 个表面点、无配准的双向非平方 CD；用 cKDTree 求与官方 dense cdist 相同的最小距离，启动时做数值等价断言。IoU 保留原版“表面体素索引集合”实现，不把它说成实体体积 IoU。DINOv2 使用固定本机权重及相同渲染。缺失输出进入全任务分母，不能只展示成功样本的均值。

自动评分沿用发布代码的 1–7 rubric；方法名对判定模型隐藏，不提供参考答案。主代理通过率还要求正体积有效实体、无游离面。它是同系列单 VLM 的代理评价，**没有**替代五位专家的人类 Acceptance。

## 观测通道诊断

```bash
research/harness_study/run.sh observation register
research/harness_study/run.sh observation generate
research/harness_study/run.sh observation judge
```

12 个确定几何状态对 × 三种反馈方式，共 36 次盲判。自动 FitAll、固定世界相机、自动 FitAll+精确包围盒三个条件比较可观测性；不把这项判定实验算进三 harness 的 72 次编辑，也不声称它证明了端到端算法改进。注册、原始图片和 API 回应保存在 `.local/observation-probe`。

## AI-DBCAD 原生 AI 使用

打开本机 AI-DBCAD 网页，创建/打开项目，在右侧 AI CAD 输入文字，生成预览，再选择应用或放弃。支持立方体/球/柱/锥/环、平移、旋转、缩放、删除和布尔操作；当前没有原生倒角/圆角工具，也没有把全部 CadQuery MCP 工具移植进来。

`CAD_DB_AI_CONFIG` 指向一个私有 JSON。字段为 `base_url`、`credentials_file`、`ledger`、`limit_cny`、`pricing`、`render_python`，可选 `max_calls`（每执行段默认 20、最多 40）、`job_soft_limit_cny`（单任务累计软限，默认 4）、`trace_dir`。凭据文件仍使用 `CAD_API_KEY=...`；API 必须 HTTPS。本机启动器检测 `.local/macos/ai.json` 后自动设置路径。没有这个文件时 AI 关闭，基础 CAD 功能不受影响。

`enforce_budget` 默认为 `true`，兼容有明确金额授权的既有研究配置。日常服务可明确设为 `false`：总额、provider 子上限、单任务软限和超预留结算不再因金额中止，但继续使用原账本原位记录已知费用与未知预留；无需也不得清空旧费用。状态 API 用 `budget.enforced=false` 与 `limit_cny=null` 表达不限金额，账本中原 `limit_cny` 不改写。未确认调用仍不自动重放，分段轮次、版本检查和提供方错误处理保持有效。独立实验配置未显式关闭时继续执行原预算。

`render_python` 指向上述含 VTK 的研究环境；API 进程无需安装 VTK/OpenAI SDK，也不导入 ACIS。AI 的独立 worker session 只生成候选 SAT/GLB/图资产，用户应用时才检查捕获的 `base_version` 并发布一次版本。每段调用用尽后暂停，网页显示未完成的中间预览，可补充要求并点击“继续生成”；同一任务可以分多段超过累计 40 次调用，仍受原累计费用和全局账本限制。未完成预览不能应用。项目版本已被人工改变时，继续返回 409，不覆盖新模型。失败、停止或放弃不提交候选；已有组件资产不被覆盖。

配置 trace_dir 时，私有完整检查点保存在 trace_dir/checkpoints/（权限 600），已暂停/已完成任务在服务重启后可恢复；未配置时仅支持当前进程内续作。运行中的任务遇到服务停止仍取消。旧公开失败轨迹只读保留，不能冒充完整续作检查点。已应用版本持久保存。当前不支持跨服务器/多 uvicorn worker 共享任务；部署此功能使用单 API worker。

已有版本上的 AI 编辑支持原有撤销/重做；从全空项目创建首个版本仍遵循底座既有行为（没有可恢复的父版本），应用前可以放弃预览。

汇总与报告：`research/harness_study/run.sh summarize` 固定结果后，用 `create_report.py --markdown-only` 生成文字版；PDF 生成需要 reportlab/Pillow。`audit_proxy.py` 对全部代理通过候选做后验包围盒核查。本轮发现其中两例有明显异常，原始代理分数完整保留；工程几何指标排序与语义成功率不能混用。
