import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage
from agent.tools import hitl_tools
from agent.middlewares import order_preflight
from api_view.interrupts import format_interrupts, validate_resume
from backend.auth import current_identity


@pytest.fixture(autouse=True)
def purchase_identity():
    token = current_identity.set({'role': 'purchaser'})
    yield
    current_identity.reset(token)


def pending(monkeypatch, orders):
    captured = {}
    monkeypatch.setattr(hitl_tools, 'interrupt', lambda value: captured.update(value) or {'supplement': '继续'})
    hitl_tools.request_order_info.invoke({'missing_fields': '数量、单价、预计交货日期', 'collected_data': json.dumps(orders)})
    return format_interrupts([SimpleNamespace(id='form-1', value=captured)], 'thread')[0]


def values(item):
    return {f['path']: f['value'] for f in item['fields'] if f.get('value') is not None}


def test_multi_order_prefill_and_paths(monkeypatch):
    item = pending(monkeypatch, [
        {'supplier_id': 15, 'order_detail': [{'part_id': 1}, {'partId': 2, 'quantity': 3, 'unitPrice': 0}]},
        {'supplierId': 16, 'orderDetail': [{'partId': 3}]},
    ])
    fields = {f['path']: f for f in item['fields']}
    assert fields['0.supplierId']['value'] == 15
    assert fields['0.orderDetail.1.unitPrice']['value'] == 0
    assert fields['1.orderDetail.0.quantity']['required'] is True
    assert fields['0.expectedDeliveryDate']['type'] == 'date'
    assert fields['0.orderDetail.0.quantity']['label'] == '数量'
    assert len(item['orders']) == 2


def test_structured_resume_normalizes_and_keeps_notes(monkeypatch):
    item = pending(monkeypatch, [{'supplierId': 15, 'expectedDeliveryDate': '2099-01-01', 'orderDetail': [{'partId': 1}]}])
    data = values(item) | {'0.orderDetail.0.quantity': '2', '0.orderDetail.0.unitPrice': '12.30'}
    payload = {'interrupt_id': 'form-1', 'values': data, 'supplement': '包装需确认'}
    result = validate_resume([item], payload)['form-1']
    assert set(result) == {'supplement'}
    decoded = json.loads(result['supplement'])
    detail = decoded['orders'][0]['orderDetail'][0]
    assert detail == {'partId': 1, 'quantity': 2, 'unitPrice': '12.30'}
    assert decoded['supplement'] == '包装需确认'
    assert payload['values'] == data


@pytest.mark.parametrize('path,bad', [
    ('0.supplierId', True), ('0.supplierId', '1.5'),
    ('0.orderDetail.0.quantity', '0'), ('0.orderDetail.0.quantity', '1.2'),
    ('0.orderDetail.0.unitPrice', '-1'), ('0.orderDetail.0.unitPrice', 'NaN'),
    ('0.orderDetail.0.unitPrice', 'Infinity'), ('0.orderDetail.0.unitPrice', {}),
    ('0.expectedDeliveryDate', '2099-02-30'), ('0.expectedDeliveryDate', '2020-01-01'),
    ('0.expectedDeliveryDate', '20990101'),
])
def test_rejects_bad_structured_values(monkeypatch, path, bad):
    item = pending(monkeypatch, [{'supplierId': 15, 'expectedDeliveryDate': '2099-01-01', 'orderDetail': [{'partId': 1, 'quantity': 2, 'unitPrice': 3}]}])
    data = values(item) | {path: bad}
    with pytest.raises(HTTPException) as exc:
        validate_resume([item], {'interrupt_id': 'form-1', 'values': data})
    assert exc.value.status_code == 422


def test_required_unknown_paths_and_legacy(monkeypatch):
    item = pending(monkeypatch, [{}])
    for data in ({}, {'0.__proto__.x': '1'}, {'9.supplierId': 1}):
        with pytest.raises(HTTPException) as exc:
            validate_resume([item], {'interrupt_id': 'form-1', 'values': data})
        assert exc.value.status_code == 422
    assert validate_resume([item], {'interrupt_id': 'form-1', 'supplement': '物料待确认'}) == {'form-1': {'supplement': '物料待确认'}}


def test_unstructured_collected_data_is_preserved(monkeypatch):
    captured = {}
    monkeypatch.setattr(hitl_tools, 'interrupt', lambda value: captured.update(value) or {})
    hitl_tools.request_order_info.invoke({'missing_fields': '交货方式待确认', 'collected_data': '供应商可能是甲或乙'})
    item = format_interrupts([SimpleNamespace(id='x', value=captured)], 't')[0]
    assert item['collected_data'] == '供应商可能是甲或乙'
    assert item['fields']
    assert all(f['value'] is None for f in item['fields'])


def test_preflight_groups_errors_and_skips_entire_batch(monkeypatch):
    captured = {}
    monkeypatch.setattr(order_preflight, 'interrupt', lambda value: captured.update(value) or {'supplement': '稍后确认'})
    middleware = order_preflight.OrderPreflightMiddleware(interrupt_on={'order_create': True})
    message = AIMessage(content='', tool_calls=[
        {'id': 'a', 'name': 'order_create', 'args': {'orderDetail': [{'partId': 1}, {'partId': 2}]}},
        {'id': 'b', 'name': 'order_create', 'args': {}},
        {'id': 'c', 'name': 'other_tool', 'args': {}},
    ])
    result = middleware.after_model({'messages': [message]}, None)
    assert result['jump_to'] == 'model'
    assert [m.tool_call_id for m in result['messages']] == ['a', 'b', 'c']
    fields = {f['path']: f for f in captured['fields']}
    assert fields['0.orderDetail.1.quantity']['error']
    assert fields['1.supplierId']['error']
    assert captured['order_call_ids'] == ['a', 'b']

def test_explicit_tool_orders_and_old_checkpoint_upgrade(monkeypatch):
    captured = {}
    monkeypatch.setattr(hitl_tools, 'interrupt', lambda value: captured.update(value) or {})
    hitl_tools.request_order_info.invoke({'missing_fields': 'quantity', 'collected_data': '物料待确认', 'orders': [{'supplierId': 22}]})
    assert captured['fields'][0]['value'] == 22
    old = format_interrupts([SimpleNamespace(id='old', value={'type': 'order_info_request', 'collected_data': '{"supplier_id": 23}', 'missing_fields': 'quantity'})], 't')[0]
    assert old['fields'][0]['value'] == 23
    assert validate_resume([old], {'responses': {'old': {'supplement': '稍后确认'}}})['old']['supplement'] == '稍后确认'


def test_multiple_order_resume_and_atomic_validation(monkeypatch):
    orders = [
        {'supplierId': 15, 'expectedDeliveryDate': '2099-01-01', 'orderDetail': [{'partId': 1, 'quantity': 2, 'unitPrice': 0}, {'partId': 2, 'quantity': 3, 'unitPrice': '4.50'}]},
        {'supplierId': 16, 'expectedDeliveryDate': '2099-02-01', 'orderDetail': [{'partId': 3, 'quantity': 4, 'unitPrice': '5.25'}]},
    ]
    item = pending(monkeypatch, orders)
    data = values(item) | {'1.orderDetail.0.quantity': '7'}
    decoded = json.loads(validate_resume([item], {'interrupt_id': 'form-1', 'values': data})['form-1']['supplement'])
    assert decoded['orders'][0]['orderDetail'][1]['quantity'] == 3
    assert decoded['orders'][1]['orderDetail'][0]['quantity'] == 7
    payload = {'responses': {'form-1': {'values': data}, 'stale': {'supplement': 'x'}}}
    original = json.dumps(payload)
    with pytest.raises(HTTPException) as exc:
        validate_resume([item], payload)
    assert exc.value.status_code == 409
    assert json.dumps(payload) == original


@pytest.mark.parametrize('extra', [{'totalAmount': 99}, {'status': 3}, {'actualDeliveryDate': '2099-01-01'}])
def test_structured_values_must_obey_create_constraints(monkeypatch, extra):
    item = pending(monkeypatch, [{'supplierId': 15, 'expectedDeliveryDate': '2099-01-01', 'orderDetail': [{'partId': 1, 'quantity': 2, 'unitPrice': 3}], **extra}])
    with pytest.raises(HTTPException) as exc:
        validate_resume([item], {'interrupt_id': 'form-1', 'values': values(item)})
    assert exc.value.status_code == 422

@pytest.mark.parametrize('kind,label', [('integer', '整数'), ('decimal', '金额'), ('date', '日期'), ('datetime-local', '日期和时间'), ('text', '文本')])
def test_invalid_value_type_uses_chinese(kind, label):
    from agent.order_fields import _parse_value
    with pytest.raises(ValueError) as exc:
        _parse_value({'label': '测试字段', 'type': kind}, {})
    assert str(exc.value) == f'测试字段：请输入有效的{label}'


def test_cross_field_error_is_chinese_on_resume(monkeypatch):
    item = pending(monkeypatch, [{'supplierId': 15, 'expectedDeliveryDate': '2099-01-01', 'orderDetail': [{'partId': 1, 'quantity': 2, 'unitPrice': 3}], 'totalAmount': 99}])
    with pytest.raises(HTTPException) as exc:
        validate_resume([item], {'interrupt_id': 'form-1', 'values': values(item)})
    assert '总金额必须等于明细数量乘单价之和' in exc.value.detail
    assert 'Value error' not in exc.value.detail
    assert 'totalAmount' not in exc.value.detail


def test_cross_field_error_is_chinese_in_preflight(monkeypatch):
    captured = {}
    monkeypatch.setattr(order_preflight, 'interrupt', lambda value: captured.update(value) or {'supplement': '确认金额'})
    middleware = order_preflight.OrderPreflightMiddleware(interrupt_on={'order_create': True})
    message = AIMessage(content='', tool_calls=[{'id': 'a', 'name': 'order_create', 'args': {'supplierId': 15, 'expectedDeliveryDate': '2099-01-01', 'orderDetail': [{'partId': 1, 'quantity': 2, 'unitPrice': 3}], 'totalAmount': 99}}])
    middleware.after_model({'messages': [message]}, None)
    assert '总金额必须等于明细数量乘单价之和' in captured['missing_fields']
    assert 'Value error' not in captured['missing_fields']


def test_unknown_pydantic_error_has_chinese_fallback(monkeypatch):
    item = pending(monkeypatch, [{'supplierId': 15, 'expectedDeliveryDate': '2099-01-01', 'orderDetail': [{'partId': 1, 'quantity': 2, 'unitPrice': 3}], 'unexpected': 'x'}])
    with pytest.raises(HTTPException) as exc:
        validate_resume([item], {'interrupt_id': 'form-1', 'values': values(item)})
    assert '不支持的字段' in exc.value.detail
    assert 'Extra inputs' not in exc.value.detail


def test_purchase_request_missing_date_pauses_before_execution(monkeypatch):
    from agent.middlewares import order_preflight
    from langchain_core.messages import AIMessage
    from types import SimpleNamespace
    captured={}
    monkeypatch.setattr(order_preflight,'interrupt',lambda value:captured.update(value) or {'supplement':'交货日期2099-01-01'})
    middleware=order_preflight.OrderPreflightMiddleware(interrupt_on={'purchase_request_create':True})
    result=middleware.after_model({'messages':[AIMessage(content='',tool_calls=[{'id':'p','name':'purchase_request_create','args':{'part_id':7,'quantity':20,'unit_price':2,'reason':'主动备货','request_id':'test'}}])]},None)
    assert result['jump_to']=='model'
    assert captured['type']=='purchase_info_request'
    assert '预计交货日期' in captured['missing_fields']
    item=format_interrupts([SimpleNamespace(id='p',value=captured)],'t')[0]
    assert item['interrupt_type']=='order_info_supplement' and item['fields']==[]
    assert validate_resume([item],{'interrupt_id':'p','supplement':'交货日期2099-01-01'})['p']['supplement']

def test_web_service_exposes_only_narrow_start_purchase_route():
    from api_view.web_main import app
    assert 'post' in app.openapi()['paths']['/api/operations/receiving/{order_id}/start']
