"""일일손실 상태머신 (M3a §5.3) — DoD: 일일손실 한도 발동 시 halt.

baseline 영속·복원 / 손실≥한도→HALTED_DAILY / 신규 reject·reduce-only EXIT pass / 거래일 경계 리셋.
"""

from __future__ import annotations

from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION
from app.models.order_dto import IntentKind, OrderIntent, OrderType, Side
from app.risk.daily_loss import DailyLossMonitor
from app.risk.gate import RiskContext, RiskGate, RiskResult
from app.risk.limits import RiskLimits
from tests._fut_helpers import make_repo

FUT = MARKET_OVERSEAS_FUTUREOPTION
PT = "ACTIVE"  # 런타임 EngineState 단일 권위(M3b)
D1 = "20260619"
D2 = "20260620"


async def _limits(repo) -> RiskLimits:
    return await RiskLimits.load(repo, FUT)


def _intent(**kw) -> OrderIntent:
    base = dict(symbol="ADZ25", side=Side.BUY, order_type=OrderType.LIMIT, qty=1, price=0.65)
    base.update(kw)
    return OrderIntent(**base)


async def test_new_day_snapshots_baseline_active():
    repo = await make_repo()
    mon = DailyLossMonitor(repo)
    st = await mon.evaluate(
        "paper", realized_krw=0.0, unrealized_krw=0.0, limits=await _limits(repo), today=D1
    )
    assert st.reset is True and st.halt_state == "active"
    rs = await repo.get_risk_state("paper")
    assert rs["last_reset_day"] == D1 and rs["day_start_realized_krw"] == 0.0


async def test_realized_loss_triggers_halted_daily():
    repo = await make_repo()
    mon = DailyLossMonitor(repo)
    lim = await _limits(repo)
    await mon.evaluate("paper", realized_krw=0.0, unrealized_krw=0.0, limits=lim, today=D1)
    # 실현손실 1,200,000 ≥ max_daily_loss_realized(1,000,000) → halted_daily
    st = await mon.evaluate(
        "paper", realized_krw=-1_200_000.0, unrealized_krw=0.0, limits=lim, today=D1
    )
    assert st.halt_state == "halted_daily" and st.realized_loss_krw == 1_200_000.0
    assert (await repo.get_risk_state("paper"))["halt_state"] == "halted_daily"
    # risk_event(critical) 기록
    async with repo._connect() as db:
        await repo._prep(db)
        async with db.execute(
            "SELECT count(*) c FROM risk_events WHERE event_type='halted_daily'"
        ) as cur:
            assert (await cur.fetchone())["c"] == 1


async def test_eval_loss_includes_unrealized():
    repo = await make_repo()
    mon = DailyLossMonitor(repo)
    lim = await _limits(repo)
    await mon.evaluate("paper", realized_krw=0.0, unrealized_krw=0.0, limits=lim, today=D1)
    # 실현 -500k(한도 미달) + 미실현 -1.8M → eval_loss 2.3M ≥ max_daily_loss_eval(2.0M) → halted
    st = await mon.evaluate(
        "paper", realized_krw=-500_000.0, unrealized_krw=-1_800_000.0, limits=lim, today=D1
    )
    assert st.halt_state == "halted_daily"
    assert st.realized_loss_krw == 500_000.0 and st.eval_loss_krw == 2_300_000.0


async def test_baseline_restored_not_overwritten_same_day():
    """중간 재평가가 baseline 을 당일 중간값으로 덮어쓰지 않는다(예산 축소 버그 방지)."""
    repo = await make_repo()
    mon = DailyLossMonitor(repo)
    lim = await _limits(repo)
    await mon.evaluate("paper", realized_krw=500_000.0, unrealized_krw=0.0, limits=lim, today=D1)
    # 같은 날 재평가: realized 400k → 손실 100k (baseline 500k 복원되어야). 덮어썼다면 0.
    st = await mon.evaluate(
        "paper", realized_krw=400_000.0, unrealized_krw=0.0, limits=lim, today=D1
    )
    assert st.realized_loss_krw == 100_000.0
    assert (await repo.get_risk_state("paper"))["day_start_realized_krw"] == 500_000.0


async def test_day_boundary_resets_to_active():
    repo = await make_repo()
    mon = DailyLossMonitor(repo)
    lim = await _limits(repo)
    await mon.evaluate("paper", realized_krw=0.0, unrealized_krw=0.0, limits=lim, today=D1)
    await mon.evaluate("paper", realized_krw=-1_500_000.0, unrealized_krw=0.0, limits=lim, today=D1)
    assert (await repo.get_risk_state("paper"))["halt_state"] == "halted_daily"
    # 새 거래일 → 새 baseline + active
    st = await mon.evaluate(
        "paper", realized_krw=-1_500_000.0, unrealized_krw=0.0, limits=lim, today=D2
    )
    assert st.reset is True and st.halt_state == "active"
    assert (await repo.get_risk_state("paper"))["halt_state"] == "active"


async def test_gate_blocks_entry_allows_reduce_only_exit_when_halted_daily():
    repo = await make_repo()
    mon = DailyLossMonitor(repo)
    lim = await _limits(repo)
    await mon.evaluate("paper", realized_krw=0.0, unrealized_krw=0.0, limits=lim, today=D1)
    await mon.evaluate("paper", realized_krw=-1_500_000.0, unrealized_krw=0.0, limits=lim, today=D1)
    gate = RiskGate(repo)
    # 신규 ENTRY → HALTED_DAILY
    entry = await gate.pre_check(_intent(intent=IntentKind.ENTRY), engine_state=PT)
    assert entry.result == RiskResult.REJECT and "HALTED_DAILY" in entry.reasons
    # reduce-only EXIT(보유 long 5, SELL 2) → 통과
    held = [{"symbol": "ADZ25", "qty": 5, "position_side": "long"}]
    ex = await gate.pre_check(
        _intent(intent=IntentKind.EXIT, side=Side.SELL, qty=2),
        engine_state=PT,
        ctx=RiskContext(positions=held),
    )
    assert ex.ok and "HALTED_DAILY" not in ex.reasons


async def test_halted_daily_event_emitted_once():
    """같은 날 재breach 평가해도 halted_daily risk_event 는 1회만(중복 발화 0, 리뷰 #19)."""
    repo = await make_repo()
    mon = DailyLossMonitor(repo)
    lim = await _limits(repo)
    await mon.evaluate("paper", realized_krw=0.0, unrealized_krw=0.0, limits=lim, today=D1)
    await mon.evaluate("paper", realized_krw=-1_500_000.0, unrealized_krw=0.0, limits=lim, today=D1)
    await mon.evaluate("paper", realized_krw=-1_600_000.0, unrealized_krw=0.0, limits=lim, today=D1)
    async with repo._connect() as db:
        await repo._prep(db)
        async with db.execute(
            "SELECT count(*) c FROM risk_events WHERE event_type='halted_daily'"
        ) as cur:
            assert (await cur.fetchone())["c"] == 1
