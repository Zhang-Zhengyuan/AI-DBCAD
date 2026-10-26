# EXP-002 · 跨版本证据失效预实验

状态：**completed，2026-10-26 完成首轮受控检查点实验**。正式 24 个恢复分支，另有独立开发和两个事后诊断；不是自然长期编辑链，候选证据维护方法尚未实现或验证。

研究方向：持续人机 CAD 编辑中的版本感知几何证据维护与可靠续作。检验旧几何观测在外部编辑后是否进入错误行动，与当前状态按需补查和完整相关查询刷新比较。

## 实际结果与解释

| 策略 | 最终任务验收 | 曾进入违规目标状态 | 恢复 API 费用 |
|---|---:|---:|---:|
| N：完整旧历史续作 | 5/8 | 4/8 | ¥1.179744 |
| F：清除旧观测，按需补查 | 8/8 | 0/8 | ¥1.114950 |
| A：完整任务相关查询族刷新 | 8/8 | 0/8 | ¥1.759491 |

N 的四个有影响变化分支均先执行后重验或未重验，其中一个后来恢复正确。三个最终失败中，slider-E3-N 公开重复了过期的 5 mm，而当前独立真值为 0 mm、交叠约 16 mm³；另两个涉及先执行、后检测及撤销/恢复不足，具体旧数值使用归因 unknown。不能把全部失败说成已确认的同一种记忆错误。

本轮全部 194 次模型调用按登记单价计账 ¥5.207439，包括失败/开发预检、4 个开发恢复、正式检查点、24 个正式恢复和两个事后诊断；100 元上限，未结算/未知费用调用 0。价格为用户 2026-10-25 提供的输入 3 / 输出 18 元每百万 token，金额不是提供方最终账单。

事后诊断保留同一旧历史并增加一般提醒：版本回声组最终修正但曾先违规，具体当前测量组在动作前阻止；各一次，不进入正式分母，不作为平均因果效应。公开轨迹归因由执行 agent 审阅，尚无独立人类盲审；几何与费用另由程序验算。

结果支持受控机制存在和继续研究动作前证据/计划重验，不证明工业发生率、其他系统普遍失败或复杂记忆系统优越。F 是便宜且有效的强基线。见 [冻结协议](PROTOCOL.md)、[精选摘要](results/summary.json)、[全部逐格指标](results/trials.csv)、[归因标注](results/annotations.json)、[来源与哈希](results/manifest.json)。协议中的“人工核对”实际由执行 agent 逐条审阅，并非另有独立人类标注员。

## 实现与验证

- pilot.py：固定 AgenticCADedit 官方 MCP 服务器与 BaseVLM 工具编排上的预检后暂停及 N/F/A 策略。上游 c084034cdcf462025a60ee70c5ba94577428e5ca 未修改；这是客户端适配，不是原论文完整持续任务复现。
- scenes.py：两类场景、解析间隙/OCP 核对、最终 STEP 对称差评分；不判断连续运动路径。
- analyze.py：全部分配分母、中间 STEP、唯一账本及显式 unknown 标注。
- probe.py：独立事后“提醒/具体测量”诊断，共用原账本，无重试。
- figures.py：生成静态科学图，只写本地 runs/。
- test_pilot.py：真实 MCP/内核离线验证，不调用模型 API。

```bash
research/agenticcad_edit/.local/venv/bin/python -m unittest discover -s experiments/exp-002-evidence-validity -p 'test_*.py' -v
research/agenticcad_edit/.local/venv/bin/python -m unittest discover -s research/agenticcad_edit -p 'test_budget.py' -v
python3 scripts/experiments.py check
```

实测分别通过 4 项、6 项及登记/哈希检查。未改 DBCAD/ACIS/数据库/浏览器协议，未重跑这些模块的全栈验证。

已有原始运行的再分析不再调用 API：

```bash
research/agenticcad_edit/.local/venv/bin/python experiments/exp-002-evidence-validity/analyze.py --run experiments/exp-002-evidence-validity/runs/20261026-agentic-v1-s20261026
research/agenticcad_edit/.local/venv/bin/python experiments/exp-002-evidence-validity/figures.py --run experiments/exp-002-evidence-validity/runs/20261026-agentic-v1-s20261026
```

## 新运行与冻结

原始运行不在干净 clone 中。新一批付费实验须另有明确预算授权；默认参数和本轮剩余额度不构成下一批花费授权。

使用 pilot.py init --run 新的 runs/目录；checkpoint --scene pin|slider [--dev] 生成检查点；trial --scene … --event E0|E1|E3|E4 --policy N|F|A [--dev] 执行一个恢复分支。先离线检查、开发及核对真实测量/未改几何，再将 run.json 的 SHA-256 与 source_hashes 写入 freeze.json，随后 batch --run … 执行固定分配。batch 核对冻结哈希，已有尝试不重试；单分支也拒绝覆盖。AST 暂停条件不等于语义正确，必须逐条核对观测。

本次实际按固定 seed 队列最多并行两个独立 MCP 分支，共享文件锁账本，精确脚本保存为本地 execute-primary.py。模型采样 seed 未指定；20261026 只固定队列。正式输出上限 4096、预检最多 8 次、恢复最多 12 次调用。开发修订、旧配置和失败都保留。

## 本地产物与收录

原始目录：runs/20261026-agentic-v1-s20261026/。名称 v1 是初始容器名，正式冻结协议为 v4，不重写旧记录。

完整报告：docs/research/EXP002_AGENTICCADEDIT_RESULTS_20261026.md；三分钟开题稿：docs/research/EXP002_OPENING_TALK_20261026.md。包含全部结果、研究启发、可说/不可说结论、图和答辩准备。

代码、冻结协议和脱敏小指标具有共享复现价值，汇总为 Issue #20 的一个交付单元。报告/讲稿、图/PDF、模型、API 原始记录、账本保持本地忽略。回滚代码提交不删除原始运行或账本，不涉及数据库恢复。
