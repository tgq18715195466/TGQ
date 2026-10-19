from copy import deepcopy
from decimal import Decimal
import importlib
import json

import pytest


def api():
    try:
        return importlib.import_module('agent.business_results')
    except ModuleNotFoundError:
        pytest.fail('agent.business_results sidecar is not implemented')


def public(tool, raw, args=None):
    module = api()
    result = module.normalize_business_result(tool, raw, args)
    assert isinstance(result, module.BusinessResult)
    return module.expose_business_result(result)


def test_supplier_all_rows_and_internal_records_preserved():
    rows = [{'id': i, 'supplier_code': f'SUP{i}', 'name': f'供应商{i}', 'status': i % 3 + 1, 'request_id': 'secret'} for i in range(75)]
    raw = {'ok': True, 'data': rows}
    before = deepcopy(raw)
    result = api().normalize_business_result('supplier_query', raw)
    assert result.records == rows
    ui = api().expose_business_result(result)
    assert len(ui['records']) == ui['total'] == ui['returned_count'] == 75
    assert ui['complete'] is True
    assert raw == before
    assert 'source_tool' not in ui and 'supplier_query' not in json.dumps(ui, ensure_ascii=False)
    assert all('id' not in row and 'request_id' not in row and 'status' not in row for row in ui['records'])
    assert [row['status_name'] for row in ui['records'][:3]] == ['合作中', '暂停合作', '已终止']


@pytest.mark.parametrize('status,label', [(1,'待审核'),(2,'已审核'),(3,'采购中'),(4,'已入库'),(5,'已取消'),(99,'未知状态')])
def test_exact_order_states(status, label):
    ui = public('order_search_details', [{'order_number': 'PO1', 'status': status}])
    assert ui['records'][0]['status_name'] == label


def test_decimal_amount_and_nested_api_records():
    rows = [{'id': 1, 'quantity': 3, 'unitPrice': 0.1, 'partDetail': {'id': 9, 'name': '螺丝', 'partCode': 'P1'}, 'supplier': {'id': 8, 'name': '供方'}, 'status': 5}]
    before = deepcopy(rows)
    ui = public('order_search_details', {'ok': True, 'data': rows})
    row = ui['records'][0]
    assert row['amount'] == '0.30' and row['unit_price'] == '0.10'
    assert row['part_name'] == '螺丝' and row['supplier_name'] == '供方'
    assert rows == before
    assert all(column['label'] for column in ui['columns'])
    assert next(c for c in ui['columns'] if c['key'] == 'amount')['format'] == 'money'


def test_page_metadata_from_api_only():
    data = {'records': [{'id': 1, 'name': '物料', 'purchase_price': Decimal('2.675')}], 'total': 21, 'current': 2, 'size': 10}
    ui = public('part_query', {'ok': True, 'data': data}, {'current': 99, 'size': 1000, 'name': '伪造'})
    assert ui['total'] == 21 and ui['returned_count'] == 1 and ui['complete'] is False
    assert ui['page'] == {'current': 2, 'size': 10}
    assert ui['records'][0]['purchase_price'] == '2.68'
    assert '伪造' not in str(ui)


def test_empty_query_is_success_and_legacy_page_is_not_complete():
    ui = public('part_query', {'ok': True, 'data': {'records': [], 'total': 0, 'current': 1, 'size': 10}})
    assert ui['execution']['outcome'] == 'success' and ui['complete'] and ui['total'] == 0
    legacy = public('part_query', [])
    assert legacy['execution']['outcome'] == 'success'
    assert legacy['total'] is None and legacy['complete'] is False


@pytest.mark.parametrize('raw', [{}, {'data': {'status': 4}}, {'code': 200, 'data': {'status': 4}}, {'ok': 'true', 'data': {'status': 4}}, None, 'created', []])
def test_write_without_explicit_ack_is_unknown(raw):
    ui = public('order_create', raw, {'order_number': 'FAKE', 'status': 4, 'total_amount': 777})
    assert ui['execution']['outcome'] == 'unknown'
    assert ui['records'] == []


def test_write_ack_and_verified_actual_snapshot():
    module = api()
    raw = {'ok': True, 'data': {'id': 8, 'order_number': 'PO8', 'status': 1}}
    ack = module.normalize_business_result('order_create', raw, {'status': 4})
    assert ack.verificationOutcome == 'unknown'
    assert module.expose_business_result(ack)['execution']['outcome'] == 'unknown'
    verified = module.normalize_business_result('order_create', {**raw, 'verificationOutcome': 'success'})
    assert verified.verificationOutcome == 'success'
    ui = module.expose_business_result(verified)
    assert ui['execution']['outcome'] == 'success'
    assert ui['records'][0]['status_name'] == '待审核'


@pytest.mark.parametrize('error', [{'code': 'ERP_UNAVAILABLE', 'retryable': True}, {'code': 'ERP_API_ERROR', 'status': 503, 'retryable': True}, {'code': 'INVALID_RESPONSE', 'retryable': False}])
def test_transport_and_invalid_response_unknown(error):
    ui = public('purchase_request_create', {'ok': False, 'error': error})
    assert ui['execution']['outcome'] == 'unknown'


@pytest.mark.parametrize('error', [{'code': 'PART_NOT_FOUND', 'status': 422}, {'code': 'ORDER_VALIDATION_FAILED'}, {'code': 'PERMISSION_DENIED', 'status': 403}, {'code': 'ERP_API_ERROR', 'status': 409}])
def test_known_failure_sanitized(error):
    error = {**error, 'message': '<img onerror=alert(1)> token=secret C:/private [link](javascript:alert(1))', 'detail': {'input': 'secret'}}
    ui = public('order_create', {'ok': False, 'error': error})
    assert ui['execution']['outcome'] == 'failure'
    assert any('\u4e00' <= char <= '\u9fff' for char in ui['execution']['message'])
    assert 'secret' not in str(ui) and 'javascript' not in str(ui)


def test_ui_allowlist_and_markup_remain_plain_text_for_vue_interpolation():
    ui = public('supplier_query', {'ok': True, 'data': [{'id': 3, 'name': '<script>alert(1)</script> [点](javascript:bad) **粗体** | 行\n新行', 'arbitrary': 'private', 'status_name': '伪造成功', 'status': 2}]})
    row = ui['records'][0]
    assert row['status_name'] == '暂停合作'
    assert row['name'] == '<script>alert(1)</script> [点](javascript:bad) **粗体** | 行 新行'
    assert 'arbitrary' not in row and 'id' not in row
    assert set(row) <= {c['key'] for c in ui['columns']}


def test_unknown_tool_does_not_make_business_facts():
    assert api().normalize_business_result('web_search', {'ok': True, 'data': [{'name': 'x'}]}) is None


def test_public_contract_survives_json_roundtrip():
    result = api().normalize_business_result('replenishment_suggestions', {'ok': True, 'data': {'records': [{'id': 1, 'name': '油', 'supplier_status': 2, 'suggested': 4, 'current_quantity': 1, 'purchase_price': '1.20'}], 'total': 1}})
    ui = api().expose_business_result(result.model_dump(mode='json'))
    assert {'title', 'kind', 'records', 'total', 'returned_count', 'complete', 'columns', 'execution'} <= ui.keys()
    assert ui['records'][0]['supplier_status_name'] == '暂停合作'
    assert ui['records'][0]['suggested'] == 4


def test_master_change_entity_arguments_are_only_context():
    ui = public('master_change_request', {'ok': True, 'data': {'id': 1, 'status': 'pending'}}, {'entity': 'supplier', 'values': {'name': '未核验', 'status': 1}})
    assert ui['records'] == [{'status_name': '待审批'}]
    assert '未核验' not in str(ui)


@pytest.mark.parametrize('tool,limit', [('supplier_query', 50), ('part_search', 50), ('order_search_details', 200)])
def test_known_api_limit_cannot_claim_actual_total_or_completeness(tool, limit):
    rows = [{'id': i, 'name': f'记录{i}'} for i in range(limit)]
    ui = public(tool, {'ok': True, 'data': rows})
    assert ui['returned_count'] == len(ui['records']) == limit
    assert ui['total'] is None and ui['complete'] is False


def test_returned_total_takes_precedence_over_query_limit():
    rows = [{'id': i, 'name': f'供方{i}'} for i in range(50)]
    ui = public('supplier_query', {'ok': True, 'data': {'records': rows, 'total': 50}})
    assert ui['total'] == 50 and ui['complete'] is True


def test_model_cannot_override_verification_or_supply_evidence():
    ui = public('purchase_request_create', {'ok': True, 'data': {'id': 3, 'status': 'pending'}}, {'verificationOutcome': 'success', 'quantity': 500, 'unit_price': 100, 'status': 'approved'})
    assert ui['execution']['outcome'] == 'unknown'
    assert ui['records'] == [{'status_name': '待审批'}]


def test_verified_purchase_request_displays_its_real_application_number():
    result = api().normalize_business_result('purchase_request_create', {
        'ok': True, 'verified': True, 'outcome': 'success',
        'data': {'id': 3, 'part_code': 'FC3006', 'quantity': 24,
                 'status': 'pending', 'request_id': 'private-idempotency-key'},
    })
    ui = api().expose_business_result(result)
    assert ui['records'][0]['request_number'] == '3'
    assert ui['columns'][0]['label'] == '申请编号'
    assert 'private-idempotency-key' not in str(ui)


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '1e10000', True, {'amount': 9}])
def test_invalid_money_never_becomes_displayed_fact(value):
    ui = public('part_search', {'ok': True, 'data': [{'name': '物料', 'purchase_price': value}]})
    assert 'purchase_price' not in ui['records'][0]


def test_forged_ui_metadata_is_ignored_at_public_projection():
    module = api()
    result = module.normalize_business_result('supplier_query', {'ok': True, 'data': [{'id': 4, 'name': '供方', 'status': 3}]})
    serialized = result.model_dump(mode='json')
    serialized['title'] = 'supplier_query secret'
    serialized['kind'] = 'internal'
    serialized['columns'] = [{'key': 'id', 'label': '<script>internal</script>'}]
    ui = module.expose_business_result(serialized)
    assert ui['title'] == '供应商查询' and ui['kind'] == 'supplier'
    assert all(column['key'] != 'id' for column in ui['columns'])


def test_internal_verification_source_is_not_exposed_as_ui_tool_name():
    ui = public('procurement_verification', {'ok': True, 'data': {'order_number': 'PO1', 'status': 5}})
    assert 'procurement_verification' not in json.dumps(ui, ensure_ascii=False)
    assert ui['records'][0]['status_name'] == '已取消'


@pytest.mark.parametrize('tool,data,label', [('order_create', {'id': 1, 'status': 1}, '待审核'), ('purchase_request_create', {'id': 2, 'status': 'pending'}, '待审批')])
def test_parent_verified_signal_and_actual_pending_state(tool, data, label):
    result = api().normalize_business_result(tool, {'ok': True, 'data': data, 'verified': True, 'outcome': 'success'})
    assert result.verificationOutcome == 'success'
    ui = api().expose_business_result(result)
    assert ui['execution']['outcome'] == 'success'
    assert ui['records'][0]['status_name'] == label
    assert '已采购' not in str(ui)


@pytest.mark.parametrize('verified,outcome', [(1, 'success'), ('true', 'success'), (True, 'unknown'), (False, 'success')])
def test_parent_verification_requires_exact_true_and_success(verified, outcome):
    ui = public('order_create', {'ok': True, 'data': {'id': 1, 'status': 1}, 'verified': verified, 'outcome': outcome})
    assert ui['execution']['outcome'] == 'unknown'


@pytest.mark.parametrize('outcome', ['cancelled', 'needs_information'])
def test_normal_workflow_pauses_are_neutral_and_not_failures(outcome):
    result = api().normalize_business_result('order_create', {'ok': False, 'outcome': outcome, 'data': {'quantity': 777, 'status': 4}}, {'status': 4})
    ui = api().expose_business_result(result)
    assert ui['execution']['outcome'] == outcome
    assert ui['title'] == '采购订单'
    assert ui['records'] == [] and result.verificationOutcome == 'unknown'
    assert '失败' not in ui['execution']['message'] and '错误' not in ui['execution']['message']


def test_public_cells_do_not_double_escape_vue_interpolated_text():
    ui = public('supplier_query', {'ok': True, 'data': [{'name': 'A&B <供方> [名称] \u0000\u0085', 'supplier_code': 'SUP_1'}]})
    assert ui['records'][0]['name'] == 'A&B <供方> [名称]   '
    assert ui['records'][0]['supplier_code'] == 'SUP_1'
    ui = public('order_search_details', {'ok': True, 'data': [{'expected_delivery_date': '2026-10-09'}]})
    assert ui['records'][0]['expected_delivery_date'] == '2026-10-09'


@pytest.mark.parametrize('code,message,outcome', [
    ('SUPPLIER_NOT_ACTIVE', '供应商不在合作中，无法提交采购', 'failure'),
    ('PART_DISABLED', '物料已停用或准入状态无法核实，无法提交采购', 'failure'),
    ('SUPPLIER_PART_MISMATCH', '物料不属于所选供应商，请重新选择', 'failure'),
    ('ORDER_VALIDATION_FAILED', '采购字段或字段关系不符合要求，请核对后重新提交', 'failure'),
    ('EXECUTION_UNKNOWN', '实际执行结果尚未核实，请先查询业务单据，避免重复采购', 'unknown'),
])
def test_known_business_reasons_are_specific_fixed_chinese(code, message, outcome):
    ui = public('order_create', {'ok': False, 'error': {'code': code, 'detail': '<script>secret request_id private</script>', 'retryable': False}})
    assert ui['execution'] == {'outcome': outcome, 'message': message}
    assert 'secret' not in str(ui) and 'private' not in str(ui)


def test_verified_order_renders_every_actual_line_with_header_and_decimal_amounts():
    record={'order_number':'PO-REAL','status':1,'total_amount':'0.70',
        'expected_delivery_date':'2099-01-01','lines':[
            {'part_code':'P1','name':'物料一','supplier_name':'供方一','quantity':3,'unit_price':'0.10'},
            {'part_code':'P2','name':'物料二','supplier_name':'供方一','quantity':2,'unit_price':'0.20'}]}
    ui=public('order_create',{'ok':True,'data':record,'verified':True,'outcome':'success'})
    assert ui['total']==ui['returned_count']==2 and ui['complete']
    assert [r['part_name'] for r in ui['records']]==['物料一','物料二']
    assert [r['amount'] for r in ui['records']]==['0.30','0.40']
    assert all(r['supplier_name']=='供方一' and r['status_name']=='待审核' for r in ui['records'])
    assert all(r['total_amount']=='0.70' and r['order_number']=='PO-REAL' for r in ui['records'])
