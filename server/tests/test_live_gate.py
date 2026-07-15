"""LIVE 리스크 게이트 (M4b/M4c §6) — allow_live 마스터 토글 + LIVE 소액 캡 + notional-필수 가드.

- allow_live=false → KR/OVS 하드 거부(LIVE_DISABLED) — registry·부트와 3중 방어 중 게이트 레이어.
- allow_live=true  → 캡 통과분만 PASS/WARN, 초과분·명목미상은 REJECT.
- notional 미상(시장가/price None) → NO_NOTIONAL_FOR_LIVE(캡 우회 차단, §17 L1-1).
"""

from __future__ import annotations

from app.core.engine_state import EngineState
from app.core.mode_matrix import BUCKET_LIVE, MARKET_KOREA_STOCK, MARKET_OVERSEAS_STOCK
from app.models.order_dto import OrderIntent, OrderType, Side
from app.portfolio.fx import FxRateProvider
from app.risk.gate import RiskContext, RiskGate
from tests._fut_helpers import fake_settings, make_repo

ACTIVE = EngineState.ACTIVE


def _fx(repo):
    return FxRateProvider(usd_krw=1400.0, hkd_krw=180.0, repo=repo)


def _kr(**kw):
    base = dict(
        market=MARKET_KOREA_STOCK, symbol="005930", side=Side.BUY,
        order_type=OrderType.LIMIT, qty=1, price=50000, currency="KRW",
    )
    base.update(kw)
    return OrderIntent(**base)


def _ovs(**kw):
    base = dict(
        market=MARKET_OVERSEAS_STOCK, symbol="82:TSLA", side=Side.BUY,
        order_type=OrderType.LIMIT, qty=1, price=40.0, currency="USD",
    )
    base.update(kw)
    return OrderIntent(**base)


async def _gate(*, allow_live: bool):
    repo = await make_repo()
    gate = RiskGate(repo, fx=_fx(repo), settings=fake_settings(allow_live=allow_live))
    return gate


# ── allow_live 마스터 토글 ─────────────────────────────────────────────────
async def test_kr_rejected_when_allow_live_false():
    gate = await _gate(allow_live=False)
    d = await gate.pre_check(_kr(), engine_state=ACTIVE, ctx=RiskContext())
    assert not d.ok
    assert "LIVE_DISABLED" in d.reasons


async def test_ovs_rejected_when_allow_live_false():
    gate = await _gate(allow_live=False)
    d = await gate.pre_check(_ovs(), engine_state=ACTIVE, ctx=RiskContext())
    assert not d.ok
    assert "LIVE_DISABLED" in d.reasons


# ── allow_live=true: 캡 통과분만 발사 ───────────────────────────────────────
async def test_kr_within_cap_passes():
    gate = await _gate(allow_live=True)
    d = await gate.pre_check(_kr(qty=1, price=50000), engine_state=ACTIVE, ctx=RiskContext())
    assert d.ok  # PASS/WARN(orderable 미상 WARN 은 통과)
    assert "PER_ORDER_CAP_LIVE" not in d.reasons
    assert "LIVE_DISABLED" not in d.reasons


async def test_kr_over_live_cap_rejected():
    gate = await _gate(allow_live=True)
    # 캡 100,000 KRW 초과(200,000) → PER_ORDER_CAP_LIVE.
    d = await gate.pre_check(_kr(qty=1, price=200000), engine_state=ACTIVE, ctx=RiskContext())
    assert not d.ok
    assert "PER_ORDER_CAP_LIVE" in d.reasons


async def test_kr_market_order_rejected_no_notional():
    gate = await _gate(allow_live=True)
    d = await gate.pre_check(
        _kr(order_type=OrderType.MARKET, price=None), engine_state=ACTIVE, ctx=RiskContext()
    )
    assert not d.ok
    assert "NO_NOTIONAL_FOR_LIVE" in d.reasons


async def test_ovs_within_usd_cap_passes():
    gate = await _gate(allow_live=True)
    d = await gate.pre_check(_ovs(qty=1, price=40.0), engine_state=ACTIVE, ctx=RiskContext())
    assert d.ok
    assert "PER_ORDER_CAP_LIVE" not in d.reasons


async def test_ovs_over_usd_cap_rejected():
    gate = await _gate(allow_live=True)
    # 캡 50 USD 초과(100) → PER_ORDER_CAP_LIVE.
    d = await gate.pre_check(_ovs(qty=1, price=100.0), engine_state=ACTIVE, ctx=RiskContext())
    assert not d.ok
    assert "PER_ORDER_CAP_LIVE" in d.reasons


# ── 엔진 상태 게이트(step0)는 시장 무관 — LIVE 도 ACTIVE 아니면 거부 ──────────
async def test_live_rejected_when_engine_not_active():
    gate = await _gate(allow_live=True)
    d = await gate.pre_check(_kr(), engine_state=EngineState.READ_ONLY, ctx=RiskContext())
    assert not d.ok
    assert "ENGINE_NOT_ACTIVE" in d.reasons


# ── 첫주문 완충 가드(§10.2): LIVE 는 가드 on 동안 단일종목만 ─────────────────
def _held(symbol):
    return RiskContext(positions=[{
        "symbol": symbol, "eval_krw": 50000.0, "market": MARKET_KOREA_STOCK,
        "currency": "KRW", "position_side": "long", "qty": 1,
    }])


async def test_first_order_guard_blocks_second_symbol():
    gate = await _gate(allow_live=True)  # first_order_guard 기본 on
    d = await gate.pre_check(_kr(symbol="005930"), engine_state=ACTIVE, ctx=_held("000660"))
    assert not d.ok
    assert "FIRST_ORDER_GUARD" in d.reasons  # 이미 다른 종목 보유 → 신규 종목 차단


async def test_first_order_guard_allows_same_symbol():
    gate = await _gate(allow_live=True)
    d = await gate.pre_check(_kr(symbol="005930"), engine_state=ACTIVE, ctx=_held("005930"))
    assert "FIRST_ORDER_GUARD" not in d.reasons  # 같은 종목 유지는 가드 통과


# ── 누적/일일 명목 캡(§6 step4''): 당일 누적 + 이번 주문 > 캡 → 거부 ──────────
async def test_daily_notional_cap_rejects_over_accumulation():
    repo = await make_repo()
    gate = RiskGate(repo, fx=_fx(repo), settings=fake_settings(allow_live=True))
    # 캡 300,000. 당일 누적 290,000 → 50,000 주문(340,000) 초과 거부.
    await repo.set_risk_state(BUCKET_LIVE, daily_notional_used_krw=290_000)
    d = await gate.pre_check(_kr(price=50000), engine_state=ACTIVE, ctx=RiskContext())
    assert not d.ok
    assert "DAILY_NOTIONAL_CAP" in d.reasons


async def test_daily_notional_cap_passes_under_limit():
    repo = await make_repo()
    gate = RiskGate(repo, fx=_fx(repo), settings=fake_settings(allow_live=True))
    await repo.set_risk_state(BUCKET_LIVE, daily_notional_used_krw=100_000)
    d = await gate.pre_check(_kr(price=50000), engine_state=ACTIVE, ctx=RiskContext())
    assert "DAILY_NOTIONAL_CAP" not in d.reasons  # 150,000 < 300,000
