"""시세 API 테스트 — app.state.sessions 를 가짜로 주입.

검증:
  - /market/quote 정상 envelope.
  - 미인증 시장 → MARKET_UNAUTHENTICATED error envelope (크래시 아님).
  - 미지원 시장 → UNSUPPORTED_MARKET.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.adapters.base import MarketDataError
from app.models.dto import Quote


class _FakeSessions:
    """is_authenticated 만 보는 가짜 SessionManager.

    quote 응답은 MarketService 를 우회해 라우터까지의 흐름을 검증하기 위해
    어댑터 레지스트리를 패치하는 대신, 여기서는 인증 여부만 제어한다.
    """

    def __init__(self, authed: dict[str, bool]):
        self._authed = authed

    def is_authenticated(self, market: str) -> bool:
        return self._authed.get(market, False)

    def status(self):
        return {
            m: {
                "authenticated": v,
                "mode": "live",
                "status": "authenticated" if v else "unauthenticated",
            }
            for m, v in self._authed.items()
        }


@pytest_asyncio.fixture
async def api_client(app, monkeypatch):
    """app.state.sessions 주입 + 어댑터를 가짜로 패치한 클라이언트."""
    # 국내만 인증, 해외주식 미인증
    app.state.sessions = _FakeSessions(
        {"korea_stock": True, "overseas_stock": False, "overseas_futureoption": False}
    )

    # MarketService 가 부르는 어댑터를 가짜로 패치(네트워크 차단)
    class _FakeAdapter:
        def __init__(self, market):
            self.market = market

        async def get_quote(self, symbol):
            return Quote(
                symbol=symbol, market=self.market, price=79800.0,
                prev_close=79000.0, change=800.0, change_rate=1.02, volume=123,
            )

        async def get_ohlcv(self, symbol, period="D", count=100):
            return []

    import app.services.market_service as svc

    def _fake_make(market, session):
        return _FakeAdapter(market)

    monkeypatch.setattr(svc, "make_market_data_adapter", _fake_make)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with app.router.lifespan_context(app):
            # lifespan 이 app.state.sessions 를 진짜로 덮어쓰므로 재주입
            app.state.sessions = _FakeSessions(
                {"korea_stock": True, "overseas_stock": False, "overseas_futureoption": False}
            )
            yield ac


async def test_quote_ok_envelope(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/market/quote?market=korea_stock&symbol=005930")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["symbol"] == "005930"
    assert body["data"]["price"] == 79800.0
    assert body["data"]["market"] == "korea_stock"


async def test_quote_unauthenticated_market(api_client: AsyncClient):
    resp = await api_client.get(
        "/api/v1/market/quote?market=overseas_stock&symbol=TSLA"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "MARKET_UNAUTHENTICATED"


async def test_quote_unsupported_market(api_client: AsyncClient):
    resp = await api_client.get("/api/v1/market/quote?market=crypto&symbol=BTC")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "UNSUPPORTED_MARKET"


def test_market_data_error_carries_code():
    err = MarketDataError("X_CODE", "msg")
    assert err.code == "X_CODE"
