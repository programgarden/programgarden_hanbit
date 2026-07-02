"""주문/리스크 API e2e 테스트 — lifespan 우회 + fake adapter (네트워크/실세션 없음).

검증: commit(FUT) 수락, KR/OVS 403, open/history, cancel, killswitch 후 신규 거부,
risk/limits, system/metrics, quote 미리보기.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.event_bus import EventBus
from app.main import create_app
from app.services.order_service import OrderService
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter

V1 = "/api/v1"


@pytest_asyncio.fixture
async def api(monkeypatch):
    repo = await make_repo()
    fake = FakeOrderAdapter()
    patch_adapter(monkeypatch, fake)
    app = create_app()
    # lifespan 우회: app.state 를 테스트 더블로 직접 채운다(세션 로그인/네트워크 없음).
    app.state.sessions = None
    app.state.repo = repo
    app.state.event_bus = EventBus()
    app.state.order_service = OrderService(
        repo, None, fake_settings("PAPER_TRADING"), event_bus=app.state.event_bus
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, repo, fake


def _commit_body(**kw):
    base = dict(symbol="ADZ25", side="buy", order_type="limit", qty=2, price=0.65)
    base.update(kw)
    return base


async def test_commit_fut_accepted(api):
    ac, repo, fake = api
    r = await ac.post(f"{V1}/orders/commit", json=_commit_body())
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] and data["order"]["status"] == "accepted"


async def test_commit_live_market_403(api):
    ac, repo, fake = api
    r = await ac.post(f"{V1}/orders/commit", json=_commit_body(market="korea_stock"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "LIVE_DISABLED"
    assert fake.calls == []  # 어댑터 호출 없음


async def test_open_and_cancel(api):
    ac, repo, fake = api
    await ac.post(f"{V1}/orders/commit", json=_commit_body())
    open_r = await ac.get(f"{V1}/orders/open")
    orders = open_r.json()["data"]["orders"]
    assert len(orders) == 1
    oid = orders[0]["id"]
    c = await ac.post(f"{V1}/orders/{oid}/cancel")
    assert c.status_code == 200 and c.json()["data"]["ok"]
    # 취소 후 open 비어야
    again = await ac.get(f"{V1}/orders/open")
    assert again.json()["data"]["orders"] == []


async def test_killswitch_blocks_new_orders(api):
    ac, repo, fake = api
    ks = await ac.post(f"{V1}/risk/killswitch", json={"scope": "global", "action": "engage"})
    assert ks.status_code == 200 and ks.json()["data"]["state"] == "killed"
    r = await ac.post(f"{V1}/orders/commit", json=_commit_body())
    assert r.status_code == 422
    reasons = r.json()["error"]["detail"]["decision"]["reasons"]
    assert "KILL_SWITCH" in reasons
    # 해제 후 다시 허용
    await ac.post(f"{V1}/risk/killswitch", json={"scope": "global", "action": "release"})
    ok = await ac.post(f"{V1}/orders/commit", json=_commit_body())
    assert ok.status_code == 200 and ok.json()["data"]["ok"]


async def test_risk_limits_and_metrics(api):
    ac, repo, fake = api
    await ac.post(f"{V1}/orders/commit", json=_commit_body())
    lim = (await ac.get(f"{V1}/risk/limits")).json()["data"]
    assert lim["limits"]["max_contracts_per_order"] == 10
    assert lim["halt"]["global"] == "active"
    metrics = (await ac.get(f"{V1}/system/metrics")).json()["data"]["metrics"]
    assert metrics["orders_placed"] == 1


async def test_quote_preview(api):
    ac, repo, fake = api
    q = await ac.post(f"{V1}/orders/quote", json=_commit_body())
    data = q.json()["data"]
    assert "decision" in data
    # 화이트리스트 + PAPER_TRADING → 통과(confirm_token 발급)
    assert data["decision"]["result"] in ("pass", "warn")
    assert data["confirm_token"]


async def test_health_reports_runtime_engine_state(api):
    ac, repo, fake = api
    h = (await ac.get(f"{V1}/system/health")).json()["data"]
    # M3b §11 — 런타임 EngineState 노출(PAPER_TRADING fixture → ACTIVE) + milestone M3.
    assert h["milestone"] == "M3" and h["engine_state"] == "ACTIVE"
