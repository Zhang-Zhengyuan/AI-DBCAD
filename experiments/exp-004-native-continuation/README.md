# EXP-004：官方循环与原生 ACIS 的连续人机编辑

Issue #30。协议与代码准备阶段，结果尚未产生。研究会检验并可能否证“旧几何证据在人工改变共享模型后导致不满足约束的续作”，不预设成熟框架必须失败。

区别于 EXP-002：原样运行固定官方 `_run_mcp`，工具进入真实 ACIS；先完成十个实际需求阶段及五次以上几何编辑，再从真实前缀产物进行成对人工变化与 N/F/A 续作。并非把工具调用次数当作对话阶段数。详见 [PROTOCOL.md](PROTOCOL.md)。

两场景 × 五外部条件 × 三策略，共 30 分支，每分支两个续作阶段；每场景十阶段前缀只执行一次共享。每次运行另建 runs/，原始响应、模型、几何、账本不入 Git。内存版本存储隔离，ACIS 和官方 MCP 都是真进程；不宣称已验收 Neo4j 生产持久化。

```bash
# 使用已安装后端依赖的 Python，从仓库根运行
python -m unittest discover -s experiments/exp-004-native-continuation -p 'test_*.py' -v
python experiments/exp-004-native-continuation/pilot.py register --run experiments/exp-004-native-continuation/runs/NEW --config /path/to/private-config.json
python experiments/exp-004-native-continuation/pilot.py run --run experiments/exp-004-native-continuation/runs/NEW --execute
python experiments/exp-004-native-continuation/pilot.py summarize --run experiments/exp-004-native-continuation/runs/NEW
```

私有配置包含 `ai_config`（已有 official/native 配置）以及可选 models 数组，每项 label、pricing（模型名、确认的输入/输出价、上下文上限和来源）。所有模型、失败、探针与续作共享原授权总账 ¥300，已有费用与未确认预留不清空。未加 `--execute` 不调用模型。不得覆盖已有 prefix/分支或改写冻结文件后继续运行。

若主策略不出现确认旧证据误用，按实际结果报告；若强基线解决问题，不宣称需要复杂记忆系统。几何验收与明确阻塞说明分别保存，未执行及前序失败保留分母，服务/工具失败单列。
