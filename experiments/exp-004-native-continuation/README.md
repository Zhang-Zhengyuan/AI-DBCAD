# EXP-004：官方循环与原生 ACIS 的连续人机编辑

Issue #30。2026-10-28 首轮运行已结束：[精选结果](results/20261028/RESULTS.md)、[逐分支指标](results/20261028/trials.csv)、[来源与哈希](results/20261028/manifest.json)。Qwen3.6-27B 完成 30/30 分支；Qwen3.8-Max 完成 18/30，11 个预算截断、1 个未调用工具结束。完整分支最终几何 48/48 正确，9 个分支曾出现随后修复的私有预览间隙违规。**本轮未确认旧几何记忆误用，不能用来证明成熟框架必然存在该问题。**

区别于 EXP-002：原样运行固定官方 `_run_mcp`，工具进入真实 ACIS；先完成十个实际需求阶段及五次以上几何编辑，再从真实前缀产物进行成对人工变化与 N/F/A 续作。并非把工具调用次数当作对话阶段数。详见 [PROTOCOL.md](PROTOCOL.md)。

两场景 × 五外部条件 × 三策略，共 30 分支，每分支两个续作阶段；每场景十阶段前缀只执行一次共享。每次运行另建 runs/，原始响应、模型、几何、账本不入 Git。内存版本存储隔离，ACIS 和官方 MCP 都是真进程；不宣称已验收 Neo4j 生产持久化。

本轮两模型共预分配 160 个用户阶段，保留全部 60 个分支分母。正式实验费用（包含前缀与未完成阶段）合计 ¥282.647874；历史与探针一起的总账占用 ¥287.780844，未超过既有 ¥300 授权。费用按原登记输入价保守估计，不释放尚未核实的缓存折扣。

```bash
# 使用已安装后端依赖的 Python，从仓库根运行
python -m unittest discover -s experiments/exp-004-native-continuation -p 'test_*.py' -v
python experiments/exp-004-native-continuation/pilot.py register --run experiments/exp-004-native-continuation/runs/NEW --config /path/to/private-config.json
python experiments/exp-004-native-continuation/pilot.py run --run experiments/exp-004-native-continuation/runs/NEW --execute
python experiments/exp-004-native-continuation/pilot.py summarize --run experiments/exp-004-native-continuation/runs/NEW
```

私有配置包含 `ai_config`（已有 official/native 配置）以及可选 models 数组，每项 label、pricing（模型名、确认的输入/输出价、上下文上限和来源）。所有模型、失败、探针与续作共享原授权总账 ¥300，已有费用与未确认预留不清空。未加 `--execute` 不调用模型。不得覆盖已有 prefix/分支或改写冻结文件后继续运行。

若主策略不出现确认旧证据误用，按实际结果报告；若强基线解决问题，不宣称需要复杂记忆系统。几何验收与明确阻塞说明分别保存，未执行及前序失败保留分母，服务/工具失败单列。

分析入口 `analysis/audit.py` 不调用模型，不覆盖冻结评分。可将两个模型的续跑映射到各自原始 run，以同一账本和显式人工复核记录生成结果：

```bash
python experiments/exp-004-native-continuation/analysis/audit.py \
  --source qwen36-27b=experiments/exp-004-native-continuation/runs/20261028-native-v1-s20261028 \
  --source qwen38-max=experiments/exp-004-native-continuation/runs/20261028-native-v1b-max-s20261028 \
  --ledger /path/to/original-ledger.json \
  --annotations experiments/exp-004-native-continuation/results/20261028/adjudications.json \
  --output .local/exp004-analysis
python -m unittest discover -s experiments/exp-004-native-continuation/analysis -p 'test_*.py' -v
```

原始 `record.status=completed` 仅表示脚本记录了两个阶段，不保证模型完成。分析以真实 `finished`、费用和暂停原因单独判定。原始自动文本判据有一处漏识别“拒绝”，保留原 false 并另列人工复核 true。源码冻结后发现的交互修复（保留模型无工具文本、人工回答后继续、显式 overview 全景视图）在运行结束后提交，**没有回填或宣称已被本次付费结果验证**。
