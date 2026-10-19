# 架构与 ADR-001

这是在空目录中新建的 Python 3.12 / FastAPI 项目。没有旧代码可删除；业务前端现由 React 提供，`/docs` 为 OpenAPI。任务书的技术边界是设计依据。

PostgreSQL 为唯一业务事实源：原文件、规范化 PDF、页面块、资产、完整 chunk、会话消息、任务、Outbox、持久 SSE、评测、审计均落库。所有业务表均有租户字段，业务查询显式过滤，强制 RLS 在遗漏过滤时仍阻止跨租户读取。应用角色不得拥有 SUPERUSER/BYPASSRLS。

LangGraph 导入图和问答图使用 PostgreSQL checkpointer。checkpoint 仅保存租户、任务/运行 ID 和起始阶段，不保存文档正文。框架表在 `workflow` schema，以 thread_id 的租户前缀执行 FORCE RLS；框架迁移版本表不属于业务表。

Worker 由 PostgreSQL 持久任务驱动，不依赖 Redis 队列可靠性。每租户显式启动 Worker；PostgreSQL 会话 advisory lock 串行处理同文档，进程崩溃释放锁。阶段业务状态已落库，重启从当前阶段幂等执行。取消在阶段边界生效，已发出的模型 HTTP 请求最多等待超时。

Milvus 每模型/维度独立 collection，只保存向量、ID、页码、ACL 等标量。正文必须回 PostgreSQL 取。图谱保存 Document/Chunk/Entity/Alias 和证据关系；确定性抽取器只识别显式实体标签、别名和 `[主体] --关系--> [客体]`，不声称具备通用自然语言关系抽取能力。

Redis 仅提供可选短期限流计数，默认关闭以支持当前没有 Redis 的开发机。启用后 Redis 故障不丢业务数据，但请求可能失败；需按运维手册配置。

## ADR-002：文件与迁移

Blob 分表并延迟加载；50 MB / 500 页默认限制。SHA256 在租户内按角色去重。下载经用户和 KB 权限校验。硬删除等待两个外部存储完成，再清理正文、资产、Blob 和关联历史答案/事件快照；普通软删除保留历史记录。

旧系统以离线导出 manifest 迁移，工具默认 dry-run；不安装旧客户端、不访问旧基础设施、不自动删除来源数据。没有旧生产数据，本次未执行真实迁移。

## ADR-003：发布与一致性

chunk 与两类 Outbox 在同一事务写入。外部调用完成后提交 DONE 收据；远端超时后可安全重试确定性主键 upsert。发布锁定 Document 行，要求质量通过、非空 chunks、Milvus 与 Neo4j 所需收据全部 DONE，再切换 current_version_id。旧版本在新版本发布前仍可查询。

reconciler 比较 ID 集合，生成重同步/删除补偿，连续差异超过15分钟写错误日志。Milvus 删除枚举本项目所有历史模型 collection，避免更换模型后遗留旧向量。版本查询仅使用当前配置模型 collection。

## 已知边界

业务 Web 前端使用 React 19 + TypeScript + Vite，源码在 web/src/，构建产物在 web/dist/，由 FastAPI 同源提供。开发服务器通过 Vite 代理 API。登录页提交用户名密码到认证 API，PostgreSQL 的独立凭据表保存加盐密码哈希并使用固定内部租户 RLS；成功后签发短期 JWT。所有业务数据请求使用 Bearer 认证；会话历史重新验证知识库与引用文档权限。前端使用 React 文本节点展示文件和模型内容。
尚未构建企业 SSO、通用自然语言图抽取、在线模型双写+alias 无缝迁移、生产 Kubernetes/Helm。原任务书未要求已有设施之外必须新增 Kubernetes；当前提供 Compose。模型迁移当前需维护窗口与完整重建，不能声称支持无缝切换。
事实类生成采用严格摘录模式，每句必须逐字存在于所有引用证据。当前总结 / 对比路径允许改写，校验存在引用及数字出现在引用片段中；这不能证明非数字语句的语义忠实度，也不是通用事实验证。最终持久化仍重新检查所引用片段的授权范围。
MCP 搜索具有双开关和受控工具调用，目前只记录调用状态，不将外部结果作为已验证内部引用或答案；外部证据引用模型仍未实现。

## 实现时核对的官方接口

- LangGraph persistence：https://docs.langchain.com/oss/python/langgraph/persistence
- MinerU API：https://github.com/opendatalab/MinerU/blob/master/mineru/cli/fast_api.py
- MinerU pipeline blocks：https://github.com/opendatalab/MinerU/blob/master/mineru/backend/pipeline/pipeline_middle_json_mkcontent.py
- Milvus 本地 Woodpecker：https://milvus.io/docs/use-woodpecker.md

这些参考用于适配边界，实际服务联调仍需通过外部集成测试。
