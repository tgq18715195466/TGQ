"""
DeepAgent Chat API - FastAPI 主应用

提供基于 DeepAgent 的 AI 对话系统后端 API
"""

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api_view.web_config import API_TITLE, API_VERSION, API_DESCRIPTION
from api_view.api import chat, history, artifacts
from api_view.agent_loader import agent_loader
from api_view.readiness import readiness_report


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    在应用启动时初始化 Agent，在应用关闭时清理资源
    """
    # ============================================================
    # 应用启动时执行
    # ============================================================
    print("=" * 50)
    print("正在启动 DeepAgent Chat API...")
    print("=" * 50)

    try:
        # 初始化 Agent 与 PostgreSQL 持久化连接池
        await agent_loader.initialize()

        print("=" * 50)
        print("DeepAgent Chat API 启动成功!")
        print("=" * 50)

        # 继续运行
        yield
    finally:
        # ============================================================
        # 应用关闭时执行
        # ============================================================
        print("=" * 50)
        print("正在关闭 DeepAgent Chat API...")
        print("=" * 50)
        await agent_loader.close()


# 创建 FastAPI 应用
app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION,
    lifespan=lifespan,
)

# ============================================================
# CORS 中间件配置
# ============================================================
# 允许跨域请求，方便前端开发
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (3000, 3001)] + [x.strip() for x in os.getenv("AUTH_ALLOWED_ORIGINS", "").split(",") if x.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 注册路由
# ============================================================
# 对话相关接口
app.include_router(artifacts.router, prefix="/api", tags=["附件"])
app.include_router(chat.router, prefix="/api", tags=["对话"])
# 历史记录相关接口
app.include_router(history.router, prefix="/api", tags=["历史记录"])


# ============================================================
# 根路径
# ============================================================
@app.get("/", tags=["首页"])
async def root():
    """
    根路径

    返回 API 基本信息
    """
    return {
        "name": API_TITLE,
        "version": API_VERSION,
        "description": "基于 DeepAgent 的 AI 对话系统 API",
        "docs": "/docs",
        "redoc": "/redoc"
    }


# ============================================================
# 健康检查
# ============================================================
@app.get("/health", tags=["系统"])
async def health_check():
    """
    健康检查接口

    用于检查服务是否正常运行
    """
    return {
        "status": "healthy",
        "service": API_TITLE,
        "version": API_VERSION
    }


@app.get("/health/ready", tags=["系统"])
async def readiness_check():
    report, status_code = await readiness_report()
    return JSONResponse(report, status_code=status_code)


# ============================================================
# 启动命令
# ============================================================
# uvicorn api_view.web_main:app --reload --host 0.0.0.0 --port 8000


from backend.app import router as business_router
app.include_router(business_router)

from backend.dashboard import router as dashboard_router
app.include_router(dashboard_router)

from backend.operations import router as operations_router
app.include_router(operations_router)


from backend.auth import AuthMiddleware, router as auth_router
app.include_router(auth_router)
app.add_middleware(AuthMiddleware)
