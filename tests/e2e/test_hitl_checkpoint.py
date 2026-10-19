from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class State(TypedDict, total=False):
    writes: int
    decision: str


def _approval_node(state: State) -> State:
    decision = interrupt({"type": "hitl_approval"})
    return {"decision": decision["decisions"][0]["type"]}


def _write_node(state: State) -> State:
    return {"writes": state.get("writes", 0) + (1 if state["decision"] == "approve" else 0)}


def _graph():
    builder = StateGraph(State)
    builder.add_node("approval", _approval_node)
    builder.add_node("write", _write_node)
    builder.add_edge(START, "approval")
    builder.add_edge("approval", "write")
    builder.add_edge("write", END)
    return builder.compile(checkpointer=InMemorySaver())


def test_hitl_approve_resumes_checkpoint_and_writes_once():
    graph = _graph()
    config = {"configurable": {"thread_id": "approve"}}
    interrupted = graph.invoke({"writes": 0}, config)
    assert interrupted["__interrupt__"][0].value["type"] == "hitl_approval"
    result = graph.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
    assert result["writes"] == 1


def test_hitl_reject_resumes_checkpoint_with_zero_writes():
    graph = _graph()
    config = {"configurable": {"thread_id": "reject"}}
    graph.invoke({"writes": 0}, config)
    result = graph.invoke(Command(resume={"decisions": [{"type": "reject"}]}), config)
    assert result["writes"] == 0

