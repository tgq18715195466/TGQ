"""Independent, additive auth schema. No default user or password is seeded."""
STATEMENTS = (
'''CREATE TABLE IF NOT EXISTS auth_bootstrap (
 id TINYINT NOT NULL PRIMARY KEY,
 initialized BOOLEAN NOT NULL DEFAULT FALSE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS auth_user (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 username VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
 display_name VARCHAR(100) NOT NULL,
 password_hash VARCHAR(255) NOT NULL,
 role VARCHAR(20) NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT TRUE,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE KEY uk_auth_username(username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS auth_session (
 token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
 user_id BIGINT NOT NULL,
 expires_at DATETIME NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 KEY idx_auth_session_user(user_id), KEY idx_auth_session_expiry(expires_at),
 CONSTRAINT fk_auth_session_user FOREIGN KEY(user_id) REFERENCES auth_user(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
'''CREATE TABLE IF NOT EXISTS auth_audit (
 id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
 user_id BIGINT NOT NULL, actor VARCHAR(100) NOT NULL,
 method VARCHAR(10) NOT NULL, path VARCHAR(512) NOT NULL,
 status SMALLINT NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 KEY idx_auth_audit_user(user_id,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
)
