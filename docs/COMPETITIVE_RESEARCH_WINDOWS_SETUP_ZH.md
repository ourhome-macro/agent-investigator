# Windows PowerShell 启动竞品研究工作台

本文是当前本地运行指南。历史机器端口和日志不作为新环境配置。

## 1. 准备环境与配置

需要 Python 3.12+、Node.js 22+、uv，以及前端项目指定的 pnpm/Corepack。以下命令从仓库根目录运行。

```powershell
if (-not (Test-Path -LiteralPath config.yaml)) {
    Copy-Item -LiteralPath config.example.yaml -Destination config.yaml
}
if (-not (Test-Path -LiteralPath extensions_config.json)) {
    Copy-Item -LiteralPath extensions_config.example.json -Destination extensions_config.json
}
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

编辑 `config.yaml`：启用 `subagent_batches.enabled: true`，配置默认主模型，并提供名为 `deepseek-v4-flash` 的模型配置。凭据通过 `$变量名` 引用根目录 `.env`。

```yaml
subagent_batches:
  enabled: true
```

真实研究验收还需要：

- `BOCHA_API_KEY` 或 `TAVILY_API_KEY`；
- `JINA_API_KEY`；
- `CI_EMBEDDING_BASE_URL`、`CI_EMBEDDING_API_KEY`、`CI_EMBEDDING_MODEL`。

模型服务的密钥名称取决于 `config.yaml` 中使用的环境变量。生产存储等其他字段见根目录 `.env.example` 和 [当前规格](COMPETITIVE_RESEARCH_V1_SPEC.md)。

## 2. 安装依赖

```powershell
Set-Location backend
uv sync --locked --all-packages
Set-Location ..
python scripts/pnpm.py install --frozen-lockfile
```

## 3. 启动后端

在第一个 PowerShell 终端进入仓库根目录：

```powershell
$researchRoot = (Get-Location).Path
$env:DEER_FLOW_PROJECT_ROOT = $researchRoot
$env:DEER_FLOW_HOME = Join-Path $researchRoot 'backend\.deer-flow'
$env:GATEWAY_CORS_ORIGINS = 'http://127.0.0.1:13000,http://localhost:13000'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
Set-Location backend
uv run --env-file ../.env --no-sync uvicorn app.gateway.app:app --host 127.0.0.1 --port 18001
```

此命令在当前终端运行；修改后端代码后重启。Gateway 启动时会执行数据库迁移。默认开发数据保存在 `backend/.deer-flow/`。

## 4. 启动前端

在第二个 PowerShell 终端进入仓库根目录：

```powershell
$env:DEER_FLOW_INTERNAL_GATEWAY_BASE_URL = 'http://127.0.0.1:18001'
$env:DEER_FLOW_DEV_ALLOWED_ORIGINS = '127.0.0.1,localhost'
python scripts/pnpm.py dev --port 13000
```

访问 `http://127.0.0.1:13000/workspace/investigations`。首次访问按提示完成管理员设置，然后创建研究、确认竞品与官方来源、等待报告并决定是否发布。

若要运行构建后的前端，在同一终端保留上述 Gateway 地址后执行：

```powershell
python scripts/pnpm.py build
python scripts/pnpm.py start --port 13000
```

Gateway 地址必须在构建前设置，Next.js 的生产转发目标会写入构建结果。

## 5. 检查与排错

```powershell
# 后端目录；只检查配置存在性，不调用付费模型或搜索。
.\.venv\Scripts\python.exe -m scripts.benchmark.competitive_research preflight
```

| 现象 | 检查方向 |
| --- | --- |
| 研究服务尚未准备好 | 是否启用持久任务服务、模型与搜索配置是否完整 |
| 页面显示但无法交互 | 使用的访问地址是否包含在 `DEER_FLOW_DEV_ALLOWED_ORIGINS` |
| 修改 Gateway 地址后仍连接旧地址 | 是否在新的环境变量下重新构建前端 |
| 数据库提示缺少字段 | 是否重启了包含最新迁移代码的 Gateway |
| 部分项目显示缺少资料 | 打开结论与来源说明；缺少资料不代表产品没有该能力 |

配置检查通过不代表模型效果或生产部署已验收。已知的 Windows 全量测试限制见 [验证记录](COMPETITIVE_RESEARCH_CONFIDENCE_AND_WORKSPACE_ZH.md)。
