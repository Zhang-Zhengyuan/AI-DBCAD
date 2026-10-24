# 如何理解 AgenticCADedit

论文：<https://arxiv.org/html/2609.29621v1>。本地固定源码在 `.local/upstream`；以下描述以该提交的实现为准，论文报告与代码默认值分别记录。

## 一次编辑如何发生

neuralCAD-Edit 的完整脚本基线把原始 STEP 交给模型，模型返回整个 `my_cad_function`。函数在新进程中执行，模型看到执行输出和渲染图后重写函数，再从原始 STEP 开始。它有多轮对话和视觉反馈，但上一次执行中的几何变量不跨进程保留。

AgenticCADedit 的核心是一个长期存在的 CadQuery Python 会话。原始 STEP 预加载到 `result`；每轮模型可调用一个或多个工具，后续编辑直接基于现有 `result`。MCP 是通信协议，几何能力来自 CadQuery/OCP，而收益是否存在取决于工具、状态和观察设计。

例如“给孔口加 0.2 mm 倒角”，理想过程是：

1. `inspect` 查看模型范围，`list_faces`／`list_edges` 读取几何类型、位置和尺寸。
2. `render_views` 把候选面／边标红，检查是否选中了要求的孔口。
3. `run_step` 执行局部代码，使 `result` 指向修改后的模型。
4. 再次渲染和测量。错误时修复或 `undo`，满足要求后调用 `finish`。

这里的“状态保留”是运行时几何与 Python 变量，不等于找回 STEP 原本丢失的 Fusion 参数化历史。

## 按这几处读代码

- `src/harnesses/cadquery_mcp/cad_session.py`：持久 namespace、历史栈、几何查询、高亮、导出。
- `src/harnesses/cadquery_mcp/server.py`：八个实际 MCP 工具；`finish` 由客户端提供和拦截，不是服务端工具。
- `src/vlms/base_vlm.py` 的 `cadquery_mcp`／`_run_mcp`：完整 agent 循环、原始输入、工具结果和图像回填、终止及最终导出。
- 同文件的 `cadquery_script`／`visual_update_loop`：完整脚本对照循环。
- `src/vlms/vllm_openai.py`：OpenAI Chat Completions 格式的图片与工具调用，可接 API。
- `src/utils/cadquery_rendering.py`：官方 OCP/Cocoa 渲染、高亮；本机直接运行。
- `src/config/edit_192_external.json`：模型提示、采样、预算。Qwen MCP 配置为 40 轮；script 未指定 `function_kwargs.max_iters`，方法默认 100。论文写两边最大 100 轮，因此不能把“直接跑默认配置”称为严格匹配论文预算。本地冒烟统一覆盖为 12 轮、8192 token。

## 对论文证据的核对

192 题的发布 CSV 中，Qwen Script／MCP 有效视图数分别为 98／182，对应 51.04%／94.79%；有效子集 CD 中位数为 4.25725／3.72382，IoU 为 0.64202／0.71210，DINO 为 0.53149／0.57408，与论文展示精度一致。

这些是作者已有输出的重汇总，不是本机重新生成 384 个模型。该提交 CSV 中 Qwen 只有几何评分，缺少论文 Acceptance 对应的 rater 记录；`vlm_acceptance` 的全零值不能解释为论文报告的真实接受率。Gemma4／GPT-5.6-Luna 也没有对应完整发布记录可在这些 CSV 中复算。论文的自动 rater 与 neuralCAD-Edit 原论文的人类评分口径不同，跨论文绝对值不宜直接比较。

## 已验证的局限与可研究问题

普通 `result = result.translate(...)` 这种创建新对象的编辑，失败恢复和撤销已通过真实 MCP 验证。但历史快照只是 `namespace.copy()`。如果代码原地修改 `cq.Assembly` 后抛错，字典恢复仍指向已经被改动的同一个 Assembly：本地诊断中面数从 6 变成 12，错误返回后没有恢复。`./run.sh diagnostics` 可复查；本轮保留原实现，不把修复悄悄混入复现方法。

`list_faces`／`list_edges` 默认只给排序后的前 50 项，复杂模型可能需要主动提高 limit。面／边的索引和中心是在当前拓扑上临时计算的，高亮用最近中心选择；这不构成跨拓扑编辑的持久语义身份。

`run_step` 返回成功通常说明 Python 代码执行成功，并不证明满足用户意图。模型也可以不调用工具而结束，或耗尽预算时导出当前模型。因此需要分别分析：代码能否执行、实体是否有效、改动位置和尺寸是否正确、子任务是否漏做、无关结构是否被破坏。

上述是复现发现和研究假设，不能直接当作论文创新点。后续应扩大样本、做同模型消融，并核对已有工作是否已经覆盖相同机制。
