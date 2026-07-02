"use client";

/**
 * Positions — holdings & P&L (route: /positions).
 *
 * Tabs map to (bucket, market): KR/OS live in the `live` bucket, FUT in `paper`.
 * Data comes from /portfolio/positions?bucket=…, filtered by market. Values are
 * shown in each position's own currency; a ₩-converted total is a labelled
 * reference row only. "청산" opens the shared order modal (FUT paper only — KR/OS
 * is M4). Clicking a row switches the Charts symbol.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Card, Empty, ErrorState, Loading, Pnl } from "@/components/ui";
import { ModeBadge } from "@/components/ModeBadge";
import { usePositions } from "@/lib/query/hooks";
import { useOrderTicket } from "@/lib/order-ticket/store";
import type { Bucket, Market, PositionRow, TradeMode } from "@/lib/api/types";
import { fmtMoney, fmtPct } from "@/lib/format";

interface Tab {
  key: string;
  label: string;
  market: Market;
  bucket: Bucket;
  mode: TradeMode;
  ccy: string;
}

const TABS: Tab[] = [
  { key: "KR", label: "국내주식 KR", market: "korea_stock", bucket: "live", mode: "live", ccy: "KRW" },
  { key: "OS", label: "해외주식 OS", market: "overseas_stock", bucket: "live", mode: "live", ccy: "USD" },
  { key: "FUT", label: "해외선물 FUT", market: "overseas_futureoption", bucket: "paper", mode: "paper", ccy: "HKD" },
];

const mult = (p: PositionRow) => p.multiplier ?? 1;
const evalCcy = (p: PositionRow) => p.qty * (p.current_price ?? 0) * mult(p);
const buyCcy = (p: PositionRow) => p.qty * p.avg_price * mult(p);

export function PositionsView() {
  const [activeKey, setActiveKey] = useState("OS");
  const tab = TABS.find((t) => t.key === activeKey)!;
  const router = useRouter();
  const openTicket = useOrderTicket((s) => s.open);

  const q = usePositions(tab.bucket);
  const rows = (q.data?.positions ?? []).filter((p) => p.market === tab.market);

  // 현재가 없는 행은 평가·손익 합계에서 제외한다 — `current_price ?? 0` 로 조용히
  // 0 을 더하면 합계가 틀려진다. 매입(원가)은 항상 알 수 있으므로 전체 행으로 합산.
  const priced = rows.filter((p) => p.current_price != null);
  const unpriced = rows.length - priced.length;
  const totalBuy = rows.reduce((s, p) => s + buyCcy(p), 0);
  const totalEval = priced.reduce((s, p) => s + evalCcy(p), 0);
  const totalPnl = priced.reduce((s, p) => s + (p.pnl_amount ?? 0), 0);
  const totalEvalKrw = priced.reduce((s, p) => s + (p.eval_krw ?? 0), 0);

  function liquidate(p: PositionRow) {
    openTicket({
      kind: "liquidate",
      title: `${p.symbol} 청산`,
      market: tab.market,
      mode: tab.mode,
      symbol: p.symbol,
      side: p.position_side === "long" ? "sell" : "buy",
      orderType: "limit",
      qty: p.qty,
      price: p.current_price,
      intent: "exit",
      multiplier: mult(p),
      exchange: tab.market === "overseas_futureoption" ? "HKEX" : undefined,
    });
  }

  return (
    <div className="space-y-3">
      {/* Market tabs */}
      <div className="flex flex-wrap items-center gap-2">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setActiveKey(t.key)}
            className={`rounded border px-3 py-1.5 text-sm transition-colors ${
              activeKey === t.key
                ? "border-accent bg-surface-2 font-semibold text-foreground"
                : "border-border text-muted hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
        <span className="ml-auto text-xs text-muted">통화 {tab.ccy}</span>
        <button
          type="button"
          onClick={() => q.refetch()}
          className="rounded border border-border px-2 py-1 text-xs text-muted hover:text-foreground"
        >
          ↻ 새로고침
        </button>
      </div>

      <Card
        title={
          <span className="flex items-center gap-2">
            <ModeBadge mode={tab.mode} short={tab.key} /> 시장가치 통화 = {tab.ccy} · 보유 {rows.length}종목
          </span>
        }
      >
        {q.isLoading ? (
          <Loading />
        ) : q.error ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        ) : rows.length === 0 ? (
          <Empty label={`${tab.label} 보유 포지션이 없습니다`} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted">
                  <th className="px-2 py-2">symbol</th>
                  <th className="px-2 py-2 text-right">qty</th>
                  <th className="px-2 py-2 text-right">buy_price</th>
                  <th className="px-2 py-2 text-right">cur_price</th>
                  <th className="px-2 py-2 text-right">pnl</th>
                  <th className="px-2 py-2 text-right">pnl_%</th>
                  <th className="px-2 py-2 text-right">평가({tab.ccy})</th>
                  <th className="px-2 py-2 text-right">액션</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr
                    key={p.id}
                    onClick={() =>
                      router.push(`/charts?market=${p.market}&symbol=${encodeURIComponent(p.symbol)}`)
                    }
                    className="cursor-pointer border-b border-border/50 hover:bg-surface-2"
                  >
                    <td className="num px-2 py-2 text-foreground">
                      {p.symbol}
                      {p.position_side === "short" && (
                        <span className="ml-1 text-xs text-down">SHORT</span>
                      )}
                    </td>
                    <td className="num px-2 py-2 text-right">{p.qty}</td>
                    <td className="num px-2 py-2 text-right">{fmtMoney(p.avg_price, tab.ccy)}</td>
                    <td className="num px-2 py-2 text-right">
                      {p.current_price != null ? (
                        fmtMoney(p.current_price, tab.ccy)
                      ) : (
                        <span className="text-paper" title="현재가 시세 없음 — 합계 제외">
                          시세 없음
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-2 text-right">
                      {p.current_price != null ? (
                        <Pnl
                          value={p.pnl_amount ?? 0}
                          text={fmtMoney(p.pnl_amount, tab.ccy, { sign: true })}
                        />
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td
                      className={`num px-2 py-2 text-right ${
                        p.current_price == null
                          ? "text-muted"
                          : (p.pnl_rate ?? 0) >= 0
                            ? "text-up"
                            : "text-down"
                      }`}
                    >
                      {p.current_price != null ? fmtPct(p.pnl_rate, { fromRatio: true }) : "—"}
                    </td>
                    <td className="num px-2 py-2 text-right text-foreground">
                      {p.current_price != null ? fmtMoney(evalCcy(p), tab.ccy) : "—"}
                    </td>
                    <td className="px-2 py-2 text-right">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          liquidate(p);
                        }}
                        className="rounded border border-border px-2 py-0.5 text-xs text-muted hover:text-foreground"
                      >
                        청산
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-border text-xs">
                  <td className="px-2 py-2 text-muted">합계 ({tab.ccy})</td>
                  <td />
                  <td className="num px-2 py-2 text-right text-muted">{fmtMoney(totalBuy, tab.ccy)}</td>
                  <td />
                  <td className="px-2 py-2 text-right">
                    <Pnl value={totalPnl} text={fmtMoney(totalPnl, tab.ccy, { sign: true })} />
                  </td>
                  <td />
                  <td className="num px-2 py-2 text-right text-foreground">{fmtMoney(totalEval, tab.ccy)}</td>
                  <td />
                </tr>
                <tr className="text-xs text-muted">
                  <td className="px-2 py-1" colSpan={6}>
                    ₩ 환산 평가 합계 ★별도★ (참고용)
                  </td>
                  <td className="num px-2 py-1 text-right" colSpan={2}>
                    ≈ {fmtMoney(totalEvalKrw, "KRW")}
                  </td>
                </tr>
                {unpriced > 0 && (
                  <tr className="text-xs text-paper">
                    <td className="px-2 py-1" colSpan={8}>
                      ⚠ {unpriced}개 종목은 현재가 시세가 없어 평가·손익 합계에서 제외(매입가만 반영)
                    </td>
                  </tr>
                )}
              </tfoot>
            </table>
          </div>
        )}
        <p className="mt-3 text-xs text-muted">
          ⓘ 행 클릭 → Charts 심볼 전환 · 청산은 모의(FUT)만 실동작 · 🔴LIVE(KR/OS)는 M4까지 비활성
        </p>
      </Card>
    </div>
  );
}
