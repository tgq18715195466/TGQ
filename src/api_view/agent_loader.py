"""Load the ERP Agent and expose PostgreSQL-backed session operations."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from agent.postgres_persistence import postgres_persistence


class AgentLoader:
    """Manage the singleton Agent and its shared persistence lifecycle."""

    _instance: Optional["AgentLoader"] = None
    _initialized: bool = False
    _agent = None
    _persistence = postgres_persistence

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def initialize(self):
        """Initialize PostgreSQL persistence before constructing the Agent graph."""
        if self._initialized and self._agent is not None:
            return self._agent

        print("[AgentLoader] 开始初始化...")
        try:
            await self._persistence.initialize()
            from agent.main_agent import get_agent_async

            self._agent = await get_agent_async()
            self._initialized = True
            print(f"[AgentLoader] 初始化完成，checkpoint={self._persistence.mode}")
            return self._agent
        except Exception:
            await self._persistence.close()
            self._agent = None
            self._initialized = False
            raise

    async def close(self) -> None:
        """Release persistence resources and reset loader state."""
        await self._persistence.close()
        self._agent = None
        self._initialized = False

    @property
    def agent(self):
        if self._agent is None:
            raise RuntimeError("Agent 未初始化，请先调用 initialize() 方法")
        return self._agent

    def create_config(
        self,
        thread_id: Optional[str] = None,
        user_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Build the configurable values required by LangGraph."""
        return {
            "configurable": {
                "thread_id": thread_id or str(uuid.uuid4()),
                "user_id": user_id or "anonymous",
                **kwargs,
            }
        }

    async def get_state_history(
        self, thread_id: str, limit: int = 50
    ) -> List[Any]:
        """Return at most limit historical graph states for a thread."""
        config = self.create_config(thread_id)
        states = []
        async for state in self.agent.aget_state_history(config):
            states.append(state)
            if len(states) >= limit:
                break
        return states

    async def get_current_messages(self, thread_id: str) -> List[Dict[str, Any]]:
        """Return the latest LangGraph message state for a thread."""
        config = self.create_config(thread_id)
        try:
            state = await self.agent.aget_state(config)
            if state and state.values:
                return state.values.get("messages", [])
        except Exception as exc:  # noqa: BLE001
            print(f"[AgentLoader] 获取消息失败: {exc}")
        return []

    async def get_pending_interrupts(self, thread_id: str, public_id: str) -> list[dict]:
        from api_view.interrupts import format_interrupts, snapshot_interrupts
        snapshot = await self.agent.aget_state(self.create_config(thread_id), subgraphs=True)
        return format_interrupts(snapshot_interrupts(snapshot), public_id)

    async def get_all_thread_ids(self) -> List[str]:
        return await self._persistence.get_all_thread_ids()

    async def get_session_updated_at(self, thread_id: str):
        return await self._persistence.get_session_updated_at(thread_id)

    async def delete_session(self, thread_id: str) -> bool:
        return await self._persistence.delete_session(thread_id)

    async def save_display_messages(
        self, thread_id: str, messages: List[Dict[str, Any]]
    ) -> bool:
        return await self._persistence.save_display_messages(thread_id, messages)

    async def get_display_messages(
        self, thread_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        return await self._persistence.get_display_messages(thread_id)


agent_loader = AgentLoader()
