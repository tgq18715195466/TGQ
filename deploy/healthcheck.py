"""Read-only container probes; no business tool calls or model invocations."""
import asyncio
import json
import os
import sys
from urllib.request import urlopen


def http_json(url):
    with urlopen(url, timeout=12) as response:
        return json.load(response)


def backend():
    import pymysql
    if http_json("http://127.0.0.1:8080/health").get("status") != "healthy":
        raise RuntimeError("Backend health failed")
    with pymysql.connect(host=os.environ["MYSQL_HOST"], port=int(os.environ["MYSQL_PORT"]),
                         user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
                         database=os.environ["MYSQL_DB"], connect_timeout=3,
                         read_timeout=3, write_timeout=3) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM inventory LIMIT 0")


async def mcp():
    from fastmcp import Client
    async with Client("http://127.0.0.1:8001/mcp", timeout=8) as client:
        names = {tool.name for tool in await client.list_tools()}
    if not {"order_create", "order_update", "inventory_warning", "inventory_adjust"} <= names:
        raise RuntimeError("Required MCP tools missing")


def web():
    report = http_json("http://127.0.0.1:8090/health/ready")
    if report.get("status") != "healthy":
        raise RuntimeError("Web dependencies unhealthy or degraded")
    if report.get("checks", {}).get("checkpointer", {}).get("persistent") is not True:
        raise RuntimeError("PostgreSQL persistence unavailable")


if __name__ == "__main__":
    try:
        target = sys.argv[1]
        if target == "mcp":
            asyncio.run(mcp())
        elif target == "backend":
            backend()
        elif target == "web":
            web()
        else:
            raise ValueError("Unknown healthcheck")
    except Exception as exc:
        # Docker retains probe output; never print credentials or connection strings.
        print(f"Healthcheck failed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
