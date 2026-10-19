"""
Human-in-the-Loop 工具集。

提供运行时人工交互所需的工具，包括订单数据补充等。
工具通过 langgraph.types.interrupt() 暂停执行，等待人工输入后恢复。

使用方式:
    from agent.tools.hitl_tools import request_order_info
"""

from __future__ import annotations

import json

from langchain_core.tools import tool
from langgraph.types import interrupt
from agent.order_fields import build_order_form


@tool
def request_order_info(missing_fields: str, collected_data: str, orders: list[dict] | None = None) -> str:
    """当订单数据不完整时，请求人工补充缺少的字段。

    调用此工具会暂停执行，在网页补全表单展示缺失字段和已收集数据，
    等待人工输入补充信息后恢复。

    Args:
        missing_fields: 缺少的字段列表及说明（如 "partId（物料ID，必填）"）
        collected_data: 已收集到的数据（结构化描述，如 "物料ID=100, 数量=50"）

        orders: 可选的订单对象列表，使用 supplierId、expectedDeliveryDate、orderDetail 字段。
            多订单、多明细可直接提供已知值；不确定的内容保留在 collected_data 中。

    Returns:
        人工补充的数据（JSON 字符串）
    """
    from backend.auth import current_identity, can
    user = current_identity.get()
    if not can(user, 'procurement'):
        return json.dumps({'ok': False, 'error': {
            'code': 'PERMISSION_DENIED' if user else 'AUTH_REQUIRED',
            'message': '您的账号没有采购权限，请联系采购员处理。',
            'retryable': False,
        }}, ensure_ascii=False)
    response = interrupt({
        "type": "order_info_request",
        "missing_fields": missing_fields,
        "collected_data": collected_data,
        **build_order_form(collected_data, orders=orders),
    })
    return json.dumps(response, ensure_ascii=False)
