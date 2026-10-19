# 离线数据迁移（ADR-002）

基线目录为空，未发现 Qwen-VL、MinIO、MongoDB 的旧运行链路或真实数据。本次没有删除现有生产基础设施，没有执行旧数据迁移。

旧 MinIO 原文件映射 document_blobs，解析图片映射 document_assets；MongoDB 任务映射 ingest_jobs/job_steps，会话映射 chat_sessions/messages，中间结果映射 parsed_blocks/chunks，查询记录映射 query_runs/retrieval_candidates。Qwen-VL 图片 Summary 不迁移到新语义索引。

`scripts/migrate_legacy.py` 接收离线 JSON manifest。每个顶层键是已支持的目标表名，值是记录数组；UUID 和所有引用由导出工具转换为稳定 UUID，时间为 ISO8601，JSON字段为 JSON 对象。blob/asset 用相对 `data_path` 指向 manifest 同目录下文件，并必须提供 SHA256。禁止跳出目录或导入其他租户。

示例结构：

```json
{"document_blobs":[{"id":"00000000-0000-0000-0000-000000000001","tenant_id":"00000000-0000-0000-0000-000000000002","sha256":"文件的真实SHA256","mime_type":"application/pdf","blob_role":"original","data_path":"files/source.pdf"}]}
```

先准备父级 tenants/users/knowledge_bases/documents 等记录，再执行：

```powershell
python scripts/migrate_legacy.py export/manifest.json --tenant <UUID> --dry-run --report migration-dry-run.json
python scripts/migrate_legacy.py export/manifest.json --tenant <UUID> --commit --report migration-committed.json
```

缺省等同 dry-run，不连接源服务。commit 按对象事务提交、稳定主键跳过已存在对象；失败记录对象和安全错误类型，重跑从已有主键断点继续。工具不覆盖目标已有对象，也不删除源文件。
导入文档 current_version_id 被置空、版本 QUEUED，必须重新 MinerU/切分/Embedding/图谱/发布；导入 ingest_jobs 应使用 validate_file/QUEUED。不要把历史 READY 状态直接当新索引已完成。

报告包含文档、版本、Blob、chunk等输入计数及成功/已存在/错误数量；向量/图节点数在未连接外部服务时为 null，不能伪造对账成功。重建后运行 `python -m app.worker --tenant <UUID> --reconcile`，并抽样校验内容和历史引用。最后人工决定是否清理旧设施。

该工具是可运行的导出格式导入器；针对未知旧 MongoDB collection 和 MinIO 元数据的自动发现/导出器尚未实现，需要提供实际旧 schema 后编写映射。禁止依赖扫描豁免本迁移文档和检查器自身，运行代码/环境/部署不得残留旧客户端。
