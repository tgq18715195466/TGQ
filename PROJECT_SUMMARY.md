# ERP OpenClaw 项目总结

> 解析日期：2026-10-19。本文记录源码中已有的能力与工程边界，不将规划项写成已完成成果。

## 项目定位

面向零配件企业采购员、仓管员、审批人和管理员，将自然语言助手与传统 ERP 页面放在同一个工作台。业务后端在本仓库内，以 MySQL 保持业务事实，以 PostgreSQL 保持 Agent 执行状态和展示历史。

项目的核心问题是：如何让模型理解需求、调用工具，同时让采购写入、审批与库存变动遵循确定的业务规则。

## 当前能力

| 模块 | 已有实现 | 主要证据 |
| --- | --- | --- |
| Agent 编排 | 主协调器、两个 YAML 业务专家与通用助手 | [main_agent.py](src/agent/main_agent.py)、[loader.py](src/agent/subagents/loader.py) |
| 采购流程 | 字段补全、确认、重新准入检查、写入核验、恢复 | [procurement_workflow.py](src/agent/procurement_workflow.py) |
| 工具协议 | 14 个 ERP MCP 工具、鉴权上下文桥接 | [server_main.py](src/mcp_server/server_main.py)、[auth_bridge.py](src/agent/tools/auth_bridge.py) |
| 账号权限 | 首次建管理员、4 角色、会话撤销、禁止自审 | [auth.py](backend/auth.py) |
| 业务闭环 | 采购申请、订单、审批、收货、库存流水与主数据 | [app.py](backend/app.py)、[operations.py](backend/operations.py) |
| 会话恢复 | checkpoint、Store、展示历史、用户线程作用域 | [postgres_persistence.py](src/agent/postgres_persistence.py) |
| 前端体验 | 工作台、流式答案、工具详情、补全和确认面板 | [App.vue](frontend/src/App.vue)、[chat.js](frontend/src/api/chat.js) |
| 工程验证 | Python / 前端用例、DB opt-in、评测框架 | [测试说明](docs/testing.md) |

## 关键设计选择

### 用模型处理语义，用状态图约束写入

模型适合识别“帮我采购这些配件”的意图；物料是否明确、供应商能否采购、写入是否成功，应从业务接口查询并验证。`order_create` 与 `purchase_request_create` 的 Agent 入口会包装成可恢复的固定流程，避免仅靠提示词保证交易顺序。

### 把执行确认与业务审批分开

用户在聊天中确认的是“提交这次操作”；正式审批需要有审批权限的其他账号执行。这样既保留自然语言的便利，也维持采购组织中的职责分工。最终权限和自审限制在后端校验，不能只依赖前端隐藏按钮。

### 用 MCP 隔离工具协议与业务实现

Agent 获得带 schema 的工具，MCP Server 将其转为 HTTP 请求。业务事务继续放在 ERP 后端，便于页面和 Agent 共用规则；代价是部署和排错要观察 Web、MCP、ERP 三层。

### 将业务事实与模型状态分开持久化

MySQL 保存订单、库存、账号和审计；PostgreSQL 保存 checkpoint、Store 与展示历史。checkpoint 证明流程走到了哪里，业务查询核验说明数据库最终保存了什么。二者不是跨数据库事务，因此文档不承诺分布式 exactly-once。

### 文件能力按用户约束，通用执行能力不开放给模型

当前 `main_agent.py` 构造 FilesystemBackend，并配合请求用户作用域与权限中间件。已有本地图表工具可生成可展示的 SVG；源码中遗留的 LocalShellBackend 注释不能代表当前能力。

## 适合讲解的技术主题

1. 主 Agent 如何委派，YAML 工具声明如何成为运行时约束。
2. 采购状态图如何补全信息、确认、检查主数据变化并核验写入。
3. Cookie 会话如何经 MCP 转成可信身份，为什么不能接受模型填写的操作人。
4. SSE 如何同时处理结果、工具明细、中断与恢复，并过滤内部内容。
5. checkpoint、展示历史与长期偏好的区别，为什么内存降级不等于完整可用。
6. 库存事务、幂等键、负库存保护和正式审批如何共同约束写操作。

## 验证结论与不足

本次原始环境离线测试结果为 Python 734 通过、32 跳过，前端 153 通过，前端构建成功；详见 [测试记录](docs/testing.md)。没有据此推导真实模型成功率、P95 延迟或生产可用性。

接下来最值得做的工作包括：登录后的真实模型黑盒评测、固定依赖版本、容器全链路复验、多实例恢复协调、可观测性与前端拆包。它们在 [项目计划](docs/project-plan.md) 中明确列为待办。
