# GitHub 发布说明

## 发布布局

目标仓库：`tgq18715195466/TGQ`。RAG 源码与文档放在独立 `RAG_AGENT/` 目录，根目录保留原 ERP；根 README 增加入口，根 `.github/workflows/rag-tests.yml` 提供在 RAG 子目录执行的 CI。

本次参照 FinanceWiki Agent 的项目介绍组织方式重写 README，并新增快速开始、项目解析。介绍依据本项目源码，不复制参考项目的业务实现或宣称拥有其记忆 / 技能系统。

## 此次验证（2026-10-19，北京时间）

- Python 回归：**83 passed, 3 skipped, 1 warning**。运行 `scripts/run_local_tests.py -q -p no:cacheprovider`，使用现有独立 RAG 开发 PostgreSQL，临时目录指向项目 `.runtime/`。
- 跳过：2 项显式开关控制的真实外部集成；1 项本机 ERP 配套模块检查，因为目标文件已不存在。
- 告警：Starlette / AnyIO 的 `BlockingPortal` 弃用提示，未隐藏。
- 前端：`npm test --prefix web`，**6 个测试文件、17 项通过**。
- `npm run build --prefix web`：TypeScript 检查和 Vite 生产构建通过。
- `ruff check app scripts eval tests --select F`、`scripts/check_forbidden.py` 和 `pip check` 均通过。
- 前端测试最初受沙箱临时目录权限限制，改用项目内临时目录后通过；后端在允许连接本机数据库的环境重跑。

未在此次发布中重新进行真实模型调用、完整 OCR / 检索业务验收、压力测试或恢复演练。历史真实服务记录见 [local_dependencies.md](local_dependencies.md)，不能等同于此次新增验证。

## 小范围整理

- 移除 `app/query.py` 未使用的 `Chunk` 导入，使既有 Ruff F 检查通过。
- 本机 ERP 可选测试改为检查目标模块文件是否存在，避免只剩空目录时错误执行。
- 为历史状态文档增加更新说明，区分事实问答摘录与总结改写的校验边界。
- 扩充 Git / Docker 忽略项，避免发布配置和运行数据。

## 文件范围

发布 Python / React 源码、迁移、测试、依赖锁定、示例配置、部署文件、文档及人工 fixture。详细文件哈希见 [publication-manifest.json](publication-manifest.json)，清单不包含其自身以避免自引用。

排除 `.env` 和非示例环境文件、`.runtime/`、`.venv/`、`node_modules/`、构建产物、缓存、日志、数据库备份、`data/` 下载资料和 `tmp/`。本地原件保留。外部模型权重、私有知识库文件和账号密码不随仓库发布。

未替作者新增许可证或变更现有代码授权。真实部署需单独准备凭据、模型权重与必要外部服务。
