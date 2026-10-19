import pytest
from typing import TypedDict
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.graph import StateGraph, START, END
from agent.middlewares.memory_update import _extract_entities

class State(TypedDict):
    extracted: dict

@pytest.mark.asyncio
async def test_entity_extraction_updates_state_without_public_tokens():
    model=FakeListChatModel(responses=['{"suppliers":["合成供应商"],"query":"合成采购查询"}'])
    async def extract(state):
        return {"extracted":await _extract_entities(model,"查询供应商","查询完成")}
    async def answer(state):
        await FakeListChatModel(responses=["正常回答"]).ainvoke("reply")
        return {}
    graph=StateGraph(State)
    graph.add_node("memory_update",extract)
    graph.add_node("answer",answer)
    graph.add_edge(START,"answer"); graph.add_edge("answer","memory_update"); graph.add_edge("memory_update",END)
    tokens=[]; final=None
    async for mode, data in graph.compile().astream({"extracted":{}},stream_mode=["messages","values"]):
        if mode=="messages": tokens.append(data[0].content)
        if mode=="values": final=data
    assert final["extracted"]=={"suppliers":["合成供应商"],"query":"合成采购查询"}
    assert "".join(tokens)=="正常回答"
