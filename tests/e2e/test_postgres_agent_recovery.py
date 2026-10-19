"""Opt-in, offline-model DeepAgents HITL recovery against local PostgreSQL."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "src/agent/subagents/configs/procurement_order.yaml"
NAMES = ("order_create", "inventory_adjust")
pytestmark = pytest.mark.skipif(
    os.environ.get("ERP_TEST_POSTGRES") != "1",
    reason="Local PostgreSQL integration requires ERP_TEST_POSTGRES=1",
)


def _graph(persistence, writes):
    from deepagents import create_deep_agent
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool
    from langchain_core.utils.function_calling import convert_to_openai_tool
    import yaml

    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    for name in NAMES:
        assert name in config["tools"], f"Missing configured tool: {name}"
        assert set(config["interrupt_on"][name]["allowed_decisions"]) == {
            "approve", "reject"
        }, f"Missing approval policy: {name}"

    class ScriptedModel(BaseChatModel):
        """Derive the next call from checkpointed messages, never a process counter."""

        role: str
        bound_names: tuple[str, ...] = ()

        @property
        def _llm_type(self):
            return "offline-recovery-script"

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            names = tuple(convert_to_openai_tool(t)["function"]["name"] for t in tools)
            required = {"task"} if self.role == "main" else set(NAMES)
            assert required <= set(names), "Script must call actual bound tools"
            return self.model_copy(update={"bound_names": names})

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if any(isinstance(message, ToolMessage) for message in messages):
                answer = AIMessage(content=f"{self.role} complete")
            else:
                if self.role == "main":
                    calls = [{
                        "name": "task", "id": "delegate-order", "args": {
                            "subagent_type": config["name"],
                            "description": "Run the two synthetic write tools in parallel.",
                        },
                    }]
                else:
                    calls = [
                        {"name": name, "id": f"synthetic-{name}",
                         "args": {"marker": f"test-{name}"}}
                        for name in NAMES
                    ]
                assert all(call["name"] in self.bound_names for call in calls)
                answer = AIMessage(content="", tool_calls=calls)
            return ChatResult(generations=[ChatGeneration(message=answer)])

    @tool
    def order_create(marker: str) -> str:
        """Record a synthetic order write without any ERP connection."""
        writes.append(("order_create", marker))
        return "synthetic order recorded"

    @tool
    def inventory_adjust(marker: str) -> str:
        """Record a synthetic inventory write without any ERP connection."""
        writes.append(("inventory_adjust", marker))
        return "synthetic inventory recorded"

    return create_deep_agent(
        model=ScriptedModel(role="main"),
        system_prompt="Delegate the synthetic request to the procurement subagent.",
        subagents=[{
            "name": config["name"],
            "description": config["description"],
            "system_prompt": "Execute the deterministic synthetic tool calls.",
            "model": ScriptedModel(role="order"),
            "tools": [order_create, inventory_adjust],
            "interrupt_on": config["interrupt_on"],
        }],
        checkpointer=persistence.checkpointer,
        store=persistence.store,
    )


async def _phase(phase, thread_id, decisions):
    from agent.postgres_persistence import PostgresPersistence
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore
    from langgraph.types import Command

    persistence = PostgresPersistence()
    persistence.required = True
    namespace = ("test_postgres_agent_recovery", thread_id)
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    writes = []
    try:
        await persistence.initialize()
        assert persistence.persistent
        assert isinstance(persistence.checkpointer, AsyncPostgresSaver)
        assert isinstance(persistence.store, AsyncPostgresStore)
        if phase == "cleanup":
            # Exact generated thread only; never enumerate/delete other sessions.
            await persistence.checkpointer.adelete_thread(thread_id)
            await persistence.store.adelete(namespace, "suspended")
            assert await persistence.checkpointer.aget_tuple(cfg) is None
            assert await persistence.store.aget(namespace, "suspended") is None
            return {"cleaned": True}

        graph = _graph(persistence, writes)
        if phase == "suspend":
            result = await graph.ainvoke(
                {"messages": [("user", "Run synthetic order and inventory writes.")]}, cfg
            )
            interrupts = result.get("__interrupt__", ())
        else:
            state = await graph.aget_state(cfg)
            interrupts = state.interrupts
            item = await persistence.store.aget(namespace, "suspended")
            assert item is not None, "Isolated store marker must survive restart"
            assert item.value["pid"] != os.getpid(), "Recovery must use a new process"
            assert [i.id for i in interrupts] == item.value["interrupt_ids"]

        assert writes == [], "Neither sink may execute before approval"
        assert len(interrupts) == 1, "One nested HITL batch must contain both calls"
        pending = interrupts[0]
        actions = pending.value["action_requests"]
        assert [a["name"] for a in actions] == list(NAMES)
        assert [a["args"] for a in actions] == [
            {"marker": f"test-{name}"} for name in NAMES
        ]
        reviews = pending.value["review_configs"]
        assert {r["action_name"] for r in reviews} == set(NAMES)
        assert all(set(r["allowed_decisions"]) == {"approve", "reject"} for r in reviews)
        state = await graph.aget_state(cfg)
        assert state.next and state.tasks, "Main graph must retain its pending subagent task"
        # A nonempty checkpoint namespace proves the child graph itself was persisted.
        checkpoints = [c async for c in persistence.checkpointer.alist(cfg)]
        assert any(c.config["configurable"]["checkpoint_ns"] for c in checkpoints)

        if phase == "suspend":
            await persistence.store.aput(namespace, "suspended", {
                "pid": os.getpid(), "interrupt_ids": [i.id for i in interrupts],
            })
            return {"pending": list(NAMES), "writes": [], "pid": os.getpid()}

        result = await graph.ainvoke(Command(resume={pending.id: {
            "decisions": [{"type": decision} for decision in decisions],
        }}), cfg)
        expected = sorted(
            (name, f"test-{name}") for name, decision in zip(NAMES, decisions)
            if decision == "approve"
        )
        assert sorted(writes) == expected, "Only approved sinks may run, exactly once"
        assert not result.get("__interrupt__"), "Resume must finish without another approval"
        assert result["messages"][-1].content == "main complete"
        final = await graph.aget_state(cfg)
        assert not final.next and not final.interrupts
        # Reading the finished checkpoint cannot rerun an approved sink.
        await graph.aget_state(cfg)
        assert sorted(writes) == expected
        return {"writes": sorted(writes), "pid": os.getpid(), "complete": True}
    finally:
        await persistence.close()


def _worker(phase, thread_id, decisions):
    """Keep dotenv credentials inside the child, out of arguments and pytest output."""
    import asyncio
    import logging
    import socket

    from dotenv import load_dotenv
    from psycopg.conninfo import conninfo_to_dict

    sys.path.insert(0, str(ROOT / "src"))
    load_dotenv(ROOT / ".env", override=False)
    # Disable tracing even when the developer's dotenv enables it.
    for key in ("LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"):
        os.environ[key] = "false"
    logging.disable(logging.CRITICAL)
    uri = os.environ.get("POSTGRES_URI", "")
    assert uri, "POSTGRES_URI must be configured for this opt-in test"
    connection = conninfo_to_dict(uri)
    assert connection.get("host") in {"localhost", "127.0.0.1", "::1"}, "Local PostgreSQL only"
    assert connection.get("hostaddr", "127.0.0.1") in {"127.0.0.1", "::1"}
    os.environ["PGCONNECT_TIMEOUT"] = "5"
    original_connect = socket.socket.connect

    def local_only(sock, address):
        assert isinstance(address, tuple) and address[0] in {
            "localhost", "127.0.0.1", "::1"
        }, "External networking is forbidden"
        return original_connect(sock, address)

    socket.socket.connect = local_only
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(_phase(phase, thread_id, decisions))


def _run_phase(phase, thread_id, decisions):
    env = dict(os.environ, ERP_TEST_POSTGRES="1", PYTHONUTF8="1")
    process = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), phase, thread_id, *decisions],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=90,
    )
    # Never echo arbitrary child output: library exceptions can contain credentials.
    lines = [line for line in process.stdout.splitlines() if line.startswith("RECOVERY_RESULT=")]
    assert lines, f"{phase}: child returned no sanitized result (exit {process.returncode})"
    result = json.loads(lines[-1].split("=", 1)[1])
    assert process.returncode == 0, f"{phase}: {result}"
    return result


@pytest.mark.parametrize("decisions", [
    ("approve", "approve"), ("reject", "reject"),
    ("approve", "reject"), ("reject", "approve"),
], ids=["approve-both", "reject-both", "approve-order", "approve-inventory"])
def test_nested_agent_postgres_recovery(decisions):
    thread_id = f"e2e-recovery-{uuid.uuid4().hex}"
    try:
        suspended = _run_phase("suspend", thread_id, decisions)
        assert suspended["writes"] == []
        assert suspended["pending"] == list(NAMES)
        resumed = _run_phase("resume", thread_id, decisions)
        assert resumed["pid"] != suspended["pid"]
        assert resumed["complete"]
        assert resumed["writes"] == sorted(
            [name, f"test-{name}"] for name, decision in zip(NAMES, decisions)
            if decision == "approve"
        )
    finally:
        assert _run_phase("cleanup", thread_id, decisions) == {"cleaned": True}


if __name__ == "__main__":
    # Deliberately exclude exception strings, locals, source lines and raw tracebacks.
    try:
        payload = _worker(sys.argv[1], sys.argv[2], sys.argv[3:])
    except Exception as exc:
        import traceback

        payload = {
            "error_type": type(exc).__name__,
            "frames": [{"file": Path(f.filename).name, "line": f.lineno,
                        "function": f.name} for f in traceback.extract_tb(exc.__traceback__)],
        }
        print("RECOVERY_RESULT=" + json.dumps(payload))
        sys.exit(1)
    print("RECOVERY_RESULT=" + json.dumps(payload))
