import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from agent.middlewares.order_preflight import OrderPreflightMiddleware

class Model(BaseChatModel):
    @property
    def _llm_type(self): return "offline-order-validation"
    def bind_tools(self, tools, **kwargs): return self
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        results=[m for m in messages if isinstance(m,ToolMessage)]
        if len(results)>1:
            message=AIMessage(content="done")
        else:
            args={} if not results else {"supplier_id":15,"expected_delivery_date":"2099-01-01","order_detail":[{"partId":1,"quantity":2,"unitPrice":10}]}
            message=AIMessage(content="",tool_calls=[{"name":"order_create","args":args,"id":"create-"+str(len(results))}])
        return ChatResult(generations=[ChatGeneration(message=message)])

@pytest.mark.asyncio
@pytest.mark.parametrize("decision",["approve","reject"])
async def test_missing_fields_then_approval_before_execution(decision):
    writes=[]
    @tool
    def order_create(supplier_id:int,expected_delivery_date:str,order_detail:list[dict]):
        """Synthetic order sink, no database writes."""
        writes.append(supplier_id)
        return "created"
    graph=create_agent(Model(),tools=[order_create],middleware=[OrderPreflightMiddleware(interrupt_on={"order_create":{"allowed_decisions":["approve","reject"]}})],checkpointer=InMemorySaver())
    config={"configurable":{"thread_id":"order-test"}}
    result=await graph.ainvoke({"messages":[HumanMessage(content="create order")]},config)
    pending=result["__interrupt__"][0].value
    assert pending["type"]=="order_info_request"
    assert "supplierId" in pending["missing_fields"]
    assert writes==[]
    result=await graph.ainvoke(Command(resume={"supplement":"供应商15，物料1，数量2，单价10，2099-01-01交货"}),config)
    assert result["__interrupt__"][0].value["action_requests"][0]["name"]=="order_create"
    assert writes==[]
    await graph.ainvoke(Command(resume={"decisions":[{"type":decision}]}),config)
    assert writes==([15] if decision=="approve" else [])
