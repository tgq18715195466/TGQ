from agent.business_results import expose_business_result, normalize_business_result
from backend.procurement_eligibility import classify_purchase_readiness
from backend.operations import calculate
from backend import app as backend_app


def test_shortage_does_not_imply_purchase_eligibility():
    terminated = classify_purchase_readiness({
        'current_quantity': 0, 'safety_stock': 19, 'supplier_status': 3,
        'enabled': 1, 'on_order': 0, 'pending_quantity': 0,
    })
    assert terminated['stock_gap'] == 19
    assert terminated['purchase_status_name'] == '不可采购：供应商已终止合作'
    assert terminated['suggested_request_quantity'] == 0

    covered = classify_purchase_readiness({
        'current_quantity': 3, 'safety_stock': 27, 'supplier_status': 1,
        'enabled': 1, 'on_order': 79, 'pending_quantity': 0,
    })
    assert covered['stock_gap'] == 24
    assert covered['purchase_status_name'] == '可采购'
    assert covered['uncovered_shortage'] == 0
    assert covered['suggested_request_quantity'] == 0


def test_blocked_and_unknown_states_fail_closed():
    base = {'current_quantity': 2, 'safety_stock': 26, 'on_order': 0, 'pending_quantity': 0, 'enabled': 1}
    assert classify_purchase_readiness({**base, 'supplier_status': 2})['purchase_status_name'] == '不可采购：供应商暂停合作'
    assert classify_purchase_readiness({**base, 'supplier_status': None})['purchase_status_name'] == '不可采购：供应商状态未知'
    assert classify_purchase_readiness({**base, 'supplier_status': 1, 'enabled': 0})['purchase_status_name'] == '不可采购：物料已停用'
    pending = classify_purchase_readiness({**base, 'supplier_status': 1, 'pending_quantity': 5})
    assert pending['purchase_status_name'] == '已有待审批申请，暂不可重复申请'
    assert pending['suggested_request_quantity'] == 0


def test_inventory_business_table_exposes_separate_stock_and_purchase_fields():
    row = {'part_code': 'PC2010', 'part_name': '离合器片', 'supplier_status': 3,
           'supplier_name': '盖茨汽车配件（上海）有限公司', 'current_quantity': 0,
           'safety_stock': 19, 'enabled': 1, 'on_order': 0, 'pending_quantity': 0}
    enriched = {**row, **classify_purchase_readiness(row)}
    public = expose_business_result(normalize_business_result('inventory_warning', {'ok': True, 'data': [enriched]}))
    item = public['records'][0]
    assert item['supplier_status_name'] == '已终止'
    assert item['purchase_status_name'] == '不可采购：供应商已终止合作'
    assert item['stock_gap'] == 19
    assert item['suggested_request_quantity'] == 0


def test_replenishment_suggestion_does_not_offer_blocked_supplier_quantity():
    blocked = calculate({'current_quantity': 0, 'stock_warning_value': 19,
                         'supplier_status': 3, 'enabled': 1, 'on_order': 0,
                         'pending_quantity': 0}, 0)
    assert blocked['suggested'] == 19  # raw uncovered demand retained for diagnostics
    assert blocked['purchase_status_name'] == '不可采购：供应商已终止合作'
    assert blocked['suggested_request_quantity'] == 0
    ui = expose_business_result(normalize_business_result('replenishment_suggestions',
                                 {'ok': True, 'data': {'records': [blocked], 'total': 1}}))
    assert ui['records'][0]['purchase_status_name'] == '不可采购：供应商已终止合作'
    assert ui['records'][0]['suggested_request_quantity'] == 0


def test_warning_endpoint_enriches_erp_rows_without_inventing_eligibility(monkeypatch):
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, sql):
            assert 'supplier_status' in sql and 'pending_quantity' in sql and 'on_order' in sql
        def fetchall(self):
            return [{'part_code': 'PC2010', 'part_name': '离合器片',
                     'current_quantity': 0, 'safety_stock': 19,
                     'supplier_status': 3, 'enabled': 1,
                     'on_order': 0, 'pending_quantity': 0}]

    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass

    monkeypatch.setattr(backend_app, '_connect', lambda: Connection())
    item = backend_app.inventory_warning()['data'][0]
    assert item['purchase_status_name'] == '不可采购：供应商已终止合作'
    assert item['stock_gap'] == 19
    assert item['suggested_request_quantity'] == 0
