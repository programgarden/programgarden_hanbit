/**
 * Wire types — TypeScript mirrors of the backend (FastAPI) response shapes.
 *
 * Single source of truth on the server side: app/models/{schemas,dto,order_dto,
 * portfolio_dto}.py + migrations/000{1,2,3}*.sql. These interfaces only model
 * what the dashboard actually consumes; they are intentionally permissive
 * (lots of `| null`) because rows come straight from SQLite where most columns
 * are nullable.
 *
 * Every REST route wraps its payload in a success/failure/stub envelope
 * (schemas.py). See `client.ts` for how the envelope is unwrapped.
 */

import type { TradeMode } from "@/lib/modes";

export type { TradeMode };

/** Backend market keys. */
export type Market =
  | "korea_stock"
  | "overseas_stock"
  | "overseas_futureoption";

/** Isolation buckets: live = KR+OS (real money), paper = FUT. Never summed. */
export type Bucket = "live" | "paper";

export type Side = "buy" | "sell";
export type OrderTypeT = "market" | "limit";
export type IntentKind = "entry" | "exit";

export type OrderStatus =
  | "approved"
  | "submitted"
  | "in_doubt"
  | "accepted"
  | "partially_filled"
  | "filled"
  | "rejected"
  | "canceled"
  | "expired"
  | "quarantined";

// ── Response envelope ────────────────────────────────────────────────────────

export interface ApiSuccess<T> {
  ok: true;
  data: T;
  server_time: string;
  note?: string;
}

export interface ApiFailure {
  ok: false;
  error: { code: string; message: string; detail?: unknown };
  server_time: string;
}

export type ApiEnvelope<T> = ApiSuccess<T> | ApiFailure;

// ── Market data ──────────────────────────────────────────────────────────────

export interface Quote {
  symbol: string;
  market: string;
  price: number;
  prev_close: number | null;
  change: number | null;
  change_rate: number | null;
  volume: number | null;
  ts: string | null;
}

/** OHLCV candle. Note the low is serialized under the alias `l`. */
export interface Candle {
  date: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface OhlcvResponse {
  market: string;
  symbol: string;
  period: string;
  candles: Candle[];
}

// ── Orders ───────────────────────────────────────────────────────────────────

/** A row of the `orders` table (superset of order_dto.OpenOrder). */
export interface OrderRow {
  id: number;
  idempotency_key: string;
  account_id: number | null;
  instrument_id: number | null;
  market: string;
  trading_mode: TradeMode;
  side: Side;
  order_type: OrderTypeT;
  qty: number;
  price: number | null;
  status: OrderStatus;
  broker_order_id: string | null;
  strategy_id: number | null;
  created_at: string;
  updated_at: string;
  exchange: string | null;
  currency: string | null;
  position_effect: "open" | "close" | null;
  tr_code: string | null;
  broker_org_ord_no: string | null;
  filled_qty: number;
  remaining_qty: number;
  avg_fill_price: number | null;
  reject_reason: string | null;
  rsp_cd: string | null;
  error_msg: string | null;
  parent_order_id: number | null;
  relation: "new" | "modify" | "cancel";
  reconcile_key: string | null;
  submitted_at: string | null;
  accepted_at: string | null;
  terminal_at: string | null;
}

export interface OrdersResponse {
  orders: OrderRow[];
}

/** One orderable symbol from GET /orders/whitelist (FUT = HKEX whitelist). */
export interface WhitelistSymbol {
  symbol: string;
  name: string | null;
  exchange: string | null;
  /** Contract multiplier, parsed from instruments.meta_json (null if unknown). */
  multiplier: number | null;
}

export interface WhitelistResponse {
  market: string;
  symbols: WhitelistSymbol[];
}

export interface RiskDecision {
  result: "pass" | "warn" | "reject";
  reasons: string[];
  adjusted_qty: number | null;
  reclassified_entry: boolean;
}

/** POST /orders/quote — risk-gate dry run + a single-use confirm token. */
export interface QuoteResponse {
  decision: RiskDecision;
  /** null when the decision is a reject (no token issued). */
  confirm_token: string | null;
}

export interface OrderAck {
  ok: boolean;
  broker_ord_no: string | null;
  rsp_cd: string | null;
  rsp_msg: string | null;
  error_msg: string | null;
  status_code: number | null;
}

/** POST /orders/commit — accepted path. (Reject path → 422 ORDER_NOT_ACCEPTED.) */
export interface CommitResponse {
  ok: boolean;
  order?: OrderRow;
  ack?: OrderAck;
  idempotent?: boolean;
  in_doubt?: boolean;
  decision?: RiskDecision;
}

/** Request body for /orders/quote and /orders/commit (CommitBody). */
export interface OrderIntentBody {
  market?: Market;
  symbol: string;
  side: Side;
  order_type?: OrderTypeT;
  qty: number;
  price?: number | null;
  exchange?: string;
  due_yymm?: string | null;
  intent?: IntentKind;
  client_order_id?: string | null;
}

export interface AmendBody {
  qty: number;
  price: number;
}

// ── Portfolio / positions / accounts ─────────────────────────────────────────

/** Per-bucket headline KPIs (bucket_kpi table + portfolio_dto). */
export interface BucketKpi {
  bucket: Bucket;
  account_pnl_rate: number | null;
  total_eval_krw: number;
  total_buy_krw: number;
  total_pnl_krw: number;
  position_count: number;
  hhi: number | null;
  norm_hhi: number | null;
  eff_n: number | null;
  top1_weight: number | null;
  currency_hhi: number | null;
  daily_realized_krw: number | null;
  daily_pnl_krw: number | null;
  drawdown_pct: number | null;
  risk_budget_left_krw: number | null;
  halted: boolean;
  by_currency?: Record<string, number>;
  by_market?: Record<string, number>;
  id?: number;
  ts?: string;
}

export interface PortfolioResponse {
  buckets: { live: BucketKpi | null; paper: BucketKpi | null };
  /** Display-only KRW sum across buckets — never a trading figure (§3). */
  totals: {
    total_eval_krw: number;
    total_buy_krw: number;
    total_pnl_krw: number;
    position_count: number;
  };
  totals_note: string;
}

/** A `positions` row joined with instruments.symbol. */
export interface PositionRow {
  id: number;
  account_id: number;
  instrument_id: number;
  qty: number;
  avg_price: number;
  realized_pnl: number | null;
  updated_at: string;
  bucket: Bucket;
  market: string;
  currency: string;
  asset_class: string | null;
  position_side: "long" | "short";
  current_price: number | null;
  pnl_amount: number | null;
  pnl_rate: number | null;
  eval_krw: number | null;
  fx_now: number | null;
  fx_at_buy: number | null;
  fx_estimated: number;
  multiplier: number | null;
  margin_used: number | null;
  pos_updated_at: string | null;
  symbol: string;
}

export interface PositionsResponse {
  bucket: Bucket;
  positions: PositionRow[];
}

export interface BalanceRow {
  account_id: number;
  currency: string;
  deposit: number;
  orderable_amount: number | null;
  margin_total: number | null;
  withdrawable: number | null;
  realized_pnl: number | null;
  exchange_rate: number | null;
  updated_at: string;
}

export interface AccountRow {
  id: number;
  account_no: string;
  market: string;
  trading_mode: TradeMode;
  currency: string;
  label: string | null;
  created_at: string;
  balances: BalanceRow[];
}

export interface AccountsResponse {
  accounts: AccountRow[];
}

// ── Risk ─────────────────────────────────────────────────────────────────────

export type Severity = "info" | "warn" | "critical";

export interface RiskEventRow {
  id: number;
  event_type: string;
  severity: Severity;
  scope: string | null;
  scope_ref: string | null;
  message: string;
  detail_json: string | null;
  created_at: string;
}

export interface RiskEventsResponse {
  events: RiskEventRow[];
}

export interface RiskLimitsResponse {
  limits: Record<string, number>;
  halt: { global: string; overseas_futureoption: string };
}

export interface DailyLoss {
  day_start_realized_krw: number | null;
  day_start_unrealized_krw: number | null;
  day_start_equity_krw: number | null;
  daily_notional_used_krw: number | null;
  last_reset_day: string | null;
  now_realized_krw: number | null;
  now_unrealized_krw: number | null;
  realized_loss_krw: number | null;
  eval_loss_krw: number | null;
  max_daily_loss_realized: number | null;
  max_daily_loss_eval: number | null;
}

export type BucketHaltState = "active" | "halted_daily" | "killed";

export interface BucketHalt {
  bucket: Bucket;
  state: BucketHaltState;
  kill_switch: "active" | "killed";
  daily_loss_state: "active" | "halted_daily";
  daily_loss?: DailyLoss;
}

export interface HaltStateResponse {
  buckets: { live: BucketHalt; paper: BucketHalt };
}

/** POST /risk/killswitch — engage(L1/L2)/release. */
export interface KillSwitchBody {
  scope?: string;
  action: "engage" | "release";
  level?: 1 | 2;
  confirm_token?: string | null;
}

export interface KillSwitchResponse {
  scope: string;
  state?: string;
  level?: number;
  requires_confirm?: boolean;
  confirm_token?: string;
  warning?: string;
  canceled?: number;
  [k: string]: unknown;
}

// ── System ───────────────────────────────────────────────────────────────────

export type EngineState = "READ_ONLY" | "RECONCILING" | "ACTIVE";

export interface HealthResponse {
  status: string;
  mode: string;
  engine_state: EngineState;
  allow_live: boolean;
  realtime_fills: boolean;
  sessions: Record<string, unknown | null>;
  milestone: string;
}

export interface ClockResponse {
  server_time: string;
  market_sessions: Record<string, { state: string; note?: string }>;
}

export interface MetricsResponse {
  metrics: Record<string, number>;
}

export interface QuarantineResponse {
  orders: OrderRow[];
  count: number;
}
