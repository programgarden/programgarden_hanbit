"""EXIT reduce-only 가드 (M3a §5.5) — intent 신뢰 금지·증가 EXIT→ENTRY 재분류·시장가드 비우회."""

from __future__ import annotations

from app.core.mode_matrix import MARKET_KOREA_STOCK, MARKET_OVERSEAS_FUTUREOPTION
from app.models.order_dto import IntentKind, OrderIntent, OrderType, Side
from app.portfolio.fx import FxRateProvider
from app.risk import halt
from app.risk.gate import RiskContext, RiskGate, RiskResult
from tests._fut_helpers import make_repo

FUT = MARKET_OVERSEAS_FUTUREOPTION
PT = "ACTIVE"  # 런타임 EngineState 단일 권위(M3b)


def _exit(**kw) -> OrderIntent:
    base = dict(
        symbol="ADZ25",
        side=Side.SELL,
        order_type=OrderType.LIMIT,
        qty=1,
        price=0.65,
        intent=IntentKind.EXIT,
    )
    base.update(kw)
    return OrderIntent(**base)


def _long(qty=5):
    return [{"symbol": "ADZ25", "qty": qty, "position_side": "long"}]


def _short(qty=5):
    return [{"symbol": "ADZ25", "qty": qty, "position_side": "short"}]


async def test_valid_reduce_only_long_exit_bypasses_halt():
    repo = await make_repo()
    await halt.engage(repo, FUT, reason="test")
    gate = RiskGate(repo)
    # 보유 long 5, SELL 3 → reduce-only → halt 우회 PASS
    d = await gate.pre_check(
        _exit(side=Side.SELL, qty=3), engine_state=PT, ctx=RiskContext(positions=_long(5))
    )
    assert d.result == RiskResult.PASS and not d.reclassified_entry


async def test_valid_reduce_only_short_exit_via_buy():
    repo = await make_repo()
    gate = RiskGate(repo)
    # 보유 short 5, BUY 2 → reduce-only(반대방향)
    d = await gate.pre_check(
        _exit(side=Side.BUY, qty=2), engine_state=PT, ctx=RiskContext(positions=_short(5))
    )
    assert d.result == RiskResult.PASS and not d.reclassified_entry


async def test_increasing_exit_reclassified_to_entry_and_halted():
    repo = await make_repo()
    await halt.engage(repo, FUT, reason="test")
    gate = RiskGate(repo)
    # 보유 long 2, SELL 5(>보유) → reduce-only 아님 → ENTRY 재분류 → killswitch 적용
    d = await gate.pre_check(
        _exit(side=Side.SELL, qty=5), engine_state=PT, ctx=RiskContext(positions=_long(2))
    )
    assert d.result == RiskResult.REJECT and "KILL_SWITCH" in d.reasons
    assert d.reclassified_entry is True


async def test_exit_with_no_holding_reclassified_to_entry():
    repo = await make_repo()
    await halt.engage(repo, FUT, reason="test")
    gate = RiskGate(repo)
    # 보유 없음 → EXIT 신뢰 안 함 → ENTRY 재분류 → killswitch
    d = await gate.pre_check(_exit(), engine_state=PT, ctx=RiskContext(positions=[]))
    assert d.result == RiskResult.REJECT and "KILL_SWITCH" in d.reasons
    assert d.reclassified_entry is True


async def test_same_direction_exit_increases_so_not_reduce_only():
    repo = await make_repo()
    gate = RiskGate(repo)
    # 보유 long 5, EXIT 인데 side=BUY(같은 방향=증가) → reduce-only 아님 → ENTRY 재분류
    d = await gate.pre_check(
        _exit(side=Side.BUY, qty=1), engine_state=PT, ctx=RiskContext(positions=_long(5))
    )
    assert d.reclassified_entry is True


async def test_reclassified_exit_gets_full_gate_per_order_cap():
    """증가 EXIT → ENTRY 재분류 → 전체 게이트(per_order_cap_krw)를 실제로 받는다(리뷰 #4)."""
    repo = await make_repo()
    fx = FxRateProvider(usd_krw=1400.0, hkd_krw=180.0)
    gate = RiskGate(repo, fx=fx)
    # 보유 long 1, SELL 5(증가) → 재분류. notional=5*5.0*100=2500 → ceil KRW 3.57M > 3M cap.
    d = await gate.pre_check(
        _exit(side=Side.SELL, qty=5, price=5.0),
        engine_state=PT,
        ctx=RiskContext(multiplier=100, positions=_long(1)),
    )
    assert d.reclassified_entry is True
    assert d.result == RiskResult.REJECT and "PER_ORDER_CAP_KRW" in d.reasons


async def test_exit_korea_rejected_live_disabled_non_bypassable():
    """시장가드는 EXIT 도 비우회 — EXIT+korea → LIVE_DISABLED (§5.1 step2)."""
    repo = await make_repo()
    gate = RiskGate(repo)
    d = await gate.pre_check(
        _exit(market=MARKET_KOREA_STOCK, symbol="005930", side=Side.SELL),
        engine_state=PT,
        ctx=RiskContext(positions=[]),
    )
    assert d.result == RiskResult.REJECT and "LIVE_DISABLED" in d.reasons
