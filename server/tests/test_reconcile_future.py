"""reconcile (CIDBQ02400 종목별/부분·완전체결/orphan) + 장마감 타임아웃(expired) 테스트."""

from __future__ import annotations

from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION
from app.models.order_dto import OpenOrder, OrderIntent, OrderState, OrderType, Position, Side
from app.services.order_service import OrderService
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter


def _intent(**kw):
    base = dict(symbol="ADZ25", side=Side.BUY, order_type=OrderType.LIMIT, qty=3, price=0.65)
    base.update(kw)
    return OrderIntent(**base)


async def _svc(monkeypatch):
    repo = await make_repo()
    fake = FakeOrderAdapter()
    patch_adapter(monkeypatch, fake)
    return OrderService(repo, session=None, settings=fake_settings()), repo, fake


def _exec_row(ordno, *, exec_no, exec_qty, exec_price, remaining):
    return OpenOrder(
        broker_ord_no=ordno, exec_no=exec_no, symbol="ADZ25", side=Side.BUY,
        qty=3, price=0.65, exec_qty=exec_qty, exec_price=exec_price, remaining_qty=remaining,
        ord_status_code="2",
    )


async def test_reconcile_partial_then_full(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    oid = (await svc.place(_intent()))["order"]["id"]  # accepted, OrdNo O-1

    # 1) 부분체결 1계약
    fake.open_orders["ADZ25"] = [
        _exec_row("O-1", exec_no="E1", exec_qty=1, exec_price=0.64, remaining=2)
    ]
    await svc.reconcile()
    o = await repo.get_order(oid)
    assert o["status"] == OrderState.PARTIALLY_FILLED.value and o["filled_qty"] == 1

    # 2) 추가 체결 2계약 → 완전체결 (E1 은 멱등 무시, E2 만 반영)
    fake.open_orders["ADZ25"] = [
        _exec_row("O-1", exec_no="E1", exec_qty=1, exec_price=0.64, remaining=2),
        _exec_row("O-1", exec_no="E2", exec_qty=2, exec_price=0.66, remaining=0),
    ]
    await svc.reconcile()
    o = await repo.get_order(oid)
    assert o["status"] == OrderState.FILLED.value and o["filled_qty"] == 3


async def test_reconcile_vanished_order_canceled(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    oid = (await svc.place(_intent()))["order"]["id"]
    fake.open_orders["ADZ25"] = []  # 브로커에 없음(소멸), 장중
    await svc.reconcile(market_closed=False)
    assert (await repo.get_order(oid))["status"] == OrderState.CANCELED.value


async def test_reconcile_orphan_registered(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    await svc.place(_intent())  # O-1 (DB 가 아는 주문)
    # 브로커에 우리가 모르는 OrdNo O-99 (체결분 포함)
    fake.open_orders["ADZ25"] = [
        _exec_row("O-1", exec_no="E1", exec_qty=3, exec_price=0.65, remaining=0),
        _exec_row("O-99", exec_no="E9", exec_qty=1, exec_price=0.70, remaining=0),
    ]
    res = await svc.reconcile()
    orders = await repo.list_orders()
    orphan = [o for o in orders if o["reconcile_key"] == "recon:O-99"]
    assert len(orphan) == 1 and orphan[0]["status"] == OrderState.FILLED.value
    assert res["found"] >= 1


async def test_reconcile_positions_upsert(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    await svc.place(_intent())
    fake.positions = [Position(symbol="ADZ25", qty=3, avg_price=0.65, side=Side.BUY)]
    await svc.reconcile()
    acct = await repo.get_account_id(MARKET_OVERSEAS_FUTUREOPTION)
    positions = await repo.list_positions(acct)
    assert any(p["qty"] == 3 for p in positions)


async def test_market_close_timeout_expires_unfilled(monkeypatch):
    """accepted 무체결 주문 + 장마감 reconcile → expired (DoD: 장마감 타임아웃)."""
    svc, repo, fake = await _svc(monkeypatch)
    oid = (await svc.place(_intent()))["order"]["id"]
    # 브로커엔 미체결로 남아있으나 체결 없음
    fake.open_orders["ADZ25"] = [
        OpenOrder(broker_ord_no="O-1", symbol="ADZ25", side=Side.BUY, qty=3, price=0.65,
                  exec_qty=0, remaining_qty=3, ord_status_code="0")
    ]
    await svc.reconcile(market_closed=True)
    assert (await repo.get_order(oid))["status"] == OrderState.EXPIRED.value
