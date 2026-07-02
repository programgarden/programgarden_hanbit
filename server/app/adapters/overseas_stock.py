"""해외주식 read-only 시세 어댑터.

현재가: overseas_stock().market().g3101 (G3101InBlock: delaygb, keysymbol, exchcd, symbol)
일/주/월봉: overseas_stock().chart().g3103
  (G3103InBlock: delaygb, keysymbol, exchcd, symbol, gubun, date)

InBlock/OutBlock 필드명은 설치된 소스(blocks.py)에서 확인됨.

⚠ symbol→exchcd 매핑(거래소 결정)은 키/마스터 없이는 확정 불가하다.
  - exchcd: '81'=NYSE/AMEX, '82'=NASDAQ (소스 확인).
  - keysymbol = exchcd+symbol (예: '82TSLA') (소스 예시 확인).
  호출자는 "82:TSLA"(또는 "82TSLA") 형태로 거래소를 명시할 수 있고,
  미지정 시 NASDAQ('82')로 가정한다. # TODO(M1-④): 키/마스터로 거래소 자동 해석 확정.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from programgarden_finance.ls.overseas_stock.chart.g3103.blocks import G3103InBlock
from programgarden_finance.ls.overseas_stock.market.g3101.blocks import G3101InBlock

from app.adapters.base import MarketDataError, require_price
from app.core.mode_matrix import MARKET_OVERSEAS_STOCK
from app.models.dto import Candle, Quote

if TYPE_CHECKING:
    from app.core.sessions import SessionManager

# g3103 gubun: '2'=일 '3'=주 '4'=월 (년 미지원)
_PERIOD_TO_GUBUN = {"D": "2", "W": "3", "M": "4"}

_DEFAULT_EXCHCD = "82"  # NASDAQ (소스 기본 예시). # TODO(M1-④): 마스터로 확정.


def _split_symbol(symbol: str) -> tuple[str, str, str]:
    """호출 symbol 을 (exchcd, ticker, keysymbol) 로 분해한다.

    허용 형태:
      - "82:TSLA" → exchcd='82', ticker='TSLA'
      - "82TSLA"  → exchcd='82', ticker='TSLA'
      - "TSLA"    → exchcd=_DEFAULT_EXCHCD, ticker='TSLA'
    """
    raw = symbol.strip().upper()
    exchcd = _DEFAULT_EXCHCD
    ticker = raw
    if ":" in raw:
        prefix, ticker = raw.split(":", 1)
        if prefix in ("81", "82"):
            exchcd = prefix
    elif raw[:2] in ("81", "82") and len(raw) > 2 and not raw[2].isdigit():
        exchcd, ticker = raw[:2], raw[2:]
    return exchcd, ticker, f"{exchcd}{ticker}"


class OverseasStockAdapter:
    """해외주식 시세 어댑터."""

    market = MARKET_OVERSEAS_STOCK

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
        exchcd, ticker, keysymbol = _split_symbol(symbol)
        # G3101InBlock: delaygb='R'(실시간), keysymbol, exchcd, symbol(ticker)
        tr = facade.market().g3101(
            body=G3101InBlock(
                delaygb="R",
                keysymbol=keysymbol,
                exchcd=exchcd,
                symbol=ticker,
            ),
            options=self._session.quote_opts(),
        )
        resp = await tr.req_async()
        block = getattr(resp, "block", None)
        if block is None:
            raise MarketDataError(
                "QUOTE_UNAVAILABLE",
                f"no quote for {symbol}: {getattr(resp, 'rsp_msg', '')}",
            )
        # G3101OutBlock: price(현재가), sign(전일대비구분 '+'/'-'),
        # diff(전일대비 '절대값'), rate(등락률), volume. 전일종가 단독 필드 없음.
        # ⚠ diff 는 부호 없는 절대값으로 오므로 sign 으로 방향을 적용한다(M1-④ 라이브 확인).
        price = require_price(block.price, symbol, getattr(resp, "rsp_msg", ""))
        direction = -1.0 if str(getattr(block, "sign", "")).strip() == "-" else 1.0
        change = direction * abs(_to_float(block.diff))
        change_rate = direction * abs(_to_float(getattr(block, "rate", 0.0)))
        return Quote(
            symbol=symbol,
            market=self.market,
            price=price,
            # 전일종가 = 현재가 - 변화량(change=현재-전일). g3101 에 단독 필드 없어 도출.
            prev_close=round(price - change, 6),
            change=change,
            change_rate=change_rate,
            volume=int(block.volume),
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
        exchcd, ticker, keysymbol = _split_symbol(symbol)
        # G3103InBlock: delaygb='R', keysymbol, exchcd, symbol, gubun, date.
        # date='' 은 InBlock 이 required 라 빈 문자열을 넘긴다(최신 기준).
        # # TODO(M1-④): date 기준일 의미/연속조회(cts) 를 키로 확정.
        tr = facade.chart().g3103(
            body=G3103InBlock(
                delaygb="R",
                keysymbol=keysymbol,
                exchcd=exchcd,
                symbol=ticker,
                gubun=gubun,
                date="",
            ),
            options=self._session.quote_opts(),
        )
        resp = await tr.req_async()
        # G3103: 봉 rows 는 block1(List[G3103OutBlock1]).
        # row: chedate(영업일자), open, high, low, price(종가; 별도 close 없음), volume
        rows = getattr(resp, "block1", None) or []
        out = [
            Candle(
                date=row.chedate,
                o=float(row.open),
                h=float(row.high),
                low=float(row.low),
                c=_to_float(row.price),
                v=int(row.volume),
            )
            for row in rows
        ]
        return out[: int(count)] if count else out


def _to_float(value: object) -> float:
    """LS 가 문자열로 주는 숫자를 안전하게 float 로 변환(빈 값→0.0)."""
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
