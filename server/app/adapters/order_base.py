"""주문 어댑터 공통 인터페이스 (M2) — 시장별 LS 주문/조회 TR 을 시장 무관 형태로 흡수.

M1 의 ``MarketDataAdapter`` 는 read-only(주문 메서드 금지) 불변이므로, 주문 경로는
이 별도 인터페이스로 분리한다. M2 구현체는 해외선물(paper) 하나뿐이며, 레지스트리가
``overseas_futureoption`` 외 시장의 생성을 거부한다(KR/OVS 주문 경로 부재 — M4까지 닫힘).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.models.order_dto import (
    AmendRequest,
    CancelRequest,
    OpenOrder,
    OrderAck,
    OrderIntent,
    Position,
)


class OrderError(Exception):
    """주문 도메인 에러(미인증/미지원/잘못된 입력 등)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@runtime_checkable
class BrokerOrderAdapter(Protocol):
    """시장별 주문/조회 어댑터 인터페이스 (paper FUT 한정 구현)."""

    market: str

    async def place_order(self, intent: OrderIntent) -> OrderAck:
        """신규 주문 TR 호출 → OrderAck(성공판정 + OrdNo)."""
        ...

    async def amend_order(self, req: AmendRequest) -> OrderAck:
        """정정 주문 TR 호출."""
        ...

    async def cancel_order(self, req: CancelRequest) -> OrderAck:
        """취소 주문 TR 호출."""
        ...

    async def get_open_orders(
        self, symbol: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[OpenOrder]:
        """미체결/체결 조회(종목+날짜창 필수) → reconcile 1차 소스."""
        ...

    async def get_positions(self) -> list[Position]:
        """미결제잔고 조회 → 포지션 + orderable."""
        ...
