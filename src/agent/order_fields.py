"""Order supplement form contract. This module never executes an order tool."""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import re

ALIASES = {
    'supplier_id': 'supplierId', 'order_detail': 'orderDetail', 'part_id': 'partId',
    'unit_price': 'unitPrice', 'expected_delivery_date': 'expectedDeliveryDate',
    'actual_delivery_date': 'actualDeliveryDate', 'order_number': 'orderNumber',
    'total_amount': 'totalAmount', 'order_time': 'orderTime', 'created_by': 'createdBy',
}
# label, input type, required, minimum
ORDER_FIELDS = {
    'supplierId': ('供应商编号（ID）', 'integer', True, 1),
    'expectedDeliveryDate': ('预计交货日期', 'date', True, None),
    'orderNumber': ('订单编号', 'text', False, None),
    'totalAmount': ('总金额', 'decimal', False, 0),
    'remark': ('备注', 'text', False, None),
    'status': ('订单状态（新订单为1）', 'integer', False, 1),
    'createdBy': ('创建人ID', 'integer', False, 1),
    'orderTime': ('下单时间', 'datetime-local', False, None),
    'actualDeliveryDate': ('实际交货日期', 'date', False, None),
}
DETAIL_FIELDS = {
    'partId': ('物料ID', 'integer', True, 1),
    'quantity': ('数量', 'integer', True, 1),
    'unitPrice': ('单价', 'decimal', True, 0),
    'remark': ('明细备注', 'text', False, None),
}


TYPE_LABELS = {
    'integer': '整数', 'decimal': '金额', 'date': '日期',
    'datetime-local': '日期和时间', 'text': '文本',
}


def order_validation_message(error):
    """Translate validation reasons without exposing Pydantic's English prose."""
    labels = {name: spec[0] for name, spec in {**ORDER_FIELDS, **DETAIL_FIELDS}.items()}
    labels['orderDetail'] = '订单明细'
    message = error.get('msg', '').removeprefix('Value error, ')
    message = re.sub(r'\b[A-Za-z_][A-Za-z_0-9]*\b',
                     lambda match: labels.get(ALIASES.get(match[0], match[0]), match[0]), message)
    message = re.sub(r'(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])', '', message)
    if re.search(r'[\u4e00-\u9fff]', message) and not re.search(r'[A-Za-z_]{3,}', message):
        return message
    translations = {
        'missing': '必填，请补充此字段',
        'extra_forbidden': '包含不支持的字段，请核对订单信息',
        'int_type': '请输入整数', 'int_parsing': '请输入整数',
        'int_from_float': '请输入整数，不能包含小数',
        'decimal_type': '请输入有效金额', 'decimal_parsing': '请输入有效金额',
        'finite_number': '请输入有限数值',
        'date_type': '请输入有效日期', 'date_parsing': '请输入有效日期',
        'date_from_datetime_parsing': '请输入有效日期',
        'datetime_type': '请输入有效日期和时间',
        'datetime_parsing': '请输入有效日期和时间',
        'datetime_from_date_parsing': '请输入有效日期和时间',
        'string_type': '请输入文本', 'string_too_short': '文本不能为空',
        'list_type': '订单明细必须为列表', 'too_short': '请至少填写一条订单明细',
        'greater_than': '数值必须大于允许下限',
        'greater_than_equal': '数值不能小于允许下限',
        'less_than_equal': '数值不能大于允许上限',
    }
    return translations.get(error.get('type'), '订单字段或字段之间的关系不符合要求，请核对后重新提交')


def _canonical(data):
    if not isinstance(data, dict):
        return {}
    # Prefer explicit API aliases. The original text remains visible for ambiguity.
    result = {ALIASES.get(k, k): deepcopy(v) for k, v in data.items()}
    result.update({k: deepcopy(v) for k, v in data.items() if k not in ALIASES})
    return result


def build_order_form(collected_data='', *, orders=None, errors=None):
    """Produce stable indexed fields without guessing values from prose."""
    source = orders if orders is not None else collected_data
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except (ValueError, TypeError):
            source = None
    if isinstance(source, dict):
        source = source.get('orders', [source])
    if not isinstance(source, list) or not source:
        source = [{}]
    normalized, fields = [], []
    errors = errors or {}

    def add_fields(data, specs, prefix, order_index, detail_index=None):
        for name, (label, kind, required, minimum) in specs.items():
            if not required and name not in data:
                continue
            path = f'{prefix}.{name}'
            field = dict(path=path, name=name, label=label, type=kind,
                         required=required, value=data.get(name), order_index=order_index,
                         detail_index=detail_index)
            if minimum is not None:
                field['min'] = minimum
            if name == 'status':
                field['max'] = 1
            if name == 'expectedDeliveryDate':
                field['min'] = date.today().isoformat()
            if path in errors:
                field['error'] = errors[path]
            fields.append(field)

    for index, raw in enumerate(source):
        order = _canonical(raw)
        details = order.get('orderDetail')
        if not isinstance(details, list) or not details:
            details = [{}]
        order['orderDetail'] = [_canonical(detail) for detail in details]
        normalized.append(order)
        add_fields(order, ORDER_FIELDS, str(index), index)
        for di, detail in enumerate(order['orderDetail']):
            add_fields(detail, DETAIL_FIELDS, f'{index}.orderDetail.{di}', index, di)
    return {'form_version': 1, 'orders': normalized, 'fields': fields}


def _parse_value(field, value):
    label, kind = field['label'], field['type']
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f'{label}：请输入有效的{TYPE_LABELS.get(kind, "字段值")}')
    text = str(value).strip()
    if kind == 'integer':
        if not re.fullmatch(r'[0-9]+', text):
            raise ValueError(f'{label}：请输入整数')
        number = int(text)
        # JSON/JavaScript must preserve the exact ID/quantity.
        if number < field.get('min', 1) or number > field.get('max', 9007199254740991):
            raise ValueError(f'{label}：数值超出允许范围')
        return number
    if kind == 'decimal':
        if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', text):
            raise ValueError(f'{label}：请输入大于或等于0的有限小数')
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f'{label}：请输入有效金额') from exc
        if not number.is_finite() or number < 0:
            raise ValueError(f'{label}：金额不能为负数或非有限值')
        return text  # Preserve money precision; do not round through binary floats.
    if kind in ('date', 'datetime-local'):
        try:
            if kind == 'date':
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
                    raise ValueError()
                parsed = date.fromisoformat(text)
                if field['name'] == 'expectedDeliveryDate' and parsed < date.today():
                    raise ValueError()
            else:
                if not re.match(r'^\d{4}-\d{2}-\d{2}T', text):
                    raise ValueError()
                datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f'{label}：请输入有效日期，预计交货日期不能早于今天') from exc
        return text
    if not isinstance(value, str):
        raise ValueError(f'{label}：请输入文本')
    return text


def structured_supplement(item, response):
    """Validate checkpoint-bound values and return the legacy replanning text."""
    values = response.get('values')
    note = response.get('supplement', '')
    if not isinstance(values, dict) or not isinstance(note, str):
        raise ValueError('订单表单值必须为对象，补充说明必须为文本')
    # Rebuild the allowlist from server-owned orders, never client field metadata.
    form = build_order_form(orders=item.get('orders'))
    allowed = {field['path']: field for field in form['fields']}
    if not item.get('fields') or any(path not in allowed for path in values):
        raise ValueError('订单字段已失效或包含未知字段，请刷新表单')
    for path, field in allowed.items():
        value = values.get(path, field['value'])
        oi, di, name = field['order_index'], field['detail_index'], field['name']
        target = form['orders'][oi] if di is None else form['orders'][oi]['orderDetail'][di]
        if value is None or (isinstance(value, str) and not value.strip()):
            if field['required']:
                raise ValueError(f'订单{oi + 1} {field["label"]}：必填')
            target.pop(name, None)
            continue
        target[name] = _parse_value(field, value)
    # Apply the same cross-field constraints as preflight (totals, initial status).
    from backend.app import OrderCreate
    from pydantic import ValidationError
    for oi, order in enumerate(form['orders']):
        try:
            OrderCreate.model_validate(order)
        except ValidationError as exc:
            messages = '；'.join(order_validation_message(error) for error in exc.errors(include_input=False))
            raise ValueError(f'订单{oi + 1}：{messages}；不确定的信息可改用自由补充') from exc
    return json.dumps({'orders': form['orders'], 'supplement': note.strip()}, ensure_ascii=False)
