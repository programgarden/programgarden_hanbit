import { Card, DeferredBadge } from "@/components/ui";
import { ModeBadge } from "@/components/ModeBadge";
import type { TradeMode } from "@/lib/api/types";

/**
 * Disabled preview of the planned strategy cards (M4). Static, non-interactive —
 * shows the intended layout (allocation, run state, active-limit mini-panel)
 * without pretending the strategy engine exists yet.
 */

interface Preview {
  name: string;
  market: string;
  mode: TradeMode;
  alloc: string;
  running: boolean;
}

const PREVIEW: Preview[] = [
  { name: "MA-Cross", market: "국내주식 KR", mode: "live", alloc: "₩ 24.0M (40%)", running: true },
  { name: "Momentum-OS", market: "해외주식 OS", mode: "live", alloc: "$ 3,200 (64%)", running: false },
  { name: "HKEX-Paper", market: "해외선물 FUT", mode: "paper", alloc: "HK$ 40k (50%)", running: true },
];

export function StrategyCards() {
  return (
    <div className="grid grid-cols-1 gap-3 opacity-60 lg:grid-cols-2">
      {PREVIEW.map((s) => (
        <Card
          key={s.name}
          className={s.mode === "live" ? "border-live/40" : "border-paper/40"}
          title={
            <span className="flex items-center gap-2">
              {s.name} <ModeBadge mode={s.mode} /> <span className="text-muted">· {s.market}</span>
              <DeferredBadge label="M4" />
            </span>
          }
        >
          <div className="space-y-2 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted">상태</span>
              <span className={s.running ? "text-up" : "text-muted"}>
                {s.running ? "▶ running" : "⏸ stopped"}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-muted">할당 자본</span>
              <span className="num text-foreground">{s.alloc}</span>
            </div>
            <div className="rounded border border-border bg-surface-2 p-2 text-xs text-muted">
              활성 한도(소액상한·누적명목·일손실·킬스위치) 미니패널 · 실행 로그(signal/order/skip/error) — M4
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                disabled
                className="cursor-not-allowed rounded border border-border px-3 py-1 text-xs text-muted"
              >
                {s.running ? "정지" : "시작"}
              </button>
              <button
                type="button"
                disabled
                className="cursor-not-allowed rounded border border-border px-3 py-1 text-xs text-muted"
              >
                파라미터 편집
              </button>
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}
