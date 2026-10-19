from contextlib import asynccontextmanager
import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

from mcp_server.server_config import JAVA_API_BASE_URL


# 关键点：lifespan 返回的字典会通过 ctx.request_context.lifespan_context 传递给每个工具函数，
# 避免了全局变量或手动关闭的麻烦。

async def forward_authenticated_user(request: httpx.Request):
    # Read request-local headers at dispatch time, never change shared client headers.
    headers = get_http_headers(include={'authorization'})
    request.headers.pop('authorization', None)
    request.headers.pop('cookie', None)
    for name in ('x-user-id', 'x-username'):
        request.headers.pop(name, None)
    authorization = headers.get('authorization')
    if authorization and authorization.startswith('Bearer '):
        request.headers['Authorization'] = authorization


@asynccontextmanager
async def mcp_lifespan(server: FastMCP):
    """
    FastMCP 生命周期管理：初始化 / 关闭 HTTP 客户端

    Args:
        server: FastMCP 实例（由框架自动传入）
    """
    # 启动时创建 HTTP 客户端（连接池）
    http_client = httpx.AsyncClient(
        base_url=JAVA_API_BASE_URL,
        timeout=30.0,
        event_hooks={'request':[forward_authenticated_user]},
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
    )
    # http_client.put()
    # 将 http_client 存入 lifespan_context，供工具函数通过 ctx 获取
    yield {"http_client": http_client}

    # 关闭时清理资源
    await http_client.aclose()