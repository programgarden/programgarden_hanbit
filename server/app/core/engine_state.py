"""런타임 엔진 상태 — 주문 경로 단일 권위 (M3b §0.2-3 / §7.1).

config `HANBIT_ENGINE_STATE`(READ_ONLY|PAPER_TRADING)는 **의도(intent)** 일 뿐이고,
실제 주문/정정/취소 허용은 이 **런타임 EngineState** 가 단일 권위로 판정한다.
부트 스테이트머신(`app/orders/boot.py`)이 이 객체를 소유하며 READ_ONLY → RECONCILING →
ACTIVE(또는 READ_ONLY 유지) 로 전이시킨다. 게이트 step0 와 `OrderService._get_mutable`
(amend/cancel) 이 둘 다 이 값을 읽는다(검증 Lens2-H1/Lens3-L1 — config 이중 판독 폐기).

- **READ_ONLY**   주문/정정/취소 전부 금지(부트 전·복구 실패·config 의도 READ_ONLY).
- **RECONCILING** 부트 reconcile 진행 중 — 거래 금지(§8 reconcile-구동 취소만 예외 경로).
- **ACTIVE**      게이트 step0 통과. config 의도 PAPER_TRADING + 부트 성공일 때만.
"""

from __future__ import annotations


class EngineState:
    """주문 경로 허용을 판정하는 런타임 상태(가변 단일 인스턴스)."""

    READ_ONLY = "READ_ONLY"
    RECONCILING = "RECONCILING"
    ACTIVE = "ACTIVE"

    _ALL = frozenset({READ_ONLY, RECONCILING, ACTIVE})

    def __init__(self, state: str = READ_ONLY) -> None:
        self.set(state)

    @property
    def state(self) -> str:
        return self._state

    def set(self, state: str) -> None:
        if state not in self._ALL:
            raise ValueError(f"unknown engine state: {state!r}")
        self._state = state

    @property
    def can_trade(self) -> bool:
        """신규/정정/취소 허용 여부 — ACTIVE 일 때만."""
        return self._state == self.ACTIVE

    @classmethod
    def from_config(cls, settings) -> EngineState:
        """config 의도에서 초기 런타임 상태 도출.

        PAPER_TRADING(거래 의도) → ACTIVE(빈 책 부트 결과와 동일), READ_ONLY → READ_ONLY.
        실제 운영 부팅은 `boot_engine` 이 이 위에서 READ_ONLY→RECONCILING→ACTIVE 를 재구동한다.
        """
        enabled = bool(getattr(settings, "engine_trading_enabled", False))
        return cls(cls.ACTIVE if enabled else cls.READ_ONLY)

    def __repr__(self) -> str:  # pragma: no cover - 디버그 표현
        return f"EngineState({self._state})"
