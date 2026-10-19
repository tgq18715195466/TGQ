"""Deterministic ERP facts, independent of model prose and caller arguments.

normalize_business_result consumes API evidence. expose_business_result is the
only UI boundary: raw records stay internal; public cells are allowlisted text.
A write acknowledgement is not a verified final outcome. The trusted workflow
must supply verificationOutcome='success', or verified=True and outcome='success',
with its queried actual snapshot. These signals belong at the raw top level.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

VerificationOutcome = Literal['success', 'failure', 'unknown']
Outcome = Literal['success', 'failure', 'unknown', 'cancelled', 'needs_information']
ORDER_STATUS = {1: '待审核', 2: '已审核', 3: '采购中', 4: '已入库', 5: '已取消'}
SUPPLIER_STATUS = {1: '合作中', 2: '暂停合作', 3: '已终止'}
REQUEST_STATUS = {'pending': '待审批', 'approved': '已批准', 'rejected': '已拒绝', 'applied': '已生效'}
STATE_LABEL = {'ordered': '待收货', 'partial': '部分收货', 'complete': '已收货'}


class BusinessColumn(BaseModel):
    """Describe one public cell with a fixed Chinese label."""

    key: str
    label: str
    format: Literal['money', 'number', 'text'] | None = None


class BusinessExecution(BaseModel):
    """Report a known outcome or explicitly retain uncertainty."""

    outcome: Outcome
    message: str


class BusinessPage(BaseModel):
    """Retain only pagination validated and returned by the API."""

    current: int | None = Field(default=None, ge=1)
    size: int | None = Field(default=None, ge=1)
    pages: int | None = Field(default=None, ge=0)


class BusinessResult(BaseModel):
    """Carry all returned evidence; call expose_business_result before display."""

    model_config = ConfigDict(extra='forbid')
    title: str
    kind: str
    source_tool: str
    records: list[dict[str, Any]] = Field(default_factory=list)
    total: int | None = Field(default=None, ge=0)
    returned_count: int = Field(default=0, ge=0)
    complete: bool = False
    columns: list[BusinessColumn] = Field(default_factory=list)
    execution: BusinessExecution
    page: BusinessPage | None = None
    verificationOutcome: VerificationOutcome = 'unknown'
    result_id: str | None = None


# Tool names and identities never become UI text. Arguments may select an entity
# type, but their names, quantities, status and prices never become evidence.
_TOOL_TYPES = {
    'daily_receipts': ('入库物料汇总', 'receipt', False),
    'supplier_query': ('供应商查询', 'supplier', False),
    'part_query': ('物料查询', 'part', False),
    'part_search': ('物料查询', 'part', False),
    'part_by_supplier': ('供应商物料', 'part', False),
    'order_search_details': ('采购订单明细', 'order_detail', False),
    'order_create': ('采购订单', 'order', True),
    'order_update': ('采购订单', 'order', True),
    'inventory_warning': ('库存预警', 'inventory', False),
    'inventory_adjust': ('库存调整', 'inventory_adjustment', True),
    'replenishment_suggestions': ('补货建议', 'replenishment', False),
    'master_change_request': ('主数据维护申请', 'master_change', True),
    'purchase_request_create': ('采购申请', 'purchase_request', True),
    'procurement_verification': ('采购结果核验', 'procurement', False),
}

# key, Chinese label, accepted API aliases, optional display format.
_PART = [
    ('part_code', '物料编号', ('part_code', 'partCode'), None),
    ('name', '物料名称', ('name', 'part_name', 'partName'), None),
    ('model', '型号', ('model',), None),
    ('specification', '规格', ('specification',), None),
    ('category', '分类', ('category',), None),
    ('unit', '单位', ('unit',), None),
    ('purchase_price', '采购单价', ('purchase_price', 'purchasePrice'), 'money'),
    ('suggested_retail_price', '建议零售价', ('suggested_retail_price', 'suggestedRetailPrice'), 'money'),
    ('stock_warning_value', '库存预警值', ('stock_warning_value', 'stockWarningValue'), 'number'),
    ('supplier_name', '供应商', ('supplier_name', 'supplierName'), None),
    ('state_label', '启用状态', (), None),
]
_ORDER = [
    ('order_number', '订单编号', ('order_number', 'orderNumber'), None),
    ('order_time', '下单时间', ('order_time', 'orderTime'), None),
    ('status_name', '订单状态', (), None),
    ('total_amount', '订单总金额', ('total_amount', 'totalAmount'), 'money'),
    ('expected_delivery_date', '预计交货日期', ('expected_delivery_date', 'expectedDeliveryDate'), None),
    ('actual_delivery_date', '实际交货日期', ('actual_delivery_date', 'actualDeliveryDate'), None),
    ('state_label', '收货状态', (), None),
]
_LINE = [
    ('part_code', '物料编号', ('part_code', 'partCode'), None),
    ('part_name', '物料名称', ('part_name', 'partName', 'name'), None),
    ('supplier_name', '供应商', ('supplier_name', 'supplierName'), None),
    ('quantity', '采购数量', ('quantity',), 'number'),
    ('unit_price', '采购单价', ('unit_price', 'unitPrice'), 'money'),
    ('amount', '明细金额', ('subtotal', 'amount'), 'money'),
]
_SCHEMAS = {
    'receipt': [
        ('part_code', '物料编号', ('part_code',), None),
        ('part_name', '物料名称', ('part_name',), None),
        ('model', '型号', ('model',), None),
        ('supplier_name', '供应商', ('supplier_name',), None),
        ('accepted', '实际入库数量', ('accepted',), 'number'),
        ('unit', '单位', ('unit',), None),
        ('received_at', '实际入库时间', ('received_at',), None),
        ('order_number', '采购订单编号', ('order_number',), None),
    ],
    'supplier': [
        ('supplier_code', '供应商编号', ('supplier_code', 'supplierCode'), None),
        ('name', '供应商名称', ('name', 'supplier_name', 'supplierName'), None),
        ('status_name', '合作状态', (), None),
        ('contact_person', '联系人', ('contact_person', 'contactPerson'), None),
        ('phone', '电话', ('phone',), None),
        ('email', '邮箱', ('email',), None),
        ('address', '地址', ('address',), None),
        ('credit_rating', '信用等级', ('credit_rating', 'creditRating'), None),
    ],
    'part': _PART,
    'order': _ORDER + _LINE,
    'order_detail': _ORDER + _LINE,
    'inventory': [
        ('part_code', '物料编号', ('part_code', 'partCode'), None),
        ('part_name', '物料名称', ('part_name', 'partName', 'name'), None),
        ('current_quantity', '当前库存', ('current_quantity', 'currentQuantity'), 'number'),
        ('safety_stock', '安全库存', ('safety_stock', 'safetyStock'), 'number'),
        ('stock_gap', '账面缺口', ('stock_gap',), 'number'),
        ('on_order', '在途采购数量', ('on_order',), 'number'),
        ('pending_quantity', '待审批申请数量', ('pending_quantity',), 'number'),
        ('uncovered_shortage', '未覆盖缺口', ('uncovered_shortage',), 'number'),
        ('suggested_request_quantity', '建议新增申请数量', ('suggested_request_quantity',), 'number'),
        ('supplier_name', '供应商', ('supplier_name',), None),
        ('supplier_status_name', '供应商合作状态', (), None),
        ('purchase_status_name', '采购准入状态', ('purchase_status_name',), None),
        ('warehouse_location', '库位', ('warehouse_location', 'warehouseLocation'), None),
        ('last_inbound_time', '最近入库时间', ('last_inbound_time', 'lastInboundTime'), None),
    ],
    'inventory_adjustment': [
        ('before_quantity', '调整前库存', ('before_quantity', 'beforeQuantity'), 'number'),
        ('delta', '调整数量', ('delta',), 'number'),
        ('after_quantity', '调整后库存', ('after_quantity', 'afterQuantity'), 'number'),
    ],
    'replenishment': _PART + [
        ('supplier_status_name', '供应商状态', (), None),
        ('purchase_status_name', '采购准入状态', ('purchase_status_name',), None),
        ('current_quantity', '当前库存', ('current_quantity', 'currentQuantity'), 'number'),
        ('on_order', '在途采购数量', ('on_order', 'onOrder'), 'number'),
        ('pending_quantity', '待审批数量', ('pending_quantity', 'pendingQuantity'), 'number'),
        ('demand', '已确认额外需求', ('demand',), 'number'),
        ('suggested', '未覆盖需求量', ('suggested',), 'number'),
        ('suggested_request_quantity', '可新增申请数量', ('suggested_request_quantity',), 'number'),
    ],
    'purchase_request': [
        ('request_number', '申请编号', (), None),
        ('status_name', '申请状态', (), None),
        ('part_code', '物料编号', ('part_code', 'partCode'), None),
        ('part_name', '物料名称', ('part_name', 'partName', 'name'), None),
        ('supplier_name', '供应商', ('supplier_name', 'supplierName'), None),
        ('quantity', '申请数量', ('quantity',), 'number'),
        ('unit_price', '采购单价', ('unit_price', 'unitPrice'), 'money'),
        ('amount', '申请金额', ('subtotal', 'amount', 'total_amount', 'totalAmount'), 'money'),
        ('expected_date', '预计交货日期', ('expected_date', 'expectedDate'), None),
    ],
    'master_change': [('status_name', '申请状态', (), None)],
    'procurement': _ORDER + _LINE,
}


# Existing list endpoints do not return a count. At their documented SQL cap,
# the matching total is unknown; retain every returned row without truncation.
_QUERY_CAPS = {'supplier_query': 50, 'part_search': 50, 'order_search_details': 200}


def _integer(value: Any, minimum: int = 0) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= minimum:
        return value
    if isinstance(value, str) and value.isdigit() and len(value) < 20:
        number = int(value)
        return number if number >= minimum else None
    return None


def _first(row: dict, aliases: tuple) -> Any:
    return next((row[key] for key in aliases if key in row and row[key] is not None), None)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value))
        # Keep corrupted or adversarial exponents from consuming unbounded memory.
        return number if number.is_finite() and abs(number.adjusted()) <= 100 else None
    except (InvalidOperation, ValueError):
        return None


def _money(value: Any) -> str | None:
    number = _decimal(value)
    if number is None:
        return None
    with localcontext() as context:
        context.prec = 220
        return format(number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), '.2f')


def _text(value: Any) -> str:
    """Return plain text for Vue interpolation, removing only control characters.

    The public contract is text, not HTML or Markdown. Render cells through
    interpolation/textContent; escaping here would display literal entities and
    backslashes. Never send these cells through v-html or a Markdown renderer.
    """
    return re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', str(value))


def _project(row: dict, kind: str) -> dict:
    evidence = dict(row)
    part = row.get('partDetail', row.get('part_detail', {}))
    if isinstance(part, dict):
        for key, aliases in [('part_name', ('name',)), ('part_code', ('part_code', 'partCode')), ('model', ('model',)), ('category', ('category',))]:
            if key not in evidence:
                evidence[key] = _first(part, aliases)
    supplier = row.get('supplier')
    if isinstance(supplier, dict) and 'supplier_name' not in evidence:
        evidence['supplier_name'] = supplier.get('name')
    status = _first(row, ('status',))
    if status is not None:
        mapping = SUPPLIER_STATUS if kind == 'supplier' else ORDER_STATUS if kind in ('order', 'order_detail', 'procurement') and _integer(status) is not None else REQUEST_STATUS
        evidence['status_name'] = mapping.get(_integer(status) if mapping is not REQUEST_STATUS else status, '未知状态') if isinstance(status, (str, int)) else '未知状态'
    else:
        evidence.pop('status_name', None)
    supplier_status = _first(row, ('supplier_status', 'supplierStatus'))
    if supplier_status is not None:
        evidence['supplier_status_name'] = SUPPLIER_STATUS.get(_integer(supplier_status), '未知状态')
    else:
        evidence.pop('supplier_status_name', None)
    evidence.pop('state_label', None)
    receipt_state = _first(row, ('receipt_state', 'receiptState'))
    if isinstance(receipt_state, str):
        evidence['state_label'] = STATE_LABEL.get(receipt_state, '未知状态')
    elif isinstance(row.get('enabled'), (bool, int)) and row['enabled'] in (True, False, 0, 1):
        evidence['state_label'] = '启用' if row['enabled'] else '停用'
    if kind in ('order', 'order_detail', 'purchase_request', 'procurement'):
        quantity = _decimal(row.get('quantity'))
        price = _decimal(_first(row, ('unit_price', 'unitPrice')))
        if quantity is not None and price is not None:
            with localcontext() as context:
                context.prec = 220
                evidence['amount'] = quantity * price
    projected = {}
    for key, _label, aliases, fmt in _SCHEMAS[kind]:
        value = evidence.get(key) if not aliases else _first(evidence, (key,) + aliases)
        if value is None:
            continue
        if fmt == 'money':
            value = _money(value)
        elif fmt == 'number':
            number = _decimal(value)
            value = int(number) if number is not None and number == number.to_integral_value() else format(number, 'f') if number is not None else None
        elif isinstance(value, (str, int, float, bool, Decimal)):
            value = _text(value)
        else:
            value = None
        if value is not None:
            projected[key] = value
    return projected


def _failure(error: Any) -> BusinessExecution:
    error = error if isinstance(error, dict) else {}
    code = error.get('code')
    status = _integer(error.get('status'))
    uncertain = (error.get('retryable') is True or (status is not None and status >= 500)
                 or code in ('ERP_UNAVAILABLE', 'INVALID_RESPONSE', 'TRANSPORT_ERROR') or not error)
    if code == 'EXECUTION_UNKNOWN':
        return BusinessExecution(outcome='unknown', message='实际执行结果尚未核实，请先查询业务单据，避免重复采购')
    if uncertain:
        return BusinessExecution(outcome='unknown', message='业务结果未知，请先查询核验后再决定后续操作')
    reason = {
        'AUTH_REQUIRED': '请先登录后再操作',
        'PERMISSION_DENIED': '当前账号没有执行此操作的权限',
        'PART_NOT_FOUND': '物料不存在，业务校验未通过',
        'PART_IDENTITY_MISMATCH': '物料名称、编码或内部 ID 不一致，请核实完整物料编码后重新发起采购',
        'PART_AMBIGUOUS': '匹配到多个同名物料，请提供完整物料编码后重新发起采购',
        'INVENTORY_NOT_FOUND': '库存记录不存在，业务校验未通过',
        'IDEMPOTENCY_CONFLICT': '重复请求的业务内容不一致，操作未执行',
        'SUPPLIER_NOT_ACTIVE': '供应商不在合作中，无法提交采购',
        'PART_DISABLED': '物料已停用或准入状态无法核实，无法提交采购',
        'SUPPLIER_PART_MISMATCH': '物料不属于所选供应商，请重新选择',
        'ORDER_VALIDATION_FAILED': '采购字段或字段关系不符合要求，请核对后重新提交',
        'PENDING_PURCHASE_REQUEST': '该物料已有待审批申请，请到采购申请页面处理原申请，勿重复提交',
        'INVENTORY_DATA_INCOMPLETE': '库存或预警值尚未完善，请先维护库存和主数据',
        'QUANTITY_EXCEEDS_SUGGESTION': '申请数量超过当前补货建议，请核实额外需求；主动采购需明确确认数量',
        'DELIVERY_DATE_EXPIRED': '预计交货日期已过期，请重新选择有效日期',
    }.get(code, '业务校验未通过，操作未执行')
    return BusinessExecution(outcome='failure', message=reason)


def normalize_business_result(tool_name: str, raw_result: Any, arguments: dict | None = None) -> BusinessResult | None:
    """Normalize explicit API evidence, or legacy lists from known query tools.

    Caller arguments cannot supply records, totals, pagination, status or money.
    verificationOutcome is trusted workflow metadata, never read from arguments.
    Alternatively, verified is True AND outcome == 'success' confirms the
    parent's fixed workflow verification. cancelled and needs_information are
    neutral pauses and carry no unvalidated input as records.
    Missing ok is uncertain, except a legacy list from a known query tool.
    """
    spec = _TOOL_TYPES.get(tool_name)
    if spec is None:
        return None
    title, kind, write = spec
    raw = raw_result if isinstance(raw_result, dict) else {}
    legacy = isinstance(raw_result, list) and not write
    pause = raw.get('outcome') if raw.get('outcome') in ('cancelled', 'needs_information') else None
    acknowledged = (raw.get('ok') is True or legacy) and pause is None
    payload = raw_result if legacy else raw.get('data') if acknowledged else None
    records: list[dict] = []
    page = None
    total = None
    valid = False
    if isinstance(payload, list):
        valid = all(isinstance(record, dict) for record in payload)
        records = [deepcopy(record) for record in payload if isinstance(record, dict)]
        if tool_name != 'part_query' and len(records) != _QUERY_CAPS.get(tool_name):
            total = len(records)
    elif isinstance(payload, dict):
        if 'records' in payload:
            rows = payload['records']
            valid = isinstance(rows, list) and all(isinstance(record, dict) for record in rows)
            records = [deepcopy(record) for record in rows if isinstance(record, dict)] if isinstance(rows, list) else []
            total = _integer(payload.get('total'))
            paging = {key: _integer(payload.get(key), 1 if key != 'pages' else 0) for key in ('current', 'size', 'pages')}
            if any(value is not None for value in paging.values()):
                page = BusinessPage(**paging)
        elif write or tool_name == 'procurement_verification':
            valid = bool(payload)
            records = [deepcopy(payload)] if payload else []
            if kind == 'order' and payload:
                lines = payload.get('lines', payload.get('order_detail'))
                if isinstance(lines, list) and lines:
                    valid = all(isinstance(line, dict) for line in lines)
                    header = {key: value for key, value in payload.items() if key not in ('lines', 'order_detail')}
                    # Preserve actual line evidence; header state and order
                    # identity cannot be overridden by a line's own fields.
                    records = [{**deepcopy(line), **deepcopy(header)} for line in lines if isinstance(line, dict)]
            total = len(records)
    complete = valid and total is not None and total == len(records) and (page is None or page.current in (None, 1))
    verification: VerificationOutcome = 'unknown'
    if pause is not None:
        execution = BusinessExecution(outcome=pause, message={'cancelled': '操作已取消', 'needs_information': '请补充必要的业务信息后继续'}[pause])
    elif raw.get('ok') is False or ('error' in raw and not acknowledged):
        execution = _failure(raw.get('error'))
        verification = execution.outcome if execution.outcome == 'failure' else 'unknown'
    elif acknowledged and valid:
        if write:
            requested_verification = raw.get('verificationOutcome', raw.get('verification_outcome'))
            if requested_verification in ('success', 'failure'):
                verification = requested_verification
            elif raw.get('verified') is True and raw.get('outcome') == 'success':
                verification = 'success'
            execution = BusinessExecution(
                outcome=verification,
                message={'success': '业务结果已查询核验', 'failure': '查询核验未通过，请检查实际业务状态', 'unknown': '业务服务已确认接收，最终结果尚未查询核验'}[verification],
            )
        else:
            verification = 'success'
            execution = BusinessExecution(outcome='success', message='查询成功' if records else '查询成功，未找到符合条件的记录')
    else:
        execution = BusinessExecution(outcome='unknown', message='业务结果未知，请先查询核验后再决定后续操作')
    projected = [_project(record, kind) for record in records]
    keys = {key for record in projected for key in record}
    columns = [BusinessColumn(key=key, label=label, format=fmt) for key, label, _aliases, fmt in _SCHEMAS[kind] if key in keys]
    return BusinessResult(title=title, kind=kind, source_tool=tool_name, records=records, total=total,
                          returned_count=len(records), complete=complete, columns=columns,
                          execution=execution, page=page, verificationOutcome=verification)


def expose_business_result(result: BusinessResult | dict) -> dict:
    """Return the allowlisted public contract, including every returned record.

    Accept a BusinessResult or its model_dump(mode='json') representation. Titles,
    columns and status labels are program owned; no tool names or internal IDs
    reach the public boundary. Render cell values as text, never raw HTML.
    """
    result = result if isinstance(result, BusinessResult) else BusinessResult.model_validate(result)
    spec = _TOOL_TYPES.get(result.source_tool)
    if spec is None:
        raise ValueError('Unsupported business source')
    title, kind, _write = spec
    rows = []
    for record in result.records:
        evidence = record
        if kind == 'purchase_request' and result.verificationOutcome == 'success' and result.execution.outcome == 'success':
            # The ERP row ID is the public application number shown in the
            # request list. Never expose the separate idempotency request_id.
            number = _integer(record.get('id'), 1)
            if number is not None:
                evidence = {**record, 'request_number': str(number)}
        rows.append(_project(evidence, kind))
    keys = {key for row in rows for key in row}
    public = {
        'title': title, 'kind': kind, 'records': rows, 'total': result.total,
        'returned_count': len(rows), 'complete': result.complete,
        'columns': [BusinessColumn(key=key, label=label, format=fmt).model_dump(exclude_none=True) for key, label, _aliases, fmt in _SCHEMAS[kind] if key in keys],
        'execution': {'outcome': result.execution.outcome, 'message': _text(result.execution.message)},
    }
    if result.page is not None:
        public['page'] = result.page.model_dump(exclude_none=True)
    if result.result_id is not None:
        public['result_id'] = _text(result.result_id)
    return public
