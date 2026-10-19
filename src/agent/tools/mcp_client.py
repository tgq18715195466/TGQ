"""
MCP 工具客户端。

在 Agent 启动时连接所有 MCP Server，获取全部 MCP 工具，
并按分组筛选后分配给不同的子 Agent。

使用方式:
    from agent.tools.mcp_client import load_mcp_tools

    all_tools, analyst_tools, order_tools, chart_tools = await load_mcp_tools()
"""

import os
from typing import List, Tuple

from langchain_mcp_adapters.client import MultiServerMCPClient

# MCP Server 连接配置
MCP_SERVER_CONFIG = {
    "erp-api": {
        "url": os.getenv("ERP_MCP_URL", "http://127.0.0.1:8001/mcp"),
        "transport": "streamable_http",
    },
    "analysis": {
        "url": os.getenv("CHART_MCP_URL", ""),
        "transport": "streamable_http",
    },
}

# ERP 业务分组只使用精确白名单；未知工具必须先注册权限策略。
ANALYST_TOOL_NAMES = frozenset({
    "supplier_query", "part_query", "part_search", "part_by_supplier",
    "inventory_warning", "order_search_details", "daily_receipts",
})
ORDER_TOOL_NAMES = frozenset({
    "order_create", "order_update", "order_search_details", "inventory_adjust",
    "master_change_request", "purchase_request_create", "replenishment_suggestions",
    "supplier_query", "part_query", "part_by_supplier", "procurement_verification",
})
CHART_TOOL_PREFIXES = ("generate_",)  # 可选外部 MCP 的可视化工具前缀


async def load_mcp_tools(
    server_config: dict | None = None,
) -> Tuple[List, List, List, List]:
    """
    连接到所有 MCP Server，加载全部工具并分组。

    Args:
        server_config: MCP Server 连接配置，默认使用 MCP_SERVER_CONFIG。

    Returns:
        (all_tools, analyst_tools, order_tools, chart_tools) 四元组
        - all_tools: 全部 MCP 工具列表（ERP + 图表）
        - analyst_tools: 供应商查询 + 零部件查询 + 库存预警工具
        - order_tools: 订单创建 + 订单更新 + 订单搜索 + 库存调整工具
        - chart_tools: 图表/地图/可视化生成工具（来自实际配置并成功连接的外部 MCP Server）
    """
    if server_config is None:
        server_config = MCP_SERVER_CONFIG

    print("[INFO] 正在连接 MCP Server...")
    from agent.tools.auth_bridge import authenticated_mcp_call
    mcp_client = MultiServerMCPClient(server_config, tool_interceptors=[authenticated_mcp_call])

    # 从 ERP MCP Server 获取业务工具（本地服务，必需）
    try:
        erp_tools = await mcp_client.get_tools(server_name="erp-api")
        print(f"[INFO] 已从 ERP MCP Server 加载 {len(erp_tools)} 个工具")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("Required ERP MCP Server is unavailable") from e

    # Validate all ERP registrations before grouping or role-specific views.
    from agent.tools.auth_bridge import build_tool_registry, validate_tool_registry
    erp_tools = list(validate_tool_registry(erp_tools, erp_only=True).values())

    # 从外部图表 MCP Server 获取图表工具（外部服务，失败则跳过）
    try:
        if not server_config.get("analysis", {}).get("url") or os.getenv("DISABLE_CHART_MCP", "").lower() == "true":
            raise RuntimeError("chart MCP disabled")
        analysis_tools = await mcp_client.get_tools(server_name="analysis")
        print(f"[INFO] 已从外部图表 MCP Server 加载 {len(analysis_tools)} 个工具（可视化+其他）")
    except Exception as e:  # noqa: BLE001
        print(f"[WARNING] 外部图表 MCP Server 连接失败（跳过图表工具）: {e}")
        analysis_tools = []

    # 合并全部工具
    all_tools = list(build_tool_registry(list(erp_tools) + list(analysis_tools)).values())

    # 精确名称分组：业务工具
    analyst_tools = [
        t for t in erp_tools
        if t.name in ANALYST_TOOL_NAMES
    ]
    order_tools = [
        t for t in erp_tools
        if t.name in ORDER_TOOL_NAMES
    ]

    # 图表工具（可选外部扩展，本地类型由统一入口提供）
    chart_tools = [
        t for t in analysis_tools
        if t.name.startswith(CHART_TOOL_PREFIXES)
    ]

    print(
        f"[INFO] 工具分组完成: "
        f"分析类 {len(analyst_tools)} 个, "
        f"订单类 {len(order_tools)} 个, "
        f"图表类 {len(chart_tools)} 个"
    )

    return all_tools, analyst_tools, order_tools, chart_tools
