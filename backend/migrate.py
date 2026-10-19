"""Non-destructive schema bootstrap: python -m backend.migrate.

Only create missing tables. Never seed, backfill or modify business data.
"""

STATEMENTS = (
    '''CREATE TABLE IF NOT EXISTS inventory_adjustment (
        request_id VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
        part_id BIGINT NOT NULL,
        delta INT NOT NULL,
        reason VARCHAR(1000) NOT NULL,
        before_quantity INT NULL,
        after_quantity INT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (request_id),
        KEY idx_inventory_adjustment_part (part_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
    '''CREATE TABLE IF NOT EXISTS purchase_order_inventory_receipt (
        order_id BIGINT NOT NULL,
        posted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (order_id),
        CONSTRAINT fk_receipt_order FOREIGN KEY (order_id) REFERENCES purchase_order (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
)



def migrate(connection):
    """Create missing ledger tables on a caller-owned connection, safely repeatable."""
    with connection.cursor() as cursor:
        from backend.operations_schema import STATEMENTS as OPERATIONS
        from backend.auth_schema import STATEMENTS as AUTH
        for statement in STATEMENTS + OPERATIONS + AUTH:
            cursor.execute(statement)
    connection.commit()


def main():
    # Share the backend's environment precedence and EVAL_MODE guard.
    from backend.app import _connect
    connection = _connect()
    try:
        migrate(connection)
    finally:
        connection.close()
    print('ERP ledger schema migration complete')


if __name__ == '__main__':
    main()
