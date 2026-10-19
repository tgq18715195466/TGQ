"""Read actual procurement documents without replaying any business write."""
import json
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder


router = APIRouter(prefix='/api/operations', tags=['采购单据核验'])


def _request_record(cur, request_id):
    # The acknowledgement is only an ID locator. Its status/values are not facts.
    cur.execute('SELECT result FROM erp_operation WHERE request_id=%s', (request_id,))
    operation = cur.fetchone()
    if not operation or operation['result'] is None:
        return None
    result = operation['result']
    if isinstance(result, (str, bytes)):
        result = json.loads(result)
    if not isinstance(result, dict) or type(result.get('id')) is not int or result['id'] <= 0:
        return None
    if result.get('status') not in ('pending', 'approved', 'rejected'):
        return None
    cur.execute(
        'SELECT r.*, p.name, p.part_code, s.name AS supplier_name '
        'FROM purchase_request r LEFT JOIN part p ON p.id=r.part_id '
        'LEFT JOIN supplier s ON s.id=r.supplier_id WHERE r.id=%s',
        (result['id'],),
    )
    return cur.fetchone()


def _order_record(cur, order_number):
    cur.execute(
        'SELECT id, order_number, status, total_amount, expected_delivery_date '
        'FROM purchase_order WHERE order_number=%s AND deleted=0', (order_number,),
    )
    record = cur.fetchone()
    # MySQL's order_number collation is case insensitive; require the exact key.
    if not record or record['order_number'] != order_number:
        return None
    cur.execute(
        'SELECT d.id, d.part_id, d.quantity, d.unit_price, p.name, p.part_code, '
        'p.supplier_id, s.name AS supplier_name FROM order_detail d '
        'LEFT JOIN part p ON p.id=d.part_id LEFT JOIN supplier s ON s.id=p.supplier_id '
        'WHERE d.order_id=%s AND d.deleted=0 ORDER BY d.id', (record['id'],),
    )
    lines = cur.fetchall()
    # purchase_order has no supplier column. Ambiguous/missing associations stay
    # unknown so the workflow can retain recovery rather than confirm a draft.
    suppliers = {line['supplier_id'] for line in lines}
    record['supplier_id'] = next(iter(suppliers)) if len(suppliers) == 1 else None
    record['lines'] = lines
    record['order_detail'] = lines
    return record


@router.get('/verification')
def procurement_verification(
    operation: Literal['order_create', 'purchase_request_create'],
    request_id: str | None = Query(None, min_length=1, max_length=128),
    order_number: str | None = Query(None, min_length=1, max_length=50),
):
    """Return only the matching persisted document; an ACK alone is not proof."""
    key = request_id if operation == 'purchase_request_create' else order_number
    if not key or not key.strip():
        raise HTTPException(422, '采购申请核验需要 request_id；订单核验需要 order_number')
    # Share connection/config and authentication with the backend only. Keep all
    # reads on one connection so header and detail belong to the same snapshot.
    from backend.app import _connect
    conn = None
    try:
        conn = _connect()
        with conn.cursor() as cur:
            record = (_request_record(cur, request_id) if operation == 'purchase_request_create'
                      else _order_record(cur, order_number))
        return {'ok': True, 'data': {'found': record is not None,
                                    'record': jsonable_encoder(record, custom_encoder={Decimal: str})}}
    except Exception as exc:
        # Failed reads must never look like a confirmed missing document.
        raise HTTPException(503, {'code': 'ERP_UNAVAILABLE', 'retryable': True}) from exc
    finally:
        if conn is not None:
            conn.close()
