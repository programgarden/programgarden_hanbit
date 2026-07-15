"use client";

/**
 * Live arming panel — 사이트에서 실거래를 켜는(무장) 컨트롤. POST /system/live-arming.
 *
 * 2-key 안전: 서버 env `HANBIT_ALLOW_LIVE=true`(허용 ceiling) + 사이트 무장(강한 확인).
 * 허용이 꺼져 있으면 여기서 무장할 수 없다(실주문 0 불변식). 무장은 위험 인지 체크 + 정확한
 * 확인 문구 타이핑을 요구한다(체크박스 하나로 실거래 금지). 무장 해제는 킬스위치처럼 즉시.
 */

import { useState } from "react";
import { useLiveArming, useSetLiveArming } from "@/lib/query/hooks";

const ARM_PHRASE = "실거래 활성화"; // 서버 LIVE_ARM_PHRASE 와 일치해야 함

export function LiveArmingPanel() {
  const arming = useLiveArming();
  const setArm = useSetLiveArming();
  const [ack, setAck] = useState(false);
  const [phrase, setPhrase] = useState("");

  const data = arming.data;
  const permission = data?.permission ?? false;
  const armed = data?.armed ?? false;
  const canArm = permission && ack && phrase.trim() === ARM_PHRASE && !setArm.isPending;

  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <div className="mb-3 text-xs font-medium uppercase tracking-wide text-muted">
        실거래 활성화 (무장)
      </div>

      {arming.isLoading ? (
        <p className="text-sm text-muted">불러오는 중…</p>
      ) : armed ? (
        <div className="space-y-2">
          <div className="rounded border border-live bg-live/15 px-3 py-2 text-sm font-semibold text-live">
            ⚠ 실거래 무장됨 — 실제 돈이 움직입니다.
          </div>
          <p className="text-xs text-muted">
            전략·주문이 실제 계좌로 발주됩니다(소액캡·2단계 확인·킬스위치는 그대로 적용). 사용을
            마치면 즉시 무장 해제하세요.
          </p>
          <button
            type="button"
            disabled={setArm.isPending}
            onClick={() => setArm.mutate({ armed: false })}
            className="w-full rounded border border-live bg-live/20 px-2 py-1 text-xs font-semibold text-live disabled:opacity-40"
          >
            실거래 무장 해제
          </button>
        </div>
      ) : !permission ? (
        <div className="space-y-2">
          <div className="text-sm text-foreground">
            현재 <span className="num text-muted">무장 해제 · 허용 꺼짐</span>
          </div>
          <p className="text-xs text-muted">
            실거래를 켜려면 먼저 서버 <code className="num">.env</code> 에{" "}
            <code className="num text-paper">HANBIT_ALLOW_LIVE=true</code> 를 설정하고 재기동해야
            합니다(허용 ceiling). 이 값이 꺼져 있으면 사이트에서 무장할 수 없습니다 — 실주문 0.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="text-sm text-foreground">
            현재 <span className="num text-up">허용됨</span> ·{" "}
            <span className="num text-muted">무장 해제</span>
          </div>
          <p className="text-xs text-muted">
            무장하면 전략·주문이 <b className="text-live">실제 돈</b>으로 발주됩니다. 주식 실주문
            경로는 아직 라이브 미검증이라 첫 주문은 첫주문가드(단일종목·소액)로 제한됩니다.
          </p>
          <label className="flex items-center gap-2 text-xs text-foreground">
            <input
              type="checkbox"
              checked={ack}
              onChange={(e) => setAck(e.target.checked)}
            />
            실제 돈이 움직이는 것을 이해했습니다.
          </label>
          <input
            value={phrase}
            onChange={(e) => setPhrase(e.target.value)}
            placeholder={`확인 문구 입력 — "${ARM_PHRASE}"`}
            className="num w-full rounded border border-border bg-surface-2 px-2 py-1 text-sm"
          />
          <button
            type="button"
            disabled={!canArm}
            onClick={() => setArm.mutate({ armed: true, confirm: phrase.trim() })}
            className="w-full rounded border border-live bg-live/10 px-2 py-1 text-xs font-semibold text-live disabled:opacity-40"
          >
            ⚠ 실거래 무장
          </button>
          {setArm.data?.ok === false && (
            <p className="text-xs text-down">
              무장 실패:{" "}
              {setArm.data.reason === "BAD_CONFIRM"
                ? "확인 문구가 일치하지 않습니다"
                : "서버 허용(env)이 꺼져 있습니다"}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
