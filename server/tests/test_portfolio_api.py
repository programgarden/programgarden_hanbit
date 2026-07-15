"""포트폴리오/계좌/위험 API 실데이터 e2e (M3b §11) — lifespan 우회 + fake adapter.

검증: GET /portfolio(버킷 KPI·currency_hhi·참고합산) · GET /portfolio/positions(버킷 태깅) ·
GET /accounts(통화별 잔고) · GET /risk/halt_state(버킷별 상태+일일손실 진행) ·
GET /system/quarantine · POST /risk/killswitch L2(confirm_token 2단계) ·
GET /system/health(런타임 engine_state) · WS risk.halt_state push(event_bus).
모두 집계기/reconcile 이 채운 DB 행을 API 가 읽기만 한다(직접 계좌 TR 0).
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.event_bus import EventBus
from app.core.mode_matrix import BUCKET_PAPER, MARKET_OVERSEAS_FUTUREOPTION
from app.main import create_app
from app.models.order_dto import OrderState
from app.services.order_service import OrderService
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter

V1 = "/api/v1"
FUT = MARKET_OVERSEAS_FUTUREOPTION


async def _seed(repo) -> tuple[int, int]:
    """paper 버킷 한 종목(롱 5계약) + 잔고/KPI/risk_state 시드(집계기 산출물 모사)."""
    acct = await repo.ensure_account(FUT, "PAPER-ACC", trading_mode="paper", currency="USD")
    inst = await repo.ensure_instrument(FUT, "ADZ25", exchange="HKEX")
    await repo.upsert_position_authority(
        acct, inst, bucket=BUCKET_PAPER, market=FUT, currency="USD",
        position_side="long", qty=5, avg_price=0.65, margin_used=100.0,
    )
    await repo.upsert_position_marks(
        acct, inst, current_price=0.70, pnl_amount=0.25, pnl_rate=0.07,
        fx_now=1400.0, fx_at_buy=1400.0, fx_estimated=1, eval_krw=4900.0,
    )
    await repo.upsert_balance_snapshot(
        acct, "USD", deposit=10000.0, orderable_amount=8000.0, margin_total=100.0,
        withdrawable=8000.0, realized_pnl=12.0, exchange_rate=1400.0,
    )
    await repo.insert_bucket_kpi(
        BUCKET_PAPER, account_pnl_rate=0.07, total_eval_krw=4900.0, total_buy_krw=4550.0,
        total_pnl_krw=350.0, position_count=1, hhi=1.0, norm_hhi=0.0, eff_n=1.0,
        top1_weight=1.0, currency_hhi=1.0, daily_realized_krw=12.0, daily_pnl_krw=362.0,
    )
    await repo.set_risk_state(
        BUCKET_PAPER, halt_state="active", day_start_realized_krw=100.0,
        day_start_unrealized_krw=500.0, day_start_equity_krw=5000.0,
        daily_notional_used_krw=0.0, last_reset_day="20260619",
    )
    return acct, inst


@pytest_asyncio.fixture
async def api(monkeypatch):
    repo = await make_repo()
    fake = FakeOrderAdapter()
    patch_adapter(monkeypatch, fake)
    await _seed(repo)
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
        yield ac, repo, fake, app


# ── /portfolio ──────────────────────────────────────────────────────────────
async def test_portfolio_root_bucket_kpi(api):
    ac, *_ = api
    data = (await ac.get(f"{V1}/portfolio")).json()["data"]
    paper = data["buckets"]["paper"]
    assert paper["currency_hhi"] == 1.0 and paper["position_count"] == 1
    # 참고 합산(표시 전용)은 KRW 환산값 합.
    assert data["totals"]["total_eval_krw"] == 4900.0
    assert data["totals"]["position_count"] == 1
    assert data["buckets"]["live"] is None  # live 버킷 KPI 아직 없음


async def test_portfolio_positions_bucket_tagged(api):
    ac, *_ = api
    data = (await ac.get(f"{V1}/portfolio/positions", params={"bucket": "paper"})).json()["data"]
    assert data["bucket"] == "paper" and len(data["positions"]) == 1
    p = data["positions"][0]
    assert p["symbol"] == "ADZ25" and p["eval_krw"] == 4900.0
    assert p["fx_now"] == 1400.0 and p["bucket"] == "paper"
    # 다른 버킷은 격리(빈 결과), 잘못된 버킷은 422.
    live = (await ac.get(f"{V1}/portfolio/positions", params={"bucket": "live"})).json()["data"]
    assert live["positions"] == []
    bad = await ac.get(f"{V1}/portfolio/positions", params={"bucket": "nope"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "BAD_BUCKET"


# ── /accounts ───────────────────────────────────────────────────────────────
async def test_accounts_balances_per_currency(api):
    ac, *_ = api
    accounts = (await ac.get(f"{V1}/accounts")).json()["data"]["accounts"]
    paper = next(a for a in accounts if a["account_no"] == "PAPER-ACC")
    assert paper["market"] == FUT and paper["trading_mode"] == "paper"
    usd = next(b for b in paper["balances"] if b["currency"] == "USD")
    assert usd["deposit"] == 10000.0 and usd["orderable_amount"] == 8000.0


# ── /risk/halt_state ──────────────────────────────────────────────────────────
async def test_halt_state_active_with_daily_progress(api):
    ac, *_ = api
    buckets = (await ac.get(f"{V1}/risk/halt_state")).json()["data"]["buckets"]
    paper = buckets["paper"]
    assert paper["state"] == "active" and paper["kill_switch"] == "active"
    dl = paper["daily_loss"]
    # realized_loss = max(0, 100 − 12) = 88; eval_loss = 88 + (500 − 350) = 238.
    assert dl["realized_loss_krw"] == 88.0 and dl["eval_loss_krw"] == 238.0
    assert dl["max_daily_loss_realized"] > 0 and dl["last_reset_day"] == "20260619"


async def test_halt_state_reflects_killswitch(api):
    ac, *_ = api
    await ac.post(f"{V1}/risk/killswitch", json={"scope": "global", "action": "engage"})
    buckets = (await ac.get(f"{V1}/risk/halt_state")).json()["data"]["buckets"]
    assert buckets["paper"]["state"] == "killed"
    assert buckets["paper"]["kill_switch"] == "killed"
    # 일일손실 상태(risk_state)는 그대로 active — 킬스위치가 최상위로 killed 강제.
    assert buckets["paper"]["daily_loss_state"] == "active"


# ── /system/quarantine ────────────────────────────────────────────────────────
async def test_quarantine_lists_quarantined_orders(api):
    ac, repo, fake, app = api
    # 격리 주문 직접 주입(boot reconcile 의 미해소 격리 모사 — 상태=quarantined).
    acct = await repo.get_account_id(FUT)
    inst = await repo.ensure_instrument(FUT, "ADZ25", exchange="HKEX")
    oid, _ = await repo.insert_order(
        idempotency_key="q-1", account_id=acct, instrument_id=inst, market=FUT,
        trading_mode="paper", side="buy", order_type="limit", qty=2, price=0.65,
        broker_order_id="O-Q1", relation="new", status=OrderState.QUARANTINED.value,
    )
    q = (await ac.get(f"{V1}/system/quarantine")).json()["data"]
    assert q["count"] == 1 and q["orders"][0]["id"] == oid


# ── /system/health ────────────────────────────────────────────────────────────
async def test_health_runtime_engine_state(api):
    ac, *_ = api
    h = (await ac.get(f"{V1}/system/health")).json()["data"]
    assert h["milestone"] == "M3" and h["engine_state"] == "ACTIVE"
    assert h["mode"] == "READ_ONLY" and h["realtime_fills"] is False


# ── killswitch L2 (2단계 확인) ─────────────────────────────────────────────────
async def test_killswitch_l2_requires_confirm_token(api):
    ac, repo, fake, app = api
    # 토큰 없이 level 2 → 미실행 + 토큰 발급(halt 변화 없음).
    r1 = (await ac.post(
        f"{V1}/risk/killswitch", json={"scope": "global", "action": "engage", "level": 2}
    )).json()["data"]
    assert r1["requires_confirm"] is True and r1["confirm_token"]
    assert await repo.get_halt_state("global") == "active"  # 미실행 확인

    # 토큰 실어 재요청 → killed + flatten(보유 롱 5 → reduce-only SELL).
    r2 = (await ac.post(
        f"{V1}/risk/killswitch",
        json={
            "scope": "global", "action": "engage", "level": 2,
            "confirm_token": r1["confirm_token"],
        },
    )).json()["data"]
    assert r2["state"] == "killed" and r2["level"] == 2
    # flatten 은 버킷별 맵(§9 진화) — paper 보유 롱 5 → reduce-only SELL.
    fired = r2["flatten"]["paper"]["fired"]
    assert len(fired) == 1 and fired[0]["side"] == "sell" and fired[0]["qty"] == 5
    assert await repo.get_halt_state("global") == "killed"


# ── WS — risk.halt_state push(event_bus 생산자 계약) ──────────────────────────
async def test_killswitch_publishes_halt_state_to_bus(api):
    ac, repo, fake, app = api
    queue = app.state.event_bus.subscribe()
    await ac.post(f"{V1}/risk/killswitch", json={"scope": "global", "action": "engage"})
    msgs = []
    while not queue.empty():
        msgs.append(queue.get_nowait())
    halt_msgs = [m for m in msgs if m["topic"] == "risk.halt_state"]
    assert halt_msgs and halt_msgs[-1]["data"]["paper"]["state"] == "killed"
