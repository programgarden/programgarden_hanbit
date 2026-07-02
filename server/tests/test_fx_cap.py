"""FX 환산 명목 캡 (M3a §5.2) — DoD: FX 환산 한도.

e2e: FX → 게이트 ceil 캡 → REJECT + estimated warn.
"""

from __future__ import annotations

from app.models.order_dto import IntentKind, OrderIntent, OrderType, Side
from app.portfolio.fx import FxRateProvider
from app.risk.caps import notional_in_ccy, notional_krw_ceil
from app.risk.gate import RiskContext, RiskGate, RiskResult
from tests._fut_helpers import make_repo

PT = "ACTIVE"  # 런타임 EngineState 단일 권위(M3b)


def _fx(repo=None) -> FxRateProvider:
    return FxRateProvider(usd_krw=1400.0, hkd_krw=180.0, buffer_pct=0.02, ttl_s=300, repo=repo)


def _intent(**kw) -> OrderIntent:
    base = dict(
        symbol="ADZ25",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        qty=1,
        price=0.65,
        intent=IntentKind.ENTRY,
    )
    base.update(kw)
    return OrderIntent(**base)


def test_caps_helpers():
    assert notional_in_ccy(5, 5.0, 100) == 2500.0
    assert notional_in_ccy(5, None, 100) is None  # 시장가 → skip
    fx = _fx()
    krw, est = notional_krw_ceil(2500.0, "USD", fx)
    assert est is True and krw == 2500.0 * 1400.0 * 1.02  # ceil = 환율 올림


async def test_fx_ceil_cap_rejects_and_warns_estimated():
    repo = await make_repo()
    gate = RiskGate(repo, fx=_fx(repo))
    # notional = 5*5.0*100 = 2500 USD ; ceil KRW = 2500*1428 = 3,570,000 > per_order_cap 3,000,000
    d = await gate.pre_check(
        _intent(qty=5, price=5.0), engine_state=PT, ctx=RiskContext(multiplier=100)
    )
    assert d.result == RiskResult.REJECT
    assert "PER_ORDER_CAP_KRW" in d.reasons
    assert "FX_ESTIMATED" in d.reasons  # 고정환율 fallback → estimated warn
    # risk_event 기록(감사)
    async with repo._connect() as db:
        await repo._prep(db)
        async with db.execute(
            "SELECT count(*) c FROM risk_events WHERE event_type='pre_check_reject'"
        ) as cur:
            row = await cur.fetchone()
    assert row["c"] >= 1


async def test_live_rate_not_estimated_and_within_cap_passes():
    repo = await make_repo()
    fx = _fx(repo)
    await fx.observe("USD", 1300.0)  # 라이브 관측 → estimated 아님
    gate = RiskGate(repo, fx=fx)
    # notional = 1*0.65*100 = 65 USD ; ceil KRW = 65*1326 ≈ 86k < cap → 통과
    d = await gate.pre_check(
        _intent(qty=1, price=0.65), engine_state=PT, ctx=RiskContext(multiplier=100)
    )
    assert d.ok  # PASS/WARN(orderable unknown)
    assert "PER_ORDER_CAP_KRW" not in d.reasons
    assert "FX_ESTIMATED" not in d.reasons


async def test_no_fx_provider_skips_krw_cap_m2_compat():
    """fx 미주입(M2 단위테스트) — per_order_cap_krw 단계 skip, bucket_notional_cap 만."""
    repo = await make_repo()
    gate = RiskGate(repo)  # fx=None
    d = await gate.pre_check(
        _intent(qty=5, price=5.0), engine_state=PT, ctx=RiskContext(multiplier=100)
    )
    assert "PER_ORDER_CAP_KRW" not in d.reasons
    assert "FX_ESTIMATED" not in d.reasons
