"""Replace model-selected procurement writes with checkpointed business nodes."""
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from agent.procurement_workflow import create_procurement_graph
from agent.tools.auth_bridge import build_tool_registry


class RequestDraft(BaseModel):
    # Optional here so incomplete model drafts reach the information form. The
    # fixed workflow applies the backend's strict schema before confirmation.
    model_config = ConfigDict(extra='forbid')
    part_id: Any = Field(default=None,description='查询结果中的数据库内部 id；不是物料编码，禁止从 FC3001 等编码截取数字；未查询到 id 请留空')
    part_name: str | None = Field(default=None,description='用户指定的准确物料名称，由流程查询真实内部 ID')
    part_code: str | None = Field(default=None,description='完整物料编码，例如 FC3001，必须保留前缀')
    supplier_id: Any = None
    supplier_name: str | None = None
    quantity: Any = None
    unit_price: Any = None
    expected_date: Any = None
    reason: Any = None
    demand: Any = 0
    manual: bool = True


class OrderDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    supplier_id: Any = None
    supplier_name: str | None = None
    part_name: str | None = None
    part_code: str | None = Field(default=None,description='单项采购的完整物料编码，例如 FC3001，必须保留前缀')
    order_detail: list[dict[str,Any]] | None = Field(default=None,description='采购明细；partId 只能使用查询结果的数据库内部 id，禁止从物料编码截取数字；未知时留空')
    expected_delivery_date: Any = None
    remark: str | None = None


def create_procurement_tools(raw_tools):
    """Keep original MCP tools private and expose only the deterministic entry."""
    registry=build_tool_registry(raw_tools)
    required={'supplier_query','part_query','replenishment_suggestions',
              'order_create','purchase_request_create','procurement_verification'}
    missing=required-registry.keys()
    if missing:raise ValueError('Missing required procurement tools: '+', '.join(sorted(missing)))

    async def invoke(name,arguments):
        return await registry[name].ainvoke(arguments)

    # No separate saver: nested graph invocations inherit the Agent's task
    # namespace and PostgreSQL checkpointer, including interrupt/resume IDs.
    graph=create_procurement_graph(invoke)

    def entry(action):
        async def execute(config:RunnableConfig,**draft):
            state=await graph.ainvoke({'action':action,'draft':draft},config)
            return state['result']
        return execute

    # A completed workflow is the terminal result of this delegated purchase.
    # Re-entering the child model here lets it recreate an already saved request
    # with a fresh identity, causing repeated confirmation and duplicate errors.
    return [StructuredTool.from_function(name=name,args_schema=schema,coroutine=entry(name),return_direct=True,
        description=description) for name,schema,description in (
        ('purchase_request_create',RequestDraft,
         '根据用户需求发起采购申请。程序固定查询主数据、校验准入、补全字段、人工确认、提交并读取真实申请核验结果。主动采购数量优先于系统建议，申请提交后仍需其他审批人审批。缺字段可留空，禁止猜测。'),
        ('order_create',OrderDraft,
         '根据用户需求发起采购订单。程序固定查询主数据、校验准入、补全字段、人工确认、提交并读取真实订单核验结果。创建成功的订单为待审核，需其他审批人正式审批。缺字段可留空，禁止猜测。'))]
