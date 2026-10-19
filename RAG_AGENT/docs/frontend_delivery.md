## 完成结果

已补齐可登录的中文业务工作台，并连接本地 PostgreSQL 后端；可管理知识库、上传文档、查看和取消任务、保存问答会话、查看评测及服务状态。业务入口为 http://127.0.0.1:8000/ 。这完成了本轮本地可验证的应用补充，不代表任务书所有外部系统验收完成。

## 主要修改

- web/src/：React + TypeScript 响应式组件（工作台、知识库、文档管理、问答与引用、评测、设置）；同源请求、纯文本渲染、SSE恢复、会话切换、令牌退出清理。Vite构建输出 web/dist/。
- app/api.py：静态入口、个人信息、分页文档列表、会话历史、评测列表、服务状态。文档权限过滤先于分页，批量取得最新版本与任务；历史引用重新检查文档权限。
- scripts/start_local.ps1、scripts/open_workspace.py：本地后台启动与用户名密码登录页；Dockerfile加入前端资源。
- tests/：列表/历史/评测授权回归、真实PostgreSQL分页权限测试、SSE传输分片与恢复测试。

## 数据库迁移

2026-09-30 登录改造新增 `0002_login_credentials.py` 迁移及 `login_credentials` 表，当前共31张业务表。新环境执行 `python -m alembic upgrade head`；回滚命令 `python -m alembic downgrade base` 仅适用于隔离测试环境，会删除业务表，不应在当前开发数据上执行。

## 删除的旧组件

初始目录为空，无现存 Qwen-VL、MinIO、MongoDB 组件需要物理删除。`python scripts/check_forbidden.py` 通过，无这些旧组件的运行依赖。

## 测试结果

- `.venv/Scripts/python.exe scripts/run_local_tests.py -q`：66 passed，2 skipped。跳过的是未配置外部服务的真实集成测试；保留1条第三方库弃用警告。
- `npm test --prefix web`：17项通过，覆盖用户名密码登录、API认证生命周期、文档交互、会话切换、弹窗输入和SSE分片恢复。
- `npm run build --prefix web`：TypeScript检查与Vite生产构建通过；Ruff F规则检查、运行中API/页面冒烟检查通过。
- 浏览器实际验证：React 构建在 8000 端口打开，当前登录页显示用户名与密码输入并保留原有布局；真实 PostgreSQL 账号登录由 HTTP 冒烟检查验证。知识库创建、上传、任务取消、问题入队与历史读取另由后端和前端交互测试验证。
- 验收保留1个明确命名的测试知识库和测试文档，以及1个已取消问答；不含用户业务文件，不冒充真实知识解析结果。

## 评测结果

现有 knowledge_qa_v1 为3条fixture；本轮未新增真实业务数据和模型评测。不报告真实 Recall/MRR/RAGAS、性能或相对旧系统基线提升。页面只展示数据库实际结果，没有生成演示指标。

## 启动方式

保留当前 `.env`。在项目目录执行 `powershell -ExecutionPolicy Bypass -File scripts/start_local.ps1` 可迁移数据库、准备演示登录账号、检查/启动API并打开本地登录页面。初始 `admin` 密码存于 `.runtime/local_login.txt`。API已运行时可仅执行 `.venv/Scripts/python.exe scripts/open_workspace.py`。

手动构建：`npm ci --prefix web`、`npm run build --prefix web`；随后执行 `.venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8000`。业务入口 `/`，接口文档 `/docs`，数据库健康检查 `/health/ready`。开发时 `npm run dev --prefix web` 提供5173端口热更新并代理API。

## 剩余问题

MinerU、Milvus、Neo4j、Embedding、Reranker、LLM、扫描器和Office转换尚需部署联调，Worker需按运维说明运行。文件任务入队不等于解析完成，问答入队不等于产生真实回答。生产SSO、权限管理页面、在线数据集编辑和海量列表优化未完成。完整边界见 implementation_status.md。
