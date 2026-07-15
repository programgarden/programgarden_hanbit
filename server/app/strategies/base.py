"""전략 인터페이스 + 신호 DTO (M5).

Signal = 전략이 낸 "이 종목을 이 방향으로 이 수량만큼" 이라는 의도. StrategyEngine 이 이걸
OrderIntent 로 옮겨 안전 파이프라인으로 보낸다. 전략은 주문 어댑터/게이트를 **직접 만지지 않는다**.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.models.dto import Quote
from app.models.order_dto import IntentKind, OrderType, Side


@dataclass
class Signal:
    """전략 출력 — 안전 파이프라인으로 보낼 주문 의도."""

    market: str
    symbol: str
    side: Side
    intent: IntentKind  # ENTRY(신규 진입) / EXIT(청산·reduce-only)
    qty: int
    price: float | None = None  # 지정가(보통 현재가). None → 시장가(LIVE 는 게이트가 거부)
    reason: str = ""  # 왜 이 신호가 났는가(감사/교육용)

    @property
    def order_type(self) -> OrderType:
        return OrderType.LIMIT if self.price is not None else OrderType.MARKET


@runtime_checkable
class Strategy(Protocol):
    """전략 인터페이스 — 시세/보유 스냅을 받아 신호 리스트를 낸다(부작용 없음)."""

    name: str
    market: str
    symbols: list[str]
    enabled: bool

    def evaluate(self, quotes: dict[str, Quote], positions: dict[str, dict]) -> list[Signal]:
        """시세(quotes)+보유(positions, symbol→행)로 신호 산출. 주문은 내지 않는다(순수)."""
        ...
