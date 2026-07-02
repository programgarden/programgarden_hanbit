/**
 * Low-level HTTP client for the backend REST API.
 *
 * The backend always replies with an envelope (schemas.py):
 *   success → { ok: true,  data, server_time }
 *   failure → { ok: false, error: { code, message, detail }, server_time }
 * Domain errors sometimes ride a 200 (ok:false) and sometimes a real HTTP code
 * (orders/risk use JSONResponse with 4xx). So we never trust the HTTP status
 * alone: we parse the body, and if `ok:false` we throw a typed {@link ApiError}
 * carrying the backend `code` (LIVE_DISABLED, ENGINE_NOT_ACTIVE, …) that screens
 * branch on.
 *
 * Note the distinction: an envelope-level failure (`ok:false` + `error`) is an
 * error and throws. A *successful* envelope whose `data` happens to contain its
 * own `ok:false` (e.g. /orders/commit risk-reject payload) is returned as data
 * — the caller inspects it.
 */

import type { ApiEnvelope, ApiSuccess } from "./types";

/**
 * REST base. In the browser this is the same-origin proxy path `/api/v1`
 * (next.config.ts rewrites forward it to the backend in the same container),
 * which avoids hard-coding host↔container ports and works for remote/Tailscale
 * browsers. Override with NEXT_PUBLIC_API_BASE.
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

/**
 * Base actually used by `fetch`. A relative same-origin base has no origin to
 * resolve against during SSR, so on the server we fall back to the backend's
 * absolute in-container address.
 */
function restBase(): string {
  if (API_BASE.startsWith("/") && typeof window === "undefined") {
    return `${process.env.BACKEND_ORIGIN ?? "http://localhost:8000"}/api/v1`;
  }
  return API_BASE;
}

/** WebSocket URL for the live stream. Called only in the browser. */
export function wsUrl(): string {
  if (/^https?:\/\//.test(API_BASE)) {
    return `${API_BASE.replace(/^http/, "ws")}/stream`;
  }
  // Relative same-origin base → build an absolute ws:// URL from the page origin
  // (the dev server rewrite proxies the upgrade to the backend stream).
  const { protocol, host } = window.location;
  return `${protocol === "https:" ? "wss" : "ws"}://${host}${API_BASE}/stream`;
}

export class ApiError extends Error {
  readonly code: string;
  readonly httpStatus: number;
  readonly detail?: unknown;

  constructor(code: string, message: string, httpStatus: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.httpStatus = httpStatus;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${restBase()}${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...init?.headers },
    });
  } catch (e) {
    throw new ApiError("NETWORK", `network error: ${String(e)}`, 0);
  }

  let body: ApiEnvelope<T> | null = null;
  try {
    body = (await res.json()) as ApiEnvelope<T>;
  } catch {
    // Non-JSON body (shouldn't happen for our API) — fall through to status check.
  }

  if (body && body.ok === false) {
    throw new ApiError(body.error.code, body.error.message, res.status, body.error.detail);
  }
  if (!res.ok) {
    throw new ApiError("HTTP_ERROR", `HTTP ${res.status}`, res.status);
  }
  if (!body || !("data" in body)) {
    throw new ApiError("BAD_ENVELOPE", "response missing data envelope", res.status);
  }
  return (body as ApiSuccess<T>).data;
}

export const apiGet = <T>(path: string): Promise<T> => request<T>(path);

export const apiPost = <T>(path: string, body?: unknown): Promise<T> =>
  request<T>(path, {
    method: "POST",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
