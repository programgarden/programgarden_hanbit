"""계좌-TR 직렬 큐 (M3b §8) — 버킷 직렬·우선순위·aging·호출건수 backoff·reconcile 래핑.

DoD(STATUS): 버킷 직렬·boot 우선·killswitch 최상위·aging·락순서(코드 규칙)·reconcile 경유.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.adapters.order_base import OrderError
from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION
from app.core.tr_queue import AccountTrQueue, TrPriority, is_call_count_exceeded
from app.models.order_dto import OrderIntent, OrderType, Side
from app.services.order_service import OrderService
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter

FUT = MARKET_OVERSEAS_FUTUREOPTION


def _intent(**kw):
    base = dict(symbol="ADZ25", side=Side.BUY, order_type=OrderType.LIMIT, qty=2, price=0.65)
    base.update(kw)
    return OrderIntent(**base)


def _make(order: list, label: str):
    async def f():
        order.append(label)
        return label

    return f


# ── 분류기 ────────────────────────────────────────────────────────────────
def test_is_call_count_exceeded_classifier():
    class E(Exception):
        status = 500

    assert is_call_count_exceeded(E("boom"))
    assert is_call_count_exceeded(Exception("호출 거래건수를 초과하였습니다"))
    assert is_call_count_exceeded(Exception("호출건수 제한"))
    # 레이트/타임아웃/도메인 에러는 호출건수 초과 아님(재큐 금지)
    assert not is_call_count_exceeded(Exception("rate limited, waiting"))
    assert not is_call_count_exceeded(Exception("order ack timeout"))
    assert not is_call_count_exceeded(OrderError("LIVE_DISABLED", "disabled"))


# ── 버킷 직렬(동시 in-flight=1) ──────────────────────────────────────────────
async def test_serial_within_bucket():
    q = AccountTrQueue(min_interval_s=0)
    active = 0
    max_active = 0

    async def job():
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return True

    await asyncio.gather(*[q.submit("paper", TrPriority.ROUTINE, job) for _ in range(5)])
    assert max_active == 1  # 절대 겹치지 않음
    assert q.calls == 5


# ── 버킷 격리(다른 버킷은 동시 진행) ─────────────────────────────────────────
async def test_bucket_isolation():
    q = AccountTrQueue(min_interval_s=0)
    a_release = asyncio.Event()
    a_started = asyncio.Event()
    b_done = asyncio.Event()

    async def a_job():
        a_started.set()
        await a_release.wait()
        return "a"

    async def b_job():
        b_done.set()
        return "b"

    ta = asyncio.create_task(q.submit("paper", TrPriority.ROUTINE, a_job))
    await a_started.wait()  # 'paper' leader 가 블록됨
    tb = asyncio.create_task(q.submit("live", TrPriority.ROUTINE, b_job))
    # 'paper' 가 막혀 있어도 'live' 는 독립 진행
    await asyncio.wait_for(b_done.wait(), timeout=1.0)
    a_release.set()
    assert await ta == "a"
    assert await tb == "b"


# ── 우선순위: killswitch 최상위 > boot > routine ─────────────────────────────
async def test_priority_kill_then_boot_then_routine():
    q = AccountTrQueue(min_interval_s=0, aging_s=0)  # aging off → 순수 우선순위
    order: list[str] = []
    release = asyncio.Event()
    primer_started = asyncio.Event()

    async def primer():
        primer_started.set()
        await release.wait()
        order.append("primer")
        return "primer"

    t_primer = asyncio.create_task(q.submit("paper", TrPriority.ROUTINE, primer))
    await primer_started.wait()  # leader 블록 → 이후 submit 은 대기열에 쌓임

    # 일부러 우선순위 역순으로 등록(routine→boot→kill) — 큐가 재정렬해야 함
    t_routine = asyncio.create_task(q.submit("paper", TrPriority.ROUTINE, _make(order, "routine")))
    t_boot = asyncio.create_task(q.submit("paper", TrPriority.BOOT, _make(order, "boot")))
    t_kill = asyncio.create_task(q.submit("paper", TrPriority.KILL, _make(order, "kill")))
    for _ in range(4):
        await asyncio.sleep(0)
    release.set()
    await asyncio.gather(t_primer, t_routine, t_boot, t_kill)

    assert order == ["primer", "kill", "boot", "routine"]


# ── aging: 오래 기다린 routine 이 신규 boot 보다 먼저 ─────────────────────────
async def test_aging_promotes_starved_routine():
    clk = {"t": 0.0}
    q = AccountTrQueue(min_interval_s=0, aging_s=1.0, clock=lambda: clk["t"])
    order: list[str] = []
    release = asyncio.Event()
    primer_started = asyncio.Event()

    async def primer():
        primer_started.set()
        await release.wait()
        order.append("primer")
        return "primer"

    t_primer = asyncio.create_task(q.submit("paper", TrPriority.ROUTINE, primer))
    await primer_started.wait()

    clk["t"] = 0.0
    t_routine = asyncio.create_task(q.submit("paper", TrPriority.ROUTINE, _make(order, "routine")))
    for _ in range(4):
        await asyncio.sleep(0)
    clk["t"] = 5.0  # routine 이 5s 기아 → eff=max(0, 2-5)=0
    t_boot = asyncio.create_task(q.submit("paper", TrPriority.BOOT, _make(order, "boot")))
    for _ in range(4):
        await asyncio.sleep(0)
    release.set()
    await asyncio.gather(t_primer, t_routine, t_boot)

    # base 우선순위면 boot(1) < routine(2) 라 boot 먼저여야 하지만, aging 으로 뒤집힘
    assert order == ["primer", "routine", "boot"]


# ── 호출건수 초과: backoff 후 재큐 → 결국 성공 ───────────────────────────────
async def test_count_exceeded_requeues_then_succeeds():
    q = AccountTrQueue(min_interval_s=0, backoff_base_s=0, max_retries=3)
    n = {"i": 0}

    class Boom(Exception):
        status = 500

    async def flaky():
        n["i"] += 1
        if n["i"] < 3:
            raise Boom("호출 거래건수를 초과")
        return "ok"

    res = await q.submit("paper", TrPriority.ROUTINE, flaky)
    assert res == "ok"
    assert n["i"] == 3
    assert q.retries == 2
    assert q.calls == 1


async def test_count_exceeded_exhausts_and_raises():
    q = AccountTrQueue(min_interval_s=0, backoff_base_s=0, max_retries=2)

    class Boom(Exception):
        status = 500

    async def always():
        raise Boom("호출건수 초과")

    with pytest.raises(Boom):
        await q.submit("paper", TrPriority.ROUTINE, always)
    assert q.retries == 2  # 2회 재큐 후 3번째 실패에 전파


async def test_non_count_error_propagates_without_retry():
    q = AccountTrQueue(min_interval_s=0)

    async def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        await q.submit("paper", TrPriority.ROUTINE, boom)
    assert q.retries == 0


# ── 최소 간격(min-interval) ──────────────────────────────────────────────────
async def test_min_interval_spaces_calls():
    q = AccountTrQueue(min_interval_s=0.05)
    times: list[float] = []

    async def job():
        times.append(time.monotonic())
        return True

    await asyncio.gather(*[q.submit("paper", TrPriority.ROUTINE, job) for _ in range(3)])
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    assert len(gaps) == 2
    assert all(g >= 0.045 for g in gaps)  # ≈ min_interval(스케줄 지터 여유)


# ── reconcile 이 계좌 TR 을 큐 경유로 호출(scope→priority) ────────────────────
class _SpyQueue(AccountTrQueue):
    """submit 인자를 기록하면서 실제 큐로 위임."""

    def __init__(self) -> None:
        super().__init__(min_interval_s=0)
        self.submits: list[tuple] = []

    async def submit(self, bucket, priority, factory, *, label=None):
        self.submits.append((bucket, priority, label))
        return await super().submit(bucket, priority, factory, label=label)


async def _svc_with_spy(monkeypatch):
    repo = await make_repo()
    fake = FakeOrderAdapter()
    patch_adapter(monkeypatch, fake)
    spy = _SpyQueue()
    svc = OrderService(
        repo, session=None, settings=fake_settings("PAPER_TRADING"), tr_queue=spy
    )
    return svc, repo, fake, spy


async def test_reconcile_routes_account_tr_through_queue(monkeypatch):
    svc, _repo, _fake, spy = await _svc_with_spy(monkeypatch)
    await svc.place(_intent())  # accepted O-1 → reconcile 시 종목 조회 발생
    spy.submits.clear()

    await svc.reconcile(scope="boot")

    labels = [s[2] for s in spy.submits]
    assert {s[1] for s in spy.submits} == {TrPriority.BOOT}  # boot scope → 전부 elevated
    assert all(s[0] == "paper" for s in spy.submits)  # 버킷 키
    assert "get_positions" in labels
    assert any(label and label.startswith("get_open_orders") for label in labels)


@pytest.mark.parametrize(
    ("scope", "prio"),
    [
        ("manual", TrPriority.ROUTINE),
        ("boot", TrPriority.BOOT),
        ("kill_switch_precancel", TrPriority.KILL),
    ],
)
async def test_reconcile_scope_maps_to_priority(monkeypatch, scope, prio):
    svc, _repo, _fake, spy = await _svc_with_spy(monkeypatch)
    await svc.reconcile(scope=scope)  # 빈 책이라도 get_positions 는 항상 큐 경유
    assert spy.submits, "get_positions 가 큐를 경유해야 함"
    assert {s[1] for s in spy.submits} == {prio}
