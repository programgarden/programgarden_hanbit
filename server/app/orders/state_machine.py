"""주문 상태머신 — 명시 전이표 + 가드 + order_id 단일 writer 락.

설계: .claude/plans/2026-06-20-통합계획서.md M2 §3.

순수 검증 계층: 어떤 전이가 합법인지만 판정한다. 실제 DB 쓰기(orders.status +
order_state_transitions)는 repository/서비스가 이 판정을 거쳐 수행한다.
모든 mutate 는 order_id 별 락(OrderLocks)에서 직렬화(place/watchdog/reconcile 경쟁 방지).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from app.models.order_dto import TERMINAL_STATES, OrderState

# 합법 전이표 (from → 허용 to 집합). .claude/plans/2026-06-20-통합계획서.md M2 §3 표와 1:1.
# 주의: accepted→rejected 는 **place-time(submitted→rejected)** 전용 — accept 이후 브로커
#       거부신호는 FUT 미검증이라 canceled/expired 로만 매핑(M2).
#       M3b(§7.2): 비터미널 주문은 boot 가 quarantined(격리) 로 보낼 수 있다 — reconcile 로
#       해소 불가한 미확정 노출을 자동전이 없는 sink 로 묶는다. quarantined 이탈은 운영 수동만.
_LEGAL: dict[OrderState, frozenset[OrderState]] = {
    OrderState.APPROVED: frozenset({OrderState.SUBMITTED}),
    OrderState.SUBMITTED: frozenset(
        {
            OrderState.ACCEPTED,
            OrderState.REJECTED,
            OrderState.IN_DOUBT,
            OrderState.CANCELED,
            OrderState.QUARANTINED,
        }
    ),
    OrderState.IN_DOUBT: frozenset(
        {
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.REJECTED,
            OrderState.CANCELED,
            OrderState.EXPIRED,
            OrderState.QUARANTINED,
        }
    ),
    OrderState.ACCEPTED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.EXPIRED,
            OrderState.QUARANTINED,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {OrderState.FILLED, OrderState.CANCELED, OrderState.EXPIRED, OrderState.QUARANTINED}
    ),
    OrderState.FILLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.CANCELED: frozenset(),
    OrderState.EXPIRED: frozenset(),
    OrderState.QUARANTINED: frozenset(),  # sink — 운영 수동 resolve 만(자동 전이 없음)
}


class StateMachineError(Exception):
    """비합법 상태 전이."""

    def __init__(self, frm: OrderState, to: OrderState, reason: str) -> None:
        super().__init__(f"illegal transition {frm}→{to}: {reason}")
        self.frm = frm
        self.to = to
        self.reason = reason


def is_terminal(state: OrderState) -> bool:
    return state in TERMINAL_STATES


def can_transition(frm: OrderState, to: OrderState) -> bool:
    """from→to 가 합법인가(터미널 불변 포함)."""
    if frm in TERMINAL_STATES:
        return False
    return to in _LEGAL.get(frm, frozenset())


def assert_transition(frm: OrderState, to: OrderState) -> None:
    """비합법이면 StateMachineError. 합법이면 통과."""
    if frm in TERMINAL_STATES:
        raise StateMachineError(frm, to, "source state is terminal")
    if to not in _LEGAL.get(frm, frozenset()):
        raise StateMachineError(frm, to, "not in legal transition set")


class OrderLocks:
    """order_id 별 asyncio 락 — 단일 writer 보장(place/watchdog/reconcile 직렬화)."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def lock(self, order_id: int) -> asyncio.Lock:
        return self._locks[order_id]
