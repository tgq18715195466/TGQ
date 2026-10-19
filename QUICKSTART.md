# ERP OpenClaw 快速开始

本指南针对本次整理的源码副本。仓库不携带真实密钥、业务数据、用户会话或原始数据库导出。

## 环境准备

- 推荐 Python 3.12；本次已有环境为 3.12.14，`pyproject.toml` 声明最低 3.10。
- Node.js 22+ 与 npm（用于前端安装、测试和构建）。
- MySQL 8；空 schema 使用 `utf8mb4_0900_ai_ci`。
- PostgreSQL 16，应用账号应可在目标数据库初始化 checkpoint / Store 表。
- 有效的 DeepSeek 兼容模型 API 配置；外部模型调用需要网络。

Windows 的 `python` 如果打开 Microsoft Store，请使用已安装 Python 的完整路径。所有命令在项目根目录运行。

## 1. 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm.cmd --prefix frontend ci
Copy-Item .env.example .env
```

Linux / macOS 对应使用 `.venv/bin/python` 和 `npm`；本次验证环境为 Windows，未重新验证其他平台启动器。

## 2. 创建空数据库

在 MySQL 客户端连接自己的开发数据库服务，再执行：

```sql
CREATE DATABASE motorparts_db CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE motorparts_db;
SOURCE /absolute/path/to/ERP-OpenClaw/deploy/mysql/001-schema.sql;
```

将 `SOURCE` 路径改成实际绝对路径，Windows MySQL 客户端可使用正斜杠。该文件只创建基础表，不导入业务记录。为应用账号授予该数据库内运行和迁移需要的权限。

在 PostgreSQL 管理客户端创建 `erp_agent` 数据库，设置应用账号和密码。应用启动会创建 checkpoint、Store 与展示历史表；确认应用账号对目标 schema 有创建权限。

## 3. 编辑本地 .env

按实际环境填写下列变量；示例占位文本必须替换，真实文件不要提交到 GitHub。

```dotenv
DEEPSEEK_API_KEY=replace-with-your-model-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
MAIN_MODEL=deepseek-chat
SUMMARY_MODEL=deepseek-chat
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=your-app-user
MYSQL_PASSWORD=replace-with-your-database-password
MYSQL_DB=motorparts_db
POSTGRES_URI=postgresql://your-app-user:encoded-password@127.0.0.1:5432/erp_agent
POSTGRES_REQUIRED=true
ERP_API_BASE_URL=http://127.0.0.1:8080/api
ERP_MCP_URL=http://127.0.0.1:8001/mcp
DISABLE_WEB_SEARCH=true
DISABLE_CHART_MCP=true
```

URI 中密码的特殊字符需 URL 编码。本地图表不依赖外部图表 MCP。可选网络搜索、图表服务应在基础流程正常后单独配置。

**为什么要求 PostgreSQL：** 虽然 checkpoint / Store 有内存降级，网页最终答案发送前还需要保存展示历史；无 PostgreSQL 可能得到 `HISTORY_SAVE_FAILED`。完整运行应让就绪探针报告 `persistent=true`。

## 4. 启动应用

```powershell
.\.venv\Scripts\python.exe run_local.py
```

启动器先执行 `backend.migrate` 的增量建表，再启动：

| 服务 | 默认地址 |
| --- | --- |
| ERP 业务 API | `http://localhost:8080` |
| ERP MCP | `http://127.0.0.1:8001/mcp`，端口占用时从 8001 起选空闲端口 |
| Web API | `http://localhost:8090` |
| Vue 前端 | `http://localhost:3000` |

8080、8090、3000 必须空闲。启动器会把实际 MCP 地址传给子进程，等待 Web readiness；关闭时按 Ctrl+C。

手动启动 Web API（已启动 ERP、MCP，并设置环境变量时）：

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m uvicorn api_view.web_main:app --port 8090 --loop api_view.event_loop:selector_loop
```

Windows 使用该 SelectorEventLoop 工厂以兼容 Psycopg 异步连接。

## 5. 创建账号并建立业务数据

打开 http://localhost:3000 。首次显示“创建管理员”，密码至少 12 字符。随后在账号管理中创建采购员、仓管员和另一位审批人；创建人不能自审。

空数据库不会自动出现供应商、物料和订单。先通过主数据维护 / 申请与审批建立供应商和物料，再走采购、审核和收货流程。发布副本保留空的供应商报价 URL 映射，需按业务自行填写。

可从只读查询开始，例如“查看库存预警”“查询今天的收货记录”。创建订单会经过信息补充和执行确认，随后仍需正式审批；不要将确认面板当成业务审核。

## 6. 检查就绪状态

```powershell
Invoke-RestMethod http://localhost:8090/health/ready
```

检查 MySQL、ERP MCP、模型配置与 PostgreSQL 状态。`model.configured=true` 只表示变量存在，没有发起模型请求；实际模型可用性须通过登录后的对话另外验证。

## Docker Compose

已有六服务编排可用于容器部署，只有前端默认绑定到 `127.0.0.1:3001`。

```powershell
Copy-Item .env.docker.example .env.docker
# 编辑 .env.docker 后执行
# Compose 当前要求额外的 POSTGRES_APP_USER / POSTGRES_APP_PASSWORD，见下文

docker compose --env-file .env.docker config --quiet
docker compose --env-file .env.docker up -d mysql postgres
```

填好 MySQL、PostgreSQL 和模型配置。**当前 Compose 的 Web 服务读取 `POSTGRES_APP_USER` / `POSTGRES_APP_PASSWORD`，原示例缺少这两项；发布副本已补上空字段与说明。** Compose 不会自动为另一个应用账号建角色：先在 PostgreSQL 中创建该角色并授予目标库 / schema 权限，再启动其余服务。本地演示也可让这两项使用初始化 PostgreSQL 账号的相同用户名 / 密码，但正式部署应使用独立权限受限账号。

```powershell
docker compose --env-file .env.docker up -d --build --wait --wait-timeout 300
docker compose --env-file .env.docker ps
Invoke-RestMethod http://localhost:3001/health/ready
```

本次未重建或启动容器，不能将历史部署记录当成本次验证。MySQL 空 schema 仅在新卷初始化；已有卷由 `backend.migrate` 增量建表。普通 `down` 保留数据卷；生产升级前备份数据。

当前 Compose 未透传所有认证环境变量。自定义域名 / HTTPS 部署需在 backend 与 web 的配置中显式传入 `AUTH_ALLOWED_ORIGINS`、`AUTH_COOKIE_SECURE=true`，并配置可信入口和 TLS。

## 常见问题

| 表现 | 检查方向 |
| --- | --- |
| 登录页正常但业务表不存在 | 先导入基础空 schema，再执行 `python -m backend.migrate` |
| readiness 失败 | MySQL 权限、MCP 实际端口、模型变量、PostgreSQL 建表权限 |
| `HISTORY_SAVE_FAILED` | PostgreSQL 连接与展示历史表；内存模式不能替代完整持久链路 |
| 工具 401 / 403 | 是否登录、会话是否有效、当前角色权限；操作人不能由提示词伪造 |
| 自己无法审批 | 由另一位有审批权限的账号操作 |
| Docker 提示 POSTGRES_APP 变量缺失 | 补填变量，确认对应数据库角色真实存在 |
| 网络搜索或外部图表不可用 | 可关闭可选服务；本地图表仍可用 |

测试命令与跳过项见 [测试与验证](docs/testing.md)。
