# 运维手册

## 当前机器

已探测并连接本机 PostgreSQL 18.6，服务 `postgresql-x64-18`，安装在 `G:/postgreSQL`。
创建独立 `zhanggui_dev` 数据库及 `zhanggui_app` 非超级用户应用角色，未修改既有数据库。
真实凭据仅在被忽略的 `.env` 中，示例文件不包含它们。Python使用项目 `.venv`，版本3.12.14。
2026-10-11 更新：DeepSeek 已通过真实模型调用；Milvus、Neo4j、MinerU、Embedding、Reranker 已部署到 G 盘，并通过模型调用和项目真实外部服务集成测试。当前使用 compose.dependencies.yaml 与原生 MinerU，路径、资源限制及启动方式见 [本机依赖部署](local_dependencies.md)。此前未检测到可调用的 LibreOffice；这不等同于它在整台电脑上绝对不存在。

## 启动

```powershell
.\.venv\Scripts\Activate.ps1
python -m alembic upgrade head
python scripts/setup_checkpoints.py
python scripts/manage.py seed
python scripts/setup_local_login.py
uvicorn app.api:app --host 127.0.0.1 --port 8000
python -m app.worker --tenant <UUID>
```

已有演示租户ID保存在 `.runtime/demo.json`，无需反复 seed。本地登录账号为 `admin`，初始随机密码保存在被忽略的 `.runtime/local_login.txt`；数据库密码不可用于网页登录。执行 `python scripts/setup_local_login.py --reset` 可重置本地密码并解除锁定。
`/health/live` 检查API进程；`/health/ready` 当前只检查PostgreSQL连接，不代表解析/模型/索引全链路就绪。`/` 为中文业务工作台，`/docs` 为接口文档。

本机开发可执行 `powershell -ExecutionPolicy Bypass -File scripts/start_local.ps1` 后台启动并在浏览器登录；若 API 已运行，仅执行 `python scripts/open_workspace.py`。登录 API 验证加盐密码哈希后签发一小时令牌；启动器只打开用户名密码页，不会在 URL 中附带令牌。密码哈希前按来源限流（每进程每分钟12次）；启用 Redis 限流时跨进程共享。连续五次错误会记录十五分钟保护状态，正确密码仍可登录。生产应接入企业身份提供方和统一限流，不使用此本地账号作为生产身份体系。

前端源码在 web/src/（React/TypeScript），执行 `npm ci --prefix web`、`npm run build --prefix web` 生成 web/dist/，FastAPI 提供静态构建产物。Dockerfile 的 Node 构建阶段自动执行同一流程，Python 运行镜像只包含构建产物。修改前端后重新构建并刷新页面；无需 CDN。新增页面接口按租户、KB成员和用户归属过滤，历史引用还会重新检查文档权限。文档接口支持 offset/limit（每页最多100），权限过滤先于分页，版本与任务批量查询；前端逐页加载，超大规模工作台还需服务端聚合统计和虚拟列表。

生产必须设置真实 SCANNER_URL；协议见 ingestion_pipeline.md。DEVELOPMENT_SKIP_SCANNER 仅在 ENVIRONMENT=development 显式开启时生效。扫描失败不得跳过阶段重试；正常化要求同 SHA256 的扫描记录。Office转码运行于Worker，需安装LibreOffice。当前子进程参数不能替代操作系统进程沙箱。

## Compose

填写 `.env.example` 的所有占位值，尤其 Compose独立密码、JWT、模型端点。`docker compose up -d postgres redis neo4j milvus` 启动依赖；数据库容器对宿主使用15432，避免覆盖本机5432服务。容器内用postgres:5432。
`docker compose run --rm migrate` 创建schema，再运行seed获取TENANT_ID并填入env；为该租户中的活跃用户执行上述 `set-password` 命令后，运行 `docker compose up -d api worker`。容器场景需在可访问项目数据库的管理环境中交互执行该命令。
MinerU 为可选 parser profile；需在独立机器验证版本/模型与GPU资源，设置已审计镜像digest替换示例latest，然后 `docker compose --profile parser up -d mineru`。容器API应把MINERU_BASE_URL指向 `http://mineru:8001`。模型服务由操作者配置，本项目不下载权重。
Milvus采用本地持久卷、embedded etcd、Woodpecker；Compose仅解析验证，当前机器未执行Docker构建/启动。

## 任务恢复、一致性与告警

Worker每租户启动，可多进程；PostgreSQL文档锁避免同时索引/删除。状态与步骤在ingest_jobs/job_steps；短期故障指数退避加抖动，超过次数FAILED。任务取消在阶段边界生效，保留已发布旧版本。
`python -m app.worker --tenant <UUID> --reconcile` 比较ID集合并提交补偿。长驻Worker每60秒尝试巡检，持续15分钟差异写ERROR日志；需接入企业日志告警平台。单Worker执行长文档时巡检会延后，生产应独立调度reconcile命令。
日志记录request_id或tenant/job/error_code，不记录HTTP凭据或完整SQL参数。阶段耗时写job_steps.metrics_jsonb/query_runs.timings。当前没有独立Prometheus指标服务或OpenTelemetry exporter。

## 删除

DELETE文档立即逻辑隐藏并提交Milvus/Neo4j删除Outbox，Worker成功处理后才完成跨存储清理。hard=true仅KB owner可用；完成后清理Blob、资产、解析、chunk，并清除关联历史引用、答案与SSE正文。共享Blob仍被其他文档引用时不会误删。
已交付给客户端或离线导出的历史答案无法通过服务器删除追溯撤回。审计和无正文删除收据保留。

## 备份恢复与容量

使用专用备份角色或受控管理员连接执行pg_dump，普通应用角色受FORCE RLS限制，不可假定能导出全租户。管理员密码用进程环境PGPASSWORD或受保护pgpass，禁止写入命令行参数、仓库或工单。

```powershell
pg_dump -h 127.0.0.1 -U <备份角色> -Fc -f backup.dump zhanggui_dev
pg_restore --list backup.dump
# 在另建的空数据库恢复；不要覆盖线上库
pg_restore -h 127.0.0.1 -U <恢复角色> --no-owner --exit-on-error -d <空恢复库> backup.dump
```

完整备份覆盖public和workflow schema；记录应用版本、配置版本及业务行数。对恢复库执行RLS测试、抽样SHA256、checkpoint续跑和外部索引重建/巡检。Milvus和Neo4j可从PostgreSQL重建，但本地卷应配套快照以缩短RTO。WAL归档频率需按RPO≤15分钟配置，恢复演练验证RTO≤2小时；当前未执行恢复演练或证明RPO/RTO达标。
bytea使数据库与WAL增长明显，按文件量监测pg_total_relation_size、WAL、连接池与磁盘。临时转码目录正常退出自动删除；容器/tmp为tmpfs，重启清空。裸机异常退出残留需由系统定时任务按TTL清理 `ingestion-*` 临时目录，避免删除正在使用的目录；本项目没有自动递归清理用户目录。

## 模型和权限变更

Embedding模型/维度变更生成独立collection，禁止混写。当前没有无缝双写+alias管理器，应在维护窗口保存旧配置、重建新索引、真实评测并核对ID后切换配置。删除会清理当前collection家族的旧模型向量；不要随意改变MILVUS_COLLECTION命名前缀，否则需额外迁移登记。
用户/权限修改目前需可信管理工具/数据库操作；业务API没有开放通用用户管理或配置修改接口。此类离线管理动作应由企业运维审计补充，不能声称本版自动记录所有外部配置编辑。
可为已有活跃用户运行 `python scripts/manage.py set-password --tenant <UUID> --user <UUID> --username <全局唯一用户名>`；命令从隐藏输入读取并确认密码，不接受命令行明文密码。重设密码不会立即撤销已经签发的短期 JWT，最长一小时后到期。
