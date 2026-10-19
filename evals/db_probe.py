from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

import pymysql


def _config() -> dict[str, Any]:
    """Use a dedicated eval database. Refuse the default development DB."""
    database = os.getenv("EVAL_MYSQL_DB", "motorparts_eval")
    if database != "motorparts_eval":
        raise RuntimeError(
            "Evaluation is restricted to EVAL_MYSQL_DB=motorparts_eval."
        )
    return {
        "host": os.getenv("EVAL_MYSQL_HOST", os.getenv("MYSQL_HOST", "localhost")),
        "port": int(os.getenv("EVAL_MYSQL_PORT", os.getenv("MYSQL_PORT", "3306"))),
        "user": os.getenv("EVAL_MYSQL_USER", os.getenv("MYSQL_USER", "root")),
        "password": os.getenv("EVAL_MYSQL_PASSWORD", os.getenv("MYSQL_PASSWORD", "")),
        "database": database,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }


@contextmanager
def connection() -> Iterator[pymysql.connections.Connection]:
    conn = pymysql.connect(**_config())
    try:
        yield conn
    finally:
        conn.close()


def snapshot(case_id: str) -> dict[str, Any]:
    """Capture only stable business facts used by automatic scorers."""
    marker = f"EVAL:{case_id}"
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM purchase_order WHERE deleted = 0")
        order_count = cur.fetchone()["c"]
        cur.execute(
            "SELECT id, order_number, total_amount, status, order_time, remark "
            "FROM purchase_order WHERE remark = %s ORDER BY id",
            (marker,),
        )
        marked_orders = cur.fetchall()
        cur.execute(
            "SELECT id, order_number, total_amount, status, order_time, remark "
            "FROM purchase_order WHERE id = 1"
        )
        order_one = cur.fetchone()
        cur.execute(
            "SELECT id, part_id, quantity, unit_price, remark FROM order_detail "
            "WHERE order_id = 1 AND deleted = 0 ORDER BY id"
        )
        order_one_details = cur.fetchall()
        cur.execute(
            "SELECT part_id, current_quantity FROM inventory WHERE part_id IN (1, 3, 4, 5) ORDER BY part_id"
        )
        inventory = cur.fetchall()
    return {
        "order_count": order_count,
        "marked_orders": marked_orders,
        "order_1": order_one,
        "order_1_details": order_one_details,
        "inventory": inventory,
    }


def cleanup_case(case_id: str) -> None:
    """Delete only records carrying the case marker. Use an eval DB regardless."""
    marker = f"EVAL:{case_id}"
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM purchase_order WHERE remark = %s", (marker,))
        ids = [row["id"] for row in cur.fetchall()]
        if ids:
            placeholders = ",".join(["%s"] * len(ids))
            cur.execute(f"DELETE FROM order_detail WHERE order_id IN ({placeholders})", ids)
            cur.execute(f"DELETE FROM purchase_order WHERE id IN ({placeholders})", ids)
        conn.commit()


def restore_snapshot(case_id: str, before: dict[str, Any]) -> None:
    """Restore the small set of rows touched by the bundled evaluation cases."""
    cleanup_case(case_id)
    with connection() as conn, conn.cursor() as cur:
        order = before.get("order_1")
        if order:
            cur.execute(
                "UPDATE purchase_order SET order_number=%s, total_amount=%s, status=%s, "
                "order_time=%s, remark=%s WHERE id=1",
                (
                    order["order_number"], order["total_amount"], order["status"],
                    order["order_time"], order["remark"],
                ),
            )
        if "order_1_details" in before:
            cur.execute("DELETE FROM order_detail WHERE order_id=1")
            for detail in before["order_1_details"]:
                cur.execute(
                    "INSERT INTO order_detail (id, order_id, part_id, quantity, unit_price, remark) "
                    "VALUES (%s, 1, %s, %s, %s, %s)",
                    (detail["id"], detail["part_id"], detail["quantity"], detail["unit_price"], detail["remark"]),
                )
        for row in before.get("inventory", []):
            cur.execute(
                "UPDATE inventory SET current_quantity=%s WHERE part_id=%s",
                (row["current_quantity"], row["part_id"]),
            )
        conn.commit()
