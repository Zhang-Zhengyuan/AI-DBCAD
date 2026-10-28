# DBCAD：macOS 原生运行

适用 Apple Silicon Mac、macOS 13.3+。本次实测环境为 macOS 26.6.2、ARM64、Apple Clang 21、Node 22、Python 3.12；ACIS/InterOp 均为 2026.1.0.1。Windows 的 MSBuild 和 PowerShell 入口继续保留。

## 首次准备

安装 Xcode Command Line Tools（或完整 Xcode）、Docker Desktop、Node.js 22 和 uv，启动 Docker Desktop。SDK 与许可由有授权的成员自行提供，目录布局如下：

```text
仓库根目录/
  2026.1.0.1_acis_all_mac_m64/2026.1.0.1_acis_mac_m64/
  2026.1.0.1_iop_mac_m64/
  spatial_license.h
  scripts/macos.py
```

在仓库根目录执行：

```bash
python3 scripts/macos.py setup --team-kit /path/to/DBCAD-Team-Kit-20261019.zip
./DBCAD.command
```

setup 只从 TeamKit 提取 `_deps/vendor/nlohmann/json.hpp`，原生构建使用 Mac SDK。它安装锁定的 Python 3.12 开发环境、编译 Release worker/网格测试、执行自检，再安装和构建前端。已存在 JSON 头时可省略 `--team-kit`。其他 SDK 位置可通过 `ACIS_ROOT`（含 include、macos_m64 的那层）和 `INTEROP_ROOT` 指定。

首次安装需要联网。命令行下载工具需要代理时，在当前终端配置 `HTTPS_PROXY` / `HTTP_PROXY`，再运行 setup；这些设置不会写入仓库。

## 日常启停

双击根目录 `DBCAD.command`，或执行：

```bash
python3 scripts/macos.py start --open
python3 scripts/macos.py status
python3 scripts/macos.py stop
# 连同本仓库独立 Neo4j 容器一起停止，保留数据库与模型：
python3 scripts/macos.py stop --database
```

网页默认为 `http://127.0.0.1:18080/web/`，以启动输出为准。首次配置时选择可用端口，此后保持固定。每次 start 都重启本仓库后端和网关，使 Python 更新生效。服务在后台运行，关闭启动终端窗口不等于停止服务；需要停止时使用上面的 stop 命令。

启动器为本仓库创建独立的 `neo4j:5.26.12-community` ARM64 容器。默认 Bolt/Neo4j HTTP 端口为 17687/17474，数据库、模型、凭据、日志和进程身份记录保存在 `.local/macos/`。所有服务只监听回环地址。网页入口使用本机便利网关自动附加访问密码；后端端口默认 18000，直接访问 API 仍需要 `X-API-Password`。不要把本机网关转发到公网。

启动器核对 PID、启动时间、命令与进程组归属后才停止服务。进程登记为空或已过期时，还会检查配置端口的监听者；只有命令符合本仓库后端/网关、工作目录确为当前 `.local/macos` 且进程组身份可验证时才恢复管理，兼容目录整理前的 `CAD_DB/backend` 及仍指向同一工作区的旧路径别名。status 也会识别这些漏登服务。

其他程序占用端口时，启动器显示服务、端口和 PID，不终止对方。不要用按端口批量 kill 绕过归属检查。重复双击或同时启停会得到“Another DBCAD start/stop is in progress”，等待前一次操作结束后再执行。进程登记原子保存；后端或网关启动/就绪失败时回收本次已启动的服务并保留日志，避免下次再次撞端口。

启动器不操作其他项目的 Neo4j 容器，也不删除数据。工作区开始写入数据后若要移动目录，应先完全停止；不要直接复制已配置的 `.local` 给别人。

## AI 面板与前端更新

`setup` 和 `npm run build` 都只处理当前 checkout，不会拉取代码或合入另一个 worktree 中的修复。18080 网页使用启动后端所在仓库的 `systems/dbcad/web/dist/`。在实际运行仓库根目录重建前端可用：

```bash
git log -1 --oneline
npm --prefix systems/dbcad/web run build
```

完成后刷新浏览器页面。其他 worktree 的构建不会自动替换此处的文件；尚未合并的修复应在专属分支验证和部署，不直接写入 main。

“生成预览”按钮下方会显示阻塞原因：例如未选项目、模型未填价格/未通过视觉工具检测、旧预览等待应用、任务暂停或状态正在读取。已经在其他窗口完成检测时，使用“刷新模型状态”重新读取资格，不需要重新填写文字、附加图片或重复付费检测；窗口重新获得焦点时也会刷新。图片附加本身不代替选择项目，未完成预览仍需继续、应用或放弃。

## 工作区更名与兼容路径

科研项目现名 **ResumaCAD**。本机目录更名时，先停止已确认属于旧工作区的后端、网关及数据库，再将原目录移动到新名称，并在旧位置保留指向新目录的符号链接。旧路径仍被虚拟环境、构建缓存、容器 bind mount 或研究配置引用时，不要移除该链接。

启动器仅在配置或容器标签中的绝对路径仍实际指向当前同一目录时接受别名。复制到另一个 checkout、缺失容器标签和失效路径仍被拒绝。容器名称、workspace 标签、端口、密码和既有数据沿用原身份；品牌更名不会新建一套数据库。

这种兼容方式不等于任意路径迁移。若需要删除旧路径，须另行迁移虚拟环境、构建缓存和 Docker 挂载；不要只改 settings.json，也不要删除 volume 或模型来绕过身份检查。新 clone 使用自己的全新本机配置。

重做栈按既有协议只保存在后端内存中，重启后清空；项目、已提交版本、撤销来源、SAT/GLB 与实体图保留在 Neo4j/资产库。

## 编译与验证

```bash
./scripts/build-macos.sh Release
./build/macos/Release/dbcad_worker selftest
./build/macos/Release/mesh_tests
./scripts/build-macos.sh Debug
./build/macos/Debug/dbcad_worker selftest
./build/macos/Debug/mesh_tests
python3 scripts/tests/test-macos-launcher.py

cd systems/dbcad/CAD_DB/backend
# 对已有工作区做源码搬迁后，重建脚本入口的绝对 shebang：
uv sync --frozen --extra dev --reinstall
uv run --frozen pytest -q -m 'not integration'
uv run --frozen pytest -q tests/test_v1_real_commands_integration.py tests/test_v1_real_worker_integration.py
cd ../../web
npm run build
npx playwright install chromium
npx playwright test
```

`build/macos/<Configuration>/dbcad_worker` 是设置运行库搜索路径的启动包装，实际 ARM64 可执行文件为同目录 `.bin`。应让后端调用包装入口。默认调用 Release；可设置 `CAD_DB_WORKER_EXE` 指向 Debug 或其他构建。SDK 使用 `@executable_path` 和裸动态库名，构建目录通过本机符号链接保留运行库/子转换器，并由包装设置 `DYLD_LIBRARY_PATH`。

构建脚本只对所选 SDK 的运行库和可执行子转换器移除下载隔离属性；只读文件临时增加所有者写权限，随后恢复。没有更改系统 Gatekeeper 设置，也不改写 SDK 的二进制内容。Mac 的头文件映射独立放在 `build/macos/sdk/`，因此 `_deps` 中已有 Windows TeamKit 不会覆盖 Mac 头文件。

完整集成测试需显式设置 `CAD_DB_NEO4J_URI`、`CAD_DB_NEO4J_PASSWORD`；撤销重启测试另用 `CAD_DB_UNDO_TEST_NEO4J_URI` / `CAD_DB_UNDO_TEST_NEO4J_PASSWORD` 指向隔离测试库。STEP 测试可通过 `CAD_DB_STEP_FIXTURE` 指定一个真实 `.step`/`.stp` 文件；没有语料时会跳过，指定了不存在的路径则失败。不要把跳过视为通过。

## 本次验收（2026-10-24）

- Release/Debug 原生 ARM64 编译成功，两种配置的 worker selftest 和每种 175 项网格检查通过。
- 后端非集成基线 42 项通过；提供真实 Neo4j 和 STEP 样本后，完整后端测试 51 项通过，零跳过。
- 前端构建通过；Playwright 的两项真实浏览器测试通过，覆盖鉴权、建模、SAT 导入导出、两会话同步、渲染、测量与版本切换。
- 在独立持久化实例创建立方体、球和圆锥，连续撤销/重做 `3 → 2 → 1 → 2 → 3`，SAT/GLB/STL 导出、实体 face 查询均通过。
- 完全停止后端、网关和 Neo4j 再启动，两个验收项目及场景清单保持一致；精简 Finder 环境下的 `DBCAD.command` 启动通过。启动器四项回归覆盖端口占用、快速重启、私密配置及进程身份匹配。
- 用 Mac InterOp 从 ACIS 立方体生成 STEP，再经 worker 和 HTTP 导入，得到一个组件、12 个三角形；中文输出路径可用。此项验证了简单实体转换，尚未覆盖大型 STEP 装配体和完整 ABC 语料。

SDK、许可、凭据、数据库、模型和编译产物全部留在忽略目录；Git 只包含适配代码、脚本和说明。
