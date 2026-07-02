/**
 * React Query hooks — the data layer every screen uses.
 *
 * Reads are `useQuery`; the WebSocket invalidates them on live events. Because
 * the portfolio_snapshot live push is M4-deferred, the portfolio/positions/
 * orders/accounts queries also poll on a modest interval so the dashboard stays
 * fresh without it. Writes are `useMutation` and invalidate the affected caches
 * on success.
 */

"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";
import * as api from "@/lib/api/endpoints";
import type {
  AmendBody,
  Bucket,
  KillSwitchBody,
  Market,
  OrderIntentBody,
} from "@/lib/api/types";
import { qk } from "./keys";

/** Poll interval for snapshot-style data (no live push at M3). */
const POLL = 10_000;

// ── System ───────────────────────────────────────────────────────────────────
export const useHealth = () =>
  useQuery({ queryKey: qk.health, queryFn: api.getHealth, refetchInterval: POLL });

export const useClock = () =>
  useQuery({ queryKey: qk.clock, queryFn: api.getClock, refetchInterval: 30_000 });

export const useMetrics = () =>
  useQuery({ queryKey: qk.metrics, queryFn: api.getMetrics, refetchInterval: POLL });

// ── Portfolio / accounts ─────────────────────────────────────────────────────
export const usePortfolio = () =>
  useQuery({ queryKey: qk.portfolio, queryFn: api.getPortfolio, refetchInterval: POLL });

export const usePositions = (bucket: Bucket) =>
  useQuery({
    queryKey: qk.positions(bucket),
    queryFn: () => api.getPositions(bucket),
    refetchInterval: POLL,
  });

export const useAccounts = () =>
  useQuery({ queryKey: qk.accounts, queryFn: api.getAccounts, refetchInterval: POLL });

// ── Orders ───────────────────────────────────────────────────────────────────
export const useOpenOrders = () =>
  useQuery({ queryKey: qk.ordersOpen, queryFn: api.getOpenOrders, refetchInterval: POLL });

// limit 을 키에 포함해, '더 보기'로 limit 을 키우면 새 쿼리로 재조회된다.
// (WS 무효화는 ["orders"] 접두로 매칭되므로 라이브 갱신은 그대로 동작.)
export const useOrderHistory = (limit = 50) =>
  useQuery({
    queryKey: [...qk.ordersHistory, limit],
    queryFn: () => api.getOrderHistory(limit),
    refetchInterval: POLL,
  });

/** Orderable symbols (FUT = HKEX whitelist) — the new-order form validates against this. */
export const useWhitelist = (market = "overseas_futureoption", enabled = true) =>
  useQuery({
    queryKey: qk.whitelist(market),
    queryFn: () => api.getWhitelist(market),
    enabled,
    staleTime: 5 * 60_000, // whitelist rarely changes within a session
  });

// ── Risk ─────────────────────────────────────────────────────────────────────
export const useRiskLimits = () =>
  useQuery({ queryKey: qk.riskLimits, queryFn: api.getRiskLimits });

export const useRiskEvents = (limit = 50) =>
  useQuery({
    queryKey: qk.riskEvents,
    queryFn: () => api.getRiskEvents(limit),
    refetchInterval: POLL,
  });

export const useHaltState = () =>
  useQuery({ queryKey: qk.haltState, queryFn: api.getHaltState, refetchInterval: POLL });

// ── Market data (charts) ─────────────────────────────────────────────────────
export const useOhlcv = (
  market: Market,
  symbol: string,
  period: "D" | "W" | "M" | "Y" = "D",
  count = 120,
  enabled = true,
) =>
  useQuery({
    queryKey: qk.ohlcv(market, symbol, period, count),
    queryFn: () => api.getOhlcv(market, symbol, period, count),
    enabled: enabled && !!symbol,
    staleTime: 60_000,
  });

export const useQuote = (
  market: Market,
  symbol: string,
  opts?: Partial<UseQueryOptions<Awaited<ReturnType<typeof api.getQuote>>>>,
) =>
  useQuery({
    queryKey: qk.quote(market, symbol),
    queryFn: () => api.getQuote(market, symbol),
    enabled: !!symbol,
    refetchInterval: 5_000, // poll: no live quote topic at M3
    ...opts,
  });

// ── Mutations ────────────────────────────────────────────────────────────────
/** Invalidate the order/portfolio/position caches after a write. */
function useOrderCachesInvalidator() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: qk.orders });
    qc.invalidateQueries({ queryKey: qk.portfolio });
    qc.invalidateQueries({ queryKey: qk.positionsAll });
  };
}

export const useQuoteOrder = () =>
  useMutation({ mutationFn: (body: OrderIntentBody) => api.quoteOrder(body) });

export const useCommitOrder = () => {
  const invalidate = useOrderCachesInvalidator();
  return useMutation({
    mutationFn: (body: OrderIntentBody) => api.commitOrder(body),
    onSuccess: invalidate,
  });
};

export const useAmendOrder = () => {
  const invalidate = useOrderCachesInvalidator();
  return useMutation({
    mutationFn: ({ orderId, body }: { orderId: number; body: AmendBody }) =>
      api.amendOrder(orderId, body),
    onSuccess: invalidate,
  });
};

export const useCancelOrder = () => {
  const invalidate = useOrderCachesInvalidator();
  return useMutation({
    mutationFn: (orderId: number) => api.cancelOrder(orderId),
    onSuccess: invalidate,
  });
};

export const useReconcile = () => {
  const invalidate = useOrderCachesInvalidator();
  return useMutation({
    mutationFn: (marketClosed: boolean) => api.reconcileOrders(marketClosed),
    onSuccess: invalidate,
  });
};

export const useKillSwitch = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: KillSwitchBody) => api.killSwitch(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.risk });
      qc.invalidateQueries({ queryKey: qk.orders });
    },
  });
};
