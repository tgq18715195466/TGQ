"""Validated inventory adjustments with transactional, durable idempotency."""

import hashlib
from backend.auth import current_identity
import pymysql
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator


class InventoryAdjustment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='forbid')

    part_id: int = Field(alias='partId', strict=True, gt=0, le=9223372036854775807)
    delta: int = Field(strict=True, ge=-2147483648, le=2147483647)
    reason: str = Field(strict=True, min_length=1, max_length=1000)
    request_id: str = Field(alias='requestId', strict=True, min_length=1, max_length=128)

    @field_validator('delta')
    @classmethod
    def nonzero(cls, value):
        if value == 0:
            raise ValueError('delta must be nonzero')
        return value

    @field_validator('reason', 'request_id')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('value must not be blank')
        return value

    @field_validator('request_id')
    @classmethod
    def exact_key(cls, value):
        if value != value.strip():
            raise ValueError('requestId must not have surrounding whitespace')
        return value


def _result(row):
    return {
        'partId': row['part_id'], 'delta': row['delta'], 'reason': row['reason'],
        'requestId': row['request_id'], 'beforeQuantity': row['before_quantity'],
        'afterQuantity': row['after_quantity'],
    }


def _replay(conn, payload):
    with conn.cursor() as cur:
        cur.execute(
            'SELECT request_id, part_id, delta, reason, before_quantity, after_quantity '
            'FROM inventory_adjustment WHERE request_id = %s', (payload.request_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(409, detail={'code': 'INVENTORY_REQUEST_CONFLICT'})
    if (row['part_id'], row['delta'], row['reason']) != (payload.part_id, payload.delta, payload.reason):
        raise HTTPException(409, detail={'code': 'IDEMPOTENCY_CONFLICT'})
    return _result(row)


def adjust_inventory(conn, payload: InventoryAdjustment):
    """Own and close one connection; commit stock and ledger atomically.

    Insert the globally unique key before locking inventory. Concurrent duplicates
    wait on InnoDB's unique index, then read the winner after rolling back their
    transaction. Distinct keys for one part serialize on its inventory row lock.
    """
    user = current_identity.get()
    if user:
        payload = payload.model_copy(update={'request_id': 'user-' + str(user['id']) + '-' + hashlib.sha256(payload.request_id.encode()).hexdigest()})
    try:
        conn.begin()
        with conn.cursor() as cur:
            try:
                cur.execute(
                    'INSERT INTO inventory_adjustment (request_id, part_id, delta, reason) '
                    'VALUES (%s, %s, %s, %s)',
                    (payload.request_id, payload.part_id, payload.delta, payload.reason),
                )
            except pymysql.IntegrityError as exc:
                if exc.args[0] != 1062:
                    raise
                conn.rollback()
                return _replay(conn, payload)

            cur.execute(
                'SELECT id, current_quantity FROM inventory '
                'WHERE part_id = %s AND deleted = 0 FOR UPDATE', (payload.part_id,),
            )
            inventory = cur.fetchone()
            if inventory is None:
                raise HTTPException(404, detail={'code': 'INVENTORY_NOT_FOUND'})
            before = inventory['current_quantity']
            after = before + payload.delta
            if after < 0:
                raise HTTPException(409, detail={'code': 'INSUFFICIENT_INVENTORY'})
            if after > 2147483647:
                raise HTTPException(409, detail={'code': 'INVENTORY_QUANTITY_OVERFLOW'})
            cur.execute(
                'UPDATE inventory SET current_quantity = %s WHERE id = %s',
                (after, inventory['id']),
            )
            cur.execute(
                'UPDATE inventory_adjustment SET before_quantity = %s, after_quantity = %s '
                'WHERE request_id = %s', (before, after, payload.request_id),
            )
            cur.execute(
                'INSERT INTO stock_movement(part_id,delta,before_quantity,after_quantity,kind,reason,actor,document) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                (payload.part_id,payload.delta,before,after,'adjust',payload.reason,(current_identity.get() or {}).get('actor','legacy-agent'),payload.request_id),
            )
        conn.commit()
        return payload.model_dump(by_alias=True) | {'beforeQuantity': before, 'afterQuantity': after}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(500, detail={'code': 'INVENTORY_ADJUST_FAILED'}) from exc
    finally:
        conn.close()
