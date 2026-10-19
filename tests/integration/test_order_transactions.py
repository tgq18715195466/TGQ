import pytest
from fastapi import HTTPException

from backend import app as backend


class Cursor:
    def __init__(self, existing_status=None, receipt_inserted=True):
        self.existing_status = existing_status
        self.receipt_inserted = receipt_inserted
        self.executed = []
        self.lastrowid = 42
        self.rowcount = 1
        self._last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self._last_sql = " ".join(sql.split())
        self.executed.append((self._last_sql, params))
        if "INSERT IGNORE INTO purchase_order_inventory_receipt" in self._last_sql:
            self.rowcount = 1 if self.receipt_inserted else 0
        elif "UPDATE inventory" in self._last_sql:
            self.rowcount = 1

    def fetchone(self):
        if "FROM supplier" in self._last_sql:
            return {"id":15,"status":1}
        if "SELECT id, status FROM purchase_order" in self._last_sql:
            return {"id": 42, "status": self.existing_status}
        return None

    def fetchall(self):
        if "SELECT id, supplier_id FROM part" in self._last_sql:
            return [{"id":1,"supplier_id":15}]
        if "SELECT id FROM part" in self._last_sql:
            return [{"id": 1}]
        if "SELECT part_id, quantity FROM order_detail" in self._last_sql:
            return [{"part_id": 1, "quantity": 2}]
        return []


class Connection:
    def __init__(self, cursor):
        self.value = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def test_create_calculates_total_and_does_not_change_inventory(monkeypatch):
    cursor = Cursor()
    connection = Connection(cursor)
    monkeypatch.setattr(backend, "_connect", lambda: connection)
    result = backend.order_create(backend.OrderCreate.model_validate({
        "supplierId":15,"expectedDeliveryDate":"2099-01-01", "orderNumber": "PO-TEST", "orderDetail": [{"partId": 1, "quantity": 2, "unitPrice": 12.5}]
    }))
    order_insert = next(params for sql, params in cursor.executed if "INSERT INTO purchase_order" in sql)
    assert order_insert[1] == 25.0
    assert not any("UPDATE inventory" in sql for sql, _ in cursor.executed)
    assert result["data"]["id"] == 42
    assert connection.commits == 1


@pytest.mark.parametrize('status', [3,4])
def test_direct_received_status_rejected_without_posting(monkeypatch,status):
    cursor = Cursor(existing_status=status)
    connection = Connection(cursor)
    monkeypatch.setattr(backend, "_connect", lambda: connection)
    with pytest.raises(HTTPException) as exc:
        backend.order_update(42, backend.OrderUpdate.model_validate({"status":4}))
    assert exc.value.status_code == 409
    assert not any("UPDATE inventory" in sql for sql,_ in cursor.executed)
    assert connection.commits == 0 and connection.rollbacks == 1


def test_missing_part_is_rejected_and_transaction_rolls_back(monkeypatch):
    cursor = Cursor()
    cursor.fetchall = lambda: []
    connection = Connection(cursor)
    monkeypatch.setattr(backend, "_connect", lambda: connection)
    with pytest.raises(HTTPException) as exc:
        backend.order_create(backend.OrderCreate.model_validate({
            "supplierId":15,"expectedDeliveryDate":"2099-01-01", "orderDetail": [{"partId": 99999, "quantity": 1, "unitPrice": 1}]
        }))
    assert exc.value.status_code == 422
    assert connection.rollbacks == 1


def test_missing_order_is_rejected(monkeypatch):
    cursor = Cursor(existing_status=None)
    cursor.fetchone = lambda: None
    connection = Connection(cursor)
    monkeypatch.setattr(backend, "_connect", lambda: connection)
    with pytest.raises(HTTPException) as exc:
        backend.order_update(99999, backend.OrderUpdate.model_validate({"status": 2}))
    assert exc.value.status_code == 404
    assert connection.rollbacks == 1
