"""전략 API (M5) — 목록/토글/수동실행 e2e. lifespan 우회 + fake adapter(네트워크 없음)."""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.engine_state import EngineState
from app.core.event_bus import EventBus
from app.core.mode_matrix import BUCKET_PAPER, MARKET_OVERSEAS_FUTUREOPTION
from app.main import create_app
from app.models.dto import Quote
from app.services.order_service import OrderService
from app.strategies.engine import StrategyEngine
from app.strategies.threshold import ThresholdStrategy
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter

V1 = "/api/v1"
FUT = MARKET_OVERSEAS_FUTUREOPTION


@pytest_asyncio.fixture
async def sapi(monkeypatch):
    repo = await make_repo()
    fake = FakeOrderAdapter()
    patch_adapter(monkeypatch, fake)
    app = create_app()
    app.state.sessions = None
    app.state.repo = repo
    app.state.event_bus = EventBus()
    svc = OrderService(repo, None, fake_settings("PAPER_TRADING"), event_bus=app.state.event_bus)
    svc.engine_for(BUCKET_PAPER).set(EngineState.ACTIVE)
    app.state.order_service = svc

    quotes = {"ADZ25": Quote(symbol="ADZ25", market=FUT, price=0.65, change_rate=-4.0)}

    async def quote_fn(market, symbol):
        return quotes[symbol]

    engine = StrategyEngine(svc, repo, quote_fn, enabled=False)
    engine.add_strategy(ThresholdStrategy("t", FUT, ["ADZ25"], qty=1, buy_drop_pct=3.0))
    app.state.strategy_engine = engine

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, repo, fake, engine


async def test_list_strategies(sapi):
    ac, *_ = sapi
    r = await ac.get(f"{V1}/strategy")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["enabled"] is False
    assert data["strategies"][0]["name"] == "t"
    assert data["strategies"][0]["symbols"] == ["ADZ25"]


async def test_run_disabled_fires_nothing(sapi):
    ac, _repo, fake, _engine = sapi
    r = await ac.post(f"{V1}/strategy/run")
    assert r.json()["data"] == {"enabled": False, "fired": []}
    assert fake.calls == []  # 토글 off → 발주 0


async def test_toggle_then_run_fires_buy(sapi):
    ac, _repo, fake, _engine = sapi
    t = await ac.post(f"{V1}/strategy/toggle", json={"enabled": True})
    assert t.json()["data"]["enabled"] is True
    r = await ac.post(f"{V1}/strategy/run")
    data = r.json()["data"]
    assert data["enabled"] is True and len(data["fired"]) == 1
    fired = data["fired"][0]
    assert fired["ok"] is True and fired["side"] == "buy"  # -4% → 자동 매수
    assert any(c[0] == "place" for c in fake.calls)  # 안전 파이프라인 경유 발주
