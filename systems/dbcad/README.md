# DBCAD 底座

这里是 ResumaCAD 依托的 DBCAD v3 系统源码，独立于实验定义和结果。`CAD_DB/` 包含 ACIS worker、网格适配、FastAPI 后端与部署脚本；`web/` 是 React + three.js 客户端。原生几何只在 worker 进程中执行，SAT 和版本清单是权威状态，GLB/实体图为派生索引。

仓库根目录仍提供 `DBCAD.command`、`scripts/macos.py`、`scripts/build-macos.sh`、`scripts/build.ps1` 和 `CAD_DB.sln`。SDK、构建输出和本机状态按仓库根的忽略路径管理。

- [macOS 运行](../../docs/MACOS.md)
- [复现与部署](../../docs/GETTING_STARTED.md)
- [系统架构](../../docs/ARCHITECTURE.md)
- [后端说明](CAD_DB/backend/README.md)
- [验证矩阵](../../docs/ai/VALIDATION.md)
- [仓库与实验组织](../../docs/REPOSITORY_LAYOUT.md)

仅验证后端，无需 CAD SDK 或数据库：

```bash
cd systems/dbcad/CAD_DB/backend
uv sync --frozen --extra dev
uv run --frozen pytest -q -m 'not integration'
```
