"use client";

import Link from "next/link";

/**
 * Global kill switch button (always present in the TopBar, per the integration
 * plan). Navigates to the Risk screen, where the functional 2-step kill switch
 * (KillSwitchPanel → POST /risk/killswitch) lives — the destructive action is
 * never one click from the global bar.
 */
export function KillButton() {
  return (
    <Link
      href="/risk"
      title="위험 한도 / 킬스위치로 이동 (2단계 확인 후 동결)"
      className="rounded border border-live/50 bg-live/10 px-2.5 py-1 text-xs font-semibold text-live transition-colors hover:bg-live/20"
    >
      🔴 KILL
    </Link>
  );
}
