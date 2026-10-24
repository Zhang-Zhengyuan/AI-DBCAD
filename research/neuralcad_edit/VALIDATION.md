# 本机复现验收（2026-10-24）

执行范围为本目录，平台 macOS 26.6.2 / Apple Silicon ARM64。实际包版本由 `.local/doctor.json` 和提交的 `uv.lock` 记录。所有校验均未调用付费模型 API。

## 固定输入

- 源码：AutodeskAILab/neuralCAD-Edit，`68d1acceda0743e364f45fb7823f7cf39c530ba5`。
- 论文：arXiv:2604.16170v1，PDF 21 页，已核读正文、图表、评价 rubric 和相关代码。
- 数据：Hugging Face `autodesk/neuralCAD-Edit`，revision `b29220c3ccceecd9188ab42f1688e4e6bf5d7a7d`。
- 完整 ZIP：6,029,961,729 字节，SHA-256 `53c8ae0f7de19d280de48ceae770a3332cc2a3a675eafb5e1a7582ca5c527566`。下载结束校验通过，解压时 ZIP CRC 检查通过。
- DINOv2：`facebook/dinov2-base`，revision `f9e44c814b77203eaa57a6bdbbd535f21ede1415`，权重已缓存。

## 已执行

`uv sync` 安装并生成锁文件；`uv sync --frozen --offline` 再次检查通过。关键版本为 Python 3.12.13、CadQuery 2.6.1、OCP 7.8.1.1.post1、Open3D 0.19.0、PyTorch 2.8.0、transformers 4.57.1、decord2 3.4.0。

`./run.sh doctor`：原生几何、点云、配准、视频、模型适配器和特征模块均可导入。

`./run.sh catalog`：192 requests、960 edits、1152 breps、22 users、8122 ratings；四种模态各 48 个请求，三个难度各 64 个。

`./run.sh smoke`：使用真实任务 `ZK22J6VYRKQ2RTFD_1758875163.609787` 的输入模型，执行官方 harness 的直通函数，产生 STEP、STL 和七张非空 512×512 图片；STEP 有效、实体数量和尺度检查通过。相同点集 Chamfer = 0，平移点集 Chamfer > 0；相同 STL 的上游 IoU = 1，缓存特征自相似度约 1；真实请求视频解码为 800 帧，首帧大小 720×1108×3。

这一曲面模型的 CadQuery/STEP 往返体积从 67.0898206547 变为 67.0665860362，相对漂移约 0.0346%。高精度 OCP 积分后差异仍存在，输入/输出均为有效实体、266 个面。记录了这一转换局限；smoke 明确采用 0.1% 体积漂移上限并检查拓扑/尺度，未将其称为无损往返。

`./run.sh features`：实际调用上游 `extract_dino()` 对发布视图和新渲染图提取特征，强制执行配置意图中的 CPU 设备，未更改上游特征提取/展平逻辑。得到 197,376 维向量，自相似度约 1；发布视图重新提取值与数据库已有特征的余弦相似度约 `0.99999999998`。

`./run.sh leaderboard`：执行固定上游 notebook 中的评分汇总单元，五组各 192 条。human baseline / Claude / Gemini / GPT 的 Chamfer 距离分别为 21.87 / 53.64 / 109.74 / 50.12，与论文整数显示 22 / 54 / 110 / 50 一致；对应 IoU、DINO、Validity 与论文表 1 的显示值一致。五组人工 Acceptance 分别为请求者 0.82、另一专家 0.78、Claude 0.05、Gemini 0.10、GPT 0.25。

保留了发布数据与论文的小差异：请求者 human instruction 重算为 0.75（论文 0.74）；GPT 作为评委时对 Claude/Gemini 的 instruction 为 0.34/0.39（论文 0.35/0.40）。没有修改原始评分来凑齐表格，原因尚未向作者确认。

`./run.sh infer --request ...`：仅生成一个任务的 dry-run 计划；官方路径整理函数能处理该 parquet，STEP 路径存在，无 GT 输出字段，无 API 请求。

完整 `./setup.sh` 在本机再次执行成功，验证依赖、数据、权重和配置入口能够重复运行；阅读 notebook 已注册项目内专用 kernel，全部代码单元执行通过，执行后的含图片副本保存在 `.local/reading.executed.ipynb`。脚本语法、Python 编译、notebook schema、`git diff --check` 均通过。已有 DBCAD 后端/网关/Neo4j 保持运行，网页返回 HTTP 200。

## 边界

没有重跑三个模型的 576 次付费推理，没有进行新的 VLM 评分或专家人工评价；发布结果重算不能代替这些实验。完整数据和可执行环境已经就绪，后续仍需确定实验方案并配置 API 凭据。

本轮只验证代表性样本的几何/渲染执行，未把全部 192 个输入逐一经过 STEP 往返，也没有保证任意未来生成脚本或大型装配都能成功。
