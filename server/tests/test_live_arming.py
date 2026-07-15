"""사이트 실거래 무장(arming) — 2-key 안전(§M4d 사이트 토글).

- env HANBIT_ALLOW_LIVE(허용 ceiling) 없으면 무장 불가(PERMISSION_OFF) → env=false→실주문 0 불변식.
- 정확한 확인 문구 없으면 무장 불가(BAD_CONFIRM).
- **게이트가 런타임 무장 상태를 읽는다**: 무장해제→LIVE place 차단 / 무장→통과(핵심 정합).
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.engine_state import EngineState
from app.core.event_bus import EventBus
from app.core.mode_matrix import BUCKET_LIVE, MARKET_KOREA_STOCK
from app.main import create_app
from app.models.order_dto import OrderAck, OrderIntent, OrderType, Side
from app.services.order_service import LIVE_ARM_PHRASE, OrderService
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter

V1 = "/api/v1"


def _kr_intent():
    return OrderIntent(
        market=MARKET_KOREA_STOCK, symbol="005930", side=Side.BUY,
        order_type=OrderType.LIMIT, qty=1, price=50000, currency="KRW",
    )


async def _svc(monkeypatch, *, permission):
    repo = await make_repo()
    await repo.ensure_account(
        MARKET_KOREA_STOCK, "KR-ACCT", trading_mode="live", currency="KRW"
    )
    fake = FakeOrderAdapter()
    patch_adapter(monkeypatch, fake)
    svc = OrderService(repo, None, fake_settings(allow_live=permission))
    svc.engine_for(BUCKET_LIVE).set(EngineState.ACTIVE)
    return svc, repo, fake


# ── env 허용(permission) ceiling ────────────────────────────────────────────
async def test_arm_refused_without_permission(monkeypatch):
    svc, _repo, _fake = await _svc(monkeypatch, permission=False)
    r = await svc.arm_live(LIVE_ARM_PHRASE)
    assert r["ok"] is False and r["reason"] == "PERMISSION_OFF"
    assert svc.live_arming() == {"armed": False, "permission": False}
    assert svc._allow_live() is False  # env=false → 절대 무장 불가


async def test_arm_bad_confirm(monkeypatch):
    svc, _repo, _fake = await _svc(monkeypatch, permission=True)
    await svc.disarm_live()
    r = await svc.arm_live("아무거나")
    assert r["ok"] is False and r["reason"] == "BAD_CONFIRM"
    assert svc._allow_live() is False


async def test_arm_then_disarm(monkeypatch):
    svc, _repo, _fake = await _svc(monkeypatch, permission=True)
    await svc.disarm_live()
    r = await svc.arm_live(LIVE_ARM_PHRASE)
    assert r["ok"] is True and svc._allow_live() is True
    r2 = await svc.disarm_live()
    assert r2["armed"] is False and svc._allow_live() is False


# ── 핵심: 게이트가 런타임 무장 상태를 읽는다(생성 시 캐시 아님) ───────────────
async def test_gate_reflects_runtime_arming(monkeypatch):
    svc, _repo, fake = await _svc(monkeypatch, permission=True)
    fake.place_ack = OrderAck(ok=True, broker_ord_no="900", rsp_cd="00040")
    intent = _kr_intent()

    await svc.disarm_live()  # 허용은 있지만 무장 해제 → LIVE 차단
    r1 = await svc.place(intent)
    assert r1["ok"] is False and "LIVE_DISABLED" in r1["decision"]["reasons"]
    assert not any(c[0] == "place" for c in fake.calls)  # 어댑터 미진입

    await svc.arm_live(LIVE_ARM_PHRASE)  # 무장 → LIVE 통과
    r2 = await svc.place(intent)
    assert r2["ok"] is True
    assert any(c[0] == "place" for c in fake.calls)


# ── API e2e (lifespan 우회) ──────────────────────────────────────────────────
@pytest_asyncio.fixture
async def api(monkeypatch):
    repo = await make_repo()
    patch_adapter(monkeypatch, FakeOrderAdapter())
    app = create_app()
    app.state.sessions = None
    app.state.repo = repo
    app.state.event_bus = EventBus()
    # permission on 이지만 시작 무장(env=true→armed). 테스트가 명시적으로 조작.
    app.state.order_service = OrderService(
        repo, None, fake_settings(allow_live=True), event_bus=app.state.event_bus
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, app.state.order_service


async def test_api_arm_requires_confirm_phrase(api):
    ac, svc = api
    await svc.disarm_live()
    # 잘못된 확인 문구 → 무장 안 됨.
    bad = (await ac.post(f"{V1}/system/live-arming", json={"armed": True, "confirm": "x"})).json()
    assert bad["data"]["ok"] is False and bad["data"]["armed"] is False
    # 정확한 문구 → 무장.
    ok = (
        await ac.post(f"{V1}/system/live-arming", json={"armed": True, "confirm": LIVE_ARM_PHRASE})
    ).json()
    assert ok["data"]["ok"] is True and ok["data"]["armed"] is True
    # 상태 조회.
    st = (await ac.get(f"{V1}/system/live-arming")).json()
    assert st["data"] == {"armed": True, "permission": True}
    # 무장 해제.
    off = (await ac.post(f"{V1}/system/live-arming", json={"armed": False})).json()
    assert off["data"]["armed"] is False
