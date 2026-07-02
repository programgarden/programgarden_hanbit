"use client";

import { useEffect, useState } from "react";
import { useClock } from "@/lib/query/hooks";

/**
 * Server-authoritative clock (App Shell, wireframe header).
 *
 * Trading correctness depends on the *server's* clock — order expiry and market
 * sessions are judged there, not in the browser. So instead of `new Date()`, we
 * read GET /system/clock (`server_time`, UTC) and tick locally from it: we
 * compute the skew (server − client) once per poll and render `now + skew`,
 * re-grabbing the skew every 30s (useClock's refetch) to correct drift. Renders
 * a placeholder until mounted/loaded to avoid an SSR/CSR hydration mismatch.
 */

// 시장세션 라벨(백엔드 market_sessions 키 → 짧은 한글 표기).
const SESSION_LABEL: Record<string, string> = {
  korea_stock: "KR",
  overseas_stock: "美",
  overseas_futureoption: "HK",
};

export function ServerClock() {
  const { data } = useClock();
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    if (!data?.server_time) return;
    // 서버 시각 기준 skew 를 한 번 잡고, 매초 `로컬 + skew` 로 갱신한다.
    const skew = new Date(data.server_time).getTime() - Date.now();
    const fmt = () =>
      new Date(Date.now() + skew).toLocaleTimeString("ko-KR", {
        hour12: false,
        timeZone: "Asia/Seoul",
      });
    // 첫 페인트는 다음 틱에서(effect 본문 동기 setState 회피 — set-state-in-effect).
    const first = setTimeout(() => setText(fmt()), 0);
    const id = setInterval(() => setText(fmt()), 1000);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, [data?.server_time]);

  // 시장세션: 백엔드가 아직 M0 스텁('unknown')이면 정직하게 '세션 미상'으로 표기.
  const sessions = data?.market_sessions ?? {};
  const known = Object.entries(sessions).filter(
    ([, v]) => v?.state && v.state !== "unknown",
  );
  const sessionText =
    known.length > 0
      ? known.map(([k, v]) => `${SESSION_LABEL[k] ?? k} ${v.state}`).join(" · ")
      : "세션 미상 (M0)";

  return (
    <span
      className="num text-xs text-muted"
      title="서버 권위 시각(/system/clock) — 주문 만료·시장세션 판정의 기준"
    >
      {text ?? "--:--:--"} KST
      <span className="ml-2 hidden md:inline">{sessionText}</span>
    </span>
  );
}
