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
        candles = await self._adapter(market).get_ohlcv(
            symbol.strip(), period=period, count=count
        )
        # 어댑터는 "최신 N개"를 desc(최신순)로 준다(LS 일봉 기본). lightweight-charts
        # setData()는 시간 오름차순 필수 → 여기서 date 오름차순 정렬만 한다.
        # ⚠ 절대 재슬라이싱 금지: 이미 최신 N개로 잘린 리스트라 정렬 후 자르면
        #   가장 과거 N개가 남아 조용히 틀린 차트가 된다. (date=YYYYMMDD 고정폭 → 문자열정렬=시간순)
        return sorted(candles, key=lambda c: c.date)
