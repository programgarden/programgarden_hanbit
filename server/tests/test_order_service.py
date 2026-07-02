"""주문 서비스 place/amend/cancel/in_doubt/idempotent 통합 테스트(fake adapter)."""

from __future__ import annotations

import pytest

from app.models.order_dto import OrderIntent, OrderState, OrderType, Side
from app.services.order_service import OrderService
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter


def _intent(**kw):
    base = dict(symbol="ADZ25", side=Side.BUY, order_type=OrderType.LIMIT, qty=2, price=0.65)
    base.update(kw)
    return OrderIntent(**base)


async def _svc(monkeypatch, *, engine="PAPER_TRADING"):
    repo = await make_repo()
    fake = FakeOrderAdapter()
    patch_adapter(monkeypatch, fake)
    return OrderService(repo, session=None, settings=fake_settings(engine)), repo, fake


async def test_place_accepted(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    res = await svc.place(_intent())
    assert res["ok"] and res["order"]["status"] == OrderState.ACCEPTED.value
    assert res["order"]["broker_order_id"] == "O-1"
    assert (await repo.get_metrics())["orders_placed"] == 1


async def test_place_rejected_when_engine_read_only(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch, engine="READ_ONLY")
    res = await svc.place(_intent())
    assert res["ok"] is False
    assert "ENGINE_NOT_ACTIVE" in res["decision"]["reasons"]
    # 주문 미생성(어댑터 호출 없음)
    assert fake.calls == []
    assert await repo.list_orders() == []


async def test_place_rejected_non_whitelisted(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    res = await svc.place(_intent(symbol="ZZZ99"))
    assert res["ok"] is False and "FUT_NOT_HKEX" in res["decision"]["reasons"]
    assert fake.calls == []


async def test_place_in_doubt_on_exception(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    fake.place_raises = RuntimeError("network down")
    res = await svc.place(_intent())
    assert res["ok"] is False and res["in_doubt"] is True
    assert res["order"]["status"] == OrderState.IN_DOUBT.value


async def test_place_rejected_on_ack_failure(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    from app.models.order_dto import OrderAck

    fake.place_ack = OrderAck(ok=False, broker_ord_no=None, rsp_cd="9999", error_msg="bad")
    res = await svc.place(_intent())
    assert res["ok"] is False and res["order"]["status"] == OrderState.REJECTED.value
    assert (await repo.get_metrics())["orders_rejected"] == 1


async def test_place_idempotent_same_client_order_id(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    r1 = await svc.place(_intent(client_order_id="fixed-1"))
    r2 = await svc.place(_intent(client_order_id="fixed-1"))
    assert r2.get("idempotent") is True
    assert r2["order"]["id"] == r1["order"]["id"]
    # 브로커 place 는 한 번만
    assert sum(1 for c in fake.calls if c[0] == "place") == 1


async def test_amend_routes_through_guard_and_updates_parent(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    placed = await svc.place(_intent())
    oid = placed["order"]["id"]
    res = await svc.amend(oid, qty=3, price=0.70)
    assert res["ok"]
    parent = await repo.get_order(oid)
    assert parent["broker_order_id"] == "O-2" and parent["qty"] == 3 and parent["price"] == 0.70
    # 정정 child 행 기록
    assert any(c[0] == "amend" for c in fake.calls)


async def test_cancel_transitions_parent_to_canceled(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    placed = await svc.place(_intent())
    oid = placed["order"]["id"]
    res = await svc.cancel(oid)
    assert res["ok"]
    assert (await repo.get_order(oid))["status"] == OrderState.CANCELED.value


async def test_amend_blocked_when_read_only(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    placed = await svc.place(_intent())
    oid = placed["order"]["id"]
    # 런타임 EngineState 를 READ_ONLY 로 — 단일 권위(config 아님, M3b §7.1).
    svc._engine.set("READ_ONLY")
    from app.adapters.order_base import OrderError

    with pytest.raises(OrderError) as ei:
        await svc.amend(oid, qty=3, price=0.7)
    assert ei.value.code == "ENGINE_NOT_ACTIVE"


async def test_amend_rejected_over_per_order_cap(monkeypatch):
    """정정 노출 재검증(§7.1, 흡수 ②) — per_order_cap_krw 초과 정정은 거부."""
    svc, repo, fake = await _svc(monkeypatch)
    placed = await svc.place(_intent())
    oid = placed["order"]["id"]
    from app.adapters.order_base import OrderError

    # notional = 10 * 250 * 1 = 2500 USD → ceil KRW 2500*1400*1.02 ≈ 3.57M > 3M cap.
    with pytest.raises(OrderError) as ei:
        await svc.amend(oid, qty=10, price=250.0)
    assert ei.value.code == "AMEND_REJECTED" and "PER_ORDER_CAP_KRW" in ei.value.message
    # 브로커 정정 미발사(어댑터 amend 호출 없음)
    assert not any(c[0] == "amend" for c in fake.calls)


async def test_amend_blocked_when_halted_daily(monkeypatch):
    """일일손실 halt(risk_state)는 trading_halt 미러 없음 — amend 가 직접 확인(리뷰 #1)."""
    svc, repo, fake = await _svc(monkeypatch)
    placed = await svc.place(_intent())
    oid = placed["order"]["id"]
    await repo.set_risk_state("paper", halt_state="halted_daily")
    from app.adapters.order_base import OrderError

    with pytest.raises(OrderError) as ei:
        await svc.amend(oid, qty=5, price=0.9)  # 노출 증가 시도
    assert ei.value.code == "HALTED_DAILY"


async def test_reclassified_exit_recorded_as_open(monkeypatch):
    """보유 없는 EXIT → ENTRY 재분류 → position_effect='open'(리뷰 #8)."""
    from app.models.order_dto import IntentKind

    svc, repo, fake = await _svc(monkeypatch)
    res = await svc.place(_intent(intent=IntentKind.EXIT, side=Side.SELL))
    assert res["ok"]
    assert res["order"]["position_effect"] == "open"
