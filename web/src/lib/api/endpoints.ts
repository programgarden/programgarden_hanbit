/**
 * Typed endpoint functions — one per backend route the dashboard uses.
 *
 * These are thin wrappers over {@link apiGet}/{@link apiPost} that pin the
 * request path and response type. React Query hooks (lib/query/hooks.ts) call
 * these; nothing else should hit `fetch` directly.
 *
 * What the M3 backend actually serves (the rest is M4):
 *   reads  — system, market, orders(open/history), portfolio, accounts, risk
 *   writes — FUT *paper* orders (quote/commit/amend/cancel/reconcile), killswitch
 * KR/OVS order paths return 403 LIVE_DISABLED; strategy routes are M0 stubs.
 */

import { apiGet, apiPost } from "./client";
import type {
  AccountsResponse,
  AmendBody,
  ClockResponse,
  CommitResponse,
  HaltStateResponse,
  HealthResponse,
  KillSwitchBody,
  KillSwitchResponse,
  Market,
  MetricsResponse,
  OhlcvResponse,
  OrderIntentBody,
  OrdersResponse,
  PortfolioResponse,
  PositionsResponse,
  QuarantineResponse,
  Quote,
  QuoteResponse,
  LiveArmingResponse,
  RiskEventsResponse,
  RiskLimitsResponse,
  StrategiesResponse,
  StrategyRunResponse,
  StrategyToggleResponse,
  WhitelistResponse,
} from "./types";

// ── System ───────────────────────────────────────────────────────────────────
export const getHealth = () => apiGet<HealthResponse>("/system/health");
export const getClock = () => apiGet<ClockResponse>("/system/clock");
export const getMetrics = () => apiGet<MetricsResponse>("/system/metrics");
export const getQuarantine = () => apiGet<QuarantineResponse>("/system/quarantine");

// ── Market data (read-only, all markets) ─────────────────────────────────────
export const getQuote = (market: Market, symbol: string) =>
  apiGet<Quote>(`/market/quote?market=${market}&symbol=${encodeURIComponent(symbol)}`);

export const getOhlcv = (
  market: Market,
  symbol: string,
  period: "D" | "W" | "M" | "Y" = "D",
  count = 120,
) =>
  apiGet<OhlcvResponse>(
    `/market/ohlcv?market=${market}&symbol=${encodeURIComponent(symbol)}&period=${period}&count=${count}`,
  );

// ── Orders ───────────────────────────────────────────────────────────────────
export const getOpenOrders = () => apiGet<OrdersResponse>("/orders/open");
export const getOrderHistory = (limit = 50, offset = 0) =>
  apiGet<OrdersResponse>(`/orders/history?limit=${limit}&offset=${offset}`);
/** Orderable symbols the risk gate accepts (FUT = HKEX whitelist). */
export const getWhitelist = (market = "overseas_futureoption") =>
  apiGet<WhitelistResponse>(`/orders/whitelist?market=${market}`);

export const quoteOrder = (body: OrderIntentBody) =>
  apiPost<QuoteResponse>("/orders/quote", body);
export const commitOrder = (body: OrderIntentBody) =>
  apiPost<CommitResponse>("/orders/commit", body);
export const amendOrder = (orderId: number, body: AmendBody) =>
  apiPost<Record<string, unknown>>(`/orders/${orderId}/amend`, body);
export const cancelOrder = (orderId: number) =>
  apiPost<Record<string, unknown>>(`/orders/${orderId}/cancel`);
export const reconcileOrders = (marketClosed = false) =>
  apiPost<Record<string, unknown>>(`/orders/reconcile?market_closed=${marketClosed}`);

// ── Portfolio / accounts ─────────────────────────────────────────────────────
export const getPortfolio = () => apiGet<PortfolioResponse>("/portfolio");
export const getPositions = (bucket: "live" | "paper") =>
  apiGet<PositionsResponse>(`/portfolio/positions?bucket=${bucket}`);
export const getAccounts = () => apiGet<AccountsResponse>("/accounts");

// ── Risk ─────────────────────────────────────────────────────────────────────
export const getRiskLimits = () => apiGet<RiskLimitsResponse>("/risk/limits");
export const getRiskEvents = (limit = 50, offset = 0) =>
  apiGet<RiskEventsResponse>(`/risk/events?limit=${limit}&offset=${offset}`);
export const getHaltState = () => apiGet<HaltStateResponse>("/risk/halt_state");
export const killSwitch = (body: KillSwitchBody) =>
  apiPost<KillSwitchResponse>("/risk/killswitch", body);

// ── Strategies (M5) ───────────────────────────────────────────────────────────
export const getStrategies = () => apiGet<StrategiesResponse>("/strategy");
export const toggleStrategies = (enabled: boolean) =>
  apiPost<StrategyToggleResponse>("/strategy/toggle", { enabled });
export const runStrategies = () => apiPost<StrategyRunResponse>("/strategy/run", {});

// ── Live arming (실거래 무장) ───────────────────────────────────────────────
export const getLiveArming = () => apiGet<LiveArmingResponse>("/system/live-arming");
export const setLiveArming = (armed: boolean, confirm?: string) =>
  apiPost<LiveArmingResponse>("/system/live-arming", { armed, confirm });
