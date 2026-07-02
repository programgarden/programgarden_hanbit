-- 0002_m2_paper_orders.sql — M2 해외선물 HKEX 모의 주문 파이프라인 (paper-only)
-- 설계: docs/M2_PLAN.md §2. 0001(M0 골격) 위에 additive (ALTER ADD COLUMN + 신규 테이블).
-- SQLite 규칙: ADD COLUMN 은 상수 default 만 / UNIQUE 는 CREATE UNIQUE INDEX 로만 도입.
-- ⚠ executescript 는 시작 시 암묵 COMMIT — 본 파일이 중간에서 실패하면 일부 컬럼만 남고
--   schema_migrations 미기록 → 재기동 시 'duplicate column' 으로 막힌다. dev DB 는 실데이터
--   없으므로 복구 = `server/hanbit.db` 삭제 후 재기동 (STATUS.md/M2_PLAN.md §2 참조).

PRAGMA foreign_keys=ON;

-- orders 확장 (상태머신/정정취소/체결집계/reconcile) ----------------------
ALTER TABLE orders ADD COLUMN exchange          TEXT;
ALTER TABLE orders ADD COLUMN currency          TEXT;
ALTER TABLE orders ADD COLUMN position_effect   TEXT;            -- open / close
ALTER TABLE orders ADD COLUMN tr_code           TEXT;            -- CIDBT00100/00900/01000
ALTER TABLE orders ADD COLUMN broker_org_ord_no TEXT;            -- 정정/취소 원주문번호(OvrsFutsOrgOrdNo)
ALTER TABLE orders ADD COLUMN filled_qty        REAL NOT NULL DEFAULT 0;
ALTER TABLE orders ADD COLUMN remaining_qty     REAL;
ALTER TABLE orders ADD COLUMN avg_fill_price    REAL;
ALTER TABLE orders ADD COLUMN reject_reason     TEXT;
ALTER TABLE orders ADD COLUMN rsp_cd            TEXT;
ALTER TABLE orders ADD COLUMN error_msg         TEXT;
ALTER TABLE orders ADD COLUMN parent_order_id   INTEGER REFERENCES orders (id);
ALTER TABLE orders ADD COLUMN relation          TEXT NOT NULL DEFAULT 'new';  -- new/modify/cancel
ALTER TABLE orders ADD COLUMN reconcile_key     TEXT;
ALTER TABLE orders ADD COLUMN submitted_at      TEXT;
ALTER TABLE orders ADD COLUMN accepted_at       TEXT;
ALTER TABLE orders ADD COLUMN terminal_at       TEXT;

-- broker_order_id(=OvrsFutsOrdNo) 계좌단위 유일. account_id 비-NULL 전제(아래 시드).
CREATE UNIQUE INDEX IF NOT EXISTS ix_orders_broker
    ON orders (account_id, broker_order_id) WHERE broker_order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_orders_parent ON orders (parent_order_id);

-- fills 확장 (체결 멱등 적재) ----------------------------------------------
ALTER TABLE fills ADD COLUMN broker_ord_no   TEXT;
ALTER TABLE fills ADD COLUMN exec_qty        REAL;
ALTER TABLE fills ADD COLUMN exec_price      REAL;
ALTER TABLE fills ADD COLUMN remaining_qty   REAL;
ALTER TABLE fills ADD COLUMN ord_status_code TEXT;
ALTER TABLE fills ADD COLUMN origin          TEXT NOT NULL DEFAULT 'reconcile';  -- sc_event / reconcile
ALTER TABLE fills ADD COLUMN event_seq       TEXT;
ALTER TABLE fills ADD COLUMN raw_json        TEXT;

-- 체결 멱등: (order_id, event_seq) 유일. event_seq 는 앱이 항상 비-NULL 로 채운다
-- (recon:'+OvrsFutsExecNo / tc:'+seq). NULL 은 SQLite 에서 distinct 라 dedup 안 됨.
CREATE UNIQUE INDEX IF NOT EXISTS ix_fills_event ON fills (order_id, event_seq);
CREATE INDEX IF NOT EXISTS ix_fills_broker ON fills (broker_ord_no);

-- 주문 상태 전이 이력 (append-only) ---------------------------------------
CREATE TABLE IF NOT EXISTS order_state_transitions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders (id),
    from_state  TEXT,
    to_state    TEXT NOT NULL,
    trigger     TEXT NOT NULL,                 -- tr_response / reconcile / watchdog / manual
    event_ref   TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_ost_order ON order_state_transitions (order_id, created_at);

-- reconciliation 실행 감사 -----------------------------------------------
CREATE TABLE IF NOT EXISTS reconcile_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope           TEXT,
    started_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at     TEXT,
    diffs_found     INTEGER NOT NULL DEFAULT 0,
    diffs_resolved  INTEGER NOT NULL DEFAULT 0,
    unresolved      INTEGER NOT NULL DEFAULT 0,
    detail_json     TEXT
);

-- 킬스위치 / halt 상태 영속 ----------------------------------------------
CREATE TABLE IF NOT EXISTS trading_halt (
    scope       TEXT PRIMARY KEY,              -- 'global' / 'overseas_futureoption'
    state       TEXT NOT NULL DEFAULT 'active',-- active / halted / killed
    reason      TEXT,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- M2 최소 메트릭 카운터 --------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics_counter (
    name        TEXT PRIMARY KEY,
    value       INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- instruments 확장 (o3101 마스터 → 계약메타 + HKEX 화이트리스트) ----------
ALTER TABLE instruments ADD COLUMN multiplier    REAL;
ALTER TABLE instruments ADD COLUMN tick_size     REAL;
ALTER TABLE instruments ADD COLUMN tick_value    REAL;
ALTER TABLE instruments ADD COLUMN init_margin   REAL;
ALTER TABLE instruments ADD COLUMN maint_margin  REAL;
ALTER TABLE instruments ADD COLUMN due_yymm      TEXT;
ALTER TABLE instruments ADD COLUMN trading_start TEXT;
ALTER TABLE instruments ADD COLUMN trading_end   TEXT;
ALTER TABLE instruments ADD COLUMN whitelisted   INTEGER NOT NULL DEFAULT 0;

-- 시드 -------------------------------------------------------------------
-- 해외선물 모의 내부 계좌 앵커 (라이브러리가 실계좌를 세션에서 자동주입 — 이 행은
-- 내부 FK/유니크 앵커. account_no 는 placeholder, 실제 계좌번호 저장/출력 금지).
INSERT OR IGNORE INTO accounts (account_no, market, trading_mode, currency, label)
VALUES ('HANBIT-PAPER-FUT', 'overseas_futureoption', 'paper', NULL, '해외선물 모의(HKEX)');

-- PAPER_FUT 위험 한도(교육용 과대주문 방지). 통화/승수 미확정 → 보수 placeholder.
INSERT OR IGNORE INTO risk_limits (scope, scope_ref, limit_type, value, currency, enabled)
VALUES
  ('market', 'overseas_futureoption', 'max_contracts_per_order', 10,      NULL, 1),
  ('market', 'overseas_futureoption', 'max_open_orders',         20,      NULL, 1),
  ('market', 'overseas_futureoption', 'bucket_notional_cap',     1000000, NULL, 1),
  ('market', 'overseas_futureoption', 'order_ack_timeout_s',     30,      NULL, 1);

-- 킬스위치 기본 active(미차단).
INSERT OR IGNORE INTO trading_halt (scope, state) VALUES
  ('global', 'active'),
  ('overseas_futureoption', 'active');

-- 메트릭 0 초기화.
INSERT OR IGNORE INTO metrics_counter (name, value) VALUES
  ('orders_placed', 0),
  ('orders_rejected', 0),
  ('orders_filled', 0),
  ('reconcile_diffs', 0);
