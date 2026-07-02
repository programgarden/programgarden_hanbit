"use client";

/**
 * Orders — working orders & history (route: /orders).
 *
 * Open orders (/orders/open) and history (/orders/history) come from the real
 * backend; live order/fill events arrive over the WebSocket and prepend to the
 * activity strip. The orders table has no symbol column, so symbols are resolved
 * client-side from positions (fallback #id). New/정정/취소 go through the shared
 * order modal — FUT paper is functional; KR/OVS surfaces the M4 notice.
 */

import { useMemo, useState } from "react";
import {
  Card,
  Empty,
  ErrorState,
  Loading,
  PageHeader,
  StatusChip,
  type ChipKind,
} from "@/components/ui";
import { ModeBadge } from "@/components/ModeBadge";
import {
  useOpenOrders,
  useOrderHistory,
  usePositions,
  useReconcile,
} from "@/lib/query/hooks";
import { useOrderTicket } from "@/lib/order-ticket/store";
import { useStream } from "@/lib/ws/store";
import type { Market, OrderRow, OrderStatus } from "@/lib/api/types";
import { fmtMoney, fmtTime } from "@/lib/format";

const MARKET_LABEL: Record<string, string> = {
  korea_stock: "KR",
  overseas_stock: "OS",
  overseas_futureoption: "FUT",
};
const MARKET_CCY: Record<string, string> = {
  korea_stock: "KRW",
  overseas_stock: "USD",
  overseas_futureoption: "HKD",
};
const STATUSES: OrderStatus[] = [
  "accepted",
  "partially_filled",
  "filled",
  "rejected",
  "canceled",
  "expired",
];
/** History page size for the "더 보기" control. Server supports limit/offset. */
const HISTORY_PAGE = 50;

function chipKind(status: OrderStatus): ChipKind {
  switch (status) {
    case "filled":
      return "filled";
    case "partially_filled":
      return "partial";
    case "rejected":
    case "canceled":
    case "quarantined":
      return "rejected";
    case "expired":
      return "timeout";
    default:
      return "accepted";
  }
}

export default function OrdersPage() {
  const [market, setMarket] = useState("all");
  const [status, setStatus] = useState("all");
  const [search, setSearch] = useState("");
  const [historyLimit, setHistoryLimit] = useState(HISTORY_PAGE);

  const open = useOpenOrders();
  const history = useOrderHistory(historyLimit);
  const live = usePositions("live");
  const paper = usePositions("paper");
  const reconcile = useReconcile();
  const openTicket = useOrderTicket((s) => s.open);
  const stream = useStream();

  // instrument_id → symbol, from whatever positions we can see.
  const symMap = useMemo(() => {
    const m = new Map<number, string>();
    for (const p of [...(live.data?.positions ?? []), ...(paper.data?.positions ?? [])]) {
      m.set(p.instrument_id, p.symbol);
    }
    return m;
  }, [live.data, paper.data]);
  const sym = (o: OrderRow) => symMap.get(o.instrument_id ?? -1) ?? `#${o.instrument_id}`;

  const matches = (o: OrderRow) =>
    (market === "all" || o.market === market) &&
    (status === "all" || o.status === status) &&
    (!search ||
      sym(o).toLowerCase().includes(search.toLowerCase()) ||
      (o.broker_order_id ?? "").toLowerCase().includes(search.toLowerCase()));

  const rawOpen = open.data?.orders ?? [];
  const rawHistory = history.data?.orders ?? [];
  const openRows = rawOpen.filter(matches);
  const historyRows = rawHistory.filter(matches);
  const hasFilters = market !== "all" || status !== "all" || search !== "";
  // 받은 행이 limit 만큼 꽉 찼으면 더 있을 수 있다(서버가 limit 까지 잘라서 줌).
  const canLoadMore = rawHistory.length >= historyLimit;
  function resetFilters() {
    setMarket("all");
    setStatus("all");
    setSearch("");
  }

  function newOrder() {
    openTicket({
      kind: "new",
      title: "새 주문 (해외선물 paper)",
      market: "overseas_futureoption",
      mode: "paper",
      symbol: "HBIM26",
      side: "buy",
      orderType: "limit",
      qty: 1,
      price: 11000,
      intent: "entry",
      multiplier: 50,
      exchange: "HKEX",
    });
  }
  function amend(o: OrderRow) {
    openTicket({
      kind: "amend",
      title: `#${o.id} 정정`,
      market: o.market as Market,
      mode: o.trading_mode,
      symbol: sym(o),
      side: o.side,
      orderType: o.order_type,
      qty: o.remaining_qty || o.qty,
      price: o.price,
      intent: "entry",
      orderId: o.id,
    });
  }
  function cancel(o: OrderRow) {
    openTicket({
      kind: "cancel",
      title: `#${o.id} 취소`,
      market: o.market as Market,
      mode: o.trading_mode,
      symbol: sym(o),
      side: o.side,
      orderType: o.order_type,
      qty: o.qty,
      price: o.price,
      intent: "entry",
      orderId: o.id,
    });
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Orders — 주문/체결">
        <button
          type="button"
          onClick={() => reconcile.mutate(false)}
          disabled={reconcile.isPending}
          className="rounded border border-border px-2 py-1 text-xs text-muted hover:text-foreground disabled:opacity-40"
        >
          {reconcile.isPending ? "reconcile…" : "↻ reconcile"}
        </button>
        <button
          type="button"
          onClick={newOrder}
          className="rounded border border-paper/60 bg-paper/10 px-2 py-1 text-xs font-semibold text-paper"
        >
          + 새 주문
        </button>
      </PageHeader>

      {/* filter bar */}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <select
          value={market}
          onChange={(e) => setMarket(e.target.value)}
          className="rounded border border-border bg-surface px-2 py-1 text-xs text-foreground"
        >
          <option value="all">전체 시장</option>
          <option value="korea_stock">KR</option>
          <option value="overseas_stock">OS</option>
          <option value="overseas_futureoption">FUT</option>
        </select>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded border border-border bg-surface px-2 py-1 text-xs text-foreground"
        >
          <option value="all">전체 상태</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="심볼 / OrdNo 검색"
          className="rounded border border-border bg-surface px-2 py-1 text-xs text-foreground"
        />
        {reconcile.isSuccess && <span className="text-xs text-up">reconcile 완료</span>}
      </div>

      {/* open orders */}
      <Card title="열린 주문 (미체결)">
        {open.isLoading ? (
          <Loading />
        ) : open.error ? (
          <ErrorState error={open.error} onRetry={() => open.refetch()} />
        ) : openRows.length === 0 ? (
          <FilterEmpty
            base="열린 주문이 없습니다"
            filtered={rawOpen.length > 0 && hasFilters}
            onReset={resetFilters}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted">
                  <th className="px-2 py-2">OrdNo</th>
                  <th className="px-2 py-2">시장</th>
                  <th className="px-2 py-2">symbol</th>
                  <th className="px-2 py-2">side</th>
                  <th className="px-2 py-2 text-right">qty</th>
                  <th className="px-2 py-2 text-right">price</th>
                  <th className="px-2 py-2 text-right">체결/잔량</th>
                  <th className="px-2 py-2">상태</th>
                  <th className="px-2 py-2 text-right">액션</th>
                </tr>
              </thead>
              <tbody>
                {openRows.map((o) => (
                  <tr key={o.id} className="border-b border-border/50 hover:bg-surface-2">
                    <td className="num px-2 py-2 text-foreground">
                      {o.broker_order_id ?? `#${o.id}`}
                    </td>
                    <td className="px-2 py-2">
                      <ModeBadge mode={o.trading_mode} short={MARKET_LABEL[o.market] ?? o.market} />
                    </td>
                    <td className="num px-2 py-2 text-foreground">{sym(o)}</td>
                    <td className={`px-2 py-2 ${o.side === "buy" ? "text-up" : "text-down"}`}>
                      {o.side === "buy" ? "매수" : "매도"}
                    </td>
                    <td className="num px-2 py-2 text-right">{o.qty}</td>
                    <td className="num px-2 py-2 text-right">
                      {o.price != null ? fmtMoney(o.price, MARKET_CCY[o.market]) : "시장가"}
                    </td>
                    <td className="num px-2 py-2 text-right text-muted">
                      {o.filled_qty}/{o.remaining_qty}
                    </td>
                    <td className="px-2 py-2">
                      <StatusChip kind={chipKind(o.status)} label={o.status} />
                    </td>
                    <td className="px-2 py-2 text-right">
                      <div className="flex justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => amend(o)}
                          className="rounded border border-border px-2 py-0.5 text-xs text-muted hover:text-foreground"
                        >
                          정정
                        </button>
                        <button
                          type="button"
                          onClick={() => cancel(o)}
                          className="rounded border border-border px-2 py-0.5 text-xs text-muted hover:text-foreground"
                        >
                          취소
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* live activity + history timeline */}
      <Card
        title={
          <span className="flex items-center gap-2">
            주문/체결 타임라인
            <span className={stream.status === "open" ? "text-up" : "text-down"}>
              ● WS {stream.status}
            </span>
          </span>
        }
      >
        {(stream.fills.length > 0 || stream.orders.length > 0) && (
          <div className="mb-3 space-y-1 rounded border border-up/30 bg-up/5 p-2 text-xs">
            <div className="text-muted">실시간(WS)</div>
            {stream.fills.slice(0, 5).map((e, i) => (
              <div key={`f${i}`} className="num text-up">
                ▲ fill order#{String((e.data as { order_id?: number })?.order_id ?? "?")} ·{" "}
                {String((e.data as { exec_qty?: number })?.exec_qty ?? "")}@
                {String((e.data as { exec_price?: number })?.exec_price ?? "")} · {fmtTime(e.ts)}
              </div>
            ))}
            {stream.orders.slice(0, 5).map((e, i) => (
              <div key={`o${i}`} className="text-muted">
                ◦ order#{String((e.data as { order_id?: number })?.order_id ?? "?")}{" "}
                {String(
                  (e.data as { state?: string })?.state ??
                    (e.data as { action?: string })?.action ??
                    "",
                )}{" "}
                · {fmtTime(e.ts)}
              </div>
            ))}
          </div>
        )}

        {history.isLoading ? (
          <Loading />
        ) : history.error ? (
          <ErrorState error={history.error} onRetry={() => history.refetch()} />
        ) : historyRows.length === 0 ? (
          <FilterEmpty
            base="주문 내역이 없습니다"
            filtered={rawHistory.length > 0 && hasFilters}
            onReset={resetFilters}
          />
        ) : (
          <>
            <ul className="space-y-1 text-sm">
              {historyRows.map((o) => (
                <li
                  key={o.id}
                  className="flex flex-wrap items-center gap-2 border-b border-border/40 py-1.5"
                >
                  <span className="num text-xs text-muted">{fmtTime(o.created_at)}</span>
                  <StatusChip kind={chipKind(o.status)} label={o.status} />
                  <ModeBadge mode={o.trading_mode} short={MARKET_LABEL[o.market] ?? o.market} />
                  <span className="num text-foreground">{sym(o)}</span>
                  <span className={o.side === "buy" ? "text-up" : "text-down"}>
                    {o.side === "buy" ? "매수" : "매도"} {o.qty}
                  </span>
                  <span className="num text-muted">
                    @{o.price != null ? fmtMoney(o.price, MARKET_CCY[o.market]) : "시장가"}
                  </span>
                  {o.reject_reason && (
                    <span className="text-xs text-down">— {o.reject_reason}</span>
                  )}
                </li>
              ))}
            </ul>
            {canLoadMore && (
              <div className="mt-2 text-center">
                <button
                  type="button"
                  onClick={() => setHistoryLimit((l) => l + HISTORY_PAGE)}
                  disabled={history.isFetching}
                  className="rounded border border-border px-3 py-1 text-xs text-muted hover:text-foreground disabled:opacity-40"
                >
                  {history.isFetching ? "불러오는 중…" : `더 보기 (+${HISTORY_PAGE})`}
                </button>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

/**
 * Empty state that distinguishes "no data at all" from "filters hid everything".
 * The latter offers a one-click reset so a stray filter doesn't look like an
 * empty backend.
 */
function FilterEmpty({
  base,
  filtered,
  onReset,
}: {
  base: string;
  filtered: boolean;
  onReset: () => void;
}) {
  if (!filtered) return <Empty label={base} />;
  return (
    <div className="py-6 text-center text-sm text-muted">
      필터에 맞는 항목이 없습니다
      <button
        type="button"
        onClick={onReset}
        className="ml-2 rounded border border-border px-2 py-0.5 text-xs hover:text-foreground"
      >
        필터 초기화
      </button>
    </div>
  );
}
