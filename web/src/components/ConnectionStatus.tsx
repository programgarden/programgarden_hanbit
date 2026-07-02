"use client";

/**
 * TopBar connection indicator — reflects the real WebSocket state (Zustand
 * stream store) and the runtime engine state (/system/health), so the global
 * bar shows the actual live-link status instead of a static placeholder.
 */

import { useHealth } from "@/lib/query/hooks";
import { useStream } from "@/lib/ws/store";

const WS_STYLE = {
  open: "text-up",
  connecting: "text-paper",
  closed: "text-down",
} as const;

const ENGINE_STYLE: Record<string, string> = {
  ACTIVE: "text-up",
  RECONCILING: "text-paper",
  READ_ONLY: "text-muted",
};

export function ConnectionStatus() {
  const status = useStream((s) => s.status);
  const gaps = useStream((s) => s.gaps);
  const health = useHealth();
  const engine = health.data?.engine_state;

  return (
    <div className="flex items-center gap-3 text-xs">
      <span className={`flex items-center gap-1 ${WS_STYLE[status]}`} title="WebSocket 스트림">
        <span aria-hidden>●</span> WS {status}
        {gaps > 0 && <span className="text-paper">·gap{gaps}</span>}
      </span>
      {engine && (
        <span className={ENGINE_STYLE[engine] ?? "text-muted"} title="런타임 엔진 상태">
          ⚙ {engine}
        </span>
      )}
    </div>
  );
}
