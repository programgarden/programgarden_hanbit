/**
 * Live-stream store (Zustand).
 *
 * Holds the state pushed over the single WebSocket connection to
 * `/api/v1/stream`: connection status, the latest per-bucket halt snapshot, and
 * small ring buffers of recent order/fill/risk events for the timeline panels.
 *
 * The actual socket lifecycle lives in `client.ts`; this module is just the
 * shared state container that React components subscribe to.
 *
 * Backend reality (M3): topics `orders`, `fill`, `risk_event`, `risk.halt_state`
 * are pushed live now. `portfolio_snapshot` is advertised but its live push is
 * deferred to M4, so the dashboard refreshes portfolio/positions by polling.
 */

import { create } from "zustand";
import type { BucketHalt } from "@/lib/api/types";

export type WsStatus = "connecting" | "open" | "closed";

/** Generic stream envelope (event_bus.py): {topic,type,seq,ts,data}. */
export interface WsEnvelope {
  topic: string;
  type: "info" | "event" | "pong" | "ack";
  seq?: number;
  ts?: string;
  data?: Record<string, unknown>;
  // present on the initial "info" frame:
  topics?: string[];
  milestone?: string;
  server_time?: string;
}

/** Shapes of `data` for the topics we render (loosely typed). */
export interface OrdersEventData {
  order_id?: number;
  state?: string;
  action?: "amend" | "cancel";
  ok?: boolean;
  reconcile?: string;
  found?: number;
  resolved?: number;
}
export interface FillEventData {
  order_id?: number;
  broker_ord_no?: string;
  exec_qty?: number;
  exec_price?: number;
  origin?: string;
}
export interface RiskWsEventData {
  result?: string;
  reasons?: string[];
  symbol?: string;
}

const CAP = 80;

interface StreamStore {
  status: WsStatus;
  connectedAt: string | null;
  /** Count of detected seq gaps (dropped messages) — surfaced for diagnostics. */
  gaps: number;
  halt: Record<string, BucketHalt> | null;
  orders: WsEnvelope[];
  fills: WsEnvelope[];
  riskEvents: WsEnvelope[];
  lastSeq: Record<string, number>;

  setStatus: (s: WsStatus) => void;
  markConnected: (ts: string | null) => void;
  ingest: (env: WsEnvelope) => void;
  reset: () => void;
}

export const useStream = create<StreamStore>((set) => ({
  status: "connecting",
  connectedAt: null,
  gaps: 0,
  halt: null,
  orders: [],
  fills: [],
  riskEvents: [],
  lastSeq: {},

  setStatus: (status) => set({ status }),
  markConnected: (connectedAt) => set({ connectedAt }),

  ingest: (env) =>
    set((st) => {
      const patch: Partial<StreamStore> = {};

      // Track per-topic seq to detect dropped messages (resync trigger).
      if (typeof env.seq === "number") {
        const prev = st.lastSeq[env.topic];
        if (prev !== undefined && env.seq > prev + 1) patch.gaps = st.gaps + 1;
        patch.lastSeq = { ...st.lastSeq, [env.topic]: env.seq };
      }

      switch (env.topic) {
        case "orders":
          patch.orders = [env, ...st.orders].slice(0, CAP);
          break;
        case "fill":
          patch.fills = [env, ...st.fills].slice(0, CAP);
          break;
        case "risk_event":
          patch.riskEvents = [env, ...st.riskEvents].slice(0, CAP);
          break;
        case "risk.halt_state":
          patch.halt = (env.data as Record<string, BucketHalt> | undefined) ?? null;
          break;
        default:
          break;
      }
      return patch;
    }),

  reset: () =>
    set({ orders: [], fills: [], riskEvents: [], lastSeq: {}, gaps: 0, halt: null }),
}));
