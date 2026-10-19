"""Internal read-only snapshot tool for the fixed procurement workflow."""
from typing import Literal

from fastmcp import Context, FastMCP

from mcp_server.tool_errors import query_failure


def register_verification_tools(mcp: FastMCP):
    @mcp.tool(name='procurement_verification')
    async def procurement_verification(
        operation: Literal['order_create', 'purchase_request_create'],
        request_id: str | None = None,
        order_number: str | None = None,
        ctx: Context = None,
    ) -> dict:
        """只读核验真实采购单据。申请按 request_id，订单按 order_number 查询。

        固定采购流程写入后调用；found 仅说明找到实际单据，须比对真实快照。
        缺失、不一致或失败时保留未知结果并进入恢复；禁止自动重复提交 POST。
        """
        try:
            # http_base's shared client forwards request-local authentication
            # through its event hook. Never attach identity to shared headers.
            client = ctx.request_context.lifespan_context['http_client']
            params = {'operation': operation}
            if request_id is not None:
                params['request_id'] = request_id
            if order_number is not None:
                params['order_number'] = order_number
            response = await client.request('GET', '/operations/verification', params=params)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get('ok') is not True:
                raise ValueError('Invalid verification envelope')
            data = result.get('data')
            if not isinstance(data, dict) or type(data.get('found')) is not bool:
                raise ValueError('Invalid verification outcome')
            if data['found']:
                if not isinstance(data.get('record'), dict) or not data['record']:
                    raise ValueError('Missing actual snapshot')
            elif data.get('record') is not None:
                raise ValueError('Unexpected snapshot for missing document')
            return result
        except Exception as exc:
            return query_failure(exc)
