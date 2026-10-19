# 数据模型

`app/models.py` 定义31张表。`alembic/schema_v1.py` 是初始迁移的冻结快照；0002新增登录凭据表。

- 身份和范围：tenants、users、roles、user_roles、knowledge_bases、kb_members、login_credentials。
- 文件和版本：documents、document_versions、document_blobs。
- 解析：parsed_pages、parsed_blocks、document_assets、chunks。
- 任务一致性：ingest_jobs、job_steps、sync_outbox、reconcile_issues。
- 问答：chat_sessions、messages、query_runs、retrieval_candidates、answer_citations、feedback、run_events。
- 评测审计：eval_datasets、eval_cases、eval_runs、eval_results、audit_logs、idempotency_records。

PostgreSQL 使用 UUID、JSONB、BYTEA、TEXT[]、UUID[] 和 TSVECTOR；SQLite 仅用于纯逻辑单元测试，不能替代 PostgreSQL 的 RLS/FTS/锁/迁移集成测试。

`login_credentials` 的用户名全局唯一，关联已有业务租户和用户；记录只存随机盐 PBKDF2-SHA256 哈希、失败次数和锁定到期时间。该表的 `tenant_id` 固定为内部认证租户 UUID，仍强制 RLS；登录成功后访问用户表时切换到用户自己的租户范围。

Blob 与 asset 的 data 字段均为 deferred；常规文档列表不访问 bytes。业务外键按 `(tenant_id, id)` 组合约束；tenant/id 组合唯一，其他业务唯一约束包含 tenant_id。逻辑 UUID 主键全局唯一。

chunks 有 `(tenant_id,kb_id,version_id)`、文档版本序号索引、search_vector GIN 和未删除部分索引。中文 jieba 分词写 search_tokens；触发器产生带标题权重的 tsvector；检索 ts_rank_cd 排序后回表鉴权。

引用是不可漂移的快照：document_id、document_version、document_name、chunk_id、页码、章节、摘录、answer_version。文档软删除/新版本发布不改历史引用；硬删除按照删除策略清除有关摘录与历史答案。

`query_runs` 保存问题、KB范围、路由、模型/Prompt版本、检索配置、阶段耗时、token用量、状态与错误。候选保存来源、原始排名/分数、RRF、重排分数及是否选入。

## 迁移

`python -m alembic upgrade head`；只在可丢弃的开发数据库回滚：`python -m alembic downgrade base`。
回滚会删除本项目所有业务表，不能用于有真实数据的生产库。生产回退应恢复已验证备份或使用前向修复迁移。
checkpoint 使用 `python scripts/setup_checkpoints.py` 独立执行框架迁移，再加租户策略。
