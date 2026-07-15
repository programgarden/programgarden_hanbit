"use client";

import { PageHeader, Card, Loading, ErrorState, Empty } from "@/components/ui";
import {
  useStrategies,
  useToggleStrategies,
  useRunStrategies,
} from "@/lib/query/hooks";
import type { StrategyItem, StrategyRunResult } from "@/lib/api/types";

/**
 * Strategies — 자동매매 전략 엔진 컨트롤 (route: /strategies).
 *
 * 백엔드 `/strategy`(목록·토글·수동실행)에 실연결한다. 전략은 "신호"만 내고, 발주는 전부
 * 기존 리스크 게이트(order_service.place)를 거친다 — LIVE 는 allow_live 로 잠겨 실주문 0.
 * 마스터 토글이 꺼져 있으면 어떤 전략도 발주하지 않는다.
 */
const MARKET_LABEL: Record<string, string> = {
  korea_stock: "국내주식",
  overseas_stock: "해외주식",
  overseas_futureoption: "해외선물(모의)",
};

export default function StrategiesPage() {
  const strategies = useStrategies();
  const toggle = useToggleStrategies();
  const run = useRunStrategies();

  const data = strategies.data;
  const enabled = data?.enabled ?? false;
  const fired = run.data?.fired ?? [];

  return (
    <div className="space-y-4">
      <PageHeader title="Strategies — 자동매매 전략" />

      <Card title="전략 엔진">
        {strategies.isLoading ? (
          <Loading />
        ) : strategies.isError ? (
          <ErrorState error={strategies.error} onRetry={() => strategies.refetch()} />
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm text-foreground">
                마스터 토글{" "}
                <span
                  className={`num ml-1 rounded border px-1.5 py-0.5 text-xs ${
                    enabled
                      ? "border-up/40 text-up"
                      : "border-border text-muted"
                  }`}
                >
                  {enabled ? "ON — 발주 활성" : "OFF — 발주 안 함"}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={toggle.isPending}
                  onClick={() => toggle.mutate(!enabled)}
                  className="rounded border border-border bg-surface-2 px-2 py-1 text-xs text-foreground disabled:opacity-40"
                >
                  {enabled ? "엔진 끄기" : "엔진 켜기"}
                </button>
                <button
                  type="button"
                  disabled={run.isPending}
                  onClick={() => run.mutate()}
                  className="rounded border border-paper/50 bg-paper/10 px-2 py-1 text-xs text-paper disabled:opacity-40"
                >
                  {run.isPending ? "실행 중…" : "지금 1회 실행"}
                </button>
              </div>
            </div>
            <p className="text-xs text-muted">
              전략은 신호만 내고, 발주는 전부 리스크 게이트(소액캡·집중도·킬스위치·엔진상태)를
              거칩니다. 국내·해외주식(LIVE)은{" "}
              <code className="num text-muted">allow_live</code> 로 잠겨 있어 실주문은 나가지
              않습니다(실주문 0). 토글이 OFF 면 어떤 전략도 발주하지 않습니다.
            </p>
          </div>
        )}
      </Card>

      <Card title="등록된 전략">
        {!data ? (
          <Loading />
        ) : data.strategies.length === 0 ? (
          <Empty label="등록된 전략이 없습니다" />
        ) : (
          <ul className="space-y-2">
            {data.strategies.map((s: StrategyItem) => (
              <li
                key={s.name}
                className="rounded border border-border bg-surface-2 px-3 py-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-foreground">{s.name}</span>
                  <span className="text-xs text-muted">
                    {MARKET_LABEL[s.market] ?? s.market}
                    {" · "}
                    <span className={s.enabled ? "text-up" : "text-muted"}>
                      {s.enabled ? "활성" : "비활성"}
                    </span>
                  </span>
                </div>
                <div className="num mt-1 text-xs text-muted">
                  규칙: 전일대비 ≤ -3% 매수 / 평가수익 ≥ +5% 청산
                </div>
                <div className="num mt-1 text-xs text-muted">
                  종목: {s.symbols.join(", ") || "—"}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="최근 실행 결과">
        {run.isIdle && !run.data ? (
          <p className="text-xs text-muted">
            &ldquo;지금 1회 실행&rdquo; 을 누르면 전략을 평가해 신호를 발주하고 결과를 여기에
            표시합니다. (토글 OFF 면 발주 0)
          </p>
        ) : fired.length === 0 ? (
          <Empty label="이번 실행에서 발생한 신호가 없습니다" />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th className="py-1">전략</th>
                <th className="py-1">종목</th>
                <th className="py-1">방향</th>
                <th className="py-1">결과</th>
                <th className="py-1">사유</th>
              </tr>
            </thead>
            <tbody>
              {fired.map((r: StrategyRunResult, i: number) => (
                <tr key={`${r.strategy}-${r.symbol}-${i}`} className="border-t border-border">
                  <td className="py-1 text-xs text-foreground">{r.strategy}</td>
                  <td className="num py-1 text-xs text-foreground">{r.symbol}</td>
                  <td className="py-1 text-xs">
                    <span className={r.side === "buy" ? "text-up" : "text-down"}>
                      {r.side === "buy" ? "매수" : "청산"}
                      {" "}×{r.qty}
                    </span>
                  </td>
                  <td className="py-1 text-xs">
                    {r.ok ? (
                      <span className="text-up">발주됨</span>
                    ) : (
                      <span className="text-down">
                        차단({r.decision?.reasons?.join(",") ?? "게이트 거부"})
                      </span>
                    )}
                  </td>
                  <td className="py-1 text-xs text-muted">{r.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
