"""Additive schema for receiving, inventory audit and durable approvals."""
STATEMENTS = (
'''CREATE TABLE IF NOT EXISTS stock_issue_recipient (
 movement_id BIGINT PRIMARY KEY, recipient_name VARCHAR(100) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS purchase_order_execution (
 order_id BIGINT PRIMARY KEY, actor VARCHAR(100) NOT NULL,
 started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',

'''CREATE TABLE IF NOT EXISTS purchase_request_manual (
 request_id BIGINT PRIMARY KEY
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS purchase_order_review (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, order_id BIGINT NOT NULL,
 decision VARCHAR(20) NOT NULL, actor VARCHAR(100) NOT NULL, reason VARCHAR(1000) NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uk_review_order(order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',

'''CREATE TABLE IF NOT EXISTS erp_operation (
 request_id VARCHAR(128) COLLATE utf8mb4_bin PRIMARY KEY, payload_hash CHAR(64) NOT NULL,
 result JSON NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS stock_movement (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, part_id BIGINT NOT NULL, delta INT NOT NULL,
 before_quantity INT NOT NULL, after_quantity INT NOT NULL, kind VARCHAR(30) NOT NULL,
 reason VARCHAR(1000) NOT NULL, actor VARCHAR(100) NOT NULL, document VARCHAR(128) NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, KEY idx_movement_part(part_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS goods_receipt (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, order_id BIGINT NOT NULL, delivery_number VARCHAR(128) NOT NULL, actor VARCHAR(100) NOT NULL,
 reason VARCHAR(1000) NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 KEY idx_receipt_order(order_id), UNIQUE KEY uk_delivery(order_id,delivery_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS goods_receipt_line (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, receipt_id BIGINT NOT NULL, detail_id BIGINT NOT NULL,
 part_id BIGINT NOT NULL, accepted INT NOT NULL, rejected INT NOT NULL,
 KEY idx_receipt_detail(detail_id), KEY idx_receipt_header(receipt_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS part_control (
 part_id BIGINT PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT TRUE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS master_change (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, entity VARCHAR(20) NOT NULL, entity_id BIGINT NULL,
 proposed JSON NOT NULL, previous JSON NULL, status VARCHAR(20) NOT NULL DEFAULT 'pending',
 actor VARCHAR(100) NOT NULL, reason VARCHAR(1000) NOT NULL, reviewer VARCHAR(100) NULL,
 review_reason VARCHAR(1000) NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 reviewed_at DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS purchase_request (
 id BIGINT AUTO_INCREMENT PRIMARY KEY, part_id BIGINT NOT NULL, supplier_id BIGINT NOT NULL,
 quantity INT NOT NULL, unit_price DECIMAL(10,2) NOT NULL, expected_date DATE NOT NULL,
 demand INT NOT NULL DEFAULT 0, status VARCHAR(20) NOT NULL DEFAULT 'pending', order_id BIGINT NULL,
 actor VARCHAR(100) NOT NULL, reason VARCHAR(1000) NOT NULL, reviewer VARCHAR(100) NULL,
 review_reason VARCHAR(1000) NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 reviewed_at DATETIME NULL, KEY idx_request_part(part_id,status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
)
