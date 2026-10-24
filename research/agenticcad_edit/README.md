# AgenticCADedit 本机复现

工作目录为 InferraCAD 下的 `research/agenticcad_edit`。这里直接运行作者的工具与两套 agent 循环；本地代码负责固定环境、限制 API 预算、保留实验记录与独立评价。论文和代码导读见 [PAPER_AND_CODE.md](PAPER_AND_CODE.md)，实际结果见 [VALIDATION.md](VALIDATION.md)。

## 固定版本与入口

- 官方代码：<https://github.com/agenticCADedit/agenticCADedit>，提交 `c084034cdcf462025a60ee70c5ba94577428e5ca`，位于 `.local/upstream`，保持未修改。
- 论文：<https://arxiv.org/abs/2609.29621v1>。
- Python 3.12 ARM64、CadQuery 2.6.1、OCP 7.8.1.1.post1、MCP 1.30.0；完整依赖由 `uv.lock` 固定。
- 数据复用相邻 `neuralcad_edit/.local/data/edit_192_external` 的已校验官方发布包。推理不导入数据库、不写回数据，输出留在本目录 `.local/runs`。评价阶段读取相邻独立工作数据库取得参考模型。
- 代码 MIT；原 neuralCAD-Edit 数据仍遵循其 CC BY-NC 4.0 许可。

在本目录运行：

```bash
./setup.sh
./run.sh doctor        # 固定提交、依赖、数据存在性
./run.sh smoke         # 真实 STEP + 官方 MCP：状态、查询、高亮、失败恢复、撤销、导出
./run.sh published     # 核对作者发布的 Qwen 几何结果，无 API
./run.sh diagnostics   # 复现原地修改 Assembly 时的浅拷贝回滚局限，无 API
.local/venv/bin/python -m unittest discover -s . -p 'test_*.py' -v
```

第一次启动原生依赖可能较慢。官方渲染器已经有 Cocoa 路径，本机验证直接使用它，没有替换为另一套绘图库。Linux/CUDA Docker 和本地 vLLM 不属于本机 API 路线。

## 双 harness API 实验

把 `provider.env.example` 复制到 `.local/provider.env`，填本地密钥并执行 `chmod 600 .local/provider.env`。示例不包含真实凭据。价格配置为 `.local/pricing.json`，从 `pricing.example.json` 建立，未来实验前重新核实。

先查看计划，默认不发送请求：

```bash
./run.sh paired \
  --request ZK22J6VYRKQ2RTFD_1758875163.609787 \
  --request F332D3FXML85WLR2_1769424096.996583
```

加 `--execute` 才调用 API。默认两边都是最多 12 轮、每轮最多 8192 输出 token；使用上游 Qwen 提示、温度 0.6、top_p 0.95、thinking 开启、视频 0.5 fps。这是两任务冒烟，不是论文完整 192 题的等条件重新生成。接口中的模型 ID 不代表我们已核验其底层权重版本。

费用上限默认人民币 30 元，所有调用（含能力探针）共用 `.local/budget-pilot.json`。每次先按完整 256K 上下文和最大输出预留费用，返回后按提供商报告的 prompt/completion 用量结算；不重复加上已包含在 completion 中的 reasoning token，不假设计费缓存折扣。没有 usage 或失败的请求保留全额预留，无自动网络重试。费用是按用户提供的平台价格估算，最终账单仍以提供商为准。

原始 request/response、脚本、MCP 对话图片、STEP 和 settings 均保存在对应 task/arm 下。原始 `token_counts.cost_estimate` 有重复计入 thinking 的问题，费用分析使用 `evaluate.py` 的 API usage 汇总和预算账本。

仅对 **零 API 调用且没有 STEP 的基础设施失败**，可在修复后补跑原目录；保留失败记录，禁止覆盖已尝试的模型实验：

```bash
./run.sh paired --request REQUEST_ID --arm mcp \
  --resume .local/runs/PAIRED_RUN_DIRECTORY --execute
```

模型失败需要另建试验并明确理由，不能用 resume 挑选更好的答案。不要删除预算账本来绕过本轮额度。

## 输出与查看

实验完成后，在本目录运行独立评价：

```bash
./run.sh evaluate
```

默认评价 `.local/latest-paired.json` 指向的实验；可用 `--run /absolute/path/to/run` 指定。每题生成 `comparison.png`（输入／Script／MCP／专家参考）、七视图、STL、真实 B-Rep 有效性、几何数量、CD、原始表面 voxel IoU、共同渲染的 DINO 相似度与 token/费用汇总。参考模型只在这一步加载，不进入推理提示。

CD 使用原始 10,000 点、双向 Euclidean 距离求和，不对齐、不归一化；固定采样 seed 42。原始 voxel IoU 是表面体素索引重合，不是填充实体体积 IoU。DINO 是固定权重的全部 patch/token 特征展平后的余弦值；此处对所有方法和参考统一重新渲染，属于共同渲染诊断，不能冒充作者缓存特征的逐项复算。没有新增付费评分或人类 Acceptance。

官方源码检出、数据引用、凭据、模型输出及缓存都在忽略的 `.local` 中；只有复现脚本、依赖锁和说明进入 InferraCAD Git。
