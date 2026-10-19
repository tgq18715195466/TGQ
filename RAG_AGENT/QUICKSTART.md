# 掌柜智库快速开始

以下命令在 `TGQ/RAG_AGENT` 目录运行。已有本机环境不要覆盖 `.env` 或重复创建数据库。

## 1. 安装依赖

需要 Python 3.12 / 3.13、Node.js 24 和 PostgreSQL；参考部署使用 PostgreSQL 18。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
npm ci --prefix web
npm run build --prefix web
```

Linux / macOS 将 Python 路径替换为 `.venv/bin/python`。Node 只用于开发、测试和构建；运行构建后的页面不需要 Node。

## 2. 准备独立数据库

默认数据库 `zhanggui_dev`、角色 `zhanggui_app`。由管理员创建专用数据库和 `NOSUPERUSER NOBYPASSRLS` 应用角色，不要复制 ERP 数据库连接给 RAG。

全新本机环境也可通过进程环境安全设置 `PGADMIN_PASSWORD`，再运行：

```powershell
.\.venv\Scripts\python.exe scripts/bootstrap_local.py
```

脚本连接本机 PostgreSQL 管理员，生成随机应用密码并写入被忽略的 `.env`。若 `.env`、数据库或角色已存在则停止，不会覆盖。脚本只生成基础配置，其他服务项仍需参照 `.env.example` 补齐。

若数据库由管理员手工准备，首次复制模板：

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

填写应用 DSN 和至少 32 字节的随机 `JWT_SECRET`，本地开发设置：

```dotenv
POSTGRES_EXPECTED_DATABASE=zhanggui_dev
POSTGRES_EXPECTED_USER=zhanggui_app
ENVIRONMENT=development
DEVELOPMENT_SKIP_SCANNER=false
```

DSN 中的特殊字符需 URL 编码。数据库目标保护会拒绝误连其他数据库或使用超级用户迁移。

## 3. 配置外部服务

按 `.env.example` 填写 MinerU、Milvus、Neo4j、Embedding、Reranker、LLM 和扫描器地址与凭据。示例模型名需替换为服务实际可用的模型。

- MinerU 需要兼容项目的 `/file_parse` 契约。
- Embedding 接口兼容 `/embeddings`，输出维度必须与配置一致。
- Reranker 接口兼容 TEI `/rerank`；LLM 兼容 `/chat/completions`。
- 扫描器接收文件二进制，返回 `{"status":"clean"}` 才能继续。依赖 Compose 提供 ClamAV 和 HTTP 桥接，本机地址为 `http://127.0.0.1:8080/scan`。
- Office 转 PDF 还需要 Worker 环境安装 LibreOffice。

`.runtime/` 的模型与本地环境不在 Git 中。`compose.dependencies.yaml` 针对已准备 GPU 和本地权重的机器；完整应用 Compose 见 [运维手册](docs/operations.md)。不要同时重复启动占用相同端口的服务。

API 可先用于登录及知识库管理；完整导入和问答仍需要 Worker 和相应外部服务。

## 4. 初始化数据表与账号

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe scripts/setup_checkpoints.py
.\.venv\Scripts\python.exe scripts/manage.py seed
.\.venv\Scripts\python.exe scripts/setup_local_login.py
```

`seed` 每次创建新演示租户；已有 `.runtime/demo.json` 时通常无需重跑。初始 `admin` 随机密码保存在本机 `.runtime/local_login.txt`，与数据库密码不同。

```powershell
Get-Content .runtime/local_login.txt
# 需要更换本地密码时：
.\.venv\Scripts\python.exe scripts/setup_local_login.py --reset
```

生产应使用可信管理流程设置账号，并配置企业认证与统一限流。

## 5. 启动

终端一：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8000
```

终端二：

```powershell
$demo = Get-Content .runtime/demo.json | ConvertFrom-Json
.\.venv\Scripts\python.exe -m app.worker --tenant $demo.tenant_id
```

打开 <http://127.0.0.1:8000/>，登录、创建知识库、上传文档，等待 READY 后问答。任务未完成时检查 Worker、扫描器和外部服务。

已配置好的 Windows 本机可执行 `powershell -ExecutionPolicy Bypass -File scripts/start_local.ps1`，构建前端、迁移、准备账号并检查 / 启动 API。Worker 仍需单独运行。

前端开发执行 `npm run dev --prefix web`，访问 <http://127.0.0.1:5173/>。

## 6. 验证

```powershell
.\.venv\Scripts\python.exe scripts/run_local_tests.py -q
npm test --prefix web
npm run build --prefix web
.\.venv\Scripts\python.exe -m ruff check app scripts eval tests --select F
.\.venv\Scripts\python.exe scripts/check_forbidden.py
```

数据库回归会在开发数据库写入随机测试租户；迁移回滚仅操作随机 schema，请勿对生产数据库执行。真实外部测试需设置 `RUN_EXTERNAL_INTEGRATION=1` 和 `RUN_MINERU_INTEGRATION=1`，会产生并清理测试索引数据。

`/health/ready` 仅检查数据库连接。全链路验收还需实际文档导入、检索、模型生成和引用核验。
