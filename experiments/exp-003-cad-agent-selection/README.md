# EXP-003：完整 AI-CAD agent 选型与 ACIS 接入验收

本目录属于 Issue #26 / `feat/26-cad-agent-acis`。实验定义和可复用代码拟入 Git；运行、原始 API 输出、几何与报告留 `runs/`，不自动提交。协议见 [PROTOCOL.md](PROTOCOL.md)。

状态口径：工程选型已完成并记录于 DECISION；experiment.json 的 planned 指原计划的候选能力对照与 neural192，二者未运行，不表示工程接入未完成。以下“下次恢复”列表仅属于该候选对照草案；当前连续编辑主线移至 [EXP-004](../exp-004-native-continuation/README.md)。DECISION 中费用为当时验收快照，当前累计应读取授权账本，不能沿用旧快照推算剩余额度。

## 当前阶段（2026-10-27）

- 原始 AgenticCADedit／IterCAD 固定版本及真实协议已审计。
- **选型已定：官方 AgenticCADedit 循环经 `agenticcadedit-acis/1` 适配器接入 DBCAD/ACIS；IterCAD 不接入，保留为外部对照。** 结论、付费端到端证据（4 次运行 ¥0.6720/¥300，含真实 fillet/高亮/阶段链与独立几何审计）与诚实边界见 [DECISION.md](DECISION.md)。
- 实现 12 条两阶段任务链、官方循环 transport 接入、只读请求 loader、离线几何评分、追加式运行目录和分母保留。
- **24 个参考阶段的实体构造及 STEP 往返离线验收通过**，记录在 `runs/offline-20261027-b/offline.json`；这不是模型实验结果。
- `runs/offline-20261027-a/` 保留首轮扫掠管评分数值问题，不删除失败；随后修正体积容斥测量。
- 本目录研究侧付费调用仍为 0（接入验证的费用记在 DBCAD 原生 AI 账本）；neuralCAD 192 请求对照未运行，模型/预算冻结后另行登记。
- 未执行 neuralCAD 全部 192 请求、未将本目录结果包装为完整论文复现。
- 官方 IterCAD 权重只读取了公开元数据，未下载、未加载、未推理。本机研究环境不支持其架构，详见下方。

## 运行入口

使用已有含 CadQuery/OCP/MCP 的研究 Python，从仓库根目录运行。所有原代码、模型和数据位置通过私有配置传入，不在共享源码硬编码机器路径。

```bash
python experiments/exp-003-cad-agent-selection/experiment.py --help
python -m unittest discover -s experiments/exp-003-cad-agent-selection -p 'test_*.py' -v
python experiments/exp-003-cad-agent-selection/experiment.py offline --output experiments/exp-003-cad-agent-selection/runs/NEW-offline-run
python experiments/exp-003-cad-agent-selection/experiment.py register --config /path/to/private-config.json --run experiments/exp-003-cad-agent-selection/runs/NEW-paid-run
python experiments/exp-003-cad-agent-selection/experiment.py batch --run /path/to/registered-run --track capabilities
```

最后一条默认只打印计划。**现阶段不要添加 `--execute`**：必须先完成零费用 transport 集成验证和配置冻结。`stage`/`neural` 命令也要求显式 `--execute` 才可能调用模型。

私有配置字段：

```json
{
  "seed": 20261027,
  "budget_cny": 300,
  "ledger": "/path/to/root-managed-new-ledger.json",
  "credentials_file": "/path/to/provider.env",
  "upstreams": {"agentic": "/path/to/official-agentic", "itercad": "/path/to/official-itercad"},
  "data_dir": "/path/to/edit_192_external",
  "candidates": ["agentic", "itercad"],
  "models": [{
    "label": "registered-model-label", "model": "provider-model-alias",
    "max_tokens": 8192, "temperature": 0.6, "top_p": 0.95, "thinking": true,
    "agentic_max_calls": 40, "itercad_max_calls": 5,
    "pricing": {"context_tokens": 262144, "input_cny_per_million": 3, "output_cny_per_million": 18}
  }]
}
```

这里只展示 schema，价格及模型必须由根任务按实际已核实配置填写；不能照抄示例值代表所有模型。可选 `transport_factory: {"path": "/path/to/private-factory.py", "function": "create_transport"}`，工厂签名为 `factory(config, model, output, label)`，返回支持 `create(**kwargs)` 的对象，由根任务统一管理多模型价格、预算与授权。

每次 `register` 保存源码哈希与所有任务。注册后改源码必须新建 run，不能偷偷修改原 run。`batch` 不覆盖任何已有轨迹；`score`/`summarize` 在离线统计中保留缺失与失败。

`select --run ... --model ... --candidate ... --reason ...` 保存一次候选决策后，才允许该候选执行 `batch --track neural192`。当前只实现该全覆盖请求队列及最终 solid 可用性检查；原论文全套 CD/IoU/DINO 与专家语义验收尚待补齐。

## 权重与本机运行限制

公开权重：`KnowledgeXLab/IterCAD-RL`，revision `9512ae2e62da03fb8cd1e2d68d965fba7afa0e6a`，非 private、非 gated。经 HTTPS 验证读取 index，BF16 三个 safetensors 分片总计 **10,349,929,472 bytes**，架构 `Qwen3_5ForConditionalGeneration`。没有下载这些分片。

本机 24 GiB 统一内存，MPS 可用；现有研究环境 Python 3.12.13、torch 2.8.0、transformers 4.57.1。`AutoConfig.for_model('qwen3_5')` 实测不支持，官方训练环境要求 transformers 5.2.0，因此不能在现环境宣称已复现权重。

磁盘可用约 645 GiB。检查时空闲页约 94 MiB、压缩驻留约 9.6 GiB、wired 约 4.8 GiB，内存压力明显。不能据“权重约 10 GB”就认定此刻可以安全加载；还需模型加载峰值、KV cache、多模态激活和 CAD 并存空间。未安装新环境、未启动模型服务；后续应在独立环境和较空闲条件下测试短上下文单实例，不能升级现有研究环境或让加载失败影响在用 CAD 服务。

## 下次恢复的明确未完成项

1. 完成有限单元／反例检查后把确切数量和命令写入阶段报告。
2. 用根任务统一 transport 做零费固定响应测试，验证官方循环、图像、工具错误、预算异常及最终导出；目前此部分未运行。
3. 冻结实际模型、价格、调用额度和总预算，注册新 run，再开始真实同模型两框架对照。
4. 在不覆盖旧轨迹的条件下接续 A；依据能力和 ACIS 适配完整性做一次记录明确的选型。
5. 决定是否下载官方 IterCAD 权重并准备独立依赖环境；本地服务接口目前尚未实现。
6. 补齐 neural192 的独立参考指标与语义验收说明，再执行预算可覆盖范围；不足时明确预算截断，仍保留 192 分母。
7. ACIS 侧的同任务验收、失败原子性、人工版本变化与恢复，由主接入任务完成，不由本实验伪造。
