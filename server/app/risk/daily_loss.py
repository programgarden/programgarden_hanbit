"""일일손실 상태머신 (M3a) — .claude/plans/2026-06-20-통합계획서.md M3 §5.3. base = KRW.

버킷별:  ACTIVE ──(일중손실 ≥ 한도)──> HALTED_DAILY ──(거래일 경계 리셋)──> ACTIVE.

산식(부호 고정 — 손실이 양수):
  realized_loss = max(0, day_start_realized − now_realized)
  eval_loss     = max(0, (day_start_realized − now_realized)
                         + (day_start_unrealized − now_unrealized))
  realized_loss ≥ max_daily_loss_realized(하드)  또는
  eval_loss ≥ max_daily_loss_eval(보수)  → HALTED_DAILY.

- **realized 권위 = fills 기반 일중 실현**(§5.3) — 트래커 realized_pnl 이 lifetime
  누적이면 사용 금지. 호출자(집계기)가 같은 consistent_tick 에서 realized/unrealized 를
  함께 넘긴다(이중계상 0).
- **baseline 영속·복원**: 같은 거래일이면 stored baseline 복원(중간 부팅이 baseline 을
  당일 중간값으로 덮어써 예산을 줄이는 버그 방지), 새 거래일이면 새 스냅 + active 리셋.
  KILLED 는 수동 복귀만.

⚠ M3a 권위: 일일손실 halt 는 `risk_state.halt_state`. 게이트는 risk_state(halted_daily/
killed)와 trading_halt(killswitch) 둘 다 본다. M3b 가 런타임 EngineState + trading_halt
단일 트랜잭션 미러를 더한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DailyLossStatus:
    bucket: str
    halt_state: str  # active / halted_daily / killed
    realized_loss_krw: float
    eval_loss_krw: float
    risk_budget_left_krw: float
    reset: bool  # 이번 평가에서 새 거래일 baseline 으로 리셋했는가


class DailyLossMonitor:
    """consistent_tick 의 realized/unrealized(KRW) → 일일손실 평가 + risk_state 갱신."""

    def __init__(self, repo) -> None:
        self._repo = repo

    async def evaluate(
        self,
        bucket: str,
        *,
        realized_krw: float,
        unrealized_krw: float,
        limits,
        today: str,
        equity_krw: float | None = None,
    ) -> DailyLossStatus:
        rs = await self._repo.get_risk_state(bucket)
        # 새 거래일(또는 미초기화) → baseline 스냅 + active 리셋 (KILLED 는 유지).
        if rs is None or rs.get("last_reset_day") != today:
            prev = (rs or {}).get("halt_state")
            new_state = "killed" if prev == "killed" else "active"
            await self._repo.set_risk_state(
                bucket,
                halt_state=new_state,
                day_start_realized_krw=realized_krw,
                day_start_unrealized_krw=unrealized_krw,
                # 자본은 fabricate 안 함 — 미제공 시 None(realized+unrealized ≠ equity, 리뷰 #14).
                day_start_equity_krw=equity_krw,
                daily_notional_used_krw=0,
                last_reset_day=today,
            )
            return DailyLossStatus(
                bucket, new_state, 0.0, 0.0, float(limits.max_daily_loss_eval), reset=True
            )

        # 같은 거래일 → 저장 baseline 복원해 손실 계산(baseline 안 덮어씀).
        base_r = float(rs.get("day_start_realized_krw") or 0.0)
        base_u = float(rs.get("day_start_unrealized_krw") or 0.0)
        realized_loss = max(0.0, base_r - realized_krw)
        eval_loss = max(0.0, (base_r - realized_krw) + (base_u - unrealized_krw))
        cur_state = rs.get("halt_state") or "active"
        breach = realized_loss >= float(limits.max_daily_loss_realized) or eval_loss >= float(
            limits.max_daily_loss_eval
        )
        new_state = "halted_daily" if (cur_state == "active" and breach) else cur_state
        if new_state != cur_state:
            await self._repo.set_risk_state(bucket, halt_state=new_state)
            await self._repo.insert_risk_event(
                event_type="halted_daily",
                severity="critical",
                scope=bucket,
                scope_ref=None,
                message=f"daily loss halt: realized={realized_loss:.0f} eval={eval_loss:.0f}",
                detail={"realized_loss_krw": realized_loss, "eval_loss_krw": eval_loss},
            )
        budget = max(0.0, float(limits.max_daily_loss_eval) - eval_loss)
        return DailyLossStatus(bucket, new_state, realized_loss, eval_loss, budget, reset=False)
