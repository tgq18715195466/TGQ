"""A real Agent ToolNode must retain and resume the nested procurement checkpoint."""
import json
from typing import Annotated, TypedDict

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from test_procurement_workflow import BusinessService, buyer, draft


def raw_tools(service):
    tools=[]
    for name in ('supplier_query','part_query','replenishment_suggestions','order_create',
                 'purchase_request_create','procurement_verification'):
        def make_call(n):
            async def call(**arguments):return await service(n,arguments)
            return call
        tools.append(StructuredTool.from_function(name=name,description=name,
            coroutine=make_call(name),args_schema={'type':'object','properties':{},'additionalProperties':True}))
    return tools


class State(TypedDict):
    messages: Annotated[list,add_messages]


async def test_nested_tool_confirmation_is_persisted_and_resumes_once(buyer):
    from agent.tools.procurement import create_procurement_tools
    service=BusinessService();tools=create_procurement_tools(raw_tools(service))
    builder=StateGraph(State);builder.add_node('tools',ToolNode(tools))
    builder.add_edge(START,'tools');builder.add_edge('tools',END)
    graph=builder.compile(checkpointer=InMemorySaver())
    config={'configurable':{'thread_id':'parent-checkpoint'}}
    initial={'messages':[AIMessage(content='',tool_calls=[{'name':'purchase_request_create','args':draft(),'id':'call-purchase'}])]}
    state=await graph.ainvoke(initial,config)
    assert state['__interrupt__'][0].value['workflow_type']=='procurement'
    assert service.saved is None
    assert len(state['messages'])==1
    state=await graph.ainvoke(Command(resume={'decisions':[{'type':'approve'}]}),config)
    value=json.loads(state['messages'][-1].content)
    assert value['ok'] and value['verified']
    assert value['business']['execution']['outcome']=='success'
    assert sum(n=='purchase_request_create' for n,_ in service.calls)==1


async def test_nested_tool_missing_fields_are_not_tool_validation_errors(buyer):
    from agent.tools.procurement import create_procurement_tools
    service=BusinessService();builder=StateGraph(State)
    builder.add_node('tools',ToolNode(create_procurement_tools(raw_tools(service))))
    builder.add_edge(START,'tools');builder.add_edge('tools',END)
    g=builder.compile(checkpointer=InMemorySaver());config={'configurable':{'thread_id':'missing-parent'}}
    state=await g.ainvoke({'messages':[AIMessage(content='',tool_calls=[{'name':'purchase_request_create','args':{'part_id':7},'id':'c'}])]},config)
    assert state['__interrupt__'][0].value['type']=='order_info_request'
    assert not any(n=='purchase_request_create' for n,_ in service.calls)


def test_factory_checks_required_internal_verification_tool():
    from agent.tools.procurement import create_procurement_tools
    with pytest.raises(ValueError,match='procurement_verification'):
        create_procurement_tools(raw_tools(BusinessService())[:-1])
