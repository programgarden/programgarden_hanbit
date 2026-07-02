/**
 * Trading mode matrix — the authoritative mapping of each market to its
 * trade mode (live/paper), currency, and constraints.
 *
 * Source of truth at runtime is the backend (`GET /system/modes`), but the
 * frontend must boot and render correctly with NO backend available. So we
 * ship a fallback constant and `fetchModes()` returns it on any failure.
 *
 * Invariant (.claude/plans/2026-06-20-통합계획서.md): korea_stock + overseas_stock are always LIVE (real
 * money, small-amount caps); overseas_futureoption is always PAPER and
 * restricted to the HKEX whitelist. Live and paper P&L are never summed.
 */

export type TradeMode = "live" | "paper";

export interface MarketMode {
  /** Backend market key. */
  market: "korea_stock" | "overseas_stock" | "overseas_futureoption";
  /** Short badge label shown in the TopBar (KR / OS / FUT). */
  short: string;
  /** Human label. */
  label: string;
  mode: TradeMode;
  /** ISO-ish currency symbol family. */
  currency: "KRW" | "USD" | "HKD";
  currencySymbol: "₩" | "$" | "HK$";
  /** Per-order small-amount cap (in the market currency). */
  cap?: number;
  /** Free-form constraints note (e.g. HKEX whitelist). */
  constraints?: string;
}

export interface ModesResponse {
  markets: MarketMode[];
}

/** Fallback matrix — used when the backend is unreachable (M0). */
export const MODES_FALLBACK: ModesResponse = {
  markets: [
    {
      market: "korea_stock",
      short: "KR",
      label: "국내주식",
      mode: "live",
      currency: "KRW",
      currencySymbol: "₩",
      cap: 100000,
    },
    {
      market: "overseas_stock",
      short: "OS",
      label: "해외주식",
      mode: "live",
      currency: "USD",
      currencySymbol: "$",
      cap: 50,
    },
    {
      market: "overseas_futureoption",
      short: "FUT",
      label: "해외선물",
      mode: "paper",
      currency: "HKD",
      currencySymbol: "HK$",
      constraints: "HKEX",
    },
  ],
};

// fetchModes runs in a Server Component (TopBar). Server-side there is no page
// origin to resolve a relative URL against, so call the backend by its absolute
// in-container address (same `BACKEND_ORIGIN` the rewrite proxy uses).
const API_BASE = `${process.env.BACKEND_ORIGIN ?? "http://localhost:8000"}/api/v1`;

/**
 * Fetch the live mode matrix from the backend. Returns the fallback constant
 * on any failure (network error, non-2xx, bad shape) so the UI always has data
 * to render even when the backend is down.
 */
export async function fetchModes(): Promise<ModesResponse> {
  try {
    const res = await fetch(`${API_BASE}/system/modes`, {
      // Modes are config-ish; revalidate occasionally rather than per-request.
      next: { revalidate: 60 },
    });
    if (!res.ok) return MODES_FALLBACK;
    const data = (await res.json()) as ModesResponse;
    if (!data || !Array.isArray(data.markets) || data.markets.length === 0) {
      return MODES_FALLBACK;
    }
    return data;
  } catch {
    return MODES_FALLBACK;
  }
}
