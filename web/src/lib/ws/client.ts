/**
 * WebSocket connection manager — a single browser-side socket to
 * `/api/v1/stream`, shared by the whole app.
 *
 * Responsibilities:
 *   • connect / auto-reconnect with exponential backoff
 *   • keep-alive ping (the server replies "pong")
 *   • push every event into the Zustand {@link useStream} store
 *   • invalidate the matching React Query caches so REST-backed screens refresh
 *     the moment an order/fill/risk event lands (and on reconnect, to resync)
 *
 * It is deliberately a module-level singleton (not a React resource): there must
 * be exactly one socket regardless of how many components mount. `startStream`
 * is idempotent; `Providers` calls it once on mount.
 */

import type { QueryClient } from "@tanstack/react-query";
import { wsUrl } from "@/lib/api/client";
import { useStream, type WsEnvelope } from "./store";

/** topic → query-key prefixes to invalidate when that topic fires. */
const INVALIDATE: Record<string, readonly unknown[][]> = {
  orders: [["orders"], ["portfolio"], ["positions"]],
  fill: [["orders"], ["positions"], ["portfolio"]],
  risk_event: [["risk", "events"]],
  "risk.halt_state": [["risk"]],
  portfolio_snapshot: [["portfolio"], ["positions"]],
};

let socket: WebSocket | null = null;
let retries = 0;
let pingTimer: ReturnType<typeof setInterval> | null = null;
let stopped = false;

function startPing() {
  stopPing();
  // The server treats the literal text "ping" as a keep-alive (→ "pong").
  pingTimer = setInterval(() => {
    try {
      socket?.send("ping");
    } catch {
      /* socket closing — onclose will reconnect */
    }
  }, 20_000);
}

function stopPing() {
  if (pingTimer) clearInterval(pingTimer);
  pingTimer = null;
}

function scheduleReconnect(qc: QueryClient) {
  if (stopped) return;
  retries += 1;
  const delay = Math.min(30_000, 1_000 * 2 ** Math.min(retries, 5));
  setTimeout(() => {
    if (!stopped) connect(qc);
  }, delay);
}

function connect(qc: QueryClient) {
  const store = useStream.getState();
  store.setStatus("connecting");

  let ws: WebSocket;
  try {
    ws = new WebSocket(wsUrl());
  } catch {
    scheduleReconnect(qc);
    return;
  }
  socket = ws;

  ws.onopen = () => {
    const reconnected = retries > 0;
    retries = 0;
    useStream.getState().setStatus("open");
    startPing();
    // On a reconnect, REST snapshots may have drifted while we were offline —
    // refetch active queries to resync.
    if (reconnected) qc.invalidateQueries();
  };

  ws.onmessage = (ev) => {
    let env: WsEnvelope;
    try {
      env = JSON.parse(ev.data as string) as WsEnvelope;
    } catch {
      return;
    }
    if (env.type === "info") {
      useStream.getState().markConnected(env.server_time ?? null);
      return;
    }
    if (env.type !== "event") return; // pong / ack — ignore

    useStream.getState().ingest(env);
    for (const key of INVALIDATE[env.topic] ?? []) {
      qc.invalidateQueries({ queryKey: key });
    }
  };

  ws.onclose = () => {
    stopPing();
    useStream.getState().setStatus("closed");
    scheduleReconnect(qc);
  };

  ws.onerror = () => {
    // Let onclose drive the reconnect; closing here avoids a double-schedule.
    try {
      ws.close();
    } catch {
      /* already closing */
    }
  };
}

/** Open the shared stream (idempotent). Safe to call only in the browser. */
export function startStream(qc: QueryClient) {
  if (typeof window === "undefined") return;
  stopped = false;
  if (
    socket &&
    (socket.readyState === WebSocket.OPEN ||
      socket.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }
  connect(qc);
}

/** Close the shared stream and stop reconnecting. */
export function stopStream() {
  stopped = true;
  stopPing();
  retries = 0;
  try {
    socket?.close();
  } catch {
    /* ignore */
  }
  socket = null;
}
