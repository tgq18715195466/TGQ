# 问答链路

入口 POST `/api/v1/chat/runs` 创建持久 QueryRun 和用户消息；Worker 执行 `app/query.py`。
LangGraph 节点：authorize_scope → classify_query → rewrite_query → 三路并行召回 → fuse_candidates → rerank_candidates → build_context → check_evidence → optional_web_search → generate_answer → verify_citations → persist_answer → stream_result。

权限来源为签名 JWT、有效用户、KB membership 和文档 user_ids ACL。API、PostgreSQL 显式条件/RLS、Milvus 标量、图查询和最终回表均校验；不接受用户传入 Milvus 表达式或 Cypher。最终提交锁定相关 Document 和 QueryRun，防止硬删除/取消与答案提交竞态。

关键词默认30、向量40、图30、最多2跳、RRF k=60、重排最多50、最终上下文10、每文档最多4。摘要优先不同章节，比较优先不同文档，表格轻量加权。所有默认值在 Settings，运行时配置快照入库。

BGE 重排接兼容 TEI `/rerank`。超时保存降级原因，保留 RRF 顺序。缺少可校准的重排分数时证据门保守拒答。证据门检查前三得分、最高分、召回一致性、问题数字/编号覆盖，并保存结果；目前冲突检测是有限规则，不能承诺通用矛盾检测。

模型接口为 OpenAI-compatible `/embeddings` 和 `/chat/completions`，没有工具定义。材料放在 JSON evidence 中，System Prompt 明确隔离不可信文本。事实问答限定逐字摘录；总结 / 对比允许改写并核对数字与引用片段。总结路径的检查不等同于完整语义事实验证。通过对应校验并重新授权后才会持久化 answer.delta。

SSE 事件在 PostgreSQL 有递增 sequence，Last-Event-ID 只回放较大序号。最终 message、citation、run状态和 answer.delta/replace/completed 原子提交；API 轮询已提交事件。采用校验后流式发送，非模型 token 原样实时透传，因此首字符延迟必须实测，当前未达到或承诺5秒目标。

支持 run.started、run.stage、citation.ready、answer.delta、answer.replace、run.completed、run.failed。取消结果不会覆盖已经完成的回答。客户端错误只返回安全消息、request_id、error_code、retryable；原始异常不回传。

MCP 默认关闭，并要求请求显式开启。当前 MCP 搜索仅调用管理员配置的 search 工具，不允许文档触发工具或提升内部权限；外部结果尚未纳入可验证答案。


## 2026-10-12：jieba + BM25 关键词召回

关键词通道改为 app/keyword.py 的应用内 Okapi BM25（正值 Robertson IDF，k1=1.2，b=0.75）。查询与正文统一使用 jieba 分词、大小写归一化、过滤纯标点；不要求所有查询词都匹配。按 chunk 计算词频与长度，按 BM25 分数取 Top-K，再沿用 RRF 和 reranker。

先调用 authorized_chunks，按租户、知识库成员、文档 ACL、当前版本及删除状态确定语料，再计算 IDF 和平均长度，未授权内容不参与评分。并列分数按 ID 稳定排序。当前使用 chunk 正文，不继承旧 FTS 的标题字段权重。

无需迁移或重建向量索引；旧 search_tokens/search_vector/GIN 保留但不再参与线上关键词召回。运行配置快照记录 keyword_algorithm、bm25_k1、bm25_b。需要重启 Worker 加载新代码；API 重启后使用新的配置快照。

限制：每次查询加载授权 chunk 正文并分词，成本随授权语料增长，不宣称适用于百万片段。暂未引入缓存、独立搜索服务或持久倒排索引，以避免权限与版本缓存失效问题。生产扩容前需评测延迟与内存。
