"""주문 상태머신 + repository 단위 테스트.

검증: 전이표 합법/비합법, 터미널 불변, insert 멱등, 체결 적용(부분→완전), 체결 멱등(event_seq).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION
from app.models.order_dto import Fill, OrderState
from app.orders.state_machine import (
    StateMachineError,
    assert_transition,
    can_transition,
    is_terminal,
)
from app.repositories.db import init_db
from app.repositories.orders_repo import OrdersRepo


# ── 순수 상태머신 ────────────────────────────────────────────────────────────
def test_legal_transitions():
    assert can_transition(OrderState.APPROVED, OrderState.SUBMITTED)
    assert can_transition(OrderState.SUBMITTED, OrderState.ACCEPTED)
    assert can_transition(OrderState.SUBMITTED, OrderState.IN_DOUBT)
    assert can_transition(OrderState.SUBMITTED, OrderState.CANCELED)
    assert can_transition(OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED)
    assert can_transition(OrderState.ACCEPTED, OrderState.EXPIRED)
    assert can_transition(OrderState.IN_DOUBT, OrderState.EXPIRED)
    assert can_transition(OrderState.PARTIALLY_FILLED, OrderState.FILLED)


def test_illegal_transitions():
    assert not can_transition(OrderState.APPROVED, OrderState.ACCEPTED)  # 건너뜀 금지
    assert not can_transition(OrderState.ACCEPTED, OrderState.REJECTED)  # post-accept 거부 비허용
    for term in (OrderState.FILLED, OrderState.REJECTED, OrderState.CANCELED, OrderState.EXPIRED):
        assert is_terminal(term)
        assert not can_transition(term, OrderState.ACCEPTED)


def test_assert_transition_raises():
    with pytest.raises(StateMachineError):
        assert_transition(OrderState.FILLED, OrderState.PARTIALLY_FILLED)
    with pytest.raises(StateMachineError):
        assert_transition(OrderState.APPROVED, OrderState.FILLED)


# ── repository (임시 DB) ────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def repo():
    path = Path(tempfile.mkdtemp(prefix="hanbit-sm-")) / "t.db"
    await init_db(str(path))
    yield OrdersRepo(str(path))


async def _new_order(repo: OrdersRepo, cid: str, qty: int = 3) -> int:
    acct = await repo.get_account_id(MARKET_OVERSEAS_FUTUREOPTION)
    inst = await repo.ensure_instrument(MARKET_OVERSEAS_FUTUREOPTION, "ADZ25", exchange="HKEX")
    order_id, created = await repo.insert_order(
        idempotency_key=cid,
        account_id=acct,
        instrument_id=inst,
        market=MARKET_OVERSEAS_FUTUREOPTION,
        trading_mode="paper",
        side="buy",
        order_type="limit",
        qty=qty,
        price=0.65,
        status=OrderState.APPROVED.value,
        tr_code="CIDBT00100",
    )
    assert created
    return order_id


def _fill(seq, qty, price, rem):
    return Fill(
        broker_ord_no="O-1", exec_qty=qty, exec_price=price, remaining_qty=rem, event_seq=seq
    )


async def test_insert_order_idempotent(repo):
    id1 = await _new_order(repo, "cid-1")
    id2, created = await repo.insert_order(
        idempotency_key="cid-1",
        account_id=await repo.get_account_id(MARKET_OVERSEAS_FUTUREOPTION),
        market=MARKET_OVERSEAS_FUTUREOPTION,
        trading_mode="paper",
        side="buy",
        order_type="limit",
        qty=3,
        status=OrderState.APPROVED.value,
    )
    assert created is False
    assert id2 == id1


async def test_transition_flow_and_terminal_guard(repo):
    oid = await _new_order(repo, "cid-2")
    await repo.transition(oid, OrderState.SUBMITTED, "tr_response")
    await repo.transition(
        oid, OrderState.ACCEPTED, "tr_response", updates={"broker_order_id": "O-1"}
    )
    o = await repo.get_order(oid)
    assert o["status"] == "accepted" and o["broker_order_id"] == "O-1"
    assert o["submitted_at"] and o["accepted_at"]
    # 전이 이력 기록됨
    trans = await repo.list_transitions(oid)
    assert [t["to_state"] for t in trans] == ["submitted", "accepted"]


async def test_apply_fill_partial_then_full(repo):
    oid = await _new_order(repo, "cid-3", qty=3)
    await repo.transition(oid, OrderState.SUBMITTED, "tr_response")
    await repo.transition(oid, OrderState.ACCEPTED, "tr_response")

    applied = await repo.apply_fill(oid, _fill("recon:E1", 1, 0.64, 2))
    assert applied
    o = await repo.get_order(oid)
    assert o["status"] == "partially_filled" and o["filled_qty"] == 1 and o["remaining_qty"] == 2

    await repo.apply_fill(oid, _fill("recon:E2", 2, 0.66, 0))
    o = await repo.get_order(oid)
    assert o["status"] == "filled" and o["filled_qty"] == 3 and o["remaining_qty"] == 0
    # 가중평균가 = (1*0.64 + 2*0.66)/3
    assert abs(o["avg_fill_price"] - (0.64 + 2 * 0.66) / 3) < 1e-9

    # 터미널(filled) 이후 체결/전이 거부
    again = await repo.apply_fill(oid, _fill("recon:E3", 1, 0.7, 0))
    assert again is False
    with pytest.raises(StateMachineError):
        await repo.transition(oid, OrderState.CANCELED, "manual")


async def test_fill_idempotent_event_seq(repo):
    oid = await _new_order(repo, "cid-4", qty=5)
    await repo.transition(oid, OrderState.SUBMITTED, "tr_response")
    await repo.transition(oid, OrderState.ACCEPTED, "tr_response")
    f = _fill("recon:DUP", 2, 0.64, 3)
    assert await repo.apply_fill(oid, f) is True
    assert await repo.apply_fill(oid, f) is False  # 동일 event_seq → 멱등 무시
    o = await repo.get_order(oid)
    assert o["filled_qty"] == 2  # 중복 가산 안 됨
