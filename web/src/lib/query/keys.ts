/**
 * React Query cache keys. Centralised so hooks and the WebSocket invalidator
 * agree on exactly which queries a live event should refresh.
 *
 * Invalidation uses key *prefixes*: invalidating ["orders"] refreshes both
 * ["orders","open"] and ["orders","history"].
 */
export const qk = {
  health: ["health"] as const,
  modes: ["modes"] as const,
  clock: ["clock"] as const,
  metrics: ["metrics"] as const,
  quarantine: ["quarantine"] as const,

  portfolio: ["portfolio"] as const,
  positions: (bucket: string) => ["positions", bucket] as const,
  positionsAll: ["positions"] as const,
  accounts: ["accounts"] as const,

  orders: ["orders"] as const,
  ordersOpen: ["orders", "open"] as const,
  ordersHistory: ["orders", "history"] as const,
  whitelist: (market: string) => ["whitelist", market] as const,

  risk: ["risk"] as const,
  riskLimits: ["risk", "limits"] as const,
  riskEvents: ["risk", "events"] as const,
  haltState: ["risk", "halt_state"] as const,

  quote: (market: string, symbol: string) => ["quote", market, symbol] as const,
  ohlcv: (market: string, symbol: string, period: string, count: number) =>
    ["ohlcv", market, symbol, period, count] as const,
};
