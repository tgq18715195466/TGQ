# 实施状态（2026-09-30）

> 2026-10-19 发布校注：以下正文为历史验收记录。2026-10-11 已补充真实 MinerU / Milvus / Neo4j / 模型验证，见 [本机依赖](local_dependencies.md)。当前关键词使用 jieba + BM25；总结 / 对比路径已支持改写和数字检查，不再全部采用逐字摘录。此次复验及上传范围以 [发布说明](publication.md) 为准，生产验收仍未全部完成。

## 结论

已从空目录创建可安装、可测试的应用后端，并在本机独立PostgreSQL数据库执行迁移与集成测试；同源中文业务前端已迁移为 React + TypeScript + Vite。
**尚未达到任务书第22节“全部完成”条件。** 已写出的适配器不等于真实外部系统已通过验收；没有伪造OCR、RAGAS、性能或真实索引测试结果。

用户在2026-09-23确认外部服务尚未部署，要求“先完成本地可验证部分”。本轮按该范围交付，其余保留为明确的后续工作。

## 已实际验证

- 业务页面：原业务功能已通过浏览器或API验证；2026-09-30 改为用户名密码登录，React 登录表单与真实 PostgreSQL 登录 API 已复验。
- 最新回归：66项Python测试通过，2项外部集成跳过；17项React前端测试、TypeScript检查通过；生产构建和浏览器验收以 `docs/verification.md` 最新记录为准。
- Python 3.12.14；项目依赖安装成功且pip check通过。
- PostgreSQL 18.6，隔离数据库zhanggui_dev，应用角色NOSUPERUSER/NOBYPASSRLS。
- 31张业务表（含0002登录凭据表）及PostgreSQL LangGraph checkpoint基础设施；随机schema升级、回滚、再升级通过。
- 多租户RLS、跨租户复合外键、Blob持久化、FTS、连接重建后数据可读。
- 上传幂等、授权下载、伪造MIME拒绝、安全错误、SSE Last-Event-ID重放。
- 原生PDF/图片/Office容器格式校验、宏/外部引用拒绝、扫描默认关闭执行链路、文本/图片/PDF归一化、MinerU契约与质量门。
- 章节、正文、稳定ID、跨页表格、rowspan组、无文字图片不虚构；公式保持原子性；已修复真实API资产basename映射和附加块类型。
- 实际PostgreSQL上的导入—发布—问答—引用—SSE编排；其中MinerU和索引/模型明确使用测试替身。
- 模拟向量失败时不发布，重建连接/重启执行器后从同步阶段恢复。
- 硬删除与答案提交并发回归：文档行锁串行化，删除后无引用/事件正文残留。
- 禁止旧运行依赖检查通过；无现有旧组件需要物理删除。

## 核心验收用例分层记录

- AT01：解析/切分结构单元与编排fixture通过；真实复合原生PDF到READY未测。
- AT02：图片归一化、OCR/图注白名单、无虚构描述通过；真实中文图片OCR质量未测。
- AT03：Office格式与宏/外部关系安全校验通过；真实LibreOffice转换未测。
- AT04：上传/Blob幂等、稳定chunk和外部upsert实现；真实向量/图重复写入未测。
- AT05：原子发布门与版本序号保护已测试；真实新旧索引切换未测。
- AT06：MinerU HTTP429重试属性、持久阶段恢复已测试；真实MinerU超时+恢复未测。
- AT07：模拟Milvus故障阻止READY并可恢复通过。
- AT08：跨页表头/行组/页码单元测试通过；真实表格问题回答未测。
- AT09：1–2跳Cypher带tenant/version/ACL及逐边source_chunk_id检查；真实Neo4j执行未测。
- AT10：证据不足门与无支持引用拒绝单元测试通过；真实模型无答案集未测。
- AT11：API/RLS/过滤构造及下载安全测试通过；完整真实外部索引越权矩阵未测。
- AT12：Prompt隔离与无工具定义、逐字证据校验已实现；通用Prompt Injection攻击评测未完成。
- AT13：递增事件与断线重放、事务完成顺序通过。
- AT14：数据库重连/执行器重启持久状态通过；本机无Redis，真实FLUSHDB+API重启场景未测。
- AT15：删除补偿、历史collection覆盖和并发快照清理测试通过；真实三存储硬删除未测。
- AT16：运行代码、依赖、部署、示例环境旧依赖扫描通过。

## 未完成的功能与限制

1. 真实MinerU、Milvus、Neo4j、模型/TEI、Scanner和Office部署联调；已提供可启用集成测试和Compose。
2. 通用自然语言图关系抽取；当前为有明确证据的规则抽取器，不会猜测关系。
3. 通用语义矛盾/问题条件覆盖检测和自由概括答案；当前保守逐字摘录及有限冲突规则。
4. Embedding模型在线双写、alias自动切换和回滚管理；当前按模型/维度隔离并可清理历史collection，迁移需维护窗口。
5. MCP外部结果的完整来源模型、引用和答案生成；当前只做双开关受控调用，不能将其当完整外部搜索问答。
6. 真实300条人工数据、RAGAS运行、业务质量阈值、100万chunk与并发/首字性能、RPO/RTO演练。
7. 生产用户/权限和配置管理接口，以及这些离线动作的自动业务审计；目前仅本地管理CLI/已有业务API审计。
8. UTF-8字节上界token预算保守低于实际模型500–800 token目标；当前Markdown按文本渲染。模型专用tokenizer尚未配置。
9. 裸机转码进程的操作系统沙箱、崩溃临时目录TTL清理、生产遥测导出与企业告警接入；Compose给出基本资源边界。
10. 前端现支持本地业务流程和用户名密码登录；生产 SSO、多因素认证、统一登录限流、用户/权限管理页面、在线评测数据集编辑、海量数据虚拟列表仍未实现。真实生成答案、引用和评测结果显示需要上述外部服务，不将其标为已通过浏览器全链路验收。

## 评测

knowledge_qa_v1只有3条fixture，指标计算器已执行。手工fixture预测只能验证计算逻辑；不能用于宣称Recall/MRR/Citation目标达成。RAGAS Faithfulness、真实OCR和全部性能指标未测。基线无旧系统，无法提供改造前后质量差值。

## 实际文件范围

- app/：API/认证/配置、31表ORM、Repository、导入模块、LangGraph执行器、Worker/Outbox/巡检、模型与索引适配、检索问答、Redis临时计数、评测。
- web/src/：React + TypeScript 业务组件、API客户端、SSE解析及前端测试；Vite生成 web/dist/ 由FastAPI提供。scripts/open_workspace.py 提供本地开发登录。
- alembic/：冻结schema、0001与0002 migration、RLS、FTS触发器；scripts/setup_checkpoints.py创建框架表和租户策略。
- scripts/：本地隔离DB初始化、管理CLI、旧数据dry-run导入、依赖禁用检查、测试启动、压测和数据生成。
- tests/：单元、API、安全回归、真实PostgreSQL、带明确外部替身的编排集成、可选真实外部服务测试。
- Dockerfile、compose.yaml、deploy/、.env.example、pyproject.toml、requirements.lock、CI。
- README与docs/九份交付文档、eval数据格式/fixture/运行器。

执行明细以最终复验记录 `docs/verification.md` 为准。当前目录原本无Git，本次未创建提交或远程仓库。
