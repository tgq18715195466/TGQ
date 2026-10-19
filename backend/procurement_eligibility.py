"""Read-only purchase readiness derived from the same ERP constraints as requests.

Stock shortage, supplier admission, and an uncovered need are separate facts.
This helper never authorizes a write; the transactional request path rechecks all
constraints under locks immediately before creating a purchase request.
"""


def classify_purchase_readiness(row: dict) -> dict:
    current = row.get('current_quantity')
    safety = row.get('safety_stock')
    gap = max(0, int(safety) - int(current)) if current is not None and safety is not None else None
    on_order = max(0, int(row.get('on_order') or 0))
    pending = max(0, int(row.get('pending_quantity') or 0))
    uncovered = max(0, gap - on_order - pending) if gap is not None else None

    if row.get('enabled') == 0:
        status = '不可采购：物料已停用'
    elif row.get('enabled') != 1:
        status = '不可采购：物料启用状态未知'
    elif row.get('supplier_status') == 2:
        status = '不可采购：供应商暂停合作'
    elif row.get('supplier_status') == 3:
        status = '不可采购：供应商已终止合作'
    elif row.get('supplier_status') != 1:
        status = '不可采购：供应商状态未知'
    elif pending:
        status = '已有待审批申请，暂不可重复申请'
    else:
        status = '可采购'

    return {
        'stock_gap': gap,
        'on_order': on_order,
        'pending_quantity': pending,
        'uncovered_shortage': uncovered,
        'purchase_status_name': status,
        'suggested_request_quantity': uncovered if status == '可采购' and uncovered is not None else 0,
    }
