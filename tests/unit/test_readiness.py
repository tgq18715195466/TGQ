import pytest

from api_view import readiness


def test_memory_fallback_is_explicit(monkeypatch):
    monkeypatch.delenv("POSTGRES_URI", raising=False)
    assert readiness._checkpointer_check() == {
        "status": "degraded", "mode": "fallback_memory", "persistent": False
    }


class FakeCursor:
    def __init__(self):
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query):
        self.queries.append(query)


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_instance


def test_postgres_checkpointer_is_healthy_when_query_succeeds(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/erp_agent")
    monkeypatch.setattr(readiness.psycopg, "connect", lambda *args, **kwargs: connection)

    assert readiness._checkpointer_check() == {
        "status": "healthy", "mode": "postgresql", "persistent": True
    }
    assert connection.cursor_instance.queries == ["SELECT 1"]


def test_postgres_failure_reports_memory_fallback(monkeypatch):
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/erp_agent")

    def fail_connect(*args, **kwargs):
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(readiness.psycopg, "connect", fail_connect)

    assert readiness._checkpointer_check() == {
        "status": "degraded",
        "mode": "fallback_memory",
        "persistent": False,
        "error": "ConnectionError",
    }


@pytest.mark.asyncio
async def test_required_dependencies_make_readiness_unhealthy(monkeypatch):
    monkeypatch.setattr(readiness, "_mysql_check", lambda: {"status": "unhealthy"})
    monkeypatch.setattr(readiness, "_erp_mcp_check", lambda: _async_value({"status": "healthy"}))
    monkeypatch.setattr(readiness, "_model_check", lambda: {"status": "healthy"})
    monkeypatch.setattr(readiness, "_checkpointer_check", lambda: {"status": "degraded", "mode": "fallback_memory"})
    report, status_code = await readiness.readiness_report()
    assert status_code == 503
    assert report["status"] == "unhealthy"


async def _async_value(value):
    return value


def test_live_memory_mode_is_not_reported_persistent(monkeypatch):
    from agent.postgres_persistence import postgres_persistence
    monkeypatch.setattr(postgres_persistence, "_initialized", True)
    monkeypatch.setattr(postgres_persistence, "_mode", "fallback_memory")
    monkeypatch.setenv("POSTGRES_URI", "postgresql://localhost/erp_agent")
    monkeypatch.setattr(readiness.psycopg, "connect", lambda *a, **kw: FakeConnection())
    result = readiness._checkpointer_check()
    assert result["status"] == "degraded"
    assert result["persistent"] is False


async def test_required_persistence_failure_returns_503(monkeypatch):
    monkeypatch.setenv("POSTGRES_REQUIRED", "true")
    monkeypatch.setattr(readiness, "_mysql_check", lambda: {"status":"healthy"})
    monkeypatch.setattr(readiness, "_erp_mcp_check", lambda: _async_value({"status":"healthy"}))
    monkeypatch.setattr(readiness, "_model_check", lambda: {"status":"healthy"})
    monkeypatch.setattr(readiness, "_checkpointer_check", lambda: {"status":"degraded", "persistent":False})
    report, status = await readiness.readiness_report()
    assert status == 503
