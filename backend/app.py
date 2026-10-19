"""
最小 ERP 业务后端

复刻原 MCP 工具期望的接口契约（统一 ``{code, data}`` 响应，路由前缀 ``/api``），
直接读写本地 MySQL 的 ``motorparts_db`` 数据库。

接口契约来自 src/mcp_server/tools/*.py 中 http_client 调用的 8 个端点。

启动：
    <conda python> -m uvicorn backend.app:app --host 0.0.0.0 --port 8080

MySQL 连接从 .env 读取：
    MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DB
"""
import os
from datetime import date, datetime
from decimal import Decimal

from backend.inventory import InventoryAdjustment, adjust_inventory
from backend.procurement_eligibility import classify_purchase_readiness

import pymysql
from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

load_dotenv(override=False)

MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DB", "motorparts_db"),
    "charset": "utf8mb4",
}
if os.getenv("EVAL_MODE", "").lower() == "true" and MYSQL_CONFIG["database"] != "motorparts_eval":
    raise RuntimeError("EVAL_MODE requires MYSQL_DB=motorparts_eval")


class OrderDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    part_id: int = Field(alias="partId", gt=0)
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(alias="unitPrice", ge=0)
    remark: str | None = None


class OrderCreate(BaseModel):
    supplier_id: int = Field(alias="supplierId", gt=0, strict=True)
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    order_number: str | None = Field(None, alias="orderNumber", min_length=1)
    total_amount: Decimal | None = Field(None, alias="totalAmount", ge=0)
    status: int = Field(1, ge=1, le=5)
    order_time: datetime | None = Field(None, alias="orderTime")
    expected_delivery_date: date = Field(alias="expectedDeliveryDate")
    actual_delivery_date: date | None = Field(None, alias="actualDeliveryDate")
    created_by: int | None = Field(None, alias="createdBy")
    remark: str | None = None
    order_detail: list[OrderDetail] = Field(alias="orderDetail", min_length=1)

    @model_validator(mode="after")
    def validate_creation(self):
        calculated = sum((item.unit_price * item.quantity for item in self.order_detail), Decimal("0"))
        if self.total_amount is not None and self.total_amount != calculated:
            raise ValueError("totalAmount 必须等于明细数量乘单价之和")
        if self.expected_delivery_date < date.today():
            raise ValueError("expectedDeliveryDate 不能早于今天")
        if self.status != 1 or self.actual_delivery_date is not None:
            raise ValueError("新订单必须为待审核状态，不能直接入库或指定实际交货日期")
        return self

    @property
    def calculated_total(self) -> Decimal:
        return sum(
            (item.unit_price * item.quantity for item in self.order_detail), Decimal("0")
        )


class OrderUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    order_number: str | None = Field(None, alias="orderNumber", min_length=1)
    total_amount: Decimal | None = Field(None, alias="totalAmount", ge=0)
    status: int | None = Field(None, ge=1, le=5)
    order_time: datetime | None = Field(None, alias="orderTime")
    expected_delivery_date: date | None = Field(None, alias="expectedDeliveryDate")
    actual_delivery_date: date | None = Field(None, alias="actualDeliveryDate")
    created_by: int | None = Field(None, alias="createdBy")
    remark: str | None = None
    order_detail: list[OrderDetail] | None = Field(None, alias="orderDetail", min_length=1)

    @model_validator(mode="after")
    def reject_empty_update(self):
        if not self.model_fields_set:
            raise ValueError("更新请求至少包含一个字段")
        for field in ('status','order_detail','total_amount','order_number','order_time','expected_delivery_date'):
            if field in self.model_fields_set and getattr(self,field) is None:
                raise ValueError(f'{field} 不能显式设置为空')
        return self


def _connect() -> pymysql.connections.Connection:
    return pymysql.connect(**MYSQL_CONFIG, cursorclass=pymysql.cursors.DictCursor)


def _jsonable(obj):
    """把 datetime/date/Decimal 转成可 JSON 序列化的类型。"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


def _rows(cur) -> list:
    return [{k: _jsonable(v) for k, v in row.items()} for row in cur.fetchall()]


def _ok(data) -> dict:
    return {"code": 200, "data": data}


def _normalize_datetime(value: str) -> str:
    """把 ISO 'T' 分隔符转成 MySQL DATETIME 能接受的空格分隔。"""
    if value:
        return value.replace("T", " ").split(".")[0] if "T" in value else value
    return value


app = FastAPI(title="ERP Minimal Backend", version="1.0.0")
router = APIRouter(prefix="/api")


# ---------------------------------------------------------------
# 供应商
# ---------------------------------------------------------------
@router.get("/suppliers/search")
def suppliers_search(name: str = Query("", description="供应商名称模糊查询"),
                     supplier_id: int | None = Query(None, alias="supplierId", gt=0),
                     supplier_code: str | None = Query(None, alias="supplierCode"),
                     paged: bool = Query(False),
                     current: int = Query(1, ge=1),
                     size: int = Query(100, ge=1, le=100)):
    where, params = ["deleted = 0", "name LIKE %s"], [f"%{name}%"]
    if supplier_id is not None:
        where.append("id = %s"); params.append(supplier_id)
    if supplier_code is not None:
        where.append("supplier_code = %s"); params.append(supplier_code)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            condition = ' AND '.join(where)
            if paged:
                cur.execute(f"SELECT COUNT(*) AS total FROM supplier WHERE {condition}", tuple(params))
                total = cur.fetchone()['total']
            limit = 'ORDER BY id LIMIT %s OFFSET %s' if paged else 'LIMIT 50'
            cur.execute(
                "SELECT id, supplier_code, name, contact_person, phone, email, "
                "address, credit_rating, status "
                f"FROM supplier WHERE {condition} {limit}",
                tuple(params + [size, (current - 1) * size]) if paged else tuple(params),
            )
            records = _rows(cur)
            return _ok({'records': records, 'total': total, 'current': current, 'size': size} if paged else records)
    finally:
        conn.close()


# ---------------------------------------------------------------
# 零部件
# ---------------------------------------------------------------
@router.get("/parts/page")
def parts_page(
    current: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    name: str = Query(None),
    category: str = Query(None),
    supplier_id: int = Query(None, alias="supplierId"),
):
    where, params = ["deleted = 0"], []
    if name:
        where.append("name LIKE %s")
        params.append(f"%{name}%")
    if category:
        where.append("category = %s")
        params.append(category)
    if supplier_id is not None:
        where.append("supplier_id = %s")
        params.append(supplier_id)

    cond = " AND ".join(where)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS c FROM part WHERE {cond}", params)
            total = cur.fetchone()["c"]
            offset = (current - 1) * size
            cur.execute(
                "SELECT id, part_code, name, model, specification, unit, "
                "purchase_price, suggested_retail_price, stock_warning_value, "
                "supplier_id, category "
                f"FROM part WHERE {cond} ORDER BY id LIMIT %s OFFSET %s",
                params + [size, offset],
            )
            records = _rows(cur)
        return _ok({"records": records, "total": total, "current": current, "size": size})
    finally:
        conn.close()


@router.get("/parts/search")
def parts_search(name: str = Query(...)):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, part_code, name, model, specification, unit, "
                "purchase_price, suggested_retail_price, stock_warning_value, "
                "supplier_id, category "
                "FROM part WHERE deleted = 0 AND name LIKE %s LIMIT 50",
                (f"%{name}%",),
            )
            return _ok(_rows(cur))
    finally:
        conn.close()


@router.get("/parts/supplier/{supplier_id}")
def parts_by_supplier(supplier_id: int):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, part_code, name, model, specification, unit, "
                "purchase_price, suggested_retail_price, stock_warning_value, category "
                "FROM part WHERE deleted = 0 AND supplier_id = %s",
                (supplier_id,),
            )
            return _ok(_rows(cur))
    finally:
        conn.close()


# ---------------------------------------------------------------
# 库存预警
# ---------------------------------------------------------------
@router.post("/inventory/adjust")
def inventory_adjust(payload: InventoryAdjustment):
    return _ok(adjust_inventory(_connect(), payload))


@router.get("/inventory/warning")
def inventory_warning():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT i.id, i.part_id, i.current_quantity, i.safety_stock, "
                "i.warehouse_location, i.last_inbound_time, "
                "p.name AS part_name, p.part_code, p.category, p.unit, "
                "s.name AS supplier_name, s.status AS supplier_status, "
                "COALESCE(pc.enabled, 1) AS enabled, "
                "COALESCE(o.on_order, 0) AS on_order, "
                "COALESCE(a.pending_quantity, 0) AS pending_quantity "
                "FROM inventory i JOIN part p ON i.part_id = p.id "
                "LEFT JOIN supplier s ON s.id = p.supplier_id AND s.deleted = 0 "
                "LEFT JOIN part_control pc ON pc.part_id = p.id "
                "LEFT JOIN (SELECT d.part_id, SUM(GREATEST(d.quantity - COALESCE(r.received, 0), 0)) AS on_order "
                "FROM order_detail d JOIN purchase_order po ON po.id = d.order_id "
                "AND po.deleted = 0 AND po.status IN (1, 2, 3) "
                "LEFT JOIN (SELECT detail_id, SUM(accepted) AS received FROM goods_receipt_line GROUP BY detail_id) r "
                "ON r.detail_id = d.id WHERE d.deleted = 0 GROUP BY d.part_id) o ON o.part_id = p.id "
                "LEFT JOIN (SELECT part_id, SUM(quantity) AS pending_quantity FROM purchase_request "
                "WHERE status = 'pending' GROUP BY part_id) a ON a.part_id = p.id "
                "WHERE i.deleted = 0 AND p.deleted = 0 AND i.current_quantity < i.safety_stock "
                "ORDER BY (i.safety_stock - i.current_quantity) DESC"
            )
            return _ok([{**row, **classify_purchase_readiness(row)} for row in _rows(cur)])
    finally:
        conn.close()


# ---------------------------------------------------------------
# 订单
# ---------------------------------------------------------------
def _ensure_parts_exist(cur, part_ids: set[int]) -> None:
    placeholders = ",".join(["%s"] * len(part_ids))
    cur.execute(
        f"SELECT id FROM part WHERE deleted = 0 AND id IN ({placeholders})",
        tuple(part_ids),
    )
    found = {row["id"] for row in cur.fetchall()}
    missing = sorted(part_ids - found)
    if missing:
        raise HTTPException(status_code=422, detail={"code": "PART_NOT_FOUND", "part_ids": missing})


def _ensure_order_supplier(cur, supplier_id, part_ids):
    # Lock master data until commit so approval-time state changes cannot bypass admission.
    cur.execute("SELECT id, status FROM supplier WHERE id = %s AND deleted = 0 FOR UPDATE", (supplier_id,))
    supplier = cur.fetchone()
    if not supplier:
        raise HTTPException(422, detail={"code": "SUPPLIER_NOT_FOUND", "supplier_id": supplier_id})
    if supplier["status"] != 1:
        raise HTTPException(409, detail={"code": "SUPPLIER_NOT_ACTIVE", "supplier_id": supplier_id, "status": supplier["status"]})
    placeholders = ",".join(["%s"] * len(part_ids))
    cur.execute(f"SELECT id, supplier_id FROM part WHERE deleted = 0 AND id IN ({placeholders}) ORDER BY id FOR UPDATE", tuple(sorted(part_ids)))
    rows = cur.fetchall()
    missing = sorted(part_ids - {row["id"] for row in rows})
    if missing:
        raise HTTPException(422, detail={"code": "PART_NOT_FOUND", "part_ids": missing})
    mismatched = [row["id"] for row in rows if row["supplier_id"] != supplier_id]
    if mismatched:
        raise HTTPException(422, detail={"code": "SUPPLIER_PART_MISMATCH", "part_ids": mismatched})
    cur.execute(f"SELECT part_id FROM part_control WHERE enabled=0 AND part_id IN ({placeholders})", tuple(sorted(part_ids)))
    if cur.fetchone():
        raise HTTPException(409, "物料已停用，不可下单")


@router.post("/orders/create", status_code=201)
def order_create(payload: OrderCreate):
    """创建采购订单。body 字段为 camelCase，与 MCP order_create 工具对齐。"""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            _ensure_order_supplier(cur, payload.supplier_id, {item.part_id for item in payload.order_detail})
            total = payload.calculated_total

            cur.execute(
                "INSERT INTO purchase_order "
                "(order_number, total_amount, status, order_time, "
                "expected_delivery_date, actual_delivery_date, created_by, remark) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    payload.order_number or "",
                    float(total),
                    payload.status,
                    payload.order_time or datetime.now(),
                    payload.expected_delivery_date,
                    payload.actual_delivery_date,
                    payload.created_by,
                    payload.remark,
                ),
            )
            order_id = cur.lastrowid

            for detail in payload.order_detail:
                cur.execute(
                    "INSERT INTO order_detail (order_id, part_id, quantity, unit_price, remark) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        order_id,
                        detail.part_id,
                        detail.quantity,
                        detail.unit_price,
                        detail.remark,
                    ),
                )

            conn.commit()
            return _ok({"id": order_id, "orderNumber": payload.order_number or ""})
    except HTTPException:
        conn.rollback()
        raise
    except pymysql.IntegrityError as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail={"code": "ORDER_CONFLICT"}) from exc
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise HTTPException(status_code=500, detail={"code": "ORDER_CREATE_FAILED"}) from exc
    finally:
        conn.close()


@router.put("/orders/update/{order_id}")
def order_update(order_id: int, payload: OrderUpdate):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status FROM purchase_order WHERE id = %s AND deleted = 0 FOR UPDATE",
                (order_id,),
            )
            existing = cur.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail={"code": "ORDER_NOT_FOUND"})

            if existing["status"] not in (1,2,3):
                raise HTTPException(409, "已完成或已取消订单不可修改")
            allowed_status = {1:{1,5},2:{2,3,5},3:{3,5}}
            if payload.status is not None and payload.status not in allowed_status[existing['status']]:
                raise HTTPException(409, "订单状态变更不允许，请使用审核或收货入口")
            if payload.status == 4 or payload.actual_delivery_date is not None:
                raise HTTPException(409, "请通过收货管理按实收数量入库，不可直接改为已入库")
            cur.execute("SELECT id FROM goods_receipt WHERE order_id=%s LIMIT 1", (order_id,))
            received = cur.fetchone()
            if received and (payload.order_detail is not None or payload.status is not None or payload.total_amount is not None):
                raise HTTPException(409, "已有收货记录，不能修改订单明细、金额或状态")

            if existing["status"] == 1 and payload.status in (2,3):
                raise HTTPException(409, "待审核订单请通过入库收货页面的审核入口处理")
            if existing["status"] in (2,3) and (payload.order_detail is not None or payload.total_amount is not None or payload.status == 1):
                raise HTTPException(409, "已审核订单不可改数量、价格或回退审核；请取消后重新申请")
            if payload.order_detail is not None:
                part_ids = {item.part_id for item in payload.order_detail}
                _ensure_parts_exist(cur, part_ids)
                marks = ','.join(['%s'] * len(part_ids))
                cur.execute(f"SELECT DISTINCT supplier_id FROM part WHERE id IN ({marks})", tuple(part_ids))
                supplier_ids = [row['supplier_id'] for row in cur.fetchall()]
                if len(supplier_ids) != 1 or supplier_ids[0] is None:
                    raise HTTPException(422, "订单明细必须属于同一个有效供应商")
                _ensure_order_supplier(cur, supplier_ids[0], part_ids)
                calculated = sum(item.quantity * item.unit_price for item in payload.order_detail)
                if payload.total_amount is not None and payload.total_amount != calculated:
                    raise HTTPException(422, "订单金额与明细不一致")
                payload.total_amount = calculated
            elif payload.total_amount is not None:
                cur.execute("SELECT COALESCE(SUM(quantity*unit_price),0) AS amount FROM order_detail WHERE order_id=%s AND deleted=0", (order_id,))
                if cur.fetchone()['amount'] != payload.total_amount:
                    raise HTTPException(422, "订单金额必须与明细一致")

            values = payload.model_dump(by_alias=True, exclude_unset=True)
            fields, params = [], []
            for camel, col in [
                ("orderNumber", "order_number"),
                ("totalAmount", "total_amount"),
                ("status", "status"),
                ("expectedDeliveryDate", "expected_delivery_date"),
                ("actualDeliveryDate", "actual_delivery_date"),
                ("createdBy", "created_by"),
                ("remark", "remark"),
            ]:
                if camel in values:
                    fields.append(f"{col} = %s")
                    params.append(values[camel])
            if "orderTime" in values:
                fields.append("order_time = %s")
                params.append(values["orderTime"])
            if existing['status'] == 2 and payload.status == 3:
                from backend.auth import current_identity
                ident = current_identity.get()
                cur.execute('INSERT INTO purchase_order_execution(order_id,actor) VALUES(%s,%s)',
                            (order_id, ident['actor'] if ident else '系统'))
            if fields:
                cur.execute(
                    f"UPDATE purchase_order SET {', '.join(fields)} WHERE id = %s",
                    params + [order_id],
                )

            # 可选：替换明细
            if payload.order_detail is not None:
                cur.execute("DELETE FROM order_detail WHERE order_id = %s", (order_id,))
                for detail in payload.order_detail:
                    cur.execute(
                        "INSERT INTO order_detail (order_id, part_id, quantity, unit_price, remark) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (order_id, detail.part_id, detail.quantity,
                         detail.unit_price, detail.remark),
                    )

            conn.commit()
            return _ok({"id": order_id})
    except HTTPException:
        conn.rollback()
        raise
    except pymysql.IntegrityError as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail={"code": "ORDER_CONFLICT"}) from exc
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        raise HTTPException(status_code=500, detail={"code": "ORDER_UPDATE_FAILED"}) from exc
    finally:
        conn.close()


@router.get("/orders/search-details")
def order_search_details(
    part_name: str = Query(None, alias="partName"),
    start_date: str = Query(None, alias="startDate"),
    end_date: str = Query(None, alias="endDate"),
):
    where, params = ["po.deleted = 0 AND od.deleted = 0"], []
    if part_name:
        where.append("p.name LIKE %s")
        params.append(f"%{part_name}%")
    if start_date:
        where.append("po.order_time >= %s")
        params.append(start_date)
    if end_date:
        where.append("po.order_time <= %s")
        params.append(end_date)

    cond = " AND ".join(where)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT od.id, od.order_id, od.part_id, od.quantity, od.unit_price, "
                "od.subtotal, po.order_number, po.order_time, po.status, "
                "p.name AS part_name, p.part_code, p.category, "
                "s.name AS supplier_name "
                "FROM order_detail od "
                "JOIN purchase_order po ON od.order_id = po.id "
                "JOIN part p ON od.part_id = p.id "
                "LEFT JOIN supplier s ON p.supplier_id = s.id "
                f"WHERE {cond} ORDER BY po.order_time DESC LIMIT 200",
                params,
            )
            return _ok(_rows(cur))
    finally:
        conn.close()


app.include_router(router)


@app.get("/health")
def health():
    return {"status": "healthy", "service": "ERP Minimal Backend"}


from backend.dashboard import router as dashboard_router
app.include_router(dashboard_router)

from backend.operations import router as operations_router
app.include_router(operations_router)

from backend.procurement_verification import router as verification_router
app.include_router(verification_router)


from backend.auth import AuthMiddleware, router as auth_router
app.include_router(auth_router)
app.add_middleware(AuthMiddleware)
