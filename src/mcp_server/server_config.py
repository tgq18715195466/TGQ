import os
from dotenv import load_dotenv

load_dotenv(override=False)

ERP_API_BASE_URL = os.getenv("ERP_API_BASE_URL", "http://127.0.0.1:8080/api")
# Backward-compatible import name; this is a Python FastAPI backend, not Java.
JAVA_API_BASE_URL = ERP_API_BASE_URL

# MCP 服务监听配置
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8001"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp")
