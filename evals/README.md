> 历史评测框架说明：当前版本已加入登录与角色权限，旧数据集和 SSE 客户端需核对认证适配。本文不是当前版本的真实模型跑分记录。发布副本不包含原始业务数据或历史 traces。

# ERP Agent 评测说明

该评测通过 Web API 的 SSE 接口进行黑盒测试，覆盖主 Agent、子 Agent、MCP、ERP Backend 和 MySQL。它不是单元测试的替代品。

## 1. 前置条件

1. 创建独立数据库 `motorparts_eval`，导入 `deploy/mysql/001-schema.sql` 并自行准备合成测试数据；
2. ERP Backend 必须连接 `motorparts_eval`，不能连接开发库；
3. 复制 `.env.eval.example` 为 `.env.eval` 并填写评测库账号；
4. 启动 ERP Backend、MCP Server、Web API；
5. 评测模型固定版本，temperature 推荐设为0；
6. 评测时关闭网络搜索和外部图表 MCP，避免外部波动。

注意：`run_local.py` 当前没有自动切换评测数据库。启动服务前请令应用侧 `MYSQL_DB=motorparts_eval`，同时令评测侧 `EVAL_MYSQL_DB=motorparts_eval`。

## 2. 运行

冒烟测试：

```bash
python -m evals.run_eval --limit 5
```

全部案例：

```bash
python -m evals.run_eval --dataset evals/datasets/erp_agent_eval.jsonl
```

只测安全场景：

```bash
python -m evals.run_eval --category order_reject --category order_missing
```

命令返回非0表示至少一个案例失败，这适合接入CI。报告写入 `evals/reports/`：

- `result-*.json`：指标与每条案例的失败原因；
- `traces-*.json`：工具调用、中断、输出和数据库快照。

## 3. 指标定义

- `task_success_rate`：所有适用断言均通过的案例比例；
- `route_accuracy`：工具事件来源包含预期子 Agent 的比例；
- `tool_exact_match`：实际工具集合与标准工具集合一致的比例；
- `mean_argument_accuracy`：预期参数字段的平均正确率；
- `interrupt_exact_match`：中断类型及顺序完全一致的比例；
- `db_effect_accuracy`：写入、拒绝无写入、最小更新等数据库结果正确的比例；
- `latency_p50/p95`：完整任务延迟；
- `ttft_p50/p95`：首个文本Token延迟。

## 4. 当前限制

- SSE 的 `tool_start`、`tool_args`、`tool_result` 均携带 `tool_call_id`；采集器按 ID 关联并行工具调用，缺失或未知 ID 会记为评测错误。
- 数据库自动清理只删除带 `EVAL:{case_id}` remark 的新增订单；状态更新用例会修改固定订单1，所以建议每轮评测前重建评测库。
- 自然语言输出没有使用 LLM-as-judge，首版以可验证的路由、工具、参数、中断和数据库事实为准，避免评测模型自评带来的偏差。
- 当前数据集是首版基线。修复系统后应从失败日志和真实面试演示场景持续补充 Badcase。

## 5. 推荐正式跑分方法

1. 重建 `motorparts_eval`；
2. 固定模型、Prompt、Tool Schema和temperature；
3. 每条用例使用不同thread_id；
4. 全集运行3次；
5. 分别保存每轮结果，汇报三轮均值和最差轮，而不是只挑最好一次；
6. 修改系统后使用同一数据集复跑，禁止修改标准答案迎合实现。
