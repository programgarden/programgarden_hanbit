import type { ReactNode } from "react";

/** Page title row. */
export function PageHeader({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h1 className="text-lg font-semibold text-foreground">{title}</h1>
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
}

/** Bordered surface panel. */
export function Card({
  title,
  className = "",
  children,
}: {
  title?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      className={`rounded-lg border border-border bg-surface p-4 ${className}`}
    >
      {title && (
        <div className="mb-3 text-xs font-medium uppercase tracking-wide text-muted">
          {title}
        </div>
      )}
      {children}
    </section>
  );
}

/** Small KPI tile. */
export function Kpi({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="num mt-1 text-2xl font-semibold text-foreground">
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export type ChipKind =
  | "filled"
  | "partial"
  | "accepted"
  | "rejected"
  | "timeout";

/** Order/fill status chip with kind-based coloring. */
export function StatusChip({ kind, label }: { kind: ChipKind; label?: string }) {
  const styles: Record<ChipKind, string> = {
    filled: "border-up/40 text-up",
    partial: "border-accent/40 text-accent",
    accepted: "border-border text-muted",
    rejected: "border-down/40 text-down",
    timeout: "border-paper/40 text-paper",
  };
  const text: Record<ChipKind, string> = {
    filled: "filled",
    partial: "partial",
    accepted: "accepted",
    rejected: "rejected",
    timeout: "timeout",
  };
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-xs ${styles[kind]}`}
    >
      {label ?? text[kind]}
    </span>
  );
}

/** Colored P&L number (green up / red down). */
export function Pnl({ value, text }: { value: number; text: string }) {
  const cls = value > 0 ? "text-up" : value < 0 ? "text-down" : "text-muted";
  const arrow = value > 0 ? "▲" : value < 0 ? "▼" : "·";
  return (
    <span className={`num ${cls}`}>
      {arrow} {text}
    </span>
  );
}

/** Inline loading line for async panels. */
export function Loading({ label = "불러오는 중…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-muted">
      <span className="h-2 w-2 animate-pulse rounded-full bg-accent" aria-hidden />
      {label}
    </div>
  );
}

/** Error panel — surfaces the backend error code so failures are legible. */
export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const e = error as { code?: string; message?: string };
  return (
    <div className="rounded border border-down/40 bg-down/5 p-3 text-sm text-down">
      <div className="font-medium">데이터를 불러오지 못했습니다</div>
      <div className="mt-1 text-xs text-muted">
        {e?.code ? `[${e.code}] ` : ""}
        {e?.message ?? String(error)}
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 rounded border border-border px-2 py-0.5 text-xs text-muted hover:text-foreground"
        >
          ↻ 다시 시도
        </button>
      )}
    </div>
  );
}

/** Empty-state line for tables/lists with no rows. */
export function Empty({ label = "표시할 항목이 없습니다" }: { label?: string }) {
  return <div className="py-6 text-center text-sm text-muted">{label}</div>;
}

/**
 * "M4 예정" badge — marks a surface whose backend does not exist yet, so the
 * UI is honest about what is real vs. deferred (educational integrity).
 */
export function DeferredBadge({
  label = "M4 예정",
  note,
}: {
  label?: string;
  note?: string;
}) {
  return (
    <span
      title={note}
      className="inline-flex items-center gap-1 rounded border border-paper/40 bg-paper/5 px-1.5 py-0.5 text-xs text-paper"
    >
      ⏳ {label}
    </span>
  );
}
