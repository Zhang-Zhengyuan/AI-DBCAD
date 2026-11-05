# AI-DBCAD

**面向持续人机 CAD 编辑的可靠续作。**

*Version-Aware Geometric Evidence for Reliable Continuation in Human–AI CAD Editing*

AI-DBCAD 研究共享 CAD 项目经历人工编辑、需求更新和暂停/恢复后，AI 如何识别仍有效的几何证据、重新获取失效观测，并以可控成本正确继续设计。项目与产品统一命名为 **AI-DBCAD**，几何与协作能力由 ACIS、FastAPI 与 Neo4j 支撑。

## Linux：收到运行包后部署完整 CAD 系统

适用 **Ubuntu 22.04 / 24.04，x86_64**。维护者发送 **`AI-DBCAD-worker-ubuntu22.04-x86_64-20261105.tar.gz`**（约 70 MB）；这个包已包含 worker、ACIS/InterOp 动态库、STEP 转换器与资源。接收者无需安装 ACIS SDK 或编译 C++。它与本仓库配套，勿取另一项目的包。

首次安装需要联网。预先安装 Git、Docker Engine（含运行权限）、Node.js 22.12+（22.x）和 uv；Ubuntu 自带的 Python 3 可用于生成本机配置，后端由 uv 安装 Python 3.12。Docker 只用来运行 Neo4j；网关直接运行在 Linux 上，前端由网关托管。

**1. 拉默认分支，校验并安装运行包。** 新机器执行 clone；已有 clone 则先切到 `main` 再 `git pull --ff-only`。

```bash
git clone --branch main https://github.com/Zhang-Zhengyuan/AI-DBCAD.git
cd AI-DBCAD
# 后续步骤使用 Bash，工作目录均从仓库根开始。
set -euo pipefail
test "$(uname -m)" = x86_64
RUNTIME_ARCHIVE="$HOME/Downloads/AI-DBCAD-worker-ubuntu22.04-x86_64-20261105.tar.gz"
printf '%s  %s\n' '9013a1df53cf1f85947eb59448ed56ef726031fa4e3817bb12ff1adccc32d33c' "$RUNTIME_ARCHIVE" | sha256sum --check
# 全新安装目录；已部署时保留原目录和配置，勿混解压两个版本。
mkdir -p _deps/runtime/linux
tar -xzf "$RUNTIME_ARCHIVE" -C _deps/runtime/linux
./_deps/runtime/linux/dbcad-worker-linux/dbcad_worker selftest
```

如运行包不在 `Downloads`，只需改 `RUNTIME_ARCHIVE`。不要把 `.so` 拆散复制；`_deps/` 整目录不进 Git。

**2. 生成本机配置。** 两个密码自行输入，不会显示或写入 Git。数据库密码使用至少 16 个字符；网页访问密码是打开 CAD 网页时输入的密码。

```bash
mkdir -p .local
python3 - <<'CONFIG'
from pathlib import Path
import getpass, os, shlex
root = Path.cwd().resolve()
p = root / '.local/linux.env'
if p.exists():
    raise SystemExit('已有 .local/linux.env，保留原配置；需要修改时用编辑器打开。')
neo = getpass.getpass('设置 Neo4j 密码（至少16字符）: ')
api = getpass.getpass('设置 CAD 网页访问密码: ')
if len(neo) < 16 or not api:
    raise SystemExit('密码不满足要求，请重新执行。')
values = {
    'CAD_DB_STORAGE_BACKEND': 'neo4j',
    'CAD_DB_NEO4J_URI': 'bolt://127.0.0.1:7687',
    'CAD_DB_NEO4J_USER': 'neo4j',
    'CAD_DB_NEO4J_PASSWORD': neo,
    'CAD_DB_NEO4J_DATABASE': 'neo4j',
    'CAD_DB_API_PASSWORD': api,
    'CAD_DB_WORKER_EXE': str(root / '_deps/runtime/linux/dbcad-worker-linux/dbcad_worker'),
    'CAD_DB_COMPONENT_ROOT': str(root / '.local/linux/components'),
}
with os.fdopen(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
    f.write(''.join('export ' + k + '=' + shlex.quote(v) + '\n' for k, v in values.items()))
print('配置已保存到 .local/linux.env（仅本机）。')
CONFIG
source .local/linux.env
```

**3. 安装锁定依赖并构建网页。**

```bash
uv python install 3.12
uv sync --project systems/dbcad/CAD_DB/backend --python 3.12 --frozen
(cd systems/dbcad/web && npm ci && npm run build)
```

**4. 启动持久化数据库。** 以下容器名和数据卷归本次部署使用。首次运行执行 `docker run`；以后重启用 `docker start ai-dbcad-neo4j`。两个项目同时部署时须另配 Bolt/HTTP 端口，不能占用同一组端口。

```bash
source .local/linux.env
export NEO4J_AUTH="neo4j/$CAD_DB_NEO4J_PASSWORD"
docker run -d --name ai-dbcad-neo4j --restart unless-stopped \
  -p 127.0.0.1:7474:7474 -p 127.0.0.1:7687:7687 \
  -v ai-dbcad-neo4j-data:/data -e NEO4J_AUTH neo4j:5.26.12-community
unset NEO4J_AUTH
```

**5. 检查数据库连接，再启动网关。** 保持这个终端运行；不要只根据 `/health` 成功判断持久化已接通，当前程序在数据库故障时可能降级到内存。

```bash
source .local/linux.env
cd systems/dbcad/CAD_DB/backend
uv run --frozen python - <<'CHECK'
from app.config import settings
from neo4j import GraphDatabase
import time
for attempt in range(30):
    try:
        with GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)) as d:
            d.verify_connectivity()
            with d.session(database=settings.neo4j_database) as s:
                s.run('RETURN 1').single(strict=True)
        print('Neo4j 连接与认证通过')
        break
    except Exception:
        if attempt == 29:
            raise SystemExit('Neo4j 未就绪：检查 docker logs、端口、密码与原数据卷的密码；不要清库重试。')
        time.sleep(2)
CHECK
uv run --frozen uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**6. 打开网页并验收。** 本机访问 `http://127.0.0.1:8000/web/`；其他电脑访问 `http://服务器IP:8000/web/`（需服务器允许访问 8000 端口）。输入步骤 2 设置的 CAD 网页访问密码，创建项目并插入立方体；停止网关后重新启动，确认项目、版本和模型仍存在。`/webui` 是数据库管理页面，使用同一访问密码。

日常启动：从仓库根 `source .local/linux.env`，`docker start ai-dbcad-neo4j`，然后进入 `systems/dbcad/CAD_DB/backend` 执行最后一条 uvicorn 命令。停止网关按 Ctrl+C；停止数据库用 `docker stop ai-dbcad-neo4j`，保留数据卷。更新代码后重新执行步骤 3；只有维护者通知 worker 协议/功能更新时才替换运行包。

本步骤部署普通 CAD 建模/协作系统；AI-DBCAD 的模型调用还需要配置 provider/key、固定上游 AgenticCADedit 与渲染 Python 环境，参见 [AI 框架接入](experiments/exp-003-cad-agent-selection/README.md)。运行包不含模型服务凭据，不代表已经配置可用的大模型。

## 当前进度

已选定“持续人机 CAD 编辑中的版本感知几何证据维护与可靠续作”。已完成首轮 AgenticCADedit 受控检查点预实验；真正连续的编辑 benchmark 和候选几何证据维护方法尚未实现或验证。

AI-DBCAD v3 底座、neuralCAD-Edit/AgenticCADedit 复现环境和三 harness 单请求对照已有结果；它们不能证明长期证据失效的普遍性，也不能证明候选方法有效。EXP-002 已用独立几何真值和成对外部变化观察到旧数值误用案例；按需重查也是有效且廉价的强基线，后续方法必须正面比较。

AI-CAD-agent 底层框架已选定（EXP-003）：官方 AgenticCADedit 循环经 `agenticcadedit-acis/1` 适配器接入 AI-DBCAD/ACIS，含精确面边查询、选区高亮、拉伸/旋转/扫掠、圆角/倒角与阶段化连续对话；IterCAD 经审计不接入，保留为外部对照。AI 面板支持自定义 provider/模型选择（仅 VLM 探针通过可选）与图纸图片输入（Issue #28）。

不宣称“首次多轮 CAD”或“首次长时程 CAD”。已有 mrCAD、CADWorld、HistCAD、Concord/TRACE 等近邻工作需要明确比较；合法需求更新与误用旧观测须分别评价。新颖性、实际错误率和完整成本仍需核实。

## 从哪里开始

- [实验索引](experiments/README.md)：每个实验的稳定 ID、协议、代码、结果和本地原始记录位置。
- [EXP-001 · 已完成三 harness 对照](experiments/exp-001-three-harness/README.md)：精选文本指标、代理评分的后验局限和来源清单。
- [EXP-002 · 证据失效预实验](experiments/exp-002-evidence-validity/README.md)：已完成 24 个正式恢复分支；N/F/A 最终成功 5/8、8/8、8/8，不能外推工业发生率。
- [EXP-003 · 框架选型与 ACIS 接入](experiments/exp-003-cad-agent-selection/README.md)：官方 AgenticCADedit 循环接入 AI-DBCAD/ACIS，付费端到端验收与选型决策见 [DECISION.md](experiments/exp-003-cad-agent-selection/DECISION.md)。
- [研究适配与复现](research/README.md)：固定上游、依赖环境、数据与 harness 入口。
- [AI-DBCAD 底座](systems/dbcad/README.md)：worker、后端、前端、构建与部署。
- [代码导航](docs/ai/PROJECT_CONTEXT.md) · [Mac 接手说明](docs/ai/MAC_HANDOFF.md)。

## 仓库组织与 Git 收录

```text
systems/dbcad/    AI-DBCAD 底座源码与模块测试
research/        固定上游复现适配和既有 harness 实现
experiments/     实验定义、稳定 ID、精选文本结果；每次 runs/ 留本地
scripts/         仓库级构建、生命周期和实验登记检查
docs/           共享架构、运行和协作规则
```

完整目录、结果追溯与判断依据见 [收录与目录规则](docs/REPOSITORY_LAYOUT.md)。底座不再直接散落于根 `CAD_DB/`、`web/`；SDK、本机数据和构建缓存仍由忽略目录管理。

**每次任务先判断“仅本地 / 拟纳入 Git / 需要远端协作”，不默认 push 或提 PR。** 可复用代码、依赖锁、实验定义和必要的脱敏小型指标可以入库；一次实验运行、一次写作或一次导出不各建一个 PR。

开题报告、文献工作笔记和讲稿在本地 `docs/research/`；PPT、预览图、PDF 在本地 `output/`；原始请求/响应、几何模型、截图、账本、数据与缓存在忽略的 `.local/`、`runs/` 或既有 `research/*/output/`。这些内容默认不提交、不推送。`.gitignore` 只是防误收录，不等于数据备份；大型结果以后需要共享时，另用明确的数据存储与校验清单。

团队先读 [AGENTS.md](AGENTS.md)。只有应共享的变更才走 [Issue → 专属分支 → 验证 → 按需 PR → 人工审核](docs/ai/WORKFLOW.md)，agent 不自行合并。

## 平台支持与部署运行

| 平台 | worker（几何内核）来源 | 网关 / 前端 / Neo4j | 指南 |
| --- | --- | --- | --- |
| Windows 10/11 x64 | 本机源码构建（VS 2022 + 授权 ACIS Windows SDK） | 支持 | [复现与部署](docs/GETTING_STARTED.md) |
| macOS 13.3+（Apple Silicon） | 本机源码构建（`scripts/macos.py` + 授权 ACIS/InterOp Mac SDK） | 支持 | [macOS 指南](docs/MACOS.md) |
| Linux x86_64（Ubuntu 22.04+，glibc ≥ 2.35） | 预编译 worker 运行包（无需 SDK），或授权 SDK 自建 | 支持 | [Linux 指南](docs/LINUX.md) |

三个平台的网关、前端与 Neo4j 部署方式一致（Python 3.11+ / uv、Node.js 20.19+ 或 22.12+、Neo4j），差别只在 worker 二进制如何获得。Linux 运行包仅限 x86_64；ARM 主机与 glibc < 2.35 的旧发行版（CentOS 7、Ubuntu 20.04 等）不能运行。AI-DBCAD 与 DB-CAD 的 worker 同源但命令集不同，二进制不可互换。

### 新机器部署流程

1. **取代码**：`git pull` 本仓库。商业 SDK 与许可不随源码分发，仓库不包含任何授权组件。
2. **放运行库**：向维护者索取对应平台的依赖/运行包，统一放在仓库根 `_deps/`（整个目录不进 Git，布局见 [依赖说明](docs/DEPENDENCIES.md)）：
   - Windows 构建者：ACIS Windows TeamKit 解压为 `_deps/acis/` 等目录；
   - macOS 构建者：ACIS/InterOp Mac 原始发行包放入 `_deps/macos/`；
   - Linux 使用者：直接执行本文开头的 Linux 六步流程，运行包固定安装到 `_deps/runtime/linux/`，配置保存到 `.local/linux.env`。
3. **首次安装与构建**：Windows 运行 `scripts/check-environment.ps1` 与 `scripts/build.ps1 -Configuration Release`；macOS 运行 `python3 scripts/macos.py setup`；Linux 按 [Linux 指南](docs/LINUX.md) 安装依赖并构建前端。
4. **启动**：Windows 执行 `systems/dbcad/CAD_DB/deploy/start_dbcad_fullstack.cmd`；macOS 双击根目录 `AI-DBCAD.command`；Linux 在 `systems/dbcad/CAD_DB/backend` 运行 `uv run --frozen uvicorn app.main:app --host 127.0.0.1 --port 8000`，浏览器访问 `http://127.0.0.1:8000/web/`。Neo4j 需独立可用（连接配置见各指南与 `backend/.env.example`）。

## 运行与验证

已配置的 Mac 在仓库根双击 `AI-DBCAD.command`，或运行 `python3 scripts/macos.py start --open`。底座源码工作目录为 `systems/dbcad/`。首次安装和构建见 [macOS 指南](docs/MACOS.md)；Windows 见 [复现与部署](docs/GETTING_STARTED.md)。SDK 与许可不随源码分发，详见 [依赖说明](docs/DEPENDENCIES.md)。

```bash
# 查阅登记，不运行 CAD 或模型 API
python3 scripts/experiments.py list
python3 scripts/experiments.py check

# 仅验证后端，无需 CAD SDK 或数据库
cd systems/dbcad/CAD_DB/backend
uv sync --frozen --extra dev
uv run --frozen pytest -q -m 'not integration'
```

AI-DBCAD 是服务端权威架构：C++ ACIS worker 执行几何计算，FastAPI 管理命令与版本，Neo4j 保存版本清单和实体图投影，内容寻址资产库存 SAT/GLB/graph；React + three.js 浏览器只消费网格和查询。**SAT + 版本清单是权威状态，JSON 实体图是派生索引。** `base_version` 乐观锁、组件不可变与实体血缘保持既有契约。

[系统架构](docs/ARCHITECTURE.md) · [内核隔离](docs/KERNEL_ISOLATION.md) · [验证矩阵](docs/ai/VALIDATION.md) · [贡献说明](CONTRIBUTING.md) · [GitHub](https://github.com/Zhang-Zhengyuan/AI-DBCAD)

## 名称与兼容

仓库、网页、管理台与启动入口统一为 **AI-DBCAD**（Issue #38）。内部 `systems/dbcad/`、`CAD_DB.sln`、`dbcad_worker`、`CAD_DB_*` / `DBCAD_*` 配置、协议 schema 和浏览器存储键保持兼容，避免影响已有模型、预算账本和登录/续作状态。历史报告与实验来源保留当时名称。Mac 本地目录改名时保留旧路径符号链接，以兼容虚拟环境、容器挂载与构建缓存。

Linux worker 构建、独立运行包与网关接入见 [Linux 指南](docs/LINUX.md)（平台和验收范围见指南）。
