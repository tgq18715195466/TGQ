from datetime import datetime, timezone
from decimal import Decimal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from psycopg.types.json import Jsonb

from agent import postgres_persistence as persistence_module
from agent.postgres_persistence import PostgresPersistence


class FakeCursor:
    def __init__(self, connection=None):
        self.connection = connection
        self.executed = []
        self.executed_many = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params=None):
        self.executed.append((query, params))
        return self

    async def executemany(self, query, params):
        self.executed_many.append((query, list(params)))

    async def fetchall(self):
        return self.connection.fetchall_result

    async def fetchone(self):
        return self.connection.fetchone_result


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self):
        self.fetchall_result = []
        self.fetchone_result = None
        self.cursor_instance = FakeCursor(self)

    def cursor(self):
        return self.cursor_instance

    def transaction(self):
        return FakeTransaction()


class FakeConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.connection_instance = FakeConnection()
        self.open_calls = []
        self.closed = False
        self.__class__.instances.append(self)

    async def open(self, **kwargs):
        self.open_calls.append(kwargs)

    async def close(self):
        self.closed = True

    def connection(self):
        return FakeConnectionContext(self.connection_instance)


class FakeSaver:
    instances = []

    def __init__(self, pool):
        self.pool = pool
        self.setup_calls = 0
        self.__class__.instances.append(self)

    async def setup(self):
        self.setup_calls += 1


class FakeStore:
    instances = []

    def __init__(self, pool):
        self.pool = pool
        self.setup_calls = 0
        self.__class__.instances.append(self)

    async def setup(self):
        self.setup_calls += 1


def test_windows_configures_selector_event_loop_policy(monkeypatch):
    configured = []

    class SelectorPolicy:
        pass

    monkeypatch.setattr(persistence_module.sys, "platform", "win32")
    monkeypatch.setattr(
        persistence_module.asyncio,
        "WindowsSelectorEventLoopPolicy",
        SelectorPolicy,
        raising=False,
    )
    monkeypatch.setattr(
        persistence_module.asyncio, "get_event_loop_policy", lambda: object()
    )
    monkeypatch.setattr(
        persistence_module.asyncio,
        "set_event_loop_policy",
        lambda policy: configured.append(policy),
    )

    persistence_module.configure_postgres_event_loop_policy()

    assert len(configured) == 1
    assert isinstance(configured[0], SelectorPolicy)


async def test_missing_postgres_uri_uses_memory_fallback():
    service = PostgresPersistence(uri="")

    result = await service.initialize()

    assert isinstance(result, InMemorySaver)
    assert service.checkpointer is result
    assert isinstance(service.store, InMemoryStore)
    assert service.mode == "fallback_memory"
    assert service.persistent is False


async def test_postgres_initialization_opens_pool_and_sets_up_schemas():
    FakePool.instances.clear()
    FakeSaver.instances.clear()
    FakeStore.instances.clear()
    service = PostgresPersistence(
        uri="postgresql://user:secret@localhost/erp_agent",
        pool_factory=FakePool,
        saver_factory=FakeSaver,
        store_factory=FakeStore,
    )

    result = await service.initialize()

    pool = FakePool.instances[-1]
    saver = FakeSaver.instances[-1]
    store = FakeStore.instances[-1]
    assert result is saver
    assert pool.open_calls == [{"wait": True, "timeout": 3}]
    assert pool.kwargs["conninfo"].startswith("postgresql://")
    assert pool.kwargs["open"] is False
    assert saver.pool is pool
    assert saver.setup_calls == 1
    assert store.pool is pool
    assert store.setup_calls == 1
    assert service.store is store
    assert any(
        "CREATE TABLE IF NOT EXISTS session_display_messages" in query
        for query, _ in pool.connection_instance.cursor_instance.executed
    )
    assert service.mode == "postgresql"
    assert service.persistent is True


async def test_store_setup_failure_falls_back_all_persistence_to_memory():
    class FailingStore:
        def __init__(self, pool):
            self.pool = pool

        async def setup(self):
            raise RuntimeError("store setup failed")

    FakePool.instances.clear()
    FakeSaver.instances.clear()
    service = PostgresPersistence(
        uri="postgresql://user:secret@localhost/erp_agent",
        pool_factory=FakePool,
        saver_factory=FakeSaver,
        store_factory=FailingStore,
    )

    result = await service.initialize()

    assert isinstance(result, InMemorySaver)
    assert isinstance(service.store, InMemoryStore)
    assert service.mode == "fallback_memory"
    assert service.persistent is False
    assert isinstance(service.last_error, RuntimeError)
    assert FakePool.instances[-1].closed is True


async def test_close_resets_checkpointer_and_store_to_memory():
    FakePool.instances.clear()
    FakeSaver.instances.clear()
    FakeStore.instances.clear()
    service = PostgresPersistence(
        uri="postgresql://user:secret@localhost/erp_agent",
        pool_factory=FakePool,
        saver_factory=FakeSaver,
        store_factory=FakeStore,
    )
    await service.initialize()

    await service.close()

    assert FakePool.instances[-1].closed is True
    assert isinstance(service.checkpointer, InMemorySaver)
    assert isinstance(service.store, InMemoryStore)
    assert service.mode == "fallback_memory"
    assert service.persistent is False


def persistent_service():
    service = PostgresPersistence(uri="postgresql://test")
    pool = FakePool(conninfo="postgresql://test")
    service._pool = pool
    service._mode = "postgresql"
    service._initialized = True
    return service, pool


async def test_save_display_messages_uses_jsonb_without_mutating_input():
    service, pool = persistent_service()
    timestamp = datetime(2026, 9, 22, 8, 30, tzinfo=timezone.utc)
    oversized = "x" * 500_001
    messages = [{
        "id": "m1",
        "content": oversized,
        "created_at": timestamp,
        "metadata": {"amount": Decimal("12.50")},
    }]

    assert await service.save_display_messages("thread-1", messages) is True

    cursor = pool.connection_instance.cursor_instance
    insert_sql, rows = cursor.executed_many[-1]
    assert "session_display_messages" in insert_sql
    assert rows[0][0:2] == ("thread-1", 0)
    assert isinstance(rows[0][2], Jsonb)
    stored = rows[0][2].obj
    assert stored["created_at"] == timestamp.isoformat()
    assert stored["metadata"]["amount"] == 12.5
    assert stored["content"].endswith("...(内容过长，已截断)")
    assert messages[0]["content"] == oversized
    assert messages[0]["created_at"] is timestamp


async def test_read_display_messages_preserves_message_order():
    service, pool = persistent_service()
    pool.connection_instance.fetchall_result = [
        {"message": {"id": "m1"}},
        {"message": {"id": "m2"}},
    ]

    result = await service.get_display_messages("thread-1")

    assert result == [{"id": "m1"}, {"id": "m2"}]
    query, params = pool.connection_instance.cursor_instance.executed[-1]
    assert "ORDER BY message_index" in query
    assert params == ("thread-1",)


async def test_thread_ids_include_checkpoint_and_display_sessions():
    service, pool = persistent_service()
    pool.connection_instance.fetchall_result = [
        {"thread_id": "thread-1"},
        {"thread_id": "thread-2"},
    ]

    result = await service.get_all_thread_ids()

    assert result == ["thread-1", "thread-2"]
    query, _ = pool.connection_instance.cursor_instance.executed[-1]
    assert "FROM checkpoints" in query
    assert "FROM session_display_messages" in query


async def test_session_updated_at_uses_display_and_checkpoint_timestamps():
    service, pool = persistent_service()
    expected = datetime(2026, 9, 22, 8, 30, tzinfo=timezone.utc)
    pool.connection_instance.fetchone_result = {"updated_at": expected}

    result = await service.get_session_updated_at("thread-1")

    assert result == expected
    query, params = pool.connection_instance.cursor_instance.executed[-1]
    assert "checkpoint->>'ts'" in query
    assert params == ("thread-1", "thread-1")


async def test_delete_session_removes_checkpoints_and_display_messages():
    service, pool = persistent_service()

    assert await service.delete_session("thread-1") is True

    statements = pool.connection_instance.cursor_instance.executed
    delete_queries = [query for query, _ in statements if "DELETE FROM" in query]
    assert any("checkpoint_writes" in query for query in delete_queries)
    assert any("checkpoint_blobs" in query for query in delete_queries)
    assert any("checkpoints" in query for query in delete_queries)
    assert any("session_display_messages" in query for query in delete_queries)
    assert all(params == ("thread-1",) for query, params in statements if "DELETE FROM" in query)


async def test_required_postgres_refuses_memory_fallback(monkeypatch):
    import pytest
    monkeypatch.setenv("POSTGRES_REQUIRED", "true")
    persistence = PostgresPersistence(uri="")
    with pytest.raises(RuntimeError, match="POSTGRES"):
        await persistence.initialize()
    assert persistence._initialized is False

async def test_required_postgres_connection_error_can_be_retried(monkeypatch):
    import pytest
    monkeypatch.setenv("POSTGRES_REQUIRED", "true")
    class BrokenPool(FakePool):
        async def open(self, **kwargs): raise ConnectionError("offline")
    persistence = PostgresPersistence(uri="postgresql://localhost/test", pool_factory=BrokenPool)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="PostgreSQL"):
            await persistence.initialize()
        assert persistence._initialized is False
