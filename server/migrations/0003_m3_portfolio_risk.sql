-- 0003_m3_portfolio_risk.sql — M3 포트폴리오 집계 & 위험엔진 (M3a 데이터 모델)
-- 설계: docs/M3_PLAN.md §2. 0001/0002 위에 additive (ALTER ADD COLUMN 상수 default + 신규 테이블).
-- SQLite 규칙: ADD COLUMN 은 상수 default(NULL/0)만 / strftime default 는 CREATE TABLE 안에만 /
--   UNIQUE 는 CREATE (UNIQUE) INDEX 로만.
-- ⚠ executescript 암묵 COMMIT — 중간 실패 시 dev 복구 = `server/hanbit.db` 삭제 후 재기동.
-- 보정(M3_PLAN §1): 금액은 minor-unit 정수가 아니라 Decimal → REAL 저장. FX 는 라이브러리
--   내장(overseas)/미제공(futures) → futures 는 고정환율. (§6 FxRateProvider)

PRAGMA foreign_keys=ON;

-- positions 확장 (버킷·통화·환산·선물 메타) ------------------------------
-- ⚠ 이중 writer 필드 분할(§4.2): 권위(reconcile)=qty/avg_price/margin_used/bucket/market/
--   currency/position_side, 보강(tracker)=current_price/pnl_amount/pnl_rate/fx_now/eval_krw/
--   pos_updated_at. upsert 2메서드의 ON CONFLICT SET 목록이 서로소.
ALTER TABLE positions ADD COLUMN bucket         TEXT;            -- 'live' / 'paper'
ALTER TABLE positions ADD COLUMN market         TEXT;
ALTER TABLE positions ADD COLUMN currency       TEXT;
ALTER TABLE positions ADD COLUMN asset_class    TEXT;
ALTER TABLE positions ADD COLUMN position_side  TEXT;            -- 'long' / 'short'
ALTER TABLE positions ADD COLUMN current_price  REAL;
ALTER TABLE positions ADD COLUMN pnl_amount     REAL;            -- 통화단위 미실현
ALTER TABLE positions ADD COLUMN pnl_rate       REAL;
ALTER TABLE positions ADD COLUMN eval_krw       REAL;            -- KRW 환산 평가액
ALTER TABLE positions ADD COLUMN fx_now         REAL;
ALTER TABLE positions ADD COLUMN fx_at_buy      REAL;            -- 취득시점 환율 고정
ALTER TABLE positions ADD COLUMN fx_estimated   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE positions ADD COLUMN multiplier     REAL;            -- 선물 승수(주식 1)
ALTER TABLE positions ADD COLUMN margin_used    REAL;            -- 선물 증거금(주식 NULL)
ALTER TABLE positions ADD COLUMN pos_updated_at TEXT;            -- 보강(가격) 갱신시각
CREATE INDEX IF NOT EXISTS ix_positions_bucket ON positions (bucket, market);

-- 통화별 잔고 스냅샷 -----------------------------------------------------
CREATE TABLE IF NOT EXISTS balances_snapshot (
    account_id        INTEGER NOT NULL REFERENCES accounts (id),
    currency          TEXT NOT NULL,
    deposit           REAL,
    orderable_amount  REAL,
    margin_total      REAL,
    withdrawable      REAL,
    realized_pnl      REAL,
    exchange_rate     REAL,
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (account_id, currency)
);

-- 버킷 헤드라인 KPI + 집중도 (최신 1행 + 이력 append) --------------------
CREATE TABLE IF NOT EXISTS bucket_kpi (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket               TEXT NOT NULL,
    ts                   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    account_pnl_rate     REAL,
    total_eval_krw       REAL,
    total_buy_krw        REAL,
    total_pnl_krw        REAL,
    position_count       INTEGER,
    hhi                  REAL,
    norm_hhi             REAL,
    eff_n                REAL,
    top1_weight          REAL,
    currency_hhi         REAL,
    daily_realized_krw   REAL,
    daily_pnl_krw        REAL,
    drawdown_pct         REAL,
    risk_budget_left_krw REAL,
    halted               INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_bucket_kpi_bucket_ts ON bucket_kpi (bucket, ts);

-- 환율 캐시 (KRW base) ---------------------------------------------------
CREATE TABLE IF NOT EXISTS fx_rates (
    quote_ccy    TEXT NOT NULL,
    to_krw       REAL NOT NULL,
    source       TEXT,
    fx_estimated INTEGER NOT NULL DEFAULT 0,
    as_of        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (quote_ccy, as_of)
);

-- 버킷별 일일손실 baseline + 일중 사용량 (재기동 복원) -------------------
-- ⚠ 일일손실 halt 권위는 risk_state.halt_state (단일 authority). 킬스위치/수동 halt 는
--   trading_halt(M2) 그대로. 게이트는 두 소스를 함께 본다(§5.3).
CREATE TABLE IF NOT EXISTS risk_state (
    bucket                  TEXT PRIMARY KEY,            -- 'live' / 'paper'
    halt_state              TEXT NOT NULL DEFAULT 'active', -- active / halted_daily / killed
    day_start_realized_krw  REAL,
    day_start_unrealized_krw REAL,
    day_start_equity_krw    REAL,
    daily_notional_used_krw REAL NOT NULL DEFAULT 0,
    last_reset_day          TEXT,                        -- 버킷 거래일 YYYYMMDD(거래소 TZ)
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- 시드 -------------------------------------------------------------------
-- 버킷별 위험상태(paper 만 활성 운용; live 는 M3 read-only/off).
INSERT OR IGNORE INTO risk_state (bucket, halt_state) VALUES
  ('paper', 'active'),
  ('live',  'active');

-- M3a 위험 한도(집중도/FX 캡/일일손실) — KRW 환산 기준, 보수 placeholder.
-- ⚠ RiskLimits.load 가 이 limit_type 들을 판독하도록 확장됨(app/risk/limits.py).
INSERT OR IGNORE INTO risk_limits (scope, scope_ref, limit_type, value, currency, enabled)
VALUES
  ('market', 'overseas_futureoption', 'max_symbol_weight',       0.25,    NULL, 1),
  -- paper 버킷은 단일 시장(overseas_futureoption)·단일 통화(USD) → market/currency 비중은
  -- 구조적으로 항상 100%. 0.6 이면 모든 paper 주문이 거부됨 → 1.0(미적용). market/currency
  -- 분산 캡은 LIVE 버킷(다시장·다통화, M4)에서 의미. (사용자 결정 2026-06-19)
  ('market', 'overseas_futureoption', 'max_market_weight',       1.0,     NULL, 1),
  ('market', 'overseas_futureoption', 'max_currency_weight',     1.0,     NULL, 1),
  ('market', 'overseas_futureoption', 'max_positions',           20,      NULL, 1),
  ('market', 'overseas_futureoption', 'per_order_cap_krw',       3000000, 'KRW', 1),
  ('market', 'overseas_futureoption', 'max_daily_loss_realized', 1000000, 'KRW', 1),
  ('market', 'overseas_futureoption', 'max_daily_loss_eval',     2000000, 'KRW', 1);
