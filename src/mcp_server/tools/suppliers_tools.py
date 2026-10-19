from mcp_server.tool_errors import business_tool, erp_result, query_failure
from fastmcp import FastMCP, Context

# 分组名称
GROUP_NAME = "supplier"
def register_supplier_tools(mcp: FastMCP):
    """注册供应商分组的所有工具"""

    @mcp.tool(name=f"{GROUP_NAME}_query")
    @business_tool(f"{GROUP_NAME}_query")
    async def query_suppliers(name: str = "", ctx: Context = None, supplier_id: int | None = None, supplier_code: str | None = None) -> dict:
        """
        按名称模糊搜索，或按 supplier_id / supplier_code 精确查询供应商；读取全部分页并返回真实总数和实时 status（1合作中，2暂停，3终止）。

        Args:
            name: 可选名称筛选
            supplier_id: 可选数字主键，精确匹配
            supplier_code: 可选供应商编号，如 SUP00015，精确匹配
        """
        http_client = ctx.request_context.lifespan_context.get("http_client")

        params = {"name": name, "paged": True, "current": 1, "size": 100}
        if supplier_id is not None: params["supplierId"] = supplier_id
        if supplier_code is not None: params["supplierCode"] = supplier_code
        def incomplete():
            # An inconsistent read is unknown, not evidence of an empty result.
            return {"ok": False, "error": {"code": "INVALID_RESPONSE", "retryable": True}}

        records = []
        expected_total = None
        seen_ids = set()
        try:
            while True:
                response = await http_client.get("/suppliers/search", params=params)
                response.raise_for_status()
                result = erp_result(response.json())
                if result.get("ok") is not True:
                    return result
                payload = result.get("data")
                # Legacy services and mocks still return an unpaged list.
                if isinstance(payload, list):
                    return result if params["current"] == 1 else incomplete()
                if not isinstance(payload, dict):
                    return incomplete()
                rows, total = payload.get("records"), payload.get("total")
                if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                    return incomplete()
                if type(total) is not int or total < 0:
                    return incomplete()
                if expected_total is None:
                    expected_total = total
                if total != expected_total:
                    return incomplete()
                if "current" in payload and payload["current"] != params["current"]:
                    return incomplete()
                if not rows and len(records) < total:
                    return incomplete()
                for row in rows:
                    identifier = row.get("id")
                    if identifier is not None:
                        if not isinstance(identifier, (str, int)) or isinstance(identifier, bool):
                            return incomplete()
                        if str(identifier) in seen_ids:
                            return incomplete()
                        seen_ids.add(str(identifier))
                records.extend(rows)
                if len(records) > total:
                    return incomplete()
                if len(records) == total:
                    # No synthetic page metadata: this contains all API rows.
                    return {"ok": True, "data": {"records": records, "total": total}}
                params = {**params, "current": params["current"] + 1}
        except Exception as e:
            return query_failure(e)
