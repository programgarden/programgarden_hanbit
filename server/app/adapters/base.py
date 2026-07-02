"""시세 어댑터 공통 인터페이스 — read-only.

각 시장 어댑터는 이 Protocol 을 만족한다. 주문/체결 메서드는 절대 두지 않는다
(M1 read-only 불변식).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.models.dto import Candle, Quote


class MarketDataError(Exception):
    """시세 조회 도메인 에러(미인증/미지원 등)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def require_price(raw: object, symbol: str, rsp_msg: str = "") -> float:
    """현재가를 float 로 파싱하되, 빈 값/0 이면 QUOTE_UNAVAILABLE 로 처리한다.

    LS 는 없는 종목에도 rsp 성공 + 기본값(0/'') 블록을 줄 수 있어, block 존재만으로는
    '실제 시세 있음' 을 보장하지 못한다. 가격 0/빈값을 '시세 없음' 으로 본다.
    """
    try:
        price = float(raw)
    except (TypeError, ValueError):
        price = 0.0
    if price == 0.0:
        raise MarketDataError(
            "QUOTE_UNAVAILABLE",
            f"no quote for {symbol}: {rsp_msg or 'empty/zero price'}",
        )
    return price


@runtime_checkable
class MarketDataAdapter(Protocol):
    """시장별 read-only 시세 어댑터 인터페이스."""

    market: str

    async def get_quote(self, symbol: str) -> Quote:
        """단일 종목 현재가를 정규화 Quote 로 반환한다."""
        ...

    async def get_ohlcv(
        self, symbol: str, period: str = "D", count: int = 100
    ) -> list[Candle]:
        """OHLCV 봉 리스트를 반환한다. period: 'D'|'W'|'M' (+가능시 'Y')."""
        ...
