# neuralCAD-Edit：论文与代码阅读指南

阅读基线：论文 arXiv:2604.16170v1；官方代码 `68d1acceda0743e364f45fb7823f7cf39c530ba5`。下列实现观察均针对这一固定提交。论文、代码和本机验收属于三种证据，不能相互替代。

## 1. 先建立整体认识

你的理解基本正确：neuralCAD-Edit 是一个评测基准。完整发行物包含三部分：专家编辑任务与参考结果、供基础模型调用的 CadQuery 编辑 harness、自动/人工/VLM 的评价协议及发布评分。它没有提供一个需要在本机训练的专用神经 CAD 编辑器。

单个任务可以写成：输入 STEP 模型 M₀ + 用户请求 R → 编辑器 → 输出 STEP 模型 M̂。请求可能同时包含视频、语音转写、鼠标指向和手绘标记。自动指标把 M̂ 与请求者制作的参考结果 M_GT 比较；人工评价则直接判断 M̂ 是否满足 R。因此，一个与 M_GT 不完全相同的输出仍可能是合理解。

通用协议只要求编辑器读取 B-Rep STEP、理解请求并输出 STEP；作者实现的 CadQuery 循环只是一个可替换的 baseline harness。将来可以研究模型、输入组织、工具接口、反馈循环或评价方式，但这轮仅准备复现环境。

## 2. 论文应该按什么顺序看

1. **图 1、§2、图 2–3**：弄清楚 request、requestor GT 和 other-human baseline 的关系。每个请求有两个专家输出；另一位专家看不到请求者的参考答案。
2. **§3、图 4**：理解数据的平衡设计与难度含义。easy/medium/hard 来自预期专家编辑耗时 2/5/10 分钟，不是按面数分级。
3. **§4.1**：关注模型实际得到什么输入，以及如何迭代执行 CadQuery。
4. **§4.2、表 1、附录 B**：先弄清评价口径，再看模型名次。
5. **§4.4–4.5、图 5–10、表 2**：观察多模态理解、空间操作和自我评价各自的失败。作者的解释与实验结果要分开读。
6. **附录 C**：了解输入模型筛选，避免把这一规模的模型直接外推到大型工业装配。

PDF 共 21 页；本地副本在 `.local/paper/`。论文中的表 1 位于 PDF 第 9 页，评分 rubric 在第 20 页。

## 3. 数据在测什么

论文报告 192 个请求、384 次专家编辑，来自 10 位具有多年经验的 CAD 工程师。任务覆盖四种请求模态：text、interactive、draw-temp、draw-static；覆盖 parametric/dumb 与 single-body/assembly 的四种组合，再覆盖三个难度，每个组合四个请求。

interactive 包含模型操作、指向和语音；draw-temp 的笔迹会消失，draw-static 的笔迹持续存在。文字转写并不能保留全部空间和时序信息。论文另外提供自动转写与人工核对的 oracle 转写，研究时需要明确到底使用哪一种。

原始采集在 Autodesk Fusion 中进行，数据可能带 `.f3d` 和操作事件；作者的 CadQuery baseline 实际读取 STEP。STEP 不携带 Fusion 原有参数时间线，因此“数据源模型有参数历史”与“baseline 可操作该时间线”是两件不同的事。

## 4. 在磁盘上怎样找到一个任务

首先读 `src/utils/db.py` 和 `src/notebooks/visualise_examples.ipynb`。数据采用 Mongita 本地文件数据库保存元信息，STEP、STL、图片、视频保存在外部目录，由相对路径关联。

- `requests`：请求者、难度、模态、起始 B-Rep、文字/转写/事件/视频。
- `edits`：某个编辑者对某个请求的输出，关联 `request` 和 `brep_end`。
- `breps`：模型文件、多视图及缓存特征。
- `users`：专家或模型身份。
- `ratings`：自动相似度、人工和 VLM 评分。

沿着 `request.brep_start → breps` 找输入；沿着 `edits.request == request._id` 找各编辑者输出；其中 `edit.user == request.user` 是请求者参考结果。评分通过 `rating.edit` 关联编辑结果。

本项目保留 `.local/data/` 中的官方发布资料，另在 `.local/workspace/` 建独立工作副本；APFS 上使用写时复制，同时隔离数据库与文件资产的后续写入。配置在 `.local/config.local.json`，里面已替换作者机器的绝对路径，所有付费 rater 默认关闭。

## 5. 一次模型编辑的实际代码路径

从 `src/scripts_benchmark_inference/run_harness.py` 开始：

1. 读取一个或多个 parquet，补全输入资产的绝对路径，整理为 task dict。
2. 按配置动态加载 `src/vlms/{openai,anthropic,gemini}.py` 中的模型适配器。
3. 调用 `BaseVLM.cadquery_script()`，进入 `visual_update_loop()`。
4. 模型返回 `my_cad_function` 和 `complete`。
5. harness 将函数写入文件，在子进程中执行，再将输出文字和 `tmp.png` 放回上下文。
6. 模型继续修改函数，直到声明完成或达到默认十轮。
7. 最后保存 STEP、迭代日志、token 统计和 `settings.json`。

`complete=true` 时，本轮函数不会执行，保留上一轮结果。这条语义直接影响迭代次数和成功判定。上游提示词要求第一轮不能完成，但循环本身没有强制校验这一要求。

`src/harnesses/cadquery_script.py` 定义 `my_cad_function(args)` 的执行与导出契约；`src/utils/cadquery_rendering.py` 定义七个视角；`src/scripts_preprocess/cadquery_convert.py` 将 STEP 转为 STL 和评价视图。

还要留意执行反馈：该提交的 `run_cadquery_script()` 捕获了 stderr，却只把 stdout 返回给模型；它也没有根据子进程返回码抛出错误。导出函数中部分异常同样会打印后返回。因此，命令退出码为零不能独立证明产生了有效模型，本机 smoke 会额外检查文件、几何有效性、体积和渲染内容。

## 6. 自动指标应如何理解

**Chamfer**：`src/utils/evals_feature_geometric.py` 从 STL 表面随机采样 10,000 点，计算两个方向的最近邻欧氏距离均值之和。默认不按尺寸归一化、不做配准，因此单位和坐标系影响结果。数据库里保存的是 `1/(distance+1e-8)` 相似度；leaderboard notebook 再取倒数显示 distance。不能直接把库中的相似度当作论文距离。

**IoU**：同一文件以两模型包围盒对角线的较小值除以 100 设定 voxel size，再比较 Open3D 表面体素的索引集合。它没有先填充实体内部，也没有显式将两次体素化的原点统一。复现时应保留这个实现口径；若以后研究更严格的实体体积 IoU，应另命名并报告，不能悄悄替换后仍称原指标。

**DINOv2**：`src/utils/extract_features.py` 对等轴测图运行 Hugging Face 的 image-feature-extraction pipeline，配置为 `facebook/dinov2-base`，把输出张量展平后存库，再计算余弦相似度。它并不是对七视图做平均，也不能未经确认就改成 pooled/CLS 特征。发布库已有特征，可以先用缓存重算排行榜；新输出可重新提取。

该提取器没有把配置中的 `dino_device` 传给 pipeline。本地特征校验在调用边界明确指定 CPU，使用固定权重；未改变提取、展平或余弦计算。实测维度为 197,376，发布视图重提取与缓存值的相似度约为 0.99999999998。

**Validity**：论文将其解释为有效 CAD 输出比例；官方 leaderboard notebook 的具体代理判断是能否找到 `toprightiso` 图。我们额外执行 `shape.isValid()` 是环境校验，不能声称已经替换或重算了论文的 validity。

这些是代理指标。一个几何外观很相似的结果也可能漏掉关键编辑；一个与参考不同的结果也可能满足存在歧义的请求。

## 7. 人工 Acceptance 是核心终点

人工评价有 instruction-following 和 quality 两个 1–7 分项。论文与 leaderboard notebook 对每个编辑汇总专家评分的中位数，归一化为 `(score-1)/6`。两个分项都达到 5/7 才算接受，然后对任务求接受比例。

论文表 1 中 human baseline 的 Acceptance 为 0.78，GPT 5.2 为 0.25；相差 0.53 是百分点差，不是相对下降 53%。GT 请求者也不是 1.00，其人工 Acceptance 为 0.82。这与任务歧义和严格评分标准有关。

重新汇总作者发布的人工评分可以核对表格，但没有产生新的人工评价。若以后提出新方法，需要组织专家评价，或联系作者按其流程评价；自动指标和 VLM 打分均不能代替这一步。

## 8. 直接运行上游脚本前需要核对的细节

- `src/scripts/run_all.sh` 含作者机器的路径；它当前主要调用批量评测入口。
- `run_all_benchmarks.py` 会清理重复编辑记录；通用结果汇总对部分人工分项取平均，而论文对应的 `leaderboard.ipynb` 使用中位数。本轮复核表格用后者。
- 发布配置中至少一个 VLM rater 默认启用。这里生成的本地配置将所有 rater 设为 skip，避免离线阅读意外触发调用。
- 原始 `create_parquets.py` 有供检查使用的 GT 输出路径字段；当前发布 parquet 已不包含这些字段。本地单任务入口从发布 parquet 原样选择请求侧白名单字段，不增加额外视图，也不向模型传 GT 文件或目标图像。
- `BaseVLM.load_video()` 同时有 fps 和 subsample_every；评分配置和推理配置不同。尤其不能只看参数名字，就假定模型看到的时间轴相同。
- 上游环境未锁版本，还遗漏了实际导入的 transformers/probreg 等依赖。本地 `uv.lock` 固定了一套经过本机验证的版本；这不等于恢复了作者当时全部环境。
- notebook 对缺少自动评分的样本使用默认值；解读均值时应同时查看有效输出数、失败数和缺失值，不能只看一个汇总数。

## 9. 建议的本轮阅读活动

先在 `reading.ipynb` 选一个 text/easy 样本，只看请求和输入模型，写下你认为需要修改的几何。再看请求者 GT 与另一位专家的输出，比较解的差异。随后选一个 draw-static 或 draw-temp 样本，观察转写中哪些空间信息必须由视频中的指向或笔迹补全。

最后沿 `run_harness → BaseVLM → cadquery_script → STEP/PNG → metrics → leaderboard` 走读一遍。此时再判断自己希望研究的是请求理解、几何工具能力、执行反馈、停止策略还是评价可靠性。当前观察只能形成待验证的问题，还不足以确定论文创新点。

## 10. 复现边界

本轮交付的是固定官方代码、数据和本机依赖，真实离线链路，以及对发布结果的重新汇总。重新调用论文中三个模型、重跑全量 192 个请求、获得新的五位专家评价，属于后续实验。API 型号的可用性、服务端实现及输出随机性也会影响严格数值复现，应在开始实验时记录。

官方来源：[论文](https://arxiv.org/abs/2604.16170)、[项目页](https://autodeskailab.github.io/neuralCAD-Edit/)、[固定代码](https://github.com/AutodeskAILab/neuralCAD-Edit/tree/68d1acceda0743e364f45fb7823f7cf39c530ba5)、[数据](https://huggingface.co/datasets/autodesk/neuralCAD-Edit)。代码为 MIT 许可，数据页标注 CC BY-NC 4.0；下载资料保留其原始许可。
