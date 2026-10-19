"""Public event policy. Tool execution and user answers have separate channels."""
import json
import re
from api_view.status_display import business_status_text
from agent.tools.business_answer import AnswerReferenceError

READ_ONLY_TOOLS = frozenset({'supplier_query', 'part_query', 'part_search', 'part_by_supplier', 'inventory_warning', 'order_search_details', 'replenishment_suggestions'})
TOOL_LABELS = {'supplier_query':'查询供应商', 'part_query':'查询物料', 'part_search':'搜索物料', 'part_by_supplier':'查询供应商物料', 'inventory_warning':'查询库存预警', 'order_search_details':'查询采购订单', 'replenishment_suggestions':'计算补货建议', 'order_create':'创建采购订单', 'order_update':'更新采购订单', 'inventory_adjust':'调整库存', 'master_change_request':'提交资料变更申请', 'purchase_request_create':'提交采购申请', 'generate_visualization':'生成图表', 'task':'处理业务任务', 'request_order_info':'整理待补充信息'}
ERRORS = {'AUTH_REQUIRED':'登录已失效，请重新登录。', 'PERMISSION_DENIED':'当前账号没有此操作权限，请联系具备相应权限的人员处理。', 'INVALID_RESPONSE':'业务服务返回的数据格式不正确，请检查服务日志。', 'INVENTORY_NOT_FOUND':'未找到该物料库存记录。', 'IDEMPOTENCY_CONFLICT':'请求编号已用于其他操作，请核查原单据。', 'VALIDATION':'字段格式或数值不符合要求。', 'ORDER_CONFLICT':'订单已发生变化，请刷新并核查。', 'ORDER_NOT_FOUND':'未找到该采购订单。', 'ORDER_CREATE_FAILED':'订单创建失败，请检查订单列表确认执行结果。', 'ORDER_UPDATE_FAILED':'订单更新失败，请检查订单当前状态。', 'INVENTORY_ADJUST_FAILED':'库存调整失败，请核查库存流水。', 'ERP_UNAVAILABLE':'业务服务连接失败或响应超时。', 'ERP_API_ERROR':'业务服务未接受本次请求。', 'SUPPLIER_NOT_ACTIVE':'供应商未处于合作状态，无法执行采购操作。', 'SUPPLIER_NOT_FOUND':'供应商不存在，请核实供应商编号。', 'PART_NOT_FOUND':'物料不存在，请核实物料编号。', 'SUPPLIER_PART_MISMATCH':'物料与供应商归属不一致。', 'PART_DISABLED':'物料已停用。', 'ORDER_VALIDATION_FAILED':'订单字段或业务校验未通过。', 'INSUFFICIENT_STOCK':'可用库存不足。', 'TOOL_ERROR':'工具执行失败，请检查服务状态和输入信息。', 'SERVICE_TIMEOUT':'服务响应超时。', 'MODEL_UNAVAILABLE':'模型服务暂时不可用，请检查服务配置或网络。'}

ERRORS.update({
    'PART_IDENTITY_MISMATCH': '物料名称、编码或内部 ID 不一致，请核实完整物料编码后重新发起采购。',
    'PART_AMBIGUOUS': '匹配到多个同名物料，请提供完整物料编码后重新发起采购。',
    'REQUEST_ACTION_MISMATCH': '该操作与本轮请求不匹配，已阻止执行。请明确需要办理的业务。',
    'PENDING_PURCHASE_REQUEST': '该物料已有待审批申请，请到采购申请页面处理原申请，勿重复提交。',
    'INVENTORY_DATA_INCOMPLETE': '库存或预警值尚未完善，请先维护库存和主数据。',
    'QUANTITY_EXCEEDS_SUGGESTION': '申请数量超过当前补货建议，请核实额外需求；主动采购需明确确认数量。',
    'DELIVERY_DATE_EXPIRED': '预计交货日期已过期，请重新选择有效日期。',
})
READ_ONLY_TOOLS = READ_ONLY_TOOLS | {'daily_receipts'}
TOOL_LABELS['daily_receipts'] = '查询当日入库物料'


def decode_result(content, artifact=None):
    """Prefer original MCP evidence, which survives filesystem text eviction.

    Artifacts are tool-owned metadata, not model arguments or preview text.
    Missing/malformed artifacts retain the existing content decoding behavior;
    an offload notice alone is never proof of business success.
    """
    if isinstance(artifact, dict) and isinstance(artifact.get('structured_content'), dict):
        return artifact['structured_content']
    if isinstance(content, dict): return content
    if isinstance(content, list):
        # MCP text blocks contain an encoded structured result.
        if content and all(isinstance(x, dict) and x.get('type') == 'text' for x in content):
            decoded = [decode_result(x.get('text', '')) for x in content]
            return decoded[0] if len(decoded) == 1 else decoded
        return content
    if isinstance(content, str):
        try: return json.loads(content)
        except (ValueError, TypeError): pass
    return content

def tool_failure(name, content, status=None):
    # Filesystem probes are internal steps, not failed ERP writes.
    if name in {'read_file', 'write_file', 'edit_file', 'ls', 'glob', 'grep', 'compact_conversation'}:
        return None
    value = decode_result(content)
    if isinstance(value, list) and value:
        return next((failure for item in value if (failure := tool_failure(name, item, status))), None)
    failed = status == 'error' or isinstance(value, dict) and (value.get('ok') is False or value.get('isError') is True or bool(value.get('error')))
    if not failed: return None
    detail = value.get('error', {}) if isinstance(value, dict) else {}
    nested = detail.get('detail', {}) if isinstance(detail, dict) else {}
    code = detail.get('code') or (nested.get('code') if isinstance(nested,dict) else None) if isinstance(detail,dict) else None
    if not isinstance(code,str) or code not in ERRORS: code = 'TOOL_ERROR'
    transient = detail['retryable'] is True if isinstance(detail,dict) and 'retryable' in detail else code in {'ERP_UNAVAILABLE','SERVICE_TIMEOUT','TOOL_ERROR'}
    retryable = name in READ_ONLY_TOOLS and transient
    message=ERRORS[code]
    if isinstance(detail,dict) and detail.get('status') in {400,409,422}:
        business_detail=detail.get('detail')
        if isinstance(business_detail,str) and len(business_detail)<1000:message=public_answer(business_detail)
        elif isinstance(business_detail,list):message='提交信息不完整或格式不正确，请补充或核对数量、价格、预计交货日期和申请原因。'
    return {'code': code, 'operation': TOOL_LABELS.get(name, '业务操作'), 'message': message, 'retryable': retryable,
            'action': '可重试此查询。' if retryable else ('请核实输入或基础资料后重新查询。' if name in READ_ONLY_TOOLS else '请先检查关联单据和库存状态，再决定是否重新发起操作。')}

def public_answer(content):
    """Remove transport bookkeeping, preserving order and supplier identifiers."""
    marker = r'(?:幂等(?:键|号)|request[_ ]?id|requestId|idempotency[_ ]?key|checkpoint[_ ]?id|thread[_ ]?id)'
    content = re.sub(r'[（(][^\n（）()]*' + marker + r'[^\n（）()]*[）)]', '', content, flags=re.I)
    content = re.sub(r'(?:`?' + marker + r'`?)[\s：:=]+(?:`[^`\n]+`|"[^"\n]+"|[A-Za-z0-9_.:/-]+)', '', content, flags=re.I)
    return business_status_text(content)


def terminal_answer(state):
    """Only the last main-graph business message may become an answer."""
    for message in reversed((state or {}).get('messages', [])):
        get = message.get if isinstance(message, dict) else lambda key, default=None: getattr(message, key, default)
        kind = get('type') or get('role')
        if kind in {'system','developer'}: continue
        if kind == 'tool' and get('name') == 'publish_business_answer' and get('status', 'success') != 'error':
            return public_answer(get('content', ''))
        if kind not in {'ai','assistant','AIMessageChunk'} or get('tool_calls') or get('tool_call_chunks'): return ''
        content = get('content', '')
        if isinstance(content, str): return public_answer(content)
        return public_answer( ''.join(x.get('text','') for x in content if isinstance(x,dict) and x.get('type')=='text') if isinstance(content,list) else '')
    return ''

LABELS = {'id':'编号','name':'名称','supplierName':'供应商名称','supplierCode':'供应商编号','supplierId':'供应商ID','partName':'物料名称','partCode':'物料编号','partId':'物料ID','stock':'库存','stockQuantity':'库存数量','stockWarningValue':'预警阈值','purchasePrice':'采购价','unitPrice':'单价','quantity':'数量','unit':'单位','model':'型号','status':'状态','creditRating':'信用等级','contactPerson':'联系人','phone':'电话','email':'邮箱','address':'地址','orderNumber':'订单编号','expectedDeliveryDate':'预计交货日期','totalAmount':'总金额','total':'总数','items':'明细','data':'数据','records':'记录','supplier_id':'供应商ID','part_id':'物料ID','suggested_quantity':'建议采购量','on_order':'未到货数量','demand':'需求量','available':'可用量'}
def label(key):
    camel = re.sub(r'_([a-z])', lambda m:m[1].upper(), key)
    return LABELS.get(key, LABELS.get(camel, key))
def cell(value):
    text = json.dumps(value, ensure_ascii=False) if isinstance(value,(dict,list)) else str(value if value is not None else '—')
    return text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('\\','\\\\').replace('|','\\|').replace('\n','<br>')
def retry_answer(name, content):
    value = decode_result(content)
    if isinstance(value,dict) and value.get('ok') is True: value=value.get('data')
    if isinstance(value,dict) and isinstance(value.get('data'), (list,dict)): value=value['data']
    if isinstance(value,dict) and isinstance(value.get('records'),list): value=value['records']
    heading = '### ' + TOOL_LABELS.get(name,'业务查询') + '结果（重试）\n\n'
    if value == []: return heading + '未查询到符合条件的记录。'
    if isinstance(value,list) and value and all(isinstance(row,dict) for row in value):
        keys = list(dict.fromkeys(key for row in value for key in row))
        rows = [[label(key) for key in keys], ['---']*len(keys)]
        rows += [[row.get(key) for key in keys] for row in value]
        return heading + '\n'.join('| ' + ' | '.join(cell(x) for x in row) + ' |' for row in rows)
    if isinstance(value,dict): return heading + '\n'.join('- **'+label(k)+'**：'+cell(v) for k,v in value.items())
    return heading + cell(value)


class DisplayHistoryError(RuntimeError):
    """The display repository did not confirm a durable save."""


def run_failure(exc):
    """Explain common failures without exposing credentials or raw service responses."""
    if isinstance(exc, DisplayHistoryError):
        return '会话记录保存失败，尚未确认结果已保存。请检查数据库并刷新执行状态，避免重复提交业务操作。'
    if isinstance(exc, AnswerReferenceError):
        return '本轮报告引用失效，正式答复尚未发布。请重新提问；如涉及业务提交，请先核对单据状态。'
    name = type(exc).__name__.lower()
    status = getattr(exc, 'status_code', None)
    if 'timeout' in name:
        return '服务响应超时，本次处理未完成。请刷新执行状态并核查关联单据。'
    if status in {401,403} or 'authentication' in name or 'permission' in name:
        return '模型或业务服务鉴权失败，请检查服务密钥和访问权限。'
    if status == 429 or 'ratelimit' in name:
        return '模型服务请求受限，请稍后再试。'
    if 'connection' in name or 'connect' in name:
        return '无法连接模型或业务服务，请检查服务运行状态和网络。'
    return '本次处理未完成，请检查会话及关联单据状态后再继续。'
