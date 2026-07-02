"use client";

/**
 * Kill switch panel — wired to POST /risk/killswitch (killswitch.py).
 *
 *   L1 engage → cancel all open orders (+ raw-cancel quarantined).
 *   L2 engage → L1 + flatten all paper positions, gated by a server-issued
 *               confirm_token (2-step: first call returns the token, resend it).
 *   release   → back to active.
 *
 * The GLOBAL action is additionally gated client-side by a 위험인지 checkbox and
 * typing "HALT" (wireframe). Current effective state is read from /risk/halt_state.
 * Note: LIVE buckets (KR/OS) are a no-op-with-warning on the server until M4.
 */

import { useState } from "react";
import { useHaltState, useKillSwitch } from "@/lib/query/hooks";
import type { BucketHaltState } from "@/lib/api/types";

const STATE_COLOR: Record<BucketHaltState, string> = {
  active: "text-up",
  halted_daily: "text-paper",
  killed: "text-live",
};

export function KillSwitchPanel() {
  const halt = useHaltState();
  const ks = useKillSwitch();
  const [ack, setAck] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [pendingToken, setPendingToken] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const gate = ack && confirmText === "HALT";
  const live = halt.data?.buckets.live;
  const paper = halt.data?.buckets.paper;
  const anyKilled = live?.state === "killed" || paper?.state === "killed";

  function run(body: Parameters<typeof ks.mutate>[0], label: string) {
    setMsg(null);
    ks.mutate(body, {
      onSuccess: (r) => {
        if (r.requires_confirm && r.confirm_token) {
          setPendingToken(r.confirm_token);
          setMsg("L2 확인 토큰 발급됨 — 재확인 시 paper 포지션 청산");
        } else {
          setPendingToken(null);
          setMsg(
            `${label} 완료${typeof r.canceled === "number" ? ` · 취소 ${r.canceled}건` : ""}`,
          );
        }
      },
      onError: (e) => setMsg(`실패 [${(e as { code?: string }).code ?? "ERR"}]`),
    });
  }

  return (
    <section className="rounded-lg border border-live/50 bg-surface p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-live">
          🔴 KILL SWITCH
        </span>
        <span className="text-xs text-muted">
          live <b className={STATE_COLOR[live?.state ?? "active"]}>{live?.state ?? "—"}</b> · paper{" "}
          <b className={STATE_COLOR[paper?.state ?? "active"]}>{paper?.state ?? "—"}</b>
        </span>
      </div>

      {/* GLOBAL 2-step gate */}
      <div className="mt-3 text-sm">
        <label className="flex items-center gap-1 text-xs text-muted">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
          1단계 — 위험 인지
        </label>
        <input
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          placeholder="2단계 — 'HALT' 입력"
          className="num mt-2 w-full rounded border border-border bg-surface-2 px-2 py-1 text-sm"
        />
        <div className="mt-2 grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled={!gate || ks.isPending}
            onClick={() => run({ scope: "global", action: "engage", level: 1 }, "L1 일괄취소")}
            className="rounded border border-live/50 bg-live/10 px-2 py-1 text-xs text-live disabled:opacity-40"
          >
            동결 L1 (일괄취소)
          </button>
          <button
            type="button"
            disabled={!gate || ks.isPending}
            onClick={() => run({ scope: "global", action: "engage", level: 2 }, "L2 청산")}
            className="rounded border border-live/50 bg-live/10 px-2 py-1 text-xs text-live disabled:opacity-40"
          >
            동결 L2 (+청산)
          </button>
        </div>

        {pendingToken && (
          <button
            type="button"
            disabled={ks.isPending}
            onClick={() =>
              run(
                { scope: "global", action: "engage", level: 2, confirm_token: pendingToken },
                "L2 청산(확정)",
              )
            }
            className="mt-2 w-full rounded border border-live bg-live/20 px-2 py-1 text-xs font-semibold text-live"
          >
            ⚠ L2 재확인 — paper 전량 청산 실행
          </button>
        )}

        {anyKilled && (
          <button
            type="button"
            disabled={ks.isPending}
            onClick={() => run({ scope: "global", action: "release" }, "해제")}
            className="mt-2 w-full rounded border border-up/50 bg-up/10 px-2 py-1 text-xs text-up"
          >
            동결 해제 (release)
          </button>
        )}

        {msg && <p className="mt-2 text-xs text-muted">{msg}</p>}
      </div>

      <div className="mt-4 border-t border-border pt-3 text-xs text-muted">
        <div className="mb-1">발동 시 열린주문 정책</div>
        <div>◉ 자동취소 (L1 이 open orders 일괄취소) · ◯ 보류 — 정책 선택은 M4</div>
        <p className="mt-2">
          ⓘ LIVE 버킷(KR/OS)은 M4 까지 no-op-with-warning · paper(FUT)만 실제 취소/청산
        </p>
      </div>
    </section>
  );
}
