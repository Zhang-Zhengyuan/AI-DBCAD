# 代码、实验和本地产物的组织规则

核对日期：2026-10-26。本规则适用于 ResumaCAD；研究状态以根 README 和各实验定义为准。

## 组织原则与依据

采用一个研究仓库，分开维护底座、研究适配、实验定义、精选结果和本地产物。目录层级按职责划分，实验按稳定 ID 登记，每次运行另有唯一 run ID。

参考 [Cookiecutter Data Science](https://cookiecutter-data-science.drivendata.org/) 对源码、不可变原始数据、报告与图片的分离；参考 [DVC](https://doc.dvc.org/example-scenarios/versioning-data-and-models) 通过小型元数据关联代码、数据版本和外部产物的做法。它们提供原则，并没有规定本项目必须采用以下目录名。当前用 JSON 清单和 SHA-256 已足够，不为预实验立即增加 DVC/MLflow 服务或依赖。

## 当前目录契约

```text
systems/dbcad/             DBCAD 底座源码；底座的回归测试随模块放置
  CAD_DB/                 worker、kernel、FastAPI 后端与部署入口
  web/                    React 浏览器客户端及 e2e
research/                 固定上游复现适配、评测工具和既有 harness
  neuralcad_edit/          固定数据/环境与官方代码入口
  agenticcad_edit/         工具与双 harness 适配、预算封装
  harness_study/           已有三 harness 实现；历史报告与导出入口
experiments/              每个科学问题的一份定义，跨运行保持稳定
  README.md               实验登记与操作说明
  _template/              定义、运行记录和结论模板
  exp-NNN-slug/            实验 ID、协议、入口和验收口径
    experiment.json       状态、代码/数据引用、结果位置
    results/              经判断值得共享的脱敏小型指标与清单
    runs/<run-id>/        本地原始记录、日志、模型、图片；忽略
docs/                     共享架构、运行指南、协作规则
docs/research/            本地选题报告、文献工作笔记、开题底稿与讲稿；忽略
output/                   本地 PDF、PPT、预览图等导出；忽略
scripts/                  仓库级构建、生命周期、登记检查与流程工具
CAD_DB.sln                仓库级构建入口，引用 systems/dbcad/CAD_DB
.local/、build/、_deps/    本机状态、构建和依赖；忽略
```

已有 `research/*/.local/` 保存固定上游、13 GB 级数据、环境、账本和原始运行。它们保留原路径，不为目录美观搬动或复制；EXP-001 的 manifest 将旧运行位置登记为 legacy。新实验的定义、专用代码和结果统一从 `experiments/exp-NNN-slug/` 查找。只有被第二个实验实际复用的研究实现才提取到 `research/`；不预建一堆空的 methods/datasets 包。

底座不导入 `research/` 或 `experiments/`；实验通过既有协议/API 使用底座。现有预算实现 `systems/dbcad/CAD_DB/backend/app/ai_budget.py` 被研究适配复用，方向为研究 → 底座，后端不导入研究环境或原生 OCP。底座新增实验所需能力仍在对应模块实现和验证，实验参数与评测口径留在实验目录。

## 先判断是否纳入 Git

每次任务先简短说明分类和理由；混合任务按文件分类，不能把整个目录一起提交。

1. **仅本地**：只读调查、运行已有脚本、临时诊断、API 原始请求/响应、账本、模型与截图、选题报告/讲稿/PPT、预览图、PDF、缓存、SDK/许可、数据库和私有配置。使用忽略目录，不自动创建 Issue、分支、commit、push 或 PR。运行实验产生新文件并不自动触发入库。
2. **拟纳入 Git**：可复用源码、必要测试、依赖锁、无秘密的配置示例、实验定义/协议/种子、数据获取方式/许可/校验值、用于共享复现的操作文档，以及经过检查且支撑结论的小型指标摘要。先有真实 Issue 和专属分支；可以先积累为本地可审阅变更。不是所有 Markdown 都值得入库，也不是所有 JSON/CSV 都应该忽略。
3. **需要远端协作**：上述共享变更已经形成完整、可复核的单元，需要备份代码、供他人复现或维护者审阅时，才 push 本任务分支并提 PR。按同一 Issue 汇总成熟变更，不为一次运行、一次导出、一次改图分别提 PR。用户指定“仅本地”时，以用户指示为准。

本次目录/规则调整改变所有后续开发入口，属于应共享的仓库基础设施；开题和汇报材料继续仅本地。若只是以后重导一张 PPT 图片，则完全不触发 Git 协作流程。

## 实验与结果的关联

- 实验 ID 使用 `EXP-NNN`，目录为 `exp-NNN-slug`，不随日期或结论变化。新协议如改变研究问题或主要比较，应分配新实验；同一协议的种子/模型/重复运行使用不同 run ID。
- run ID 使用带时区的时间戳、条件和种子，例如 `20261026T150000+0800-b0-s42`；使用创建新目录的方式防覆盖。先保存实际配置和 `run.json`，再运行；失败、预算中断和负结果也保留。
- 运行记录至少关联实验 ID、实际命令与执行目录、Git 提交、工作区是否有改动及相关补丁/文件哈希、依赖锁哈希、数据版本/校验值/许可、实际模型别名与已知修订、种子、预算、起止时间和退出原因。秘密值不写入可共享清单。
- 完整成本包括生成、判定、重试/失败保留费用、上下文重建、查询、维护和延迟。模型权重修订或费用未知时记录 unknown，不把接口别名当作已核实权重。
- `results/` 只收录与结论有关的精选小型摘要，配来源 run ID、生成命令和 SHA-256 清单；默认不自动复制原始运行。原始数据通过相对路径/外部存储标识和哈希关联。改变评分口径不能覆盖旧结果。
- 忽略不是备份。原始记录先留本机；需要异机恢复时另做经授权的数据备份/对象存储，并在清单登记位置与校验值。以后规模扩大再考虑 DVC；Git LFS 不能解决凭据、授权和发布范围问题。

## 忽略与解除跟踪

共享规则写进 `.gitignore`，个人特例才写 `.git/info/exclude`。Git 官方说明：ignore 不影响已经跟踪的文件，需要显式解除跟踪；参见 [gitignore](https://git-scm.com/docs/gitignore)。

```bash
# 同时核对共享规则命中和实际追踪状态；--no-index 可检查已跟踪路径
git check-ignore -v --no-index output/slides/example.png
git ls-files -- output/ docs/research/ research/harness_study/output/
git diff --cached --name-status
```

不能用 `git add -f` 绕过本地产物边界，也不能全局忽略 `*.png`、`*.json`、`*.csv`，以免漏掉源码静态资源、配置与必要的精选指标。已上传历史不会因为解除跟踪而消失；本次不重写历史。

## 路径兼容与回滚

DBCAD 源码从根 `CAD_DB/`、`web/` 移到 `systems/dbcad/`；根 `DBCAD.command`、`scripts/macos.py`、`scripts/build*` 和 `CAD_DB.sln` 仍为入口。SDK、`build/`、`.local/macos/`、Windows `x64/` 以及研究目录中的环境/数据位置保持原有约定。HTTP/WS、worker 命令和数据格式不变。

源码工作目录和直接导入路径需要更新；其他工作树需同步后运行新的后端 `uv sync --frozen --extra dev --reinstall`（重建带旧绝对路径的脚本入口）。已运行的服务可能还载入旧路径代码，本任务不停止正在使用的服务；下一次正常启动使用新路径。回滚结构提交即可恢复源码和入口；本地忽略产物与 SDK 不删除、不恢复数据库、不重置账本。
