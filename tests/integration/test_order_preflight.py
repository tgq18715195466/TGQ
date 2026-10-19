import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from backend import app as backend
from tests.integration.test_order_transactions import Cursor, Connection

BASE={"supplierId":15,"expectedDeliveryDate":"2099-01-01","orderDetail":[{"partId":1,"quantity":2,"unitPrice":10}]}

@pytest.mark.parametrize("field",["supplierId","expectedDeliveryDate","orderDetail"])
def test_required_create_fields(field):
    data=dict(BASE); data.pop(field)
    with pytest.raises(ValidationError): backend.OrderCreate.model_validate(data)

@pytest.mark.parametrize("status",[2,3,None])
def test_supplier_blocked_before_insert(monkeypatch,status):
    cursor=Cursor()
    original_one=cursor.fetchone
    cursor.fetchone=lambda: {"id":15,"status":status} if "FROM supplier" in cursor._last_sql else original_one()
    conn=Connection(cursor); monkeypatch.setattr(backend,"_connect",lambda:conn)
    with pytest.raises(HTTPException) as exc: backend.order_create(backend.OrderCreate.model_validate(BASE))
    assert exc.value.status_code==409
    assert conn.rollbacks==1 and conn.commits==0
    assert not any("INSERT INTO" in q for q,_ in cursor.executed)

def test_supplier_part_mismatch(monkeypatch):
    cursor=Cursor(); cursor.fetchone=lambda:{"id":15,"status":1}
    cursor.fetchall=lambda:[{"id":1,"supplier_id":16}]
    conn=Connection(cursor); monkeypatch.setattr(backend,"_connect",lambda:conn)
    with pytest.raises(HTTPException) as exc: backend.order_create(backend.OrderCreate.model_validate(BASE))
    assert exc.value.detail["code"]=="SUPPLIER_PART_MISMATCH"
    assert conn.commits==0

def test_total_mismatch_rejected():
    with pytest.raises(ValidationError): backend.OrderCreate.model_validate({**BASE,"totalAmount":1})
