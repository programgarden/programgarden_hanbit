"""read-only 시세 어댑터 패키지 + 레지스트리.

``make_market_data_adapter(market, session)`` 로 시장별 어댑터를 생성한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.base import MarketDataAdapter, MarketDataError
from app.adapters.korea_stock import KoreaStockAdapter
from app.adapters.overseas_future import OverseasFutureAdapter
from app.adapters.overseas_stock import OverseasStockAdapter
from app.core.mode_matrix import (
    MARKET_KOREA_STOCK,
    MARKET_OVERSEAS_FUTUREOPTION,
    MARKET_OVERSEAS_STOCK,
)

if TYPE_CHECKING:
    from app.core.sessions import SessionManager

_ADAPTERS = {
    MARKET_KOREA_STOCK: KoreaStockAdapter,
    MARKET_OVERSEAS_STOCK: OverseasStockAdapter,
    MARKET_OVERSEAS_FUTUREOPTION: OverseasFutureAdapter,
}


def make_market_data_adapter(
    market: str, session: SessionManager
) -> MarketDataAdapter:
    """시장 키에 맞는 시세 어댑터를 생성한다. 미지원 시장 → MarketDataError."""
    cls = _ADAPTERS.get(market)
    if cls is None:
        raise MarketDataError(
            "UNSUPPORTED_MARKET", f"unsupported market '{market}'"
        )
    return cls(session)


__all__ = [
    "MarketDataAdapter",
    "MarketDataError",
    "make_market_data_adapter",
]
