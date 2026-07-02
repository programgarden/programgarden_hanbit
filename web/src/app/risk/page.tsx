"use client";

/**
 * Risk — limits / kill switch / violations (route: /risk).
 *
 * Limits and the violation log are read from the real backend (/risk/limits,
 * /risk/events) and the live WS risk_event stream. The kill switch is fully
 * functional (see KillSwitchPanel). Editing limits has no M3 write endpoint, so
 * the form is read-only with an "M4 예정" note rather than a fake save.
 */

import { useState } from "react";
import { Card, DeferredBadge, ErrorState, Loading, PageHeader } from "@/components/ui";
import { KillSwitchPanel } from "./KillSwitchPanel";
import { useRiskEvents, useRiskLimits } from "@/lib/query/hooks";
import { useStream } from "@/lib/ws/store";
import type { Severity } from "@/lib/api/types";
import { fmtNum, fmtTime } from "@/lib/format";

const LIMIT_LABEL: Record<string, string> = {
  per_order_cap_krw: "주문 1건 최대 금액 (₩)",
  bucket_notional_cap: "버킷 명목 캡",
  max_contracts_per_order: "주문당 최대 계약수",
  max_open_orders: "최대 동시 미체결",
  max_positions: "최대 동시 포지션",
  max_symbol_weight: "종목 집중도 상한",
  max_market_weight: "시장 집중도 상한",
  max_currency_weight: "통화 집중도 상한",
  max_daily_loss_realized: "일일 실현손실 한도 (₩)",
  max_daily_loss_eval: "일일 평가손실 한도 (₩)",
  order_ack_timeout_s: "주문 ACK 타임아웃 (s)",
};

const SEV_STYLE: Record<Severity, string> = {
  info: "text-muted",
  warn: "text-paper",
  critical: "text-down",
};
const SEV_ICON: Record<Severity, string> = { info: "ℹ", warn: "⚠", critical: "⛔" };

export default function RiskPage() {
  const limits = useRiskLimits();
  const events = useRiskEvents(60);
  const stream = useStream();
  const [show, setShow] = useState<Record<Severity, boolean>>({
    info: true,
    warn: true,
    critical: true,
  });

  const rows = (events.data?.events ?? []).filter((e) => show[e.severity]);

  return (
    <div>
      <PageHeader title="Risk · 위험 한도 / 킬스위치">
        <span className="text-xs text-muted">
          halt: {limits.data?.halt.overseas_futureoption ?? "—"} (FUT) · {limits.data?.halt.global ?? "—"} (global)
        </span>
      </PageHeader>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_360px]">
        {/* Limits (read-only) */}
        <Card
          title={
            <span className="flex items-center gap-2">
              위험 한도 (overseas_futureoption) <DeferredBadge label="저장 M4" note="쓰기 endpoint 없음 — 읽기 전용" />
            </span>
          }
        >
          {limits.isLoading ? (
            <Loading />
          ) : limits.error ? (
            <ErrorState error={limits.error} onRetry={() => limits.refetch()} />
          ) : (
            <>
              <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
                {Object.entries(limits.data?.limits ?? {}).map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between border-b border-border/40 py-1">
                    <dt className="text-xs text-muted">{LIMIT_LABEL[k] ?? k}</dt>
                    <dd className="num text-sm text-foreground">{fmtNum(v)}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-3 text-xs text-muted">
                ⓘ 한도 편집·저장(PUT)과 audit_log 기록은 M4 — 현재 서버는 한도 조회만 제공합니다.
                소액상한(small_amount_cap·hard)은 fail-closed 🔒.
              </p>
            </>
          )}
        </Card>

        {/* Kill switch + violations */}
        <div className="space-y-4">
          <KillSwitchPanel />

          <Card
            title={
              <span className="flex items-center gap-2">
                위반 로그
                <span className={stream.status === "open" ? "text-up" : "text-down"}>
                  ● WS {stream.status}
                </span>
              </span>
            }
          >
            {/* live WS risk_event strip */}
            {stream.riskEvents.length > 0 && (
              <ul className="mb-2 space-y-1 rounded border border-paper/30 bg-paper/5 p-2 text-xs">
                {stream.riskEvents.slice(0, 5).map((e, i) => (
                  <li key={i} className="text-paper">
                    ⚠ {String((e.data as { symbol?: string })?.symbol ?? "")}{" "}
                    {((e.data as { reasons?: string[] })?.reasons ?? []).join(", ")} · {fmtTime(e.ts)}
                  </li>
                ))}
              </ul>
            )}

            <div className="mb-2 flex flex-wrap gap-2 text-xs">
              {(["info", "warn", "critical"] as Severity[]).map((s) => (
                <label key={s} className="flex items-center gap-1 text-muted">
                  <input
                    type="checkbox"
                    checked={show[s]}
                    onChange={(e) => setShow((p) => ({ ...p, [s]: e.target.checked }))}
                  />
                  {SEV_ICON[s]} {s}
                </label>
              ))}
            </div>

            {events.isLoading ? (
              <Loading />
            ) : events.error ? (
              <ErrorState error={events.error} onRetry={() => events.refetch()} />
            ) : rows.length === 0 ? (
              <p className="py-3 text-center text-xs text-muted">위반 이벤트가 없습니다</p>
            ) : (
              <ul className="space-y-1.5 text-xs">
                {rows.map((v) => (
                  <li key={v.id} className="flex gap-2">
                    <span className="num text-muted">{fmtTime(v.created_at)}</span>
                    <span className={SEV_STYLE[v.severity]}>
                      {SEV_ICON[v.severity]} {v.event_type}
                    </span>
                    <span className="text-foreground">{v.message}</span>
                    {v.scope_ref && <span className="text-muted">· {v.scope_ref}</span>}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
