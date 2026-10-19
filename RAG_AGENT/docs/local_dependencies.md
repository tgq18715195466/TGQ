# G 盘依赖部署与启动

更新时间：2026-10-11。五项依赖已安装并通过实际验证。项目 API/Worker 继续使用原有 PostgreSQL 开发数据库。

## 已安装服务

- Milvus 2.6.0：`http://127.0.0.1:19530`；健康接口 `http://127.0.0.1:9091/healthz`。
- Neo4j 5.26 Community：`bolt://127.0.0.1:7687`；管理界面 `http://127.0.0.1:7474`。用户名 neo4j，密码保存在项目 `.env`。
- MinerU 3.4.5：`http://127.0.0.1:8001`，使用 CPU pipeline，保留项目需要的 `/file_parse` 接口。
- Embedding：BAAI/bge-m3，1024 维，`http://127.0.0.1:8002/v1`。
- Reranker：BAAI/bge-reranker-v2-m3，`http://127.0.0.1:8003`。

两个检索模型使用 TEI `89-1.9`（适配 RTX 40 系列），本地 float16 Safetensors 分片权重。原始 BAAI 权重来自 ModelScope，保留在 G 盘；转换脚本只转换浮点精度，不训练模型。每个转换后的模型约 1.14GB。半精度和 TEI 运算与原始 float32 可能存在细微数值差异；业务质量需另行评测。

DeepSeek 已配置为 `https://api.deepseek.com`、`deepseek-flash`，此前真实 JSON 调用通过。密钥只保存在 `.env`，未复制到部署文件或本文。

## 实际存储位置

- Docker 软件：`G:\Docker\DockerDesktop`。
- Docker 镜像、容器及数据库卷：`G:\Docker\wsl\disk\docker_data.vhdx`。
- WSL 交换文件：`G:\Docker\wsl\swap.vhdx`。
- MinerU Python 环境：`G:\AgentProject\RAG_AGENT\.runtime\mineru-venv`。
- MinerU 模型与配置：`G:\AgentProject\RAG_AGENT\.runtime\mineru`。
- Embedding/Reranker 原始及半精度模型：`G:\AgentProject\RAG_AGENT\.runtime\tei-models`，Compose 只读挂载其中的 `embedding-fp16` 和 `reranker-fp16`。
- pip 下载缓存与临时文件：项目 `.runtime\pip-cache`、`.runtime\tmp`。

不要删除 `.runtime`：其中现在包含实际安装环境和模型，不只是临时日志。系统级 WSL 配置文件仍按 Windows 规范位于 `C:\Users\TTT\.wslconfig`；它将 WSL 内存限制为 4GB，交换空间设为 G 盘 8GB，启用 gradual 内存回收。这个限制影响本机所有 WSL2 发行版。

## 后续启动

先启动 Docker Desktop，确认 Linux 引擎可用。在项目根目录执行：

```powershell
docker compose -f compose.dependencies.yaml up -d
powershell -ExecutionPolicy Bypass -File scripts/start_mineru.ps1
```

实际部署使用 `compose.dependencies.yaml`，不需要启动另一份 PostgreSQL，也不要用旧文档的 `docker run` 示例重复创建服务。
MinerU 脚本后台启动服务并统一设置 G 盘缓存、配置、输出路径，使用已下载的本地模型；若 8001 已监听会拒绝重复启动。
API/Worker 如已运行，需要重启以读取更新后的 `.env`。

日志和状态：

```powershell
docker compose -f compose.dependencies.yaml ps
docker compose -f compose.dependencies.yaml logs --tail 50 embedding reranker milvus neo4j
Get-Content .runtime/mineru.stderr.log -Tail 30
```

Milvus/Neo4j 设置为 unless-stopped；模型容器失败最多重试三次。重新开机后执行上述启动命令。Neo4j 配置 256MB heap、128MB page cache；两个 TEI 容器分别限制 3GB 内存、并发 4。首次启动需等待健康接口返回 200。

## 验证结果

- 项目 `ModelClient` 成功生成两条 1024 维向量。
- 真实 Reranker 将退款规则排在无关会议文本之前，示例分数约 0.5003 与 0.0000162。
- `tests/test_external_services.py` 启用两项真实集成开关后：**2 passed in 5.07s**。覆盖 Milvus/Neo4j 写入、召回、租户隔离、删除，以及 MinerU 单页 PDF 解析。
- 真实测试发现 PyMilvus 返回的是自定义主键 chunk_id；已修正 `app/adapters/stores.py` 使用请求返回的 `entity.chunk_id`，重跑通过。
- MinerU 的上游 pipeline 依赖缺少 six，已补装。安装版本清单在 `.runtime/mineru-requirements.lock`。

复验命令（会写入并清理随机生成的测试数据）：

```powershell
$env:RUN_EXTERNAL_INTEGRATION='1'
$env:RUN_MINERU_INTEGRATION='1'
.\.venv\Scripts\python.exe -m pytest tests/test_external_services.py -q -p no:cacheprovider
```

记录保存在 `.runtime/models-validation.log`、`.runtime/dependencies-validation.log`。

## 本机资源与其他项目

本机约 16GB 内存、RTX 4060 Laptop 8GB 显存。初次 CPU 模型并行启动曾造成内存不足；已改为 GPU 半精度、本地权重和 WSL 内存限制。验证时两个模型合计占用约 2.7GB 显存（包含当时其他 GPU 占用）。
经用户授权，六个 `erp-docker-smoke-*` 容器已临时停止，英雄联盟客户端已关闭。ERP 暂未恢复，避免与本项目同时争用有限内存。如要切换回 ERP，先停止不使用的 RAG 服务，再执行：

```powershell
docker compose -f compose.dependencies.yaml stop
docker start erp-docker-smoke-mysql-1 erp-docker-smoke-postgres-1 erp-docker-smoke-backend-1 erp-docker-smoke-mcp-1 erp-docker-smoke-web-1 erp-docker-smoke-frontend-1
```

上述 Compose stop 不会停止原生 MinerU；如需释放其内存，应先核对 8001 端口对应进程，再停止本项目 MinerU。没有删除 ERP 数据或容器。

## 导入与问答还需满足的条件

这些验证证明依赖及其适配已可用，不代表完整业务验收完成。上传后的处理还需要运行 Worker：

```powershell
$demo = Get-Content .runtime/demo.json | ConvertFrom-Json
.\.venv\Scripts\python.exe -m app.worker --tenant $demo.tenant_id
```

当前 `DEVELOPMENT_SKIP_SCANNER=false`，没有配置扫描器会拒绝处理文件。生产按 `docs/ingestion_pipeline.md` 部署 SCANNER_URL；仅本地可信文件调试可显式同时设置 `ENVIRONMENT=development` 和 `DEVELOPMENT_SKIP_SCANNER=true`。本次未更改扫描策略。Office 转 PDF 还需要 Worker 所在机器安装 LibreOffice。

## 官方参考

- [TEI 支持的显卡与镜像](https://huggingface.co/docs/text-embeddings-inference/supported_models)
- [MinerU 旧版接口说明](https://opendatalab.github.io/MinerU/usage/compatibility/)
- [WSL 内存与交换文件配置](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)
- [DeepSeek 官方 API](https://api-docs.deepseek.com/zh-cn/)
