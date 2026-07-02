"""국내주식 read-only 시세 어댑터.

현재가: korea_stock().market().t1102 (T1102InBlock: shcode, exchgubun)
일/주/월/년봉: korea_stock().chart().t8451 (T8451InBlock: shcode, gubun, qrycnt, ...)

InBlock/OutBlock 필드명은 설치된 소스(blocks.py)에서 확인됨.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from programgarden_finance.ls.korea_stock.chart.t8451.blocks import T8451InBlock
from programgarden_finance.ls.korea_stock.market.t1102.blocks import T1102InBlock

from app.adapters.base import MarketDataError, require_price
from app.core.mode_matrix import MARKET_KOREA_STOCK
from app.models.dto import Candle, Quote

if TYPE_CHECKING:
    from app.core.sessions import SessionManager

# t8451 gubun: '2'=일 '3'=주 '4'=월 '5'=년
_PERIOD_TO_GUBUN = {"D": "2", "W": "3", "M": "4", "Y": "5"}


class KoreaStockAdapter:
    """국내주식 시세 어댑터."""

    market = MARKET_KOREA_STOCK

    def __init__(self, session: SessionManager) -> None:
        self._session = session

    def _facade(self):
        facade = self._session.client_for(self.market)
        if facade is None:
            raise MarketDataError(
                "MARKET_UNAUTHENTICATED",
                f"market '{self.market}' is not authenticated",
            )
        return facade

    async def get_quote(self, symbol: str) -> Quote:
        facade = self._facade()
        # T1102InBlock: shcode(6자리 종목코드), exchgubun(기본 'K'=KRX)
        tr = facade.market().t1102(
            body=T1102InBlock(shcode=symbol),
            options=self._session.quote_opts(),
        )
        resp = await tr.req_async()
        block = getattr(resp, "block", None)
        if block is None:
            raise MarketDataError(
                "QUOTE_UNAVAILABLE",
                f"no quote for {symbol}: {getattr(resp, 'rsp_msg', '')}",
            )
        # T1102OutBlock: price(현재가), recprice(기준가/평가가격),
        # change(전일대비), diff(등락율), volume(누적거래량)
        # 빈/0 가격(없는 종목)은 QUOTE_UNAVAILABLE 로 처리.
        price = require_price(block.price, symbol, getattr(resp, "rsp_msg", ""))
        return Quote(
            symbol=symbol,
            market=self.market,
            price=price,
            prev_close=float(block.recprice),
            change=float(block.change),
            change_rate=float(block.diff),
            volume=int(block.volume),
        )

    async def get_ohlcv(
        self, symbol: str, period: str = "D", count: int = 100
    ) -> list[Candle]:
        facade = self._facade()
        gubun = _PERIOD_TO_GUBUN.get(period.upper())
        if gubun is None:
            raise MarketDataError(
                "UNSUPPORTED_PERIOD", f"period '{period}' not supported (D/W/M/Y)"
            )
        # T8451InBlock: shcode, gubun, qrycnt(최대 500), edate='99999999'(최신부터)
        tr = facade.chart().t8451(
            body=T8451InBlock(
                shcode=symbol,
                gubun=gubun,
                qrycnt=min(int(count), 500),
            ),
            options=self._session.quote_opts(),
        )
        resp = await tr.req_async()
        # T8451: 봉 rows 는 block1(List[T8451OutBlock1]).
        # row: date, open, high, low, close, jdiff_vol(거래량)
        rows = getattr(resp, "block1", None) or []
        return [
            Candle(
                date=row.date,
                o=float(row.open),
                h=float(row.high),
                low=float(row.low),
                c=float(row.close),
                v=int(row.jdiff_vol),
            )
            for row in rows
        ]
