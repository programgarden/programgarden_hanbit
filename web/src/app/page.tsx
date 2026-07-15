"use client";

/**
 * Overview — account / portfolio summary (route: /).
 *
 * Wired to the real M3 backend: /portfolio (per-bucket KPIs), /portfolio/
 * positions (both buckets, grouped by currency here), /accounts (deposits),
 * /risk/halt_state, /orders/open. Currency figures are shown per-currency and
 * NEVER summed across markets; the ₩-converted grand total is a clearly-labelled
 * reference box only. Strategy count is wired to the M5 strategy engine (/strategy).
 */

import { Card, ErrorState, Kpi, Loading, Pnl } from "@/components/ui";
import { ModeBadge } from "@/components/ModeBadge";
import {
  useAccounts,
  useHaltState,
  useOpenOrders,
  usePortfolio,
  usePositions,
  useStrategies,
} from "@/lib/query/hooks";
import { useStream } from "@/lib/ws/store";
import type { PositionRow, TradeMode } from "@/lib/api/types";
import { fmtMoney, fmtPct } from "@/lib/format";

const CCY_CARDS: { ccy: string; title: string; mode: TradeMode; cashLabel: string }[] = [
  { ccy: "KRW", title: "₩ 국내주식", mode: "live", cashLabel: "예수금" },
  { ccy: "USD", title: "$ 해외주식", mode: "live", cashLabel: "예수금" },
  { ccy: "HKD", title: "HK$ 해외선물", mode: "paper", cashLabel: "가용증거금" },
];

const mult = (p: PositionRow) => p.multiplier ?? 1;

export default function OverviewPage() {
  const portfolio = usePortfolio();
  const live = usePositions("live");
  const paper = usePositions("paper");
  const accounts = useAccounts();
  const halt = useHaltState();
  const open = useOpenOrders();
  const strategies = useStrategies();
  const ws = useStream();

  const positions = [
    ...(live.data?.positions ?? []),
    ...(paper.data?.positions ?? []),
  ];
  const balances = (accounts.data?.accounts ?? []).flatMap((a) => a.balances);

  function ccyGroup(ccy: string) {
    const ps = positions.filter((p) => p.currency === ccy);
    const evalCcy = ps.reduce((s, p) => s + p.qty * (p.current_price ?? 0) * mult(p), 0);
    const buyCcy = ps.reduce((s, p) => s + p.qty * p.avg_price * mult(p), 0);
    const pnlCcy = ps.reduce((s, p) => s + (p.pnl_amount ?? 0), 0);
    const bal = balances.find((b) => b.currency === ccy);
    return {
      count: ps.length,
      evalCcy,
      buyCcy,
      pnlCcy,
      pnlRate: buyCcy ? pnlCcy / buyCcy : 0,
      cash: bal?.deposit ?? null,
      orderable: bal?.orderable_amount ?? null,
    };
  }

  const liveKpi = portfolio.data?.buckets.live;
  const paperKpi = portfolio.data?.buckets.paper;
  const risk = riskSummary(halt.data?.buckets);

  // A single failed endpoint must never blank the whole page: each section
  // below degrades on its own (loading / inline error / data). That is why
  // there is no top-level `if (error) return <ErrorState/>` guard here — one
  // missing endpoint (e.g. /risk/halt_state) should not hide the rest.
  const posError = live.error ?? paper.error;
  const posLoading = live.isLoading || paper.isLoading;

  return (
    <div className="space-y-6">
      {/* KPI row */}
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi
          label="전략"
          value={
            strategies.error ? "⚠" : `${strategies.data?.strategies.length ?? "—"} 개`
          }
          hint={
            strategies.error
              ? "불러오기 실패"
              : strategies.data?.enabled
                ? "엔진 ON"
                : "엔진 OFF"
          }
        />
        <Kpi
          label="열린 주문"
          value={open.error ? "⚠" : `${open.data?.orders.length ?? "—"} 건`}
          hint={open.error ? "불러오기 실패" : undefined}
        />
        <Kpi
          label="포지션 (live / paper)"
          value={
            posError
              ? "⚠"
              : `${live.data?.positions.length ?? "—"} / ${paper.data?.positions.length ?? "—"}`
          }
          hint={posError ? "불러오기 실패" : "버킷 격리"}
        />
        <Kpi
          label="위험상태"
          value={halt.error ? "⚠" : `${risk.icon} ${risk.label}`}
          hint={halt.error ? "불러오기 실패" : risk.hint}
        />
      </section>

      {/* Currency cards — never summed into a single total */}
      <div>
        <div className="mb-2 text-xs text-muted">
          통화별 집계 (※ 단일 총액 합산 안 함 · 실거래/모의 분리)
          {accounts.error && (
            <span className="ml-2 text-down">· 예수금 불러오기 실패</span>
          )}
        </div>
        {posError ? (
          <ErrorState
            error={posError}
            onRetry={() => {
              live.refetch();
              paper.refetch();
            }}
          />
        ) : posLoading ? (
          <Loading />
        ) : (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
            {CCY_CARDS.map((c) => {
              const g = ccyGroup(c.ccy);
              return (
                <Card
                  key={c.ccy}
                  title={
                    <span className="flex items-center gap-2">
                      {c.title} <ModeBadge mode={c.mode} /> · {g.count}종목
                    </span>
                  }
                >
                  <dl className="space-y-1.5 text-sm">
                    <Row label="총평가" value={fmtMoney(g.evalCcy, c.ccy)} />
                    <Row label="매입" value={fmtMoney(g.buyCcy, c.ccy)} />
                    <div className="flex items-center justify-between">
                      <dt className="text-muted">손익</dt>
                      <dd>
                        <Pnl
                          value={g.pnlCcy}
                          text={`${fmtMoney(g.pnlCcy, c.ccy, { sign: true })} (${fmtPct(g.pnlRate, { fromRatio: true })})`}
                        />
                      </dd>
                    </div>
                    <Row label={c.cashLabel} value={fmtMoney(g.cash, c.ccy)} />
                  </dl>
                </Card>
              );
            })}
          </div>
        )}
      </div>

      {/* live/paper separation — both cards are portfolio-driven */}
      {portfolio.error ? (
        <ErrorState error={portfolio.error} onRetry={() => portfolio.refetch()} />
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Card title="실거래/모의 분리 손익 (₩ 환산, 격리·미합산)">
          <p className="text-xs text-muted">
            🔴LIVE / 🟡PAPER 손익은 물리적으로 분리되며 합산하지 않습니다.
          </p>
          <div className="mt-3 space-y-3 text-sm">
            <div className="rounded border border-live/30 bg-live/5 p-3">
              <ModeBadge mode="live" />
              <div className="num mt-2 text-foreground">
                평가손익 {fmtMoney(liveKpi?.total_pnl_krw ?? null, "KRW", { sign: true })} · 일중 실현{" "}
                {fmtMoney(liveKpi?.daily_realized_krw ?? null, "KRW", { sign: true })}
              </div>
            </div>
            <div className="rounded border border-paper/30 bg-paper/5 p-3">
              <ModeBadge mode="paper" />
              <div className="num mt-2 text-foreground">
                평가손익 {fmtMoney(paperKpi?.total_pnl_krw ?? null, "KRW", { sign: true })} · 일중 실현{" "}
                {fmtMoney(paperKpi?.daily_realized_krw ?? null, "KRW", { sign: true })}{" "}
                <span className="text-muted">(격리·미합산)</span>
              </div>
            </div>
          </div>
        </Card>

        <Card title="기준통화(₩) 환산 총액 ★별도★ (참고용)">
          <div className="num text-2xl font-semibold text-foreground">
            ≈ {fmtMoney(portfolio.data?.totals.total_eval_krw ?? null, "KRW")}
          </div>
          <div className="mt-2 text-xs text-muted">
            평가손익 합 {fmtMoney(portfolio.data?.totals.total_pnl_krw ?? null, "KRW", { sign: true })} ·{" "}
            포지션 {portfolio.data?.totals.position_count ?? "—"}개
          </div>
          <div className="mt-1 text-xs text-muted">
            ※ {portfolio.data?.totals_note ?? "참고용 합산일 뿐 — 통화·버킷 격리 원칙은 유지됩니다."}
          </div>
        </Card>
        </div>
      )}

      {/* WS receive status */}
      <div className="flex items-center gap-3 text-xs text-muted">
        <span className={ws.status === "open" ? "text-up" : "text-down"}>
          ● WS {ws.status}
        </span>
        {ws.gaps > 0 && <span className="text-paper">seq gap {ws.gaps}</span>}
        <span>실시간 PnL push 는 M4 — 현재 10초 폴링으로 갱신</span>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-muted">{label}</dt>
      <dd className="num text-foreground">{value}</dd>
    </div>
  );
}

function riskSummary(
  buckets?: { live: { state: string }; paper: { state: string } },
): { icon: string; label: string; hint: string } {
  if (!buckets) return { icon: "·", label: "—", hint: "" };
  const states = [buckets.live.state, buckets.paper.state];
  const hint = `live ${buckets.live.state} · paper ${buckets.paper.state}`;
  if (states.includes("killed")) return { icon: "🔴", label: "동결(KILL)", hint };
  if (states.includes("halted_daily")) return { icon: "🟡", label: "일일정지", hint };
  return { icon: "🟢", label: "정상", hint };
}
