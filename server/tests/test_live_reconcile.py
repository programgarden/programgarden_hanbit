"""LIVE reconcile (KR/OVS list-all, §7) — M4b/M4c.

- 체결 적용(matched OrdNo).
- ★ 소멸판정 보류 fail-safe: 브로커 스냅에 없는 DB 주문을 CANCELED/EXPIRED 로 전이하지 않는다
  (단일 페이지 미완결 가능 — 살아있는 working 주문 오소멸 차단, §17 L4-1).
- 정정 후속(OrgOrdNo 체인) orphan 오등록 차단(§17 L4-3).
- 포지션 동기화(권위 writer, 시장/버킷/통화 라우팅).
- allow_live=false 면 live reconcile 하드 거부(방어).
"""

from __future__ import annotations

import pytest

from app.adapters.order_base import OrderError
from app.core.engine_state import EngineState
from app.core.mode_matrix import BUCKET_LIVE, MARKET_KOREA_STOCK
from app.models.order_dto import (
    OpenOrder,
    OrderAck,
    OrderIntent,
    OrderState,
    OrderType,
    Position,
    Side,
)
from app.services.order_service import OrderService
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter


async def _live_svc(monkeypatch, *, allow_live=True):
    repo = await make_repo()
    await repo.ensure_account(
        MARKET_KOREA_STOCK, "KR-ACCT", trading_mode="live", currency="KRW"
    )
    fake = FakeOrderAdapter()
    patch_adapter(monkeypatch, fake)
    svc = OrderService(repo, session=None, settings=fake_settings(allow_live=allow_live))
    return svc, repo, fake


async def _insert_kr_order(repo, *, key, broker_ord_no, qty=10):
    inst = await repo.ensure_instrument(MARKET_KOREA_STOCK, "005930", exchange="KRX")
    acct = await repo.get_account_id(MARKET_KOREA_STOCK)
    oid, _created = await repo.insert_order(
        idempotency_key=key,
        account_id=acct,
        instrument_id=inst,
        market=MARKET_KOREA_STOCK,
        trading_mode="live",
        side="buy",
        order_type="limit",
        qty=qty,
        price=70000,
        exchange="KRX",
        relation="new",
        broker_order_id=broker_ord_no,
        status=OrderState.ACCEPTED.value,
    )
    return oid


# ── 체결 적용 + fail-safe(소멸 미전이) ──────────────────────────────────────
async def test_fill_applied_and_missing_order_not_expired(monkeypatch):
    svc, repo, fake = await _live_svc(monkeypatch)
    filled_id = await _insert_kr_order(repo, key="kr-1", broker_ord_no="111")
    missing_id = await _insert_kr_order(repo, key="kr-2", broker_ord_no="222")
    # 브로커 스냅: 111 은 부분체결, 222 는 아예 없음(소멸처럼 보임).
    fake.open_orders[""] = [
        OpenOrder(
            broker_ord_no="111", symbol="005930", side=Side.BUY, qty=10,
            price=70000, exec_qty=4, exec_price=70000, remaining_qty=6,
        )
    ]
    out = await svc.reconcile(scope="manual", bucket=BUCKET_LIVE)
    assert out["found"] >= 1

    o111 = await repo.get_order(filled_id)
    assert float(o111["filled_qty"]) == 4  # 체결 반영

    o222 = await repo.get_order(missing_id)
    # ★ fail-safe: 브로커에 없어도 CANCELED/EXPIRED 로 전이하지 않는다.
    assert o222["status"] not in (OrderState.CANCELED.value, OrderState.EXPIRED.value)


# ── 정정 후속(OrgOrdNo 체인)은 orphan 아님 / 진짜 미지 OrdNo 만 orphan 등록 ────
async def test_amend_successor_not_orphaned_but_unknown_is(monkeypatch):
    svc, repo, fake = await _live_svc(monkeypatch)
    acct = await repo.get_account_id(MARKET_KOREA_STOCK)
    await _insert_kr_order(repo, key="kr-parent", broker_ord_no="111")
    fake.open_orders[""] = [
        # 333 = 111 의 정정 후속(OrgOrdNo=111, DB 에 부모 존재) → orphan 아님.
        OpenOrder(broker_ord_no="333", org_ord_no="111", symbol="005930", side=Side.BUY, qty=5),
        # 444 = 부모 없는 미지 OrdNo → 진짜 orphan.
        OpenOrder(broker_ord_no="444", org_ord_no=None, symbol="005930", side=Side.BUY, qty=5),
    ]
    await svc.reconcile(scope="manual", bucket=BUCKET_LIVE)

    assert await repo.get_order_by_broker(acct, "333") is None  # 정정 후속 — 등록 안 함
    orphan = await repo.get_order_by_broker(acct, "444")
    assert orphan is not None  # 진짜 orphan — 등록됨
    assert orphan["market"] == MARKET_KOREA_STOCK


# ── 포지션 동기화(권위 writer) ──────────────────────────────────────────────
async def test_positions_synced_to_live_bucket(monkeypatch):
    svc, repo, fake = await _live_svc(monkeypatch)
    fake.positions = [
        Position(symbol="005930", qty=10, avg_price=68000, side=Side.BUY, currency="KRW")
    ]
    out = await svc.reconcile(scope="manual", bucket=BUCKET_LIVE)
    assert out["position_sync_ok"] is True
    positions = await repo.positions_for(BUCKET_LIVE)
    assert any(p.get("symbol") == "005930" for p in positions)


# ── allow_live=false → live reconcile 하드 거부(방어) ───────────────────────
async def test_live_reconcile_rejected_when_allow_live_false(monkeypatch):
    svc, _repo, _fake = await _live_svc(monkeypatch, allow_live=False)
    with pytest.raises(OrderError) as ei:
        await svc.reconcile(scope="manual", bucket=BUCKET_LIVE)
    assert ei.value.code == "LIVE_DISABLED"


# ── 행동 증명(§12): allow_live=true + ACTIVE → 캡 통과분만 실제 발사 ──────────
def _kr_intent(**kw):
    base = dict(
        market=MARKET_KOREA_STOCK, symbol="005930", side=Side.BUY,
        order_type=OrderType.LIMIT, qty=1, price=50000, currency="KRW",
    )
    base.update(kw)
    return OrderIntent(**base)


async def test_live_place_fires_only_when_cap_passed(monkeypatch):
    svc, _repo, fake = await _live_svc(monkeypatch)  # allow_live=True
    svc.engine_for(BUCKET_LIVE).set(EngineState.ACTIVE)  # 부트 성공 가정
    fake.place_ack = OrderAck(ok=True, broker_ord_no="900", rsp_cd="00040")

    # 캡 이내(50,000 ≤ 100,000) → 어댑터에 실제 발사.
    res = await svc.place(_kr_intent(price=50000))
    assert res["ok"] is True
    assert any(c[0] == "place" for c in fake.calls)

    # 캡 초과(200,000) → 게이트 REJECT, 어댑터 미진입(실주문 0).
    fake.calls.clear()
    res2 = await svc.place(_kr_intent(price=200000))
    assert res2["ok"] is False
    assert not any(c[0] == "place" for c in fake.calls)
    assert "PER_ORDER_CAP_LIVE" in res2["decision"]["reasons"]


async def test_live_place_blocked_when_allow_live_false(monkeypatch):
    """allow_live=false → LIVE place 는 게이트 LIVE_DISABLED 로 실주문 0(3중 방어)."""
    svc, _repo, fake = await _live_svc(monkeypatch, allow_live=False)
    svc.engine_for(BUCKET_LIVE).set(EngineState.ACTIVE)
    res = await svc.place(_kr_intent(price=50000))
    assert res["ok"] is False
    assert not any(c[0] == "place" for c in fake.calls)  # 어댑터 미진입


async def test_live_place_accumulates_daily_notional(monkeypatch):
    """LIVE 성공 발주분이 risk_state.daily_notional_used_krw 에 누적된다(§6 step4'')."""
    svc, repo, fake = await _live_svc(monkeypatch)
    svc.engine_for(BUCKET_LIVE).set(EngineState.ACTIVE)
    for ordno in ("900", "901"):  # OrdNo 유니크(account,broker_order_id 충돌 회피)
        fake.place_ack = OrderAck(ok=True, broker_ord_no=ordno, rsp_cd="00040")
        assert (await svc.place(_kr_intent(price=50000)))["ok"] is True
    rs = await repo.get_risk_state(BUCKET_LIVE)
    assert rs["daily_notional_used_krw"] == 100000  # 2 × 50,000 KRW(ceil 1.0)
