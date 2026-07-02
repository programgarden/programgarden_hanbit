-- 0001_initial.sql — programgarden_hanbit 초기 스키마 (M0 골격)
-- 계획서 §2 핵심 테이블. 모두 CREATE TABLE IF NOT EXISTS.
-- 컬럼은 합리적 수준의 M0 골격 — 정교한 제약/정규화는 M1+에서 확장.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 계좌 ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_no      TEXT NOT NULL UNIQUE,
    market          TEXT NOT NULL,                 -- korea_stock / overseas_stock / overseas_futureoption
    trading_mode    TEXT NOT NULL,                 -- live / paper
    currency        TEXT,
    label           TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_accounts_market ON accounts (market);

-- 종목/상품 ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS instruments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,                 -- 시장 내 식별자
    market          TEXT NOT NULL,
    exchange        TEXT,                          -- KRX / NASDAQ / HKEX 등
    name            TEXT,
    asset_type      TEXT,                          -- stock / future / option
    currency        TEXT,
    meta_json       TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (market, symbol)
);
CREATE INDEX IF NOT EXISTS ix_instruments_market ON instruments (market);

-- 주문 ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,          -- 중복주문 방지
    account_id      INTEGER REFERENCES accounts (id),
    instrument_id   INTEGER REFERENCES instruments (id),
    market          TEXT NOT NULL,
    trading_mode    TEXT NOT NULL,                 -- live / paper
    side            TEXT NOT NULL,                 -- buy / sell
    order_type      TEXT NOT NULL,                 -- market / limit 등
    qty             REAL NOT NULL,
    price           REAL,
    status          TEXT NOT NULL DEFAULT 'new',   -- new / accepted / filled / canceled / rejected
    broker_order_id TEXT,
    strategy_id     INTEGER REFERENCES strategies (id),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_orders_account ON orders (account_id);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders (status);
CREATE INDEX IF NOT EXISTS ix_orders_market ON orders (market);

-- 체결 ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL REFERENCES orders (id),
    broker_fill_id  TEXT,
    qty             REAL NOT NULL,
    price           REAL NOT NULL,
    fee             REAL DEFAULT 0,
    filled_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_fills_order ON fills (order_id);

-- 포지션 -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL REFERENCES accounts (id),
    instrument_id   INTEGER NOT NULL REFERENCES instruments (id),
    qty             REAL NOT NULL DEFAULT 0,
    avg_price       REAL,
    realized_pnl    REAL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (account_id, instrument_id)
);

-- 전략 ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'stopped', -- running / stopped / paused
    market          TEXT,
    params_json     TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- 자본 배분 ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS allocations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER NOT NULL REFERENCES strategies (id),
    account_id      INTEGER REFERENCES accounts (id),
    weight          REAL NOT NULL DEFAULT 0,        -- 0..1
    max_notional    REAL,
    currency        TEXT,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (strategy_id, account_id)
);

-- 위험 한도 ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_limits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope           TEXT NOT NULL,                 -- global / market / account / strategy
    scope_ref       TEXT,                          -- scope 식별자
    limit_type      TEXT NOT NULL,                 -- max_order / max_daily_loss / max_position 등
    value           REAL NOT NULL,
    currency        TEXT,
    enabled         INTEGER NOT NULL DEFAULT 1,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_risk_limits_scope ON risk_limits (scope, scope_ref);

-- 위험 이벤트 --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,                 -- breach / warn / kill_switch 등
    severity        TEXT NOT NULL DEFAULT 'info',  -- info / warn / critical
    scope           TEXT,
    scope_ref       TEXT,
    message         TEXT,
    detail_json     TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_risk_events_created ON risk_events (created_at);

-- 시그널 -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER REFERENCES strategies (id),
    instrument_id   INTEGER REFERENCES instruments (id),
    signal_type     TEXT NOT NULL,                 -- buy / sell / rebalance 등
    strength        REAL,
    payload_json    TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_signals_strategy ON signals (strategy_id);

-- 감사 로그 ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    actor           TEXT,                          -- system / user / strategy
    action          TEXT NOT NULL,
    target          TEXT,
    detail_json     TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_audit_log_created ON audit_log (created_at);
