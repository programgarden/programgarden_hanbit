"""해외선물 read-only 시세 어댑터.

현재가: overseas_futureoption().market().o3105 (O3105InBlock: symbol)
  ⚠ o3101 은 '마스터'라 현재가 아님 → o3105 사용(검증).
일/주/월봉: overseas_futureoption().chart().o3108
  (O3108InBlock: shcode, gubun, qrycnt, sdate, edate, cts_date)
  ⚠ o3103 은 분봉 → o3108 사용(검증).

InBlock/OutBlock 필드명은 설치된 소스(blocks.py)에서 확인됨.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from programgarden_finance.ls.overseas_futureoption.chart.o3108.blocks import O3108InBlock
from programgarden_finance.ls.overseas_futureoption.market.o3105.blocks import O3105InBlock

from app.adapters.base import MarketDataError, require_price
from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION
from app.models.dto import Candle, Quote

if TYPE_CHECKING:
    from app.core.sessions import SessionManager

# o3108 gubun: '0'=일 '1'=주 '2'=월 (국내/해외주식과 다름! 소스 확인)
_PERIOD_TO_GUBUN = {"D": "0", "W": "1", "M": "2"}


class OverseasFutureAdapter:
    """해외선물 시세 어댑터."""

    market = MARKET_OVERSEAS_FUTUREOPTION

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
        # O3105InBlock: symbol(LS 계약 심볼, 예 'ESZ25')
        tr = facade.market().o3105(
            body=O3105InBlock(symbol=symbol),
            options=self._session.quote_opts(),
        )
        resp = await tr.req_async()
        block = getattr(resp, "block", None)
        if block is None:
            raise MarketDataError(
                "QUOTE_UNAVAILABLE",
                f"no quote for {symbol}: {getattr(resp, 'rsp_msg', '')}",
            )
        # O3105OutBlock: TrdP(체결가/현재가), CloseP(전일종가),
        # YdiffP(전일대비), Diff(등락율), TotQ(누적거래량)
        # 빈/0 가격은 QUOTE_UNAVAILABLE 로 처리.
        price = require_price(block.TrdP, symbol, getattr(resp, "rsp_msg", ""))
        return Quote(
            symbol=symbol,
            market=self.market,
            price=price,
            prev_close=float(block.CloseP),
            change=float(block.YdiffP),
            change_rate=float(block.Diff),
            volume=int(block.TotQ),
        )

    async def get_ohlcv(
        self, symbol: str, period: str = "D", count: int = 100
    ) -> list[Candle]:
        facade = self._facade()
        gubun = _PERIOD_TO_GUBUN.get(period.upper())
        if gubun is None:
            raise MarketDataError(
                "UNSUPPORTED_PERIOD", f"period '{period}' not supported (D/W/M)"
            )
        # O3108InBlock: shcode(심볼!), gubun, qrycnt, sdate, edate, cts_date.
        # sdate/edate 는 required 라 '최신부터' 의미로 sdate=''/edate='99999999'.
        # # TODO(M1-④): sdate/edate 범위·연속조회(cts_date) 의미를 키로 확정.
        tr = facade.chart().o3108(
            body=O3108InBlock(
                shcode=symbol,
                gubun=gubun,
                qrycnt=int(count),
                sdate="",
                edate="99999999",
                cts_date="",
            ),
            options=self._session.quote_opts(),
        )
        resp = await tr.req_async()
        # O3108: 봉 rows 는 block1(List[O3108OutBlock1]).
        # row: date, open, high, low, close, volume
        rows = getattr(resp, "block1", None) or []
        return [
            Candle(
                date=row.date,
                o=float(row.open),
                h=float(row.high),
                low=float(row.low),
                c=float(row.close),
                v=int(row.volume),
            )
            for row in rows
        ]
