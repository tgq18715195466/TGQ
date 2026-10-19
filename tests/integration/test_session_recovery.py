from datetime import datetime, timezone
from typing import Annotated, TypedDict
import json
import httpx
import pytest
from fastapi import FastAPI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt
from api_view.agent_loader import AgentLoader
from api_view.api import chat, history
from backend.auth import current_identity


@pytest.fixture(autouse=True)
def authenticated_identity():
    token = current_identity.set({'role': 'admin'})
    yield
    current_identity.reset(token)

class State(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    writes: int
    decisions: list

def approval(state):
    result = interrupt({"action_requests": [{"name": "order_create", "args": {}}, {"name": "inventory_adjust", "args": {}}], "review_configs": [{"action_name": name, "allowed_decisions": ["approve", "reject"]} for name in ["order_create", "inventory_adjust"]]})
    return {"decisions": result["decisions"]}

def write(state):
    return {"writes": sum(d["type"] == "approve" for d in state["decisions"])}

class Repository:
    def __init__(self): self.data = {}
    async def save_display_messages(self, key, messages): self.data[key] = list(messages); return True
    async def get_display_messages(self, key): return self.data.get(key)
    async def get_all_thread_ids(self): return list(self.data)
    async def get_session_updated_at(self, key): return datetime.now(timezone.utc)
    async def delete_session(self, key): self.data.pop(key, None); return True

@pytest.fixture
async def harness(monkeypatch):
    builder = StateGraph(State)
    builder.add_node("approval", approval); builder.add_node("write", write)
    builder.add_edge(START, "approval"); builder.add_edge("approval", "write"); builder.add_edge("write", END)
    loader = object.__new__(AgentLoader)
    loader._agent = builder.compile(checkpointer=InMemorySaver())
    loader._persistence = Repository()
    monkeypatch.setattr(chat, "agent_loader", loader)
    monkeypatch.setattr(history, "agent_loader", loader)
    app = FastAPI(); app.include_router(chat.router, prefix="/api"); app.include_router(history.router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, loader

def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

async def start(client):
    r = await client.post("/api/chat/stream", json={"thread_id": "roundtrip", "message": "synthetic test"})
    assert r.status_code == 200
    return next(e for e in events(r) if e["type"] == "interrupt")

async def test_history_returns_public_id_and_pending_interrupts(harness):
    client, loader = harness
    initial = await start(client)
    sessions = (await client.get("/api/history")).json()["sessions"]
    assert sessions[0]["thread_id"] == "roundtrip"
    r = await client.get("/api/history/roundtrip/messages")
    assert r.status_code == 200
    assert r.json()["messages"][0]["content"] == "synthetic test"
    pending = r.json()["interrupts"]
    assert pending[0]["interrupt_id"] == initial["interrupts"][0]["interrupt_id"]
    assert len(pending[0]["action_requests"]) == 2

@pytest.mark.parametrize("decision,expected", [("approve", 2), ("reject", 0)])
async def test_resume_legacy_prefixed_id_does_not_double_prefix(harness, decision, expected):
    client, loader = harness
    initial = await start(client)
    payload = {"interrupt_id": initial["interrupts"][0]["interrupt_id"], "decisions": [{"type": decision}, {"type": decision}]}
    r = await client.post("/api/chat/anonymous:roundtrip/resume", json={"resume": payload})
    assert r.status_code == 200
    assert not any(e["type"] == "error" for e in events(r)), r.text
    state = await loader.agent.aget_state(loader.create_config("anonymous:roundtrip"))
    assert state.values["writes"] == expected
    assert not state.next
    assert set(loader._persistence.data) == {"anonymous:roundtrip"}
    assert (await client.post("/api/chat/roundtrip/resume", json={"resume": payload})).status_code == 409

async def test_bad_decision_count_and_new_message_cannot_bypass_pending_approval(harness):
    client, loader = harness
    await start(client)
    r = await client.post("/api/chat/roundtrip/resume", json={"resume": {"decisions": [{"type": "approve"}]}})
    assert r.status_code == 422
    r = await client.post("/api/chat/stream", json={"thread_id": "roundtrip", "message": "bypass"})
    assert r.status_code == 409
    state = await loader.agent.aget_state(loader.create_config("anonymous:roundtrip"))
    assert "writes" not in state.values

async def test_history_is_user_scoped(harness):
    client, loader = harness
    await start(client)
    r = await client.get("/api/history", headers={"x-user-id": "other"})
    assert r.json()["sessions"] == []
    assert (await client.get("/api/history/anonymous:roundtrip/messages", headers={"x-user-id": "other"})).status_code == 404

async def test_pausing_later_turn_preserves_display_history(harness):
    client, loader = harness
    loader._persistence.data["anonymous:roundtrip"] = [{"id":"earlier", "role":"user", "content":"previous turn"}]
    await start(client)
    assert [m["content"] for m in loader._persistence.data["anonymous:roundtrip"]] == ["previous turn", "synthetic test"]


async def test_chat_state_returns_public_id_and_real_message_objects(harness):
    client, loader = harness
    await start(client)
    r = await client.get("/api/chat/roundtrip")
    assert r.status_code == 200
    assert r.json()["thread_id"] == "roundtrip"
    assert r.json()["messages"][0]["role"] == "user"

async def test_stale_interrupt_id_cannot_approve_current_action(harness):
    client, loader = harness
    await start(client)
    r = await client.post("/api/chat/roundtrip/resume", json={"resume":{"interrupt_id":"expired", "decisions":[{"type":"approve"},{"type":"approve"}]}})
    assert r.status_code == 409
    state = await loader.agent.aget_state(loader.create_config("anonymous:roundtrip"))
    assert "writes" not in state.values


async def test_parallel_interrupts_can_be_resolved_independently(harness):
    client, loader = harness
    class ParallelState(TypedDict, total=False):
        messages: Annotated[list, add_messages]
        left: int
        right: int
    def branch(name):
        def run(state):
            action = 'order_create' if name == 'left' else 'inventory_adjust'
            answer = interrupt({"action_requests":[{"name":action,"args":{}}], "review_configs":[]})
            return {name:int(answer["decisions"][0]["type"] == "approve")}
        return run
    builder=StateGraph(ParallelState)
    for name in ["left","right"]:
        builder.add_node(name,branch(name));builder.add_edge(START,name);builder.add_edge(name,END)
    loader._agent=builder.compile(checkpointer=InMemorySaver())
    first=await start(client)
    assert len(first["interrupts"])==2
    pending=(await client.get("/api/history/roundtrip/messages")).json()["interrupts"]
    assert len(pending)==2
    chosen=next(i for i in pending if i["action_requests"][0]["name"]=="order_create")
    r=await client.post("/api/chat/roundtrip/resume",json={"resume":{"interrupt_id":chosen["interrupt_id"],"decisions":[{"type":"approve"}]}})
    assert r.status_code==200 and not any(e["type"]=="error" for e in events(r)),r.text
    remaining=(await client.get("/api/history/roundtrip/messages")).json()["interrupts"]
    assert len(remaining)==1 and remaining[0]["action_requests"][0]["name"]=="inventory_adjust"
    r=await client.post("/api/chat/roundtrip/resume",json={"resume":{"interrupt_id":remaining[0]["interrupt_id"],"decisions":[{"type":"reject"}]}})
    assert r.status_code==200 and not any(e["type"]=="error" for e in events(r)),r.text
    state=await loader.agent.aget_state(loader.create_config("anonymous:roundtrip"))
    assert state.values["left"]==1 and state.values["right"]==0
    assert not state.next


def test_idless_delayed_approval_cannot_match_a_new_interrupt():
    from api_view.interrupts import validate_resume
    from fastapi import HTTPException
    current = [{"interrupt_id":"approval-B", "interrupt_type":"hitl_approval", "action_requests":[{"name":"inventory_adjust","args":{}}], "review_configs":[]}]
    with pytest.raises(HTTPException) as missing:
        validate_resume(current, {"decisions":[{"type":"approve"}]})
    assert missing.value.status_code == 422
    with pytest.raises(HTTPException) as stale:
        validate_resume(current, {"interrupt_id":"approval-A", "decisions":[{"type":"approve"}]})
    assert stale.value.status_code == 409
