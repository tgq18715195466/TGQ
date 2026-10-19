# 订单准入与必填信息

## 创建必填

- supplier_id / supplierId：供应商数字主键，必须通过 supplier_query 用编号或 ID 确认；不能从 SUP 编号猜测。
- expected_delivery_date / expectedDeliveryDate：明确日期，不早于今天。
- order_detail / orderDetail：至少一行，每行 partId、正整数 quantity、非负 unitPrice。
- 所有物料属于同一所选供应商。不同供应商分单。

订单编号和下单时间由工具自动生成。备注和创建人可选。总金额默认按明细计算，若传入则必须一致；新订单只能处于待审核状态，不能直接指定已入库或实际交货日期。

## 执行顺序

订单子 Agent 获得 supplier_query、part_query、part_by_supplier 的只读权限。供应商查询支持 supplier_id / supplier_code 精确筛选，返回合作状态。仅 status=1 合作中可下单；暂停、终止、未知状态及不存在均停止。

Agent 先核实供应商和物料、收集必填字段。若仍尝试提交不完整或非法创建参数，OrderPreflightMiddleware 在最终审批前中断，网页展示需补充内容。用户补充后重新规划与校验，随后仍必须独立 approve/reject；补充输入不等于审批。

后端在写入事务中锁定供应商和物料，重新验证实时状态与归属；任何失败都会回滚。SUPPLIER_NOT_FOUND、SUPPLIER_NOT_ACTIVE、PART_NOT_FOUND、SUPPLIER_PART_MISMATCH 等错误通过 MCP 返回，不作为可重试的网络故障。

该新增强制供应商字段和交货日期契约适用于创建订单。订单更新的现有契约保持不变，不代表已补齐所有订单状态流转规则。

## 验证

真实 LangGraph + 本地确定性模型验证补全→审批→执行，分别覆盖批准和拒绝。随机隔离 MySQL 库验证缺字段、停用供应商、物料不匹配、成功创建及状态变更后再次创建被拦截；测试库已清理。未调用外部模型，未写入现有业务订单。
