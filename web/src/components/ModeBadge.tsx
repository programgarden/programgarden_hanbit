import type { TradeMode } from "@/lib/modes";

/**
 * Mode badge — LIVE(real) = red, PAPER(sim) = amber. Used in the TopBar market
 * row and across pages wherever a market's trade mode must be visible (the
 * live/paper separation is a first-class safety signal in .claude/plans/2026-06-20-통합계획서.md).
 */
export function ModeBadge({
  mode,
  short,
  note,
  className = "",
}: {
  mode: TradeMode;
  /** Optional leading market label (KR / OS / FUT). */
  short?: string;
  /** Optional trailing note (e.g. HKEX). */
  note?: string;
  className?: string;
}) {
  const isLive = mode === "live";
  const dot = isLive ? "🔴" : "🟡";
  const text = isLive ? "LIVE" : "PAPER";
  const color = isLive
    ? "border-live/40 text-live"
    : "border-paper/40 text-paper";

  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-medium ${color} ${className}`}
    >
      {short && <span className="text-muted">{short}</span>}
      <span aria-hidden>{dot}</span>
      <span>{text}</span>
      {note && <span className="text-muted">· {note}</span>}
    </span>
  );
}
