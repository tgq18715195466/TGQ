from mcp_server.tool_errors import business_tool, erp_result, http_error_detail, query_failure
from typing import Annotated

import httpx
from pydantic import Field

from fastmcp import FastMCP, Context

GROUP_NAME = "inventory"


def register_inventory_tools(mcp: FastMCP):
    """注册库存管理分组的所有工具"""

    @mcp.tool(name=f"{GROUP_NAME}_warning")
    @business_tool(f"{GROUP_NAME}_warning")
    async def list_inventory_warnings(ctx: Context) -> dict:
        """
        查询库存预警列表。
        返回当前库存低于安全库存的物料、账面缺口、供应商合作状态、
        采购准入状态、在途及待审批数量、未覆盖缺口和建议新增申请数量。
        低库存不等于可采购，账面缺口不等于应新增的采购申请数量；
        采购资格和数量只能依据返回的独立字段陈述。

        无需传参。
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        try:
            response = await http_client.get("/inventory/warning")
            response.raise_for_status()
            result = response.json()

            return erp_result(result)
        except Exception as e:
            return query_failure(e)

    @mcp.tool(name=f"{GROUP_NAME}_adjust")
    @business_tool(f"{GROUP_NAME}_adjust")
    async def inventory_adjust(
        part_id: Annotated[int, Field(strict=True, gt=0, le=9223372036854775807)],
        delta: Annotated[int, Field(strict=True, ge=-2147483648, le=2147483647, json_schema_extra={"not": {"const": 0}})],
        reason: Annotated[str, Field(min_length=1, max_length=1000, pattern=r"\S")],
        request_id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S(?:[\s\S]*\S)?$")],
        ctx: Context,
    ) -> dict:
        """Adjust inventory after approval. Positive delta adds, negative removes.

        reason and request_id are required. Reuse the SAME request_id and payload
        after a timeout/retry; changed payload under the same key returns 409.
        Only procurement-order may use this mutation, with approve/reject gating.
        """
        from backend.inventory import InventoryAdjustment
        payload = InventoryAdjustment(partId=part_id, delta=delta, reason=reason, requestId=request_id)
        http_client = ctx.request_context.lifespan_context.get('http_client')
        try:
            response = await http_client.post('/inventory/adjust', json=payload.model_dump(by_alias=True))
            response.raise_for_status()
            result = response.json()
            return erp_result(result)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            detail = http_error_detail(exc.response)
            code = detail.get('code', 'ERP_API_ERROR') if isinstance(detail, dict) else 'ERP_API_ERROR'
            return {'ok': False, 'error': {'code': code, 'status': status, 'retryable': status >= 500}}
        except httpx.RequestError:
            return {'ok': False, 'error': {'code': 'ERP_UNAVAILABLE', 'retryable': True}}
