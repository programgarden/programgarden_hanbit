"""시세 라우터 — M1 read-only 현재가/OHLCV.

GET /api/v1/market/quote?market=&symbol=
GET /api/v1/market/ohlcv?market=&symbol=&period=D&count=

미인증/미지원 시장/잘못된 symbol → 공통 error envelope(크래시 금지).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.adapters import MarketDataError
from app.api.deps import get_market_service
from app.models.schemas import failure, success
from app.services.market_service import MarketService

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/quote")
async def market_quote(
    service: Annotated[MarketService, Depends(get_market_service)],
    market: Annotated[str, Query(description="시장 키")],
    symbol: Annotated[str, Query(description="종목코드/심볼")],
) -> dict[str, Any]:
    """단일 종목 현재가."""
    try:
        quote = await service.get_quote(market, symbol)
    except MarketDataError as exc:
        return failure(exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001 — 라우터에서 크래시 금지
        return failure("QUOTE_ERROR", str(exc))
    return success(quote.model_dump())


@router.get("/ohlcv")
async def market_ohlcv(
    service: Annotated[MarketService, Depends(get_market_service)],
    market: Annotated[str, Query(description="시장 키")],
    symbol: Annotated[str, Query(description="종목코드/심볼")],
    period: Annotated[str, Query(description="D|W|M (가능 시 Y)")] = "D",
    count: Annotated[int, Query(ge=1, le=500, description="요청 봉 개수")] = 100,
) -> dict[str, Any]:
    """OHLCV 봉 리스트."""
    try:
        candles = await service.get_ohlcv(market, symbol, period=period, count=count)
    except MarketDataError as exc:
        return failure(exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001 — 라우터에서 크래시 금지
        return failure("OHLCV_ERROR", str(exc))
    return success(
        {
            "market": market,
            "symbol": symbol,
            "period": period.upper(),
            "candles": [c.model_dump(by_alias=True) for c in candles],
        }
    )
