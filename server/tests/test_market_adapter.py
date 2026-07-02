"""시세 어댑터 단위 테스트 — 가짜 LS facade 로 TR 호출을 검증.

검증:
  - get_quote 시 올바른 TR(t1102/g3101/o3105)이 symbol 담은 InBlock 으로 호출됨.
  - options= 에 on_rate_limit="wait" 가 들어감.
"""

from __future__ import annotations

from programgarden_finance.ls.models import SetupOptions

from app.adapters.korea_stock import KoreaStockAdapter
from app.adapters.overseas_future import OverseasFutureAdapter
from app.adapters.overseas_stock import OverseasStockAdapter
from app.core.mode_matrix import (
    MARKET_KOREA_STOCK,
    MARKET_OVERSEAS_FUTUREOPTION,
    MARKET_OVERSEAS_STOCK,
)


class _FakeTr:
    def __init__(self, response):
        self._response = response

    async def req_async(self):
        return self._response


class _FakeResp:
    def __init__(self, block=None, block1=None):
        self.block = block
        self.block1 = block1 or []
        self.rsp_msg = "ok"


class _Recorder:
    """TR 메서드 호출을 기록하는 가짜 market()/chart() 객체."""

    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def _make(self, name):
        def _method(*, body, options=None, header=None):
            self.calls.append({"name": name, "body": body, "options": options})
            return _FakeTr(self._response)

        return _method

    def __getattr__(self, name):
        return self._make(name)


class _FakeFacade:
    def __init__(self, market_resp, chart_resp=None):
        self._market = _Recorder(market_resp)
        self._chart = _Recorder(chart_resp or _FakeResp())

    def market(self):
        return self._market

    def chart(self):
        return self._chart


class _FakeSession:
    """client_for/quote_opts/is_authenticated 만 제공하는 가짜 세션."""

    def __init__(self, market, facade):
        self._market = market
        self._facade = facade

    def client_for(self, market):
        assert market == self._market
        return self._facade

    def is_authenticated(self, market):
        return True

    def quote_opts(self):
        return SetupOptions(on_rate_limit="wait", rate_limit_count=2, rate_limit_seconds=1)


# ── 가짜 OutBlock 들 (필요 필드만) ────────────────────────────────────────


class _KoreaBlock:
    price = 79800
    recprice = 79000
    change = 800
    diff = 1.02
    volume = 15000000


class _OverseasBlock:
    price = "150.25"
    diff = "1.10"
    rate = 0.74
    volume = 1000000


class _FutureBlock:
    TrdP = 5800.25
    CloseP = 5790.0
    YdiffP = 10.25
    Diff = 0.18
    TotQ = 150000


async def test_korea_get_quote_calls_t1102():
    facade = _FakeFacade(_FakeResp(block=_KoreaBlock()))
    session = _FakeSession(MARKET_KOREA_STOCK, facade)
    adapter = KoreaStockAdapter(session)

    quote = await adapter.get_quote("005930")

    call = facade.market().calls[0]
    assert call["name"] == "t1102"
    assert call["body"].shcode == "005930"  # symbol 이 InBlock 에 담김
    assert call["options"].on_rate_limit == "wait"
    assert quote.price == 79800.0
    assert quote.prev_close == 79000.0
    assert quote.market == MARKET_KOREA_STOCK


async def test_overseas_get_quote_calls_g3101():
    facade = _FakeFacade(_FakeResp(block=_OverseasBlock()))
    session = _FakeSession(MARKET_OVERSEAS_STOCK, facade)
    adapter = OverseasStockAdapter(session)

    quote = await adapter.get_quote("82:TSLA")

    call = facade.market().calls[0]
    assert call["name"] == "g3101"
    assert call["body"].symbol == "TSLA"
    assert call["body"].exchcd == "82"
    assert call["body"].keysymbol == "82TSLA"
    assert call["options"].on_rate_limit == "wait"
    assert quote.price == 150.25
    assert quote.change_rate == 0.74


async def test_future_get_quote_calls_o3105():
    facade = _FakeFacade(_FakeResp(block=_FutureBlock()))
    session = _FakeSession(MARKET_OVERSEAS_FUTUREOPTION, facade)
    adapter = OverseasFutureAdapter(session)

    quote = await adapter.get_quote("ESZ25")

    call = facade.market().calls[0]
    assert call["name"] == "o3105"
    assert call["body"].symbol == "ESZ25"  # symbol 이 InBlock 에 담김
    assert call["options"].on_rate_limit == "wait"
    assert quote.price == 5800.25
    assert quote.prev_close == 5790.0


async def test_korea_get_ohlcv_calls_t8451():
    rows = [type("R", (), {
        "date": "20260228", "open": 100, "high": 110,
        "low": 90, "close": 105, "jdiff_vol": 5000,
    })()]
    facade = _FakeFacade(_FakeResp(), chart_resp=_FakeResp(block1=rows))
    session = _FakeSession(MARKET_KOREA_STOCK, facade)
    adapter = KoreaStockAdapter(session)

    candles = await adapter.get_ohlcv("005930", period="D", count=10)

    call = facade.chart().calls[0]
    assert call["name"] == "t8451"
    assert call["body"].shcode == "005930"
    assert call["body"].gubun == "2"  # D → '2'
    assert call["options"].on_rate_limit == "wait"
    assert len(candles) == 1
    assert candles[0].c == 105.0
    assert candles[0].v == 5000
