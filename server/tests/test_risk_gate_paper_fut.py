"""PAPER_FUT 사전 위험 게이트 테스트.

검증: engine_state 게이트, 킬스위치(ENTRY 차단/EXIT 예외), 모드/HKEX 하드가드,
KR/OVS LIVE_DISABLED, 과대주문/동시미체결 한도, orderable WARN, 감사로그 기록.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest_asyncio

from app.core.mode_matrix import MARKET_KOREA_STOCK, MARKET_OVERSEAS_FUTUREOPTION
from app.models.order_dto import IntentKind, OrderIntent, OrderType, Side
from app.portfolio.fx import FxRateProvider
from app.repositories.db import init_db
from app.repositories.orders_repo import OrdersRepo
from app.risk import halt
from app.risk.gate import RiskContext, RiskGate, RiskResult

FUT_M = MARKET_OVERSEAS_FUTUREOPTION


def _fx():
    return FxRateProvider(usd_krw=1400.0, hkd_krw=180.0, buffer_pct=0.02, ttl_s=300)

# 런타임 EngineState 단일 권위(M3b) — 게이트는 "ACTIVE" 만 거래 허용으로 본다.
PT = "ACTIVE"


@pytest_asyncio.fixture
async def repo():
    path = Path(tempfile.mkdtemp(prefix="hanbit-rg-")) / "t.db"
    await init_db(str(path))
    r = OrdersRepo(str(path))
    # HKEX 화이트리스트 종목 1개 등록
    await r.ensure_instrument(MARKET_OVERSEAS_FUTUREOPTION, "ADZ25", exchange="HKEX")
    await r.set_whitelisted(MARKET_OVERSEAS_FUTUREOPTION, "ADZ25", True)
    return r


def _intent(**kw):
    base = dict(symbol="ADZ25", side=Side.BUY, order_type=OrderType.LIMIT, qty=1, price=0.65)
    base.update(kw)
    return OrderIntent(**base)


async def test_engine_read_only_rejects(repo):
    d = await RiskGate(repo).pre_check(_intent(), engine_state="READ_ONLY")
    assert d.result == RiskResult.REJECT and "ENGINE_NOT_ACTIVE" in d.reasons
    assert not d.ok


async def test_engine_reconciling_rejects(repo):
    """RECONCILING(부트 reconcile 중)도 ACTIVE 가 아니므로 주문 거부(§7.1)."""
    d = await RiskGate(repo).pre_check(_intent(), engine_state="RECONCILING")
    assert d.result == RiskResult.REJECT and "ENGINE_NOT_ACTIVE" in d.reasons


async def test_happy_path_pass(repo):
    d = await RiskGate(repo).pre_check(_intent(), engine_state=PT)
    # orderable 미상 → WARN 통과(ok True)
    assert d.ok and d.result in (RiskResult.PASS, RiskResult.WARN)


async def test_non_hkex_exchange_rejected(repo):
    d = await RiskGate(repo).pre_check(_intent(exchange="CME"), engine_state=PT)
    assert d.result == RiskResult.REJECT and "FUT_NOT_HKEX" in d.reasons


async def test_non_whitelisted_symbol_rejected(repo):
    d = await RiskGate(repo).pre_check(_intent(symbol="ZZZ99"), engine_state=PT)
    assert d.result == RiskResult.REJECT and "FUT_NOT_HKEX" in d.reasons


async def test_live_market_rejected(repo):
    d = await RiskGate(repo).pre_check(_intent(market=MARKET_KOREA_STOCK), engine_state=PT)
    assert d.result == RiskResult.REJECT and "LIVE_DISABLED" in d.reasons


async def test_killswitch_blocks_entry_but_not_exit(repo):
    await halt.engage(repo, MARKET_OVERSEAS_FUTUREOPTION, reason="test")
    entry = await RiskGate(repo).pre_check(_intent(intent=IntentKind.ENTRY), engine_state=PT)
    assert entry.result == RiskResult.REJECT and "KILL_SWITCH" in entry.reasons
    # EXIT(청산)은 halt 예외 — 단 reduce-only(보유 반대방향·qty≤보유)여야 함(§5.5).
    held = [{"symbol": "ADZ25", "qty": 5, "position_side": "long"}]
    ex = await RiskGate(repo).pre_check(
        _intent(intent=IntentKind.EXIT, side=Side.SELL),
        engine_state=PT,
        ctx=RiskContext(positions=held),
    )
    assert "KILL_SWITCH" not in ex.reasons


async def test_max_contracts_rejected(repo):
    d = await RiskGate(repo).pre_check(_intent(qty=11), engine_state=PT)
    assert d.result == RiskResult.REJECT and "MAX_CONTRACTS" in d.reasons


async def test_max_open_orders_rejected(repo):
    d = await RiskGate(repo).pre_check(
        _intent(), engine_state=PT, ctx=RiskContext(open_orders_count=20)
    )
    assert d.result == RiskResult.REJECT and "MAX_OPEN_ORDERS" in d.reasons


async def test_orderable_insufficient_rejected(repo):
    # notional = 5 * 0.65 * 100 = 325 > orderable 100
    d = await RiskGate(repo).pre_check(
        _intent(qty=5, price=0.65),
        engine_state=PT,
        ctx=RiskContext(multiplier=100, orderable_amount=100),
    )
    assert d.result == RiskResult.REJECT and "INSUFFICIENT_ORDERABLE" in d.reasons


async def test_orderable_headroom_krw_floor(repo):
    """orderable 헤드룸은 KRW floor 환산으로 비교(§6 방향표, item ③).

    cost_krw = 65*1400*1.02 ≈ 92,820 > orderable_krw = 60*1400*0.98 ≈ 82,320 → REJECT.
    """
    gate = RiskGate(repo, fx=_fx())
    d = await gate.pre_check(
        _intent(qty=1, price=0.65),
        engine_state=PT,
        ctx=RiskContext(multiplier=100, orderable_amount=60),
    )
    assert d.result == RiskResult.REJECT and "INSUFFICIENT_ORDERABLE" in d.reasons


async def test_exposure_cap_inv7_rejects_via_gate(repo):
    """INV-7 노출캡이 게이트 e2e 로 발화 — 보유가 큰 종목 추가매수 → MAX_SYMBOL_WEIGHT(리뷰 #5)."""
    gate = RiskGate(repo, fx=_fx())
    positions = [
        {"symbol": "ADZ25", "eval_krw": 100000.0, "market": FUT_M, "currency": "USD",
         "position_side": "long", "qty": 5},
        {"symbol": "OTHER", "eval_krw": 10000.0, "market": FUT_M, "currency": "USD",
         "position_side": "long", "qty": 1},
    ]
    # 같은 ADZ25 매수 ENTRY notional=5.0*100=500 → add_eval≈700k → projected 비중 ≫ 0.25.
    d = await gate.pre_check(
        _intent(symbol="ADZ25", qty=1, price=5.0),
        engine_state=PT,
        ctx=RiskContext(multiplier=100, positions=positions),
    )
    assert d.result == RiskResult.REJECT and "MAX_SYMBOL_WEIGHT" in d.reasons


async def test_max_positions_rejects_new_symbol(repo):
    """max_positions 캡 게이트 검증 — 신규 종목은 거부, 기존 종목 추가는 허용(리뷰 #11)."""
    gate = RiskGate(repo)
    held = [
        {"symbol": f"S{i}", "eval_krw": 1000.0, "market": FUT_M, "currency": "USD", "qty": 1}
        for i in range(20)
    ]
    new_sym = await gate.pre_check(
        _intent(symbol="ADZ25"), engine_state=PT, ctx=RiskContext(positions=held)
    )
    assert new_sym.result == RiskResult.REJECT and "MAX_POSITIONS" in new_sym.reasons
    # 기존 종목(ADZ25 포함 20개) 추가 주문은 신규 종목이 아니므로 통과
    held2 = held[:19] + [
        {"symbol": "ADZ25", "eval_krw": 1000.0, "market": FUT_M, "currency": "USD", "qty": 1}
    ]
    existing = await gate.pre_check(
        _intent(symbol="ADZ25"), engine_state=PT, ctx=RiskContext(positions=held2)
    )
    assert "MAX_POSITIONS" not in existing.reasons


async def test_unknown_currency_rejected(repo):
    """미지원 통화는 1:1 환산 누수 방지 위해 하드 거부(리뷰 #16)."""
    gate = RiskGate(repo, fx=_fx())
    d = await gate.pre_check(
        _intent(currency="EUR", price=0.65),
        engine_state=PT,
        ctx=RiskContext(multiplier=100),
    )
    assert d.result == RiskResult.REJECT and "FX_UNKNOWN_CCY" in d.reasons


async def test_audit_log_records_every_decision(repo):
    await RiskGate(repo).pre_check(_intent(), engine_state="READ_ONLY")
    await RiskGate(repo).pre_check(_intent(), engine_state=PT)
    # audit_log 에 pre_check 두 건
    async with repo._connect() as db:
        await repo._prep(db)
        async with db.execute("SELECT count(*) c FROM audit_log WHERE action='pre_check'") as cur:
            row = await cur.fetchone()
    assert row["c"] == 2
