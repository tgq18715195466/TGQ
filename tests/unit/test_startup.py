import socket
import asyncio
import pytest
import run_local
from api_view import readiness

def test_port_selection_skips_occupied_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert run_local.find_available_port(port) > port

def test_uvicorn_selector_loop():
    from api_view.event_loop import selector_loop
    loop = selector_loop()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()

async def test_mcp_404_not_healthy(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def list_tools(self): raise ConnectionError("404")
    monkeypatch.setattr(readiness, "Client", Client, raising=False)
    result = await readiness._erp_mcp_check()
    assert result["status"] == "unhealthy"


def test_local_startup_migrates_before_backend(monkeypatch):
    calls=[]
    monkeypatch.setattr(run_local, "find_available_port", lambda start: start)
    monkeypatch.setattr(run_local.subprocess, "run", lambda *args, **kw: calls.append((args,kw)))
    class StopStartup(Exception): pass
    def spawn(*args, **kwargs):
        assert calls and calls[0][0][0][-2:] == ["-m", "backend.migrate"]
        assert calls[0][1]["check"] is True
        raise StopStartup()
    monkeypatch.setattr(run_local, "_spawn", spawn)
    with pytest.raises(StopStartup): run_local.main()
