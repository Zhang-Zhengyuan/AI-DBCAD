# neuralCAD-Edit 研究工作区

后续工作统一在当前 ResumaCAD 仓库中开展。本目录用于独立研究复现，不修改正在运行的 DBCAD 产品链路。

建议先看 [论文与代码阅读指南](PAPER_AND_CODE.md)，再打开 [阅读 notebook](reading.ipynb)。当前阶段完成环境、数据和离线校验；模型实验等你研究后再开始。

## 当前已准备好

- 官方源码固定到 `68d1acceda0743e364f45fb7823f7cf39c530ba5`，位于 `.local/upstream/`，保持未修改。
- 完整官方数据压缩包 6,029,961,729 字节已下载并核验 SHA-256，解压至 `.local/data/`。
- 独立 ARM64 Python 3.12 环境与 `uv.lock`；包含 CadQuery/OCP、Cocoa/VTK 渲染、Open3D、probreg、DINO、视频解码及三家模型 SDK。
- 发布数据库的独立工作副本位于 `.local/workspace/`，原始数据库和资产保留。
- 固定版本 DINOv2 权重已缓存，可离线提取特征。
- 192 个请求、960 条编辑记录（384 次专家编辑和三组各 192 条模型记录）、1152 条 B-Rep 记录、8122 条评分已能读取。
- 真实 text/easy 样本的 STEP → 官方 harness → STEP/STL → 七视图 → 几何/特征 sanity 与视频解码通过。
- 官方 leaderboard notebook 的评分单元已对全部发布记录重新汇总；五组各 192 条，主要几何指标和 Acceptance 与论文一致，少数评分项存在 0.01 的发布数据差异，详见 [验收记录](VALIDATION.md)。

以上不代表重新调用模型生成了论文结果。当前 API 调用为零，也没有组织新的专家打分。

## 先使用这些入口

在本目录执行；从仓库根目录进入可用 `cd research/neuralcad_edit`。

```bash
./run.sh doctor       # 版本、原生模块和配置检查
./run.sh catalog      # 数据量、模态和难度
./run.sh smoke        # 真实样本：STEP、七视图、视频、指标 sanity
./run.sh features     # 实际运行官方 DINO 提取器；使用本地缓存与 CPU
./run.sh leaderboard  # 汇总作者发布的输出与评分，无 API 调用
```

这些命令不会运行付费编辑或 VLM 打分。smoke 为输入模型的直通脚本，不对真实编辑请求评分，不代表模型能力。输出在 `.local/runs/`；最近摘要在 `.local/{doctor,catalog,smoke,features,published-leaderboard}.json`，排行榜另有 CSV。

研究 notebook：

```bash
PYTHONPATH="$PWD/.local/upstream:$PWD" .local/venv/bin/python -m jupyterlab \
  --notebook-dir "$PWD" --ip 127.0.0.1
```

然后打开 `reading.ipynb`。也可以直接阅读上游的 `src/notebooks/visualise_examples.ipynb` 和 `leaderboard.ipynb`，其配置路径要改为本目录 `.local/config.local.json`；优先在本地副本里编辑 notebook，保持固定上游源码干净。

## 以后开始模型实验

先生成单任务运行计划，默认不会发送请求：

```bash
./run.sh infer --request ZK22J6VYRKQ2RTFD_1758875163.609787 \
  --model gpt-5.2_cadquery-script --max-iters 10
```

这会从官方 `val_edit_all.parquet` 选择恰好一个任务，保留发布输入，并使用字段白名单排除 GT 输出。准备文件在 `.local/tasks/`，模型输出使用 `.local/model_outputs/`。计划只显示所需凭据变量是否存在，不打印值。

等确定要开始实验后，在当前终端设置相应的 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 或 `GEMINI_API_KEY`，对同一命令加 `--execute` 才会调用官方模型 harness。支持的配置键为 `gpt-5.2_cadquery-script`、`claude-sonnet-4.5_cadquery-script`、`gemini-3-pro_cadquery-script`。

API 的账户权限、型号可用性、额度和真实响应尚未执行验证。配置中的型号和费用字段来自官方发布版本；开始实验时需要记录实际服务端版本与账单，不把历史估计当作当前价格。

生成新 STEP 后可用上游 `src/scripts_preprocess/cadquery_convert.py` 生成 STL/七视图，再按上游导入流程评价。运行批量评价时使用本地工作副本和关闭付费 rater 的配置；细节和实现差异见阅读指南。新的人工 Acceptance 仍需专家评价。

## 从零重建

需要 Apple Silicon Mac、Xcode 命令行工具、Git 和 uv。在本目录执行：

```bash
./setup.sh
```

setup 固定上游提交，按锁文件安装 `.local/venv`，下载并校验完整数据、缓存固定 DINOv2 权重、建立工作副本和本地配置。首次需要联网；如终端需要代理，先设置 `HTTPS_PROXY` / `HTTP_PROXY`。数据下载支持分块重试和续传，最终仍以完整文件 SHA-256 为准。

上游的 vLLM 本地 GPU 推理与 Ray 依赖没有纳入这个 API/CadQuery 环境；当前三家云模型适配路径均可导入，不需要这些组件。额外补齐了上游代码实际使用、但原环境文件未列出的 transformers 和 probreg。依赖版本是本机验证版本，不声称是作者原机器的锁定环境。

源码、环境、数据、权重、运行结果和凭据均留在 `.local/`，不入本仓库 Git；只提交脚本、锁文件、notebook 和说明。暂存下载分块在 `.local/downloads/chunks/`，完整归档校验成功后可自行删除这些分块来释放空间，保留 ZIP 即可再次解压。
