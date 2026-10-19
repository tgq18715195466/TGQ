"""将 MCP 调用绑定到已认证的请求，而非模型参数。

本模块是模型与它所调用的每一个工具之间的唯一关口，职责如下：

1. 维护每个 ERP 工具的精确权限策略（`TOOL_PERMISSIONS`），以及本地
   文件/实用工具和可选辅助工具（如网络搜索、各类图表生成器）的显式白名单。
2. 在任何按角色过滤之前，先校验每个已注册工具都有对应的权限策略
   （`validate_tool_registry`），使未知工具直接报错，而不是静默放行。
3. 根据后端规则判断某个工具是否允许当前身份使用（`tool_allowed_for_identity`）。
4. 拦截需要鉴权的 MCP 调用，校验调用方权限，并把调用方的 bearer token
   注入请求头——绝不注入模型可控制的参数（`authenticated_mcp_call`）。

安全原则：授权绑定到请求的已认证身份，而非模型可设置的任何内容；每个工具名
都必须映射到一条明确的策略，否则一律拒绝。
"""
from collections.abc import Iterable
import json
from typing import Any

from mcp.types import CallToolResult, TextContent

# Every ERP tool, including internal workflow queries, needs an exact policy.
TOOL_PERMISSIONS = {
    'supplier_query': 'read_procurement', 'part_query': 'read_inventory',
    'part_search': 'read_inventory', 'part_by_supplier': 'read_inventory',
    'inventory_warning': 'read_inventory', 'order_search_details': 'read_procurement',
    'replenishment_suggestions': 'read_inventory', 'order_create': 'procurement',
    'order_update': 'procurement', 'inventory_adjust': 'warehouse',
    'purchase_request_create': 'procurement', 'master_change_request': 'master',
    'procurement_verification': 'procurement',
    'daily_receipts': 'read_inventory',
}

# These names identify the existing isolated filesystem and local utility tools.
# Never allow an unknown tool just because it starts with a familiar prefix.
SAFE_LOCAL_TOOL_NAMES = frozenset({
    'ls', 'read_file', 'write_file', 'edit_file', 'glob', 'grep', 'execute',
    'task', 'write_todos', 'generate_visualization', 'download_sandbox_file',
    'publish_business_answer', 'assign_skill',
})

# Optional external tools have their own explicit policies, outside the ERP map.
AUXILIARY_TOOL_PERMISSIONS = {
    'request_order_info': 'procurement',
    'web_search': 'chat',
    'generate_area_chart': 'chat', 'generate_bar_chart': 'chat',
    'generate_boxplot_chart': 'chat', 'generate_column_chart': 'chat',
    'generate_dual_axes_chart': 'chat', 'generate_funnel_chart': 'chat',
    'generate_histogram_chart': 'chat', 'generate_line_chart': 'chat',
    'generate_liquid_chart': 'chat', 'generate_organization_chart': 'chat',
    'generate_pie_chart': 'chat', 'generate_radar_chart': 'chat',
    'generate_sankey_chart': 'chat', 'generate_scatter_chart': 'chat',
    'generate_treemap_chart': 'chat', 'generate_venn_chart': 'chat',
    'generate_violin_chart': 'chat', 'generate_waterfall_chart': 'chat',
    'generate_word_cloud_chart': 'chat', 'generate_district_map': 'chat',
    'generate_path_map': 'chat', 'generate_pin_map': 'chat',
    'generate_fishbone_diagram': 'chat', 'generate_flow_diagram': 'chat',
    'generate_network_graph': 'chat', 'generate_mind_map': 'chat',
}

INTERNAL_TOOL_NAMES = frozenset({'procurement_verification'})


def tool_name(tool: Any) -> str | None:
    """Read an exact name from a tool object or a model function schema."""
    if isinstance(tool, dict):
        name = tool.get('name')
        if name is None and isinstance(tool.get('function'), dict):
            name = tool['function'].get('name')
    else:
        name = getattr(tool, 'name', None)
    return name if isinstance(name, str) and name and name == name.strip() else None


def build_tool_registry(tools: Iterable[Any]) -> dict[str, Any]:
    """Index the full pool; repeating one object is safe, name collisions are not."""
    registry: dict[str, Any] = {}
    for tool in tools:
        name = tool_name(tool)
        if name is None:
            raise ValueError('Every registered tool must have a non-empty exact name')
        if name in registry and registry[name] is not tool:
            raise ValueError(f"Conflicting tool objects registered with name '{name}'")
        registry[name] = tool
    return registry


def validate_tool_registry(tools: Iterable[Any], *, erp_only: bool = False) -> dict[str, Any]:
    """Validate every registration before any per-role filtering or grouping."""
    registry = build_tool_registry(tools)
    for name in registry:
        known = name in TOOL_PERMISSIONS
        if not erp_only:
            known = known or name in SAFE_LOCAL_TOOL_NAMES or name in AUXILIARY_TOOL_PERMISSIONS
        if not known:
            raise ValueError(f"Registered tool '{name}' has no permission policy")
    return registry


def tool_allowed_for_identity(name: str | None, user: dict | None) -> bool:
    """Evaluate the backend's current permission rules without storing identities."""
    from backend.auth import can

    permission = TOOL_PERMISSIONS.get(name) or AUXILIARY_TOOL_PERMISSIONS.get(name)
    if permission:
        return can(user, permission)
    return name in SAFE_LOCAL_TOOL_NAMES


async def authenticated_mcp_call(request, handler):
    if request.server_name != 'erp-api':
        return await handler(request)
    from backend.auth import current_identity, current_token, can

    user, token = current_identity.get(), current_token.get()
    permission = TOOL_PERMISSIONS.get(request.name)
    if not user or not token or not permission or not can(user, permission):
        code = 'AUTH_REQUIRED' if not user or not token else 'PERMISSION_DENIED'
        return CallToolResult(isError=True, content=[TextContent(type='text', text=json.dumps(
            {'ok': False, 'error': {'code': code, 'retryable': False}}, ensure_ascii=False))])
    # Overrides are per invocation; the shared MCP client never keeps user headers.
    headers = dict(request.headers or {})
    headers['Authorization'] = 'Bearer ' + token
    return await handler(request.override(headers=headers))
