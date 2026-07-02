"""시세 서비스 — 라우터와 어댑터 사이 얇은 위임 계층.

시장 미인증/미지원 시 도메인 에러(MarketDataError)를 올린다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters import MarketDataError, make_market_data_adapter
from app.core.mode_matrix import MODE_MATRIX
from app.models.dto import Candle, Quote

if TYPE_CHECKING:
    from app.core.sessions import SessionManager

_SUPPORTED_MARKETS = {entry["market"] for entry in MODE_MATRIX}


class MarketService:
    """시세 조회 위임 서비스."""

    def __init__(self, session: SessionManager) -> None:
        self._session = session

    def _adapter(self, market: str):
        if market not in _SUPPORTED_MARKETS:
            raise MarketDataError("UNSUPPORTED_MARKET", f"unsupported market '{market}'")
        if not self._session.is_authenticated(market):
            raise MarketDataError(
                "MARKET_UNAUTHENTICATED",
                f"market '{market}' is not authenticated",
            )
        return make_market_data_adapter(market, self._session)

    async def get_quote(self, market: str, symbol: str) -> Quote:
        if not symbol or not symbol.strip():
            raise MarketDataError("INVALID_SYMBOL", "symbol is required")
        return await self._adapter(market).get_quote(symbol.strip())

    async def get_ohlcv(
        self, market: str, symbol: str, period: str = "D", count: int = 100
    ) -> list[Candle]:
        if not symbol or not symbol.strip():
            raise MarketDataError("INVALID_SYMBOL", "symbol is required")
        return await self._adapter(market).get_ohlcv(
            symbol.strip(), period=period, count=count
        )
