"use client";

/**
 * Charts — candlestick view (route: /charts).
 *
 * Historical candles come from the real /market/ohlcv endpoint and render with
 * lightweight-charts; the header quote polls /market/quote. The watchlist is the
 * seeded instrument set (FUT limited to the HKEX whitelist). Intraday (1m/5m),
 * the order book and the live tick stream have no M3 backend → marked M4.
 *
 * Note: market data needs an authenticated broker session on the server; on a
 * closed market the request may error, which is surfaced rather than faked.
 */

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  CandlestickSeries,
  createChart,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { Card, DeferredBadge, ErrorState, Loading } from "@/components/ui";
import { ModeBadge } from "@/components/ModeBadge";
import { useOhlcv, useQuote } from "@/lib/query/hooks";
import type { Candle, Market, TradeMode } from "@/lib/api/types";
import { fmtMoney, fmtPct } from "@/lib/format";

interface WatchItem {
  market: Market;
  mode: TradeMode;
  symbol: string;
  name: string;
  ccy: string;
  short: string;
}

const WATCHLIST: WatchItem[] = [
  { market: "korea_stock", mode: "live", symbol: "005930", name: "삼성전자", ccy: "KRW", short: "KR" },
  { market: "korea_stock", mode: "live", symbol: "035720", name: "카카오", ccy: "KRW", short: "KR" },
  { market: "overseas_stock", mode: "live", symbol: "AAPL", name: "Apple", ccy: "USD", short: "OS" },
  { market: "overseas_stock", mode: "live", symbol: "NVDA", name: "NVIDIA", ccy: "USD", short: "OS" },
  { market: "overseas_futureoption", mode: "paper", symbol: "HBIM26", name: "HS Biotech 26.06", ccy: "HKD", short: "FUT" },
];

const PERIODS: { key: "D" | "W" | "M"; label: string }[] = [
  { key: "D", label: "1D" },
  { key: "W", label: "1W" },
  { key: "M", label: "1M" },
];

/** "20260615" → lightweight-charts business-day string "2026-06-15". */
function toTime(yyyymmdd: string): Time {
  if (yyyymmdd.length === 8) {
    return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}` as Time;
  }
  return yyyymmdd as Time;
}

export function ChartsView() {
  const params = useSearchParams();
  const urlMarket = params.get("market") as Market | null;
  const urlSymbol = params.get("symbol");

  const initial =
    WATCHLIST.find((w) => w.market === urlMarket && w.symbol === urlSymbol) ?? WATCHLIST[2];
  const [sel, setSel] = useState<WatchItem>(initial);
  const [period, setPeriod] = useState<"D" | "W" | "M">("D");

  const ohlcv = useOhlcv(sel.market, sel.symbol, period, 120);
  const quote = useQuote(sel.market, sel.symbol);

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[200px_1fr_220px]">
      {/* left: watchlist */}
      <Card title="워치리스트" className="overflow-y-auto">
        <ul className="space-y-1 text-sm">
          {WATCHLIST.map((w) => {
            const active = w.market === sel.market && w.symbol === sel.symbol;
            return (
              <li key={`${w.market}:${w.symbol}`}>
                <button
                  type="button"
                  onClick={() => setSel(w)}
                  className={`flex w-full items-center justify-between rounded border px-2 py-1.5 text-left ${
                    active
                      ? "border-accent bg-surface-2 text-foreground"
                      : "border-transparent text-muted hover:bg-surface-2 hover:text-foreground"
                  }`}
                >
                  <span className="num">{w.symbol}</span>
                  <ModeBadge mode={w.mode} short={w.short} />
                </button>
              </li>
            );
          })}
        </ul>
        <p className="mt-2 text-xs text-muted">FUT 는 HKEX 화이트리스트만 노출</p>
      </Card>

      {/* center: chart */}
      <Card
        title={
          <span className="flex flex-wrap items-center gap-2">
            <span className="num text-sm text-foreground">{sel.symbol}</span>
            <span className="text-muted">{sel.name}</span>
            <ModeBadge mode={sel.mode} />
            {quote.data && (
              <span
                className={`num text-sm ${(quote.data.change ?? 0) >= 0 ? "text-up" : "text-down"}`}
              >
                {(quote.data.change ?? 0) >= 0 ? "▲" : "▼"} {fmtPct(quote.data.change_rate)}
              </span>
            )}
            <span className="ml-auto flex gap-1">
              {PERIODS.map((p) => (
                <button
                  key={p.key}
                  type="button"
                  onClick={() => setPeriod(p.key)}
                  className={`rounded border px-2 py-0.5 text-xs ${
                    period === p.key
                      ? "border-accent text-foreground"
                      : "border-border text-muted hover:text-foreground"
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </span>
          </span>
        }
      >
        {ohlcv.isLoading ? (
          <Loading label="OHLCV 불러오는 중…" />
        ) : ohlcv.error ? (
          <div className="space-y-2">
            <ErrorState error={ohlcv.error} onRetry={() => ohlcv.refetch()} />
            <p className="text-xs text-muted">
              ⓘ 시세는 서버의 인증된 브로커 세션이 필요합니다(휴장/미인증 시 실패). 분봉(1m/5m)·실시간
              틱은 <DeferredBadge note="실시간 시세 스트림은 M4" />.
            </p>
          </div>
        ) : (
          <CandleChart candles={ohlcv.data?.candles ?? []} />
        )}
        <p className="mt-2 text-xs text-muted">
          오버레이(진입/청산 마커·SL/TP 라인)·분봉·실시간 추종 ● 는 <DeferredBadge note="M4" />
        </p>
      </Card>

      {/* right: book / ticks (M4) */}
      <Card title="호가 · 체결 틱">
        <div className="space-y-2 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-muted">현재가</span>
            <span className="num text-foreground">
              {quote.data ? fmtMoney(quote.data.price, sel.ccy) : "—"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted">등락</span>
            <span className={`num ${(quote.data?.change ?? 0) >= 0 ? "text-up" : "text-down"}`}>
              {quote.data?.change_rate != null ? fmtPct(quote.data.change_rate) : "—"}
            </span>
          </div>
          <div className="rounded border border-border bg-surface-2 p-3 text-center text-xs text-muted">
            호가창(BID/ASK) · 실시간 체결 틱 스트림
            <div className="mt-1">
              <DeferredBadge note="호가/틱 WS 토픽은 M4" />
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}

function CandleChart({ candles }: { candles: Candle[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  // Create the chart once.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Pull colors from the active theme tokens so the chart follows the theme.
    const css = getComputedStyle(document.documentElement);
    const tok = (name: string, fallback: string) =>
      css.getPropertyValue(name).trim() || fallback;
    const cMuted = tok("--muted", "#5f6b7a");
    const cBorder = tok("--border", "#e2e5ea");
    const cUp = tok("--up", "#16a34a");
    const cDown = tok("--down", "#dc2626");
    const cAccent = tok("--accent", "#2563eb");
    const chart = createChart(el, {
      width: el.clientWidth,
      height: 360,
      layout: {
        background: { color: "transparent" },
        textColor: cMuted,
        fontFamily: "var(--font-geist-mono), monospace",
      },
      grid: {
        vertLines: { color: cBorder },
        horzLines: { color: cBorder },
      },
      rightPriceScale: { borderColor: cBorder },
      timeScale: { borderColor: cBorder },
      crosshair: { mode: 0 },
    });
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: cUp,
      downColor: cDown,
      borderUpColor: cUp,
      borderDownColor: cDown,
      wickUpColor: cUp,
      wickDownColor: cDown,
    });
    const vol = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      color: cAccent,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    chartRef.current = chart;
    candleRef.current = candle;
    volRef.current = vol;

    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  // Push data on every candle update.
  useEffect(() => {
    if (!candleRef.current || !volRef.current) return;
    // 방어적 정렬: lightweight-charts setData()는 time 오름차순 필수.
    // 서버가 이미 오름차순으로 주지만(market_service.get_ohlcv), 최신순 응답이
    // 새어들어와도 assert 로 차트가 깨지지 않도록 여기서도 오름차순 보장.
    // date=YYYYMMDD 고정폭 → localeCompare = 시간순.
    const sorted = [...candles].sort((a, b) => a.date.localeCompare(b.date));
    candleRef.current.setData(
      sorted.map((c) => ({ time: toTime(c.date), open: c.o, high: c.h, low: c.l, close: c.c })),
    );
    volRef.current.setData(
      sorted.map((c) => ({
        time: toTime(c.date),
        value: c.v,
        color: c.c >= c.o ? "#16a34a66" : "#dc262666",
      })),
    );
    chartRef.current?.timeScale().fitContent();
  }, [candles]);

  if (candles.length === 0) {
    return <div className="py-10 text-center text-sm text-muted">캔들 데이터가 없습니다</div>;
  }
  return <div ref={ref} className="w-full" />;
}
