/**
 * Display formatting helpers.
 *
 * Money is always shown WITH its currency symbol — the dashboard never sums
 * across currencies, so every figure must carry ₩/$/HK$ to make the isolation
 * visible (a core safety/教育 principle of the project).
 */

export const CURRENCY_SYMBOL: Record<string, string> = {
  KRW: "₩",
  USD: "$",
  HKD: "HK$",
};

export function ccySymbol(ccy?: string | null): string {
  if (!ccy) return "";
  return CURRENCY_SYMBOL[ccy] ?? `${ccy} `;
}

/** Format an amount in its own currency, e.g. (81400,"KRW") → "₩81,400". */
export function fmtMoney(
  value: number | null | undefined,
  ccy?: string | null,
  opts: { sign?: boolean; digits?: number } = {},
): string {
  if (value == null || Number.isNaN(value)) return "—";
  const digits = opts.digits ?? (ccy === "KRW" ? 0 : 2);
  const sign = opts.sign && value > 0 ? "+" : "";
  const body = Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  const neg = value < 0 ? "-" : "";
  return `${neg}${sign}${ccySymbol(ccy)}${body}`;
}

/** Plain number with thousands separators. */
export function fmtNum(value: number | null | undefined, digits = 0): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/**
 * Percent. Backend ratios (0.0409) and already-percent values are both common,
 * so pass `fromRatio: true` when the input is a 0–1 ratio.
 */
export function fmtPct(
  value: number | null | undefined,
  { fromRatio = false, sign = true }: { fromRatio?: boolean; sign?: boolean } = {},
): string {
  if (value == null || Number.isNaN(value)) return "—";
  const pct = fromRatio ? value * 100 : value;
  const s = sign && pct > 0 ? "+" : "";
  return `${s}${pct.toFixed(2)}%`;
}

/** Short relative-ish clock label from an ISO timestamp (HH:MM:SS KST-ish). */
export function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("ko-KR", { hour12: false });
}
