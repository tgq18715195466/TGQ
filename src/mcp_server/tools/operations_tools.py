"""Agent can propose changes; durable approval is performed in the web workbench."""
import httpx
from mcp_server.tool_errors import business_envelope, http_error_detail, query_failure, business_rejection_code
from typing import Literal
from fastmcp import Context, FastMCP

async def forward(ctx,method,path,payload):
    tool_name = {'receipts/daily':'daily_receipts','suggestions': 'replenishment_suggestions', 'master-changes': 'master_change_request', 'requests': 'purchase_request_create'}[path]
    client=ctx.request_context.lifespan_context['http_client']
    try:
        response=await client.request(method,'/operations/'+path,**({'params':payload} if method=='GET' else {'json':payload}))
        response.raise_for_status()
        return business_envelope(tool_name, {'ok':True,'data':response.json()})
    except httpx.HTTPStatusError as exc:
        detail = http_error_detail(exc.response)
        return business_envelope(tool_name, {'ok':False,'error':{'detail':detail,'status':exc.response.status_code,'code':business_rejection_code(exc.response.status_code,detail),'retryable':exc.response.status_code>=500}})
    except httpx.RequestError:
        return business_envelope(tool_name, {'ok':False,'error':{'code':'ERP_UNAVAILABLE','retryable':True}})
    except Exception as exc:
        return business_envelope(tool_name, query_failure(exc))

def register_operations_tools(mcp:FastMCP):
    @mcp.tool(name='daily_receipts')
    async def daily_receipts(date:str|None=None,ctx:Context=None)->dict:
        """查询指定北京时间日期实际验收入库的物料记录，默认今天。date 格式 YYYY-MM-DD。今日物料/入库汇总用此工具；无记录直接说明无入库，不能用物料总目录、采购订单日期或库存预警代替收货记录。拒收不算入库。"""
        return await forward(ctx,'GET','receipts/daily',{'date':date} if date else {})

    @mcp.tool(name='replenishment_suggestions')
    async def suggestions(part_id:int,demand:int=0,ctx:Context=None)->dict:
        """查询物料补货建议，扣减未到货采购与待审批申请。demand 为用户确认的额外需求量，不能猜测。suggested 是未覆盖需求量，不代表可以申请；先看 purchase_status_name，仅可采购时参考 suggested_request_quantity。"""
        return await forward(ctx,'GET','suggestions',{'part_id':part_id,'demand':demand})

    @mcp.tool(name='master_change_request')
    async def master_change_request(entity:Literal['supplier','part'],values:dict,reason:str,request_id:str,entity_id:int|None=None,ctx:Context=None)->dict:
        """提交供应商/物料新增、编辑或停用操作。entity_id 空表示新增；审批人和管理员编辑现有资料直接生效并保留修改记录。
        supplier values 必须包含 supplier_code/name，status=1合作/2暂停/3终止；可含联系人等。
        part values 必须包含 part_code/name/supplier_id/purchase_price/stock_warning_value，enabled=false停用。
        操作人由登录身份自动记录；reason 必须由用户提供；重试复用 request_id。以返回 status 为准：applied 表示编辑已生效且修改记录已保存；pending 表示新增申请待审批，引导到网页“主数据维护”由其他审批人处理。
        """
        return await forward(ctx,'POST','master-changes',dict(entity=entity,entity_id=entity_id,values=values,reason=reason,request_id=request_id))

    @mcp.tool(name='purchase_request_create')
    async def purchase_request_create(part_id:int,quantity:int,unit_price:float,expected_date:str,reason:str,request_id:str,demand:int=0,manual:bool=True,ctx:Context=None)->dict:
        """提交补货采购申请，不直接下单。先查询 replenishment_suggestions，再让用户确认数量、单价、交货日期和原因，操作人由系统自动记录。重试复用 request_id。
        manual=True 表示用户主动提出并已确认采购数量，优先于系统建议（包括建议为零），不得自动改少数量；自动按缺口补货使用 manual=False。缺少数量、价格、日期或原因时先请求补全，禁止用猜测日期提交。
        成功后引导到网页“采购申请”审批。只有网页审批通过才自动生成订单，不能另外调用 order_create 生成重复订单。
        """
        return await forward(ctx,'POST','requests',dict(part_id=part_id,quantity=quantity,unit_price=unit_price,expected_date=expected_date,reason=reason,request_id=request_id,demand=demand,manual=manual))
