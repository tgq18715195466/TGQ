# 掌柜智库实施计划

## 扫描基线（2026-09-22）

工作目录 G:/AgentProject/RAG_AGENT 为空；rg --files --hidden 无文件输出；git status 报告非 Git 仓库。
根目录及父目录未找到 AGENTS.md。没有 README、应用代码、依赖、环境变量、部署、迁移或测试。
因此 Qwen-VL、MinIO、MongoDB、上传/解析/问答入口、Milvus/Neo4j/Redis 封装、队列、会话结构均不存在。
Node v24.15.0，PowerShell 7.6.5；PATH 无 Python、Docker、PostgreSQL 和 LibreOffice。
使用 Codex 自带 Python 建立项目独立虚拟环境，不修改系统 Python。
基线测试：无现有测试可运行，不将其记为通过。

## 决策

遵从用户已指定任务书的架构和继续执行授权，无需再审批同一设计。
当前为空项目，直接在当前目录实施，不创建额外工作树。无前端框架可复用，本版提供 API、OpenAPI 和运维 CLI。
FastAPI + SQLAlchemy 2 + Alembic；PostgreSQL 为业务、任务、事件及 checkpoint 事实源。
Worker 从 PostgreSQL 领取持久任务，Redis 仅限流/短期通知；LangGraph 执行导入与问答。
MinerU、模型、Milvus、Neo4j 都使用真实适配器；fixture 仅用于测试，不以模拟结果冒充线上验收。
默认严格 READY：向量与图谱同步成功才发布。JWT 认证与多层租户/成员权限控制，外部搜索默认关闭。

## 文件级阶段计划与验证

- [x] 0 基线/设计：本文件、docs/architecture.md、docs/migration_guide.md；记录环境能力。
- [x] 1 PostgreSQL：app/models.py、app/db.py、app/repository.py、alembic/；schema、权限、事务、版本发布和真实PostgreSQL迁移测试通过。
- [x] 2 旧依赖：scripts/check_forbidden.py、tests/test_forbidden.py、迁移导入工具、CI；空基线无旧组件，运行依赖扫描通过。
- [ ] 3–4 导入：app/ingestion/、app/adapters/；先编写格式校验、MinerU 契约、质量门、章节、表格、chunk ID 测试，再实现与验证。
- [ ] 5–6 检索/问答：app/retrieval/、app/workflows.py、app/worker.py、app/api.py；验证 ACL、RRF、证据门控、引用、SSE 重放、Outbox 重试、原子发布。
- [ ] 7 评测/运维：eval/、scripts/、compose.yaml、Dockerfile、docs/；提供少量标注 fixture 与真实服务集成测试，记录未运行项。
- [ ] 最终：全量本地单元/安全测试、迁移 SQL 编译、API 冒烟、禁止依赖扫描、独立审查；逐项记录 AT01–AT16 的实际验证层级。

验证命令：python -m pytest；python -m compileall app scripts eval；alembic upgrade head --sql；python scripts/check_forbidden.py。
真实集成命令：设置 TEST_POSTGRES_DSN 和 RUN_INTEGRATION=1 后 python -m pytest -m integration。
没有真实业务数据时不捏造 300 条样本、RAGAS 分数或百万 chunk 性能数字。

2026-09-23：用户确认外部服务尚未部署，先交付本地可验证部分。阶段3–7的本地实现/测试已落地，阶段整体不勾选完成；真实联调及功能边界见implementation_status.md。最终本地复验见verification.md。
