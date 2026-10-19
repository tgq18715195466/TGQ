# 测试与验证记录

## 本次执行范围

日期：2026-10-19。执行于原工作区现有依赖环境，Python 3.12.14、Vite 5.4.21。此次主要交付为源码解析和发布副本；业务源码未更改。

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| `python -m pytest tests -q` | 734 passed，32 skipped，7 warnings | 单元测试、使用 fake 的集成测试、内存图测试 |
| `npm --prefix frontend test` | 153 passed，0 failed | Node runner、Vue 组件 / SSR 与 mock fetch |
| `npm --prefix frontend run build` | 成功 | 340 模块；有大 chunk 提示 |

实际 Python 命令附加了 `--basetemp=tmp/publish-docs-20261019`，并将以下开关显式设为 `0`：`ORDER_TEST_MYSQL`、`INVENTORY_TEST_MYSQL`、`ERP_TEST_POSTGRES`。

32 项跳过为真实 MySQL / PostgreSQL 测试，包含认证业务、库存事务、运营流程、看板、订单与跨进程恢复。这些未在本次连接真实数据库执行。

Python 告警包括 Starlette / AnyIO 废弃接口和 Pydantic 上下文序列化提示；没有将告警描述为测试失败。前端构建的 JS chunk 约 1,246.10 kB（gzip 435.34 kB），仍有优化空间。

## 离线回归命令

在完成依赖安装后，再安装测试工具：

```powershell
.\.venv\Scripts\python.exe -m pip install pytest pytest-asyncio
$env:ORDER_TEST_MYSQL='0'
$env:INVENTORY_TEST_MYSQL='0'
$env:ERP_TEST_POSTGRES='0'
.\.venv\Scripts\python.exe -m pytest tests -q
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
```

`frontend/tests/test_purchase_route_contract.py` 是额外 Python 契约测试，不被上述默认 pytest 和 Node 命令收集；需要单独运行。`tests/e2e` 下的内存图测试名称不代表真实浏览器 E2E。

## 用例覆盖的关键约束

- 角色权限、会话撤销、操作身份注入、自审限制。
- 采购字段补全、准入、物料解析、确认、写入后的事实核验。
- HITL 中断 ID、过期 / 重复 / 数量不匹配恢复请求。
- 库存非负、幂等、请求冲突、事务与收货语义。
- 用户线程与文件隔离、长期偏好、持久化模式选择。
- 答案 / 工具明细 / 内部消息区分，以及前端表单和权限界面。

## 真实服务测试应单独执行

- MySQL：`ORDER_TEST_MYSQL=1`；库存测试也支持 `INVENTORY_TEST_MYSQL=1`。按用例说明准备允许创建临时测试库的账号。
- PostgreSQL：`ERP_TEST_POSTGRES=1`。会写入测试 checkpoint / thread 并清理，应使用独立实例或测试库。
- 黑盒 Agent：`evals/` 提供 SSE 采集、工具 / 参数 / 中断 / 数据库事实评分框架。旧数据集和登录适配仍需核对，不能直接作为当前版本有效跑分。

外部模型、浏览器视觉交互、干净机器安装、Docker 全链路和真实数据库事务不是本次离线通过结果的一部分。

## 发布检查

本次发布副本另外检查：文本文件 UTF-8 可读、Python 源码语法、Markdown 相对文件链接、14 个 MCP 工具声明、禁入路径和常见密钥模式。它们不构成完整安全审计，也不代替运行测试。具体文件哈希保存在发布清单中，便于对应本次快照。
