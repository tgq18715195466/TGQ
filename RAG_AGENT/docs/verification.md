# 本地验证记录（2026-09-23）

## 范围与环境

用户确认“尚未部署，先完成本地可验证部分”。Windows / PowerShell7.6.5 / Python3.12.14 / PostgreSQL18.6。
原目录为空，无历史测试基线；创建独立zhanggui_dev数据库，未改动用户既有数据库。

## 已执行命令与结果

```text
.venv\Scripts\python.exe scripts/run_local_tests.py -q
54 passed, 2 skipped, 1 warning（7.84秒，2026-09-23复验）

.venv\Scripts\python.exe -m pip check
No broken requirements found.

.venv\Scripts\ruff.exe check app scripts eval tests --select F
All checks passed!

.venv\Scripts\python.exe -m compileall -q app scripts eval alembic
退出码0

.venv\Scripts\python.exe -m alembic upgrade head --sql
退出码0；生成.runtime/migration.sql

.venv\Scripts\python.exe scripts/check_forbidden.py
Forbidden runtime dependency scan: PASS

.venv\Scripts\python.exe eval/run_eval.py --predictions eval/expected/fixture_predictions.jsonl
3 cases calculated; fixture_only=True
```

pytest warning来自Starlette/AnyIO的BlockingPortal弃用提示，当前测试通过；未通过过滤器隐藏它。
跳过的两项明确要求真实外部服务：Milvus/Neo4j/Embedding写查删，以及真实MinerU文本解析。没有把跳过算为通过。

## 测试证据

- `test_storage.py`：全表租户字段、Blob延迟加载/租户内去重、发布需两类收据和质量通过、KB权限、幂等键冲突。
- `test_postgres.py`：真实应用账号无RLS绕过、跨租户RLS与复合外键、真实中文FTS、连接重启持久数据和SSE、随机schema迁移升级/回滚/再升级。
- `test_pipeline.py`：真实PostgreSQL/checkpoint与明确的外部测试替身，导入READY后问答引用；模拟Milvus故障阻断发布并重启恢复；并发答案提交/硬删除序列化和敏感快照清理。
- `test_api.py`：认证/安全错误、上传幂等、越权范围、原文件下载、SSE重放、MIME伪造、禁止绕过失败扫描阶段重试。
- `test_ingestion.py`、`test_regressions.py`：文件限制和危险内容、格式归一化、MinerU块/图像资产适配、质量门、切分结构/页码/表格/公式、不虚构视觉说明。
- `test_retrieval.py`、`test_adapters.py`：RRF和召回记录、UUID过滤拒绝注入、上下文上限、证据和引用验证、确定性关系证据、历史模型向量删除、当前模型巡检不被历史向量掩盖。
- `test_workflows.py`：LangGraph阶段/恢复、Outbox重试和幂等、删除等待远端收据。
- `test_migration_tool.py`、`test_forbidden.py`、`test_evaluation.py`：迁移默认dry-run/SHA256/路径限制、运行依赖扫描、真实计算公式和未测指标null。

## HTTP与部署

本地API在127.0.0.1:8000启动；`GET /health/ready` 返回HTTP200且PostgreSQL connected。最终HTTP冒烟由本次执行记录确认。
已执行 `python scripts/smoke_local.py`：health_ready=200、openapi=200、unauthorized=401、authorized_kb_list=200。API在最终代码更新后重启，后台启动器PID保存在.runtime/api.pid，日志在.runtime/api.stderr.log。
Compose使用PyYAML解析成功，列出postgres/redis/neo4j/milvus/migrate/api/worker/mineru；没有Docker可执行程序，因此没有运行docker compose config/build/up。YAML解析不等于Compose运行验证。

## 不作结论的项目

没有真实业务评测集、评分模型、模型权重或已部署解析/索引服务。没有OCR准确率、真实Recall/MRR/Faithfulness、百万chunk延迟、并发能力、RPO/RTO结果。不会以3条手工fixture预测代替这些指标。
全部未完成项与AT01–AT16分层状态见implementation_status.md。

## React 迁移复验（2026-09-25）

- `npm run test --prefix web`：14 passed；`npm run typecheck --prefix web`、`npm run build --prefix web`：通过。
- `.venv\Scripts\python.exe scripts/run_local_tests.py -q`：58 passed，2 skipped，1条第三方弃用警告。
- `.venv\Scripts\python.exe -m ruff check app scripts eval tests --select F` 与 `scripts/check_forbidden.py`：通过；默认不带 `--select F` 的全量Ruff检查报告100项样式问题。
- `.venv\Scripts\python.exe scripts/smoke_local.py`：HTTP健康、认证、构建后的React资源与业务列表均通过。Vite开发服务曾在临时5175端口返回HTTP200；临时服务已停止，常规开发默认端口为5173。

## 用户名密码登录复验（2026-09-30）

- Alembic 从 `0001` 升至 `0002 (head)`；新增凭据表使用固定认证租户的强制 RLS，真实 PostgreSQL 测试证明业务租户和未设置租户的连接均无法读取凭据。
- `.venv\Scripts\python.exe scripts/run_local_tests.py -q`：66 passed，2 skipped，1条第三方弃用警告。登录测试覆盖有效/错误密码、未知用户、停用用户、失败保护状态及正确密码恢复、每来源哈希前限流、未登录 Agent 拒绝。
- `npm run test --prefix web`：17 passed；登录页测试覆盖正确登录、错误密码、拒绝旧 URL 令牌。
- `.venv\Scripts\python.exe scripts/smoke_local.py`：以本地随机密码完成真实登录，`/me`、知识库和 Agent 会话 API 验证通过；未登录请求401。页面已在浏览器确认显示用户名、密码输入。
