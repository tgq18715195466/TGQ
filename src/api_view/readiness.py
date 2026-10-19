"""Live readiness checks for required and optional ERP Agent dependencies."""

import os

from fastmcp import Client
import psycopg
import pymysql


def _mysql_check() -> dict:
    try:
        conn = pymysql.connect(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DB", "motorparts_db"),
            connect_timeout=2,
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
        finally:
            conn.close()
        return {"status": "healthy"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "unhealthy", "error": type(exc).__name__}


async def _erp_mcp_check() -> dict:
    url = os.getenv("ERP_MCP_URL", "http://127.0.0.1:8001/mcp")
    try:
        async with Client(url, timeout=5) as client:
            tools = await client.list_tools()
        names = {tool.name for tool in tools}
        required = {"order_create", "order_update", "inventory_warning", "inventory_adjust"}
        return {"status": "healthy" if required <= names else "unhealthy", "tool_count": len(names)}
    except Exception as exc:
        return {"status": "unhealthy", "error": type(exc).__name__}


def _model_check() -> dict:
    configured = bool(os.getenv("DEEPSEEK_API_KEY") and os.getenv("DEEPSEEK_BASE_URL"))
    return {"status": "healthy" if configured else "unhealthy", "configured": configured}


def _checkpointer_check() -> dict:
    uri = os.getenv("POSTGRES_URI", "")
    if not uri:
        return {"status": "degraded", "mode": "fallback_memory", "persistent": False}
    try:
        with psycopg.connect(uri, connect_timeout=2) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        from agent.postgres_persistence import postgres_persistence
        if postgres_persistence._initialized and not postgres_persistence.persistent:
            return {"status": "degraded", "mode": "fallback_memory", "persistent": False}
        return {"status": "healthy", "mode": "postgresql", "persistent": True}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "mode": "fallback_memory", "persistent": False, "error": type(exc).__name__}


async def readiness_report() -> tuple[dict, int]:
    checks = {
        "mysql": _mysql_check(),
        "erp_mcp": await _erp_mcp_check(),
        "model": _model_check(),
        "checkpointer": _checkpointer_check(),
    }
    required_ok = all(checks[name]["status"] == "healthy" for name in ("mysql", "erp_mcp", "model"))
    status = "healthy" if required_ok and checks["checkpointer"]["status"] == "healthy" else ("degraded" if required_ok else "unhealthy")
    persistent_required = os.getenv("POSTGRES_REQUIRED", "false").lower() in {"1", "true", "yes"}
    ready = required_ok and (not persistent_required or checks["checkpointer"]["status"] == "healthy")
    return {"status": status, "checks": checks}, 200 if ready else 503
