"""
后端配置文件

包含 PostgreSQL 连接配置、项目路径等
"""

import os
from pathlib import Path

# ============================================================
# PostgreSQL 配置 - 用于存储 Agent checkpoint、长期记忆与会话展示消息
# ============================================================
# PostgreSQL 连接 URI，格式: postgresql://用户名:密码@主机地址:端口/数据库名
POSTGRES_URI = os.getenv("POSTGRES_URI", "")

# ============================================================
# 项目路径配置
# ============================================================
# 项目根目录
PROJECT_DIR = Path(__file__).parent.parent

# Agent 相关代码路径
AGENT_DIR = PROJECT_DIR / "src" / "agent"
# 技能目录（沙箱中的路径，与 sandbox_agent.py 保持一致）
SKILLS_PATH = "/workspace/skills"
# 内存文件（AGENTS.md）
MEMORY_PATH = "/AGENTS.md"
# 子代理配置文件（在 src 目录下）
SUBAGENTS_CONFIG = PROJECT_DIR / "src" / "subagents.yaml"
# AGENTS.md 文件路径
AGENTS_MD_PATH = PROJECT_DIR / "src" / "AGENTS.md"

# ============================================================
# 服务配置
# ============================================================
# API 服务标题
API_TITLE = "DeepAgent Chat API"
# API 版本
API_VERSION = "1.0.0"
# API 描述
API_DESCRIPTION = "基于 DeepAgent 的 AI 对话系统 API"
