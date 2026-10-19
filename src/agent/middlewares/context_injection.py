"""
运行时上下文注入中间件。

从 runtime.context（ProcurementContext）中提取 user_id / username，
在 Agent 启动时以 SystemMessage 形式注入到对话中。Agent 无需调用工具
即可知道当前用户身份，从而正确读写 /memories/{user_id}/preferences.md。

使用方式:
    from agent.middlewares.context_injection import ContextInjectionMiddleware
    middleware = ContextInjectionMiddleware()
"""

from __future__ import annotations

import logging
import asyncio
from typing import Any, Dict, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage
from agent.memory.language import CHINESE_REPLY_RULES

logger = logging.getLogger(__name__)


class ContextInjectionMiddleware(AgentMiddleware):
    """将 runtime.context 中的 user_id/username 注入到对话开头。"""

    def __init__(self):
        super().__init__()
        self._preference_locks = {}

    async def _ensure_preferences(self, runtime):
        ctx = getattr(runtime, "context", None)
        user_id = getattr(ctx, "user_id", None)
        store = getattr(runtime, "store", None)
        if not user_id or store is None:
            return
        lock = self._preference_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            namespace, key = (user_id,), f"/{user_id}/preferences.md"
            if await store.aget(namespace, key) is None:
                from agent.middlewares.memory_update import _create_file_value
                await store.aput(namespace, key, _create_file_value("preferred_output: table\npreferred_chart_type: bar\npreferred_currency: CNY\npreferred_language: zh\nrecent_suppliers: []\nrecent_queries: []"))

    def before_agent(
        self, state: Dict[str, Any], runtime: Any
    ) -> Optional[Dict[str, Any]]:
        """同步版本：注入用户上下文 SystemMessage。"""
        ctx = getattr(runtime, "context", None)
        if ctx is None:
            logger.warning("ContextInjectionMiddleware: runtime.context 为 None，跳过上下文注入")
            return None
        user_id = getattr(ctx, "user_id", None)
        if not user_id:
            logger.warning("ContextInjectionMiddleware: runtime.context 中没有 user_id，跳过上下文注入")
            return None
        username = getattr(ctx, "username", None) or user_id

        logger.info(f"ContextInjectionMiddleware: 注入用户上下文 user_id={user_id}, username={username}")

        from backend.auth import current_identity, can
        user = current_identity.get() or {}
        identity_notice = f"当前账号角色：{user.get('role', '未登录')}；允许权限：{', '.join(user.get('permissions', []))}。操作人由系统自动记录，不询问操作人，不接受用户指定身份或角色。工具确认只提交待审业务，正式审批由其他审批人处理。\n"
        if can(user, 'read_procurement') and not can(user, 'procurement'):
            identity_notice += '当前账号可以只读查询采购订单、采购申请和供应商信息；查询无需采购写入权限。\n'
        if not can(user, 'procurement'):
            identity_notice += '当前账号没有采购写入权限。不得发起采购申请、创建或修改采购订单，也不得要求用户补充采购字段或确认采购操作。遇到采购写入需求，请说明应由采购员处理；仓管员仍可执行收货及库存作业。\n'
        if not can(user, 'approve'):
            identity_notice += '当前账号不得审批采购申请或订单。\n'
        from datetime import datetime, timezone, timedelta
        today=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        identity_notice += f"当前业务日期（北京时间）：{today}。今天、明天、后天必须基于此日期计算，不使用训练数据中的日期，不要求用户提供今天的日期。\n"
        notice = (
            f"【系统上下文】\n"
            f"当前用户 user_id: {user_id}\n"
            f"当前用户 username: {username}\n"
            f"用户偏好文件路径: /memories/{user_id}/preferences.md\n"
            f"\n请首先使用 read_file 读取上述偏好文件了解用户偏好。"
            f"\n（recent_suppliers 和 recent_queries 由系统自动维护，你无需手动更新）"
        )
        return {"messages": [SystemMessage(content=identity_notice + notice + CHINESE_REPLY_RULES)]}

    async def abefore_agent(
        self, state: Dict[str, Any], runtime: Any
    ) -> Optional[Dict[str, Any]]:
        """异步版本：注入用户上下文 SystemMessage。"""
        await self._ensure_preferences(runtime)
        return self.before_agent(state, runtime)
