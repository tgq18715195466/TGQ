"""PostgreSQL persistence for checkpoints, long-term memory, and chat display messages."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from dotenv import load_dotenv
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

load_dotenv(override=False)

logger = logging.getLogger(__name__)


def configure_postgres_event_loop_policy() -> None:
    """Use the selector loop required by Psycopg async connections on Windows."""
    if sys.platform != "win32":
        return
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return
    if not isinstance(asyncio.get_event_loop_policy(), selector_policy):
        asyncio.set_event_loop_policy(selector_policy())


configure_postgres_event_loop_policy()


DISPLAY_MESSAGES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS session_display_messages (
    thread_id TEXT NOT NULL,
    message_index INTEGER NOT NULL,
    message JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (thread_id, message_index)
)
"""

DISPLAY_MESSAGES_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_session_display_messages_thread_updated
ON session_display_messages (thread_id, updated_at DESC)
"""


class PostgresPersistence:
    """Own PostgreSQL lifecycle and expose the current checkpointer and Store."""

    _MAX_FIELD_LENGTH = 500_000

    def __init__(
        self,
        uri: str | None = None,
        pool_factory: Callable[..., Any] = AsyncConnectionPool,
        saver_factory: Callable[..., BaseCheckpointSaver] = AsyncPostgresSaver,
        store_factory: Callable[..., BaseStore] = AsyncPostgresStore,
    ) -> None:
        self.uri = os.getenv("POSTGRES_URI", "") if uri is None else uri
        self.required = os.getenv("POSTGRES_REQUIRED", "false").lower() in {"1", "true", "yes"}
        self._pool_factory = pool_factory
        self._saver_factory = saver_factory
        self._store_factory = store_factory
        self._pool: Any | None = None
        self._checkpointer: BaseCheckpointSaver = InMemorySaver()
        self._store: BaseStore = InMemoryStore()
        self._mode = "fallback_memory"
        self._initialized = False
        self._last_error: Exception | None = None

    @property
    def checkpointer(self) -> BaseCheckpointSaver:
        return self._checkpointer

    @property
    def store(self) -> BaseStore:
        return self._store

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def persistent(self) -> bool:
        return self._mode == "postgresql"

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    async def initialize(self) -> BaseCheckpointSaver:
        """Open PostgreSQL and initialize checkpoint, Store, and display schemas."""
        if self._initialized:
            return self._checkpointer

        self._initialized = True
        if not self.uri:
            if self.required:
                self._initialized = False
                raise RuntimeError("POSTGRES_URI is required when POSTGRES_REQUIRED=true")
            logger.warning("POSTGRES_URI 未配置，checkpoint 与长期记忆使用进程内存")
            return self._checkpointer

        try:
            self._pool = self._pool_factory(
                conninfo=self.uri,
                min_size=1,
                max_size=10,
                timeout=3,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                },
                open=False,
            )
            await self._pool.open(wait=True, timeout=3)
            checkpointer = self._saver_factory(self._pool)
            await checkpointer.setup()
            store = self._store_factory(self._pool)
            await store.setup()
            await self._setup_display_schema()
        except Exception as exc:  # noqa: BLE001
            self._last_error = exc
            if self.required:
                await self._close_pool()
                self._initialized = False
                raise RuntimeError("PostgreSQL persistence is required but unavailable") from exc
            logger.warning(
                "PostgreSQL 不可用（%s），checkpoint 与长期记忆已降级为进程内存",
                exc,
            )
            await self._close_pool()
            self._checkpointer = InMemorySaver()
            self._store = InMemoryStore()
            self._mode = "fallback_memory"
            return self._checkpointer

        self._checkpointer = checkpointer
        self._store = store
        self._mode = "postgresql"
        self._last_error = None
        logger.info("PostgreSQL checkpoint、长期记忆与会话存储已启用")
        return self._checkpointer

    async def _setup_display_schema(self) -> None:
        async with self._pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(DISPLAY_MESSAGES_TABLE_SQL)
                await cursor.execute(DISPLAY_MESSAGES_INDEX_SQL)

    @classmethod
    def _normalize_json(cls, value: Any) -> Any:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, dict):
            return {str(key): cls._normalize_json(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._normalize_json(item) for item in value]
        return value

    @classmethod
    def _prepare_message(cls, message: dict[str, Any]) -> dict[str, Any]:
        prepared = cls._normalize_json(message)
        for field in ("text", "content", "args"):
            value = prepared.get(field)
            if isinstance(value, str) and len(value) > cls._MAX_FIELD_LENGTH:
                prepared[field] = (
                    value[: cls._MAX_FIELD_LENGTH] + "\n\n...(内容过长，已截断)"
                )
        return prepared

    async def get_all_thread_ids(self) -> list[str]:
        """Return every valid thread stored in checkpoints or display messages."""
        if self._pool is None:
            return []
        query = """
            SELECT DISTINCT thread_id
            FROM (
                SELECT thread_id FROM checkpoints
                UNION
                SELECT thread_id FROM session_display_messages
            ) AS stored_sessions
            WHERE thread_id IS NOT NULL AND thread_id NOT IN ('', '*')
            ORDER BY thread_id
        """
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query)
                    rows = await cursor.fetchall()
            return [row["thread_id"] for row in rows]
        except Exception as exc:  # noqa: BLE001
            logger.exception("读取 PostgreSQL 会话列表失败: %s", exc)
            return []

    async def get_session_updated_at(self, thread_id: str) -> datetime:
        """Return the newest display-message or checkpoint timestamp for a thread."""
        fallback = datetime.now(timezone.utc)
        if self._pool is None:
            return fallback
        query = """
            SELECT MAX(updated_at) AS updated_at
            FROM (
                SELECT MAX(updated_at) AS updated_at
                FROM session_display_messages
                WHERE thread_id = %s
                UNION ALL
                SELECT MAX((checkpoint->>'ts')::timestamptz) AS updated_at
                FROM checkpoints
                WHERE thread_id = %s
            ) AS timestamps
        """
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, (thread_id, thread_id))
                    row = await cursor.fetchone()
            return row["updated_at"] if row and row.get("updated_at") else fallback
        except Exception as exc:  # noqa: BLE001
            logger.exception("读取 PostgreSQL 会话更新时间失败: %s", exc)
            return fallback

    async def delete_session(self, thread_id: str) -> bool:
        """Delete all checkpoint and display-message rows for one thread."""
        if self._pool is None:
            return False
        statements = (
            "DELETE FROM checkpoint_writes WHERE thread_id = %s",
            "DELETE FROM checkpoint_blobs WHERE thread_id = %s",
            "DELETE FROM checkpoints WHERE thread_id = %s",
            "DELETE FROM session_display_messages WHERE thread_id = %s",
        )
        try:
            async with self._pool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor() as cursor:
                        for statement in statements:
                            await cursor.execute(statement, (thread_id,))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("删除 PostgreSQL 会话失败: %s", exc)
            return False

    async def save_display_messages(
        self, thread_id: str, messages: list[dict[str, Any]]
    ) -> bool:
        """Atomically replace the frontend display messages for one thread."""
        if self._pool is None:
            return False
        delete_sql = "DELETE FROM session_display_messages WHERE thread_id = %s"
        insert_sql = """
            INSERT INTO session_display_messages
                (thread_id, message_index, message, updated_at)
            VALUES (%s, %s, %s, %s)
        """
        now = datetime.now(timezone.utc)
        rows = [
            (thread_id, index, Jsonb(self._prepare_message(message)), now)
            for index, message in enumerate(messages)
        ]
        try:
            async with self._pool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor() as cursor:
                        await cursor.execute(delete_sql, (thread_id,))
                        if rows:
                            await cursor.executemany(insert_sql, rows)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("保存 PostgreSQL 展示消息失败: %s", exc)
            return False

    async def get_display_messages(
        self, thread_id: str
    ) -> list[dict[str, Any]] | None:
        """Read frontend display messages in their original stream order."""
        if self._pool is None:
            return None
        query = """
            SELECT message
            FROM session_display_messages
            WHERE thread_id = %s
            ORDER BY message_index
        """
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, (thread_id,))
                    rows = await cursor.fetchall()
            return [row["message"] for row in rows] or None
        except Exception as exc:  # noqa: BLE001
            logger.exception("读取 PostgreSQL 展示消息失败: %s", exc)
            return None

    async def _close_pool(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def close(self) -> None:
        """Close PostgreSQL resources and reset the service for a later restart."""
        await self._close_pool()
        self._checkpointer = InMemorySaver()
        self._store = InMemoryStore()
        self._mode = "fallback_memory"
        self._initialized = False


postgres_persistence = PostgresPersistence()
