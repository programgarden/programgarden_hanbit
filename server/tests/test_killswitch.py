"""킬스위치 고도화 (M3b §9) — L1 버킷분기·LIVE no-op·quarantine raw-cancel·LIVE_DISABLED
미삼킴·엔진상태 우회(§8 우선 레인); L2 flatten(fake) reduce-only·pending_flatten 재계산.

DoD(§12): L1 FUT 취소·LIVE no-op-warning·quarantine(OrdNo) raw-cancel·LIVE_DISABLED 미삼킴;
L2 flatten(fake) reduce-only·pending_flatten 재계산. 전부 fake-LS(네트워크/실세션 없음).
"""

from __future__ import annotations

import pytest

from app.adapters.order_base import OrderError
from app.core.engine_state import EngineState
from app.core.mode_matrix import BUCKET_LIVE, BUCKET_PAPER, MARKET_OVERSEAS_FUTUREOPTION
from app.models.order_dto import (
    IntentKind,
    OrderAck,
    OrderIntent,
    OrderState,
    OrderType,
    Side,
)
from app.risk import killswitch
from app.services.order_service import OrderService
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter

FUT = MARKET_OVERSEAS_FUTUREOPTION


def _intent(**kw):
    base = dict(symbol="ADZ25", side=Side.BUY, order_type=OrderType.LIMIT, qty=2, price=0.65)
    base.update(kw)
    return OrderIntent(**base)


async def _svc(monkeypatch, *, engine="PAPER_TRADING"):
    repo = await make_repo()
    fake = FakeOrderAdapter()
    patch_adapter(monkeypatch, fake)
    return OrderService(repo, session=None, settings=fake_settings(engine)), repo, fake


async def _count_events(repo, event_type):
    async with repo._connect() as db:
        await repo._prep(db)
        async with db.execute(
            "SELECT count(*) c FROM risk_events WHERE event_type=?", (event_type,)
        ) as cur:
            return (await cur.fetchone())["c"]


async def _count_audit(repo, action):
    async with repo._connect() as db:
        await repo._prep(db)
        async with db.execute(
            "SELECT count(*) c FROM audit_log WHERE action=?", (action,)
        ) as cur:
            return (await cur.fetchone())["c"]


async def _place(svc, fake, ordno, **kw):
    """ordno 를 부여해 신규 발사(주문번호 유니크 충돌 회피)."""
    fake.place_ack = OrderAck(ok=True, broker_ord_no=ordno, rsp_cd="00000")
    return (await svc.place(_intent(**kw)))["order"]["id"]


async def _quarantine(repo, order_id):
    await repo.transition(order_id, OrderState.QUARANTINED, "boot")


async def _hold(repo, *, side="long", qty=5, avg=0.65):
    acct = await repo.get_account_id(FUT)
    inst = await repo.ensure_instrument(FUT, "ADZ25", exchange="HKEX")
    await repo.upsert_position_authority(
        acct, inst, bucket=BUCKET_PAPER, market=FUT, currency="USD",
        position_side=side, qty=qty, avg_price=avg,
    )


# ── L1: paper 미체결 일괄취소 ───────────────────────────────────────────────
async def test_level1_paper_cancels_working_orders(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    a = await _place(svc, fake, "O-1")
    b = await _place(svc, fake, "O-2", price=0.66)
    assert (await repo.get_order(a))["status"] == OrderState.ACCEPTED.value

    report = await killswitch.level1(svc, bucket=BUCKET_PAPER)
    assert report["canceled"] == 2
    assert (await repo.get_order(a))["status"] == OrderState.CANCELED.value
    assert (await repo.get_order(b))["status"] == OrderState.CANCELED.value


# ── L1: LIVE 버킷 = 명시적 no-op-with-warning (어댑터 미진입) ─────────────────
async def test_level1_live_bucket_is_noop_with_warning(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    report = await killswitch.level1(svc, bucket=BUCKET_LIVE)
    assert report == {"bucket": BUCKET_LIVE, "no_op": True, "canceled": 0}
    assert fake.calls == []  # cancel 루프에 LIVE 진입 0 — 어댑터 호출 없음
    assert await _count_events(repo, "kill_switch_live_noop") == 1


# ── L1: quarantine 노출 분리 — OrdNo 보유 raw-cancel(상태 유지) / 없음 excluded ─
async def test_level1_quarantine_raw_cancel_and_excluded(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    # OrdNo 없는 in_doubt(A) — quarantine 전 먼저 생성(가드 트립 회피).
    fake.place_raises = RuntimeError("net down")
    a = (await svc.place(_intent()))["order"]["id"]
    fake.place_raises = None
    assert (await repo.get_order(a))["status"] == OrderState.IN_DOUBT.value
    # OrdNo 보유 accepted(B).
    b = await _place(svc, fake, "O-2", price=0.66)
    await _quarantine(repo, a)
    await _quarantine(repo, b)
    fake.calls.clear()

    report = await killswitch.level1(svc, bucket=BUCKET_PAPER)
    # B(OrdNo) → raw cancel 전송, 상태는 quarantined 유지(운영 수동 resolve).
    assert report["quarantine_canceled"] == 1
    assert any(c[0] == "cancel" for c in fake.calls)
    assert (await repo.get_order(b))["status"] == OrderState.QUARANTINED.value
    # A(OrdNo 없음) → 진짜 수동, excluded 로 명시 보고.
    assert report["quarantine_excluded"] == [a]
    assert (await repo.get_order(a))["status"] == OrderState.QUARANTINED.value


# ── L1: LIVE_DISABLED 미삼킴 (paper 취소 루프의 라우팅 버그 = critical 전파) ───
async def test_level1_does_not_swallow_live_disabled(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    await svc.place(_intent())  # working order
    fake.cancel_raises = OrderError("LIVE_DISABLED", "routing bug")
    with pytest.raises(OrderError) as ei:
        await killswitch.level1(svc, bucket=BUCKET_PAPER)
    assert ei.value.code == "LIVE_DISABLED"


# ── L1: 엔진상태 우회 (§8 우선 레인 — boot 실패/RECONCILING 에도 취소) ────────
async def test_level1_bypasses_engine_state_for_risk_reduction(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    a = await _place(svc, fake, "O-1")
    b = await _place(svc, fake, "O-2", price=0.66)
    # boot 실패 시뮬레이션 — 런타임 비-ACTIVE.
    svc._engine.set(EngineState.RECONCILING)
    # 일반(위험감축 아님) 취소는 막힌다.
    with pytest.raises(OrderError) as ei:
        await svc.cancel(a)
    assert ei.value.code == "ENGINE_NOT_ACTIVE"
    # 킬스위치 L1 은 ACTIVE 우회로 취소 성사.
    report = await killswitch.level1(svc, bucket=BUCKET_PAPER)
    assert report["canceled"] == 2
    assert (await repo.get_order(a))["status"] == OrderState.CANCELED.value
    assert (await repo.get_order(b))["status"] == OrderState.CANCELED.value


# ── L1: engage 오케스트레이션 (halt set + global → paper 취소 + live no-op) ───
async def test_engage_global_halts_and_cancels(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    await svc.place(_intent())
    result = await killswitch.engage(svc, scope="global")
    assert result["level"] == 1 and result["canceled"] == 1
    assert BUCKET_PAPER in result["buckets"] and BUCKET_LIVE in result["buckets"]
    assert result["buckets"][BUCKET_LIVE]["no_op"] is True
    assert await repo.get_halt_state("global") == "killed"
    # halt → 신규 ENTRY 거부.
    assert (await svc.preview(_intent())).result.value == "reject"
    # 해제 → active 복귀.
    await killswitch.release(svc, scope="global")
    assert await repo.get_halt_state("global") == "active"


# ── L2: flatten (fake) — reduce-only EXIT(반대방향·보유 클램프·멱등키) ────────
async def test_flatten_places_reduce_only_exit(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    await _hold(repo, side="long", qty=5)
    out = await killswitch.flatten_all_positions(svc, run_seq=0)
    assert out["pending"] == [] and len(out["fired"]) == 1
    fired = out["fired"][0]
    assert fired["ok"] and fired["side"] == Side.SELL.value and fired["qty"] == 5
    # 게이트를 통과한 청산 주문은 reduce-only EXIT — 반대방향 SELL, 멱등키 flat:...
    placed = [c[1] for c in fake.calls if c[0] == "place"][-1]
    assert placed.intent == IntentKind.EXIT and placed.side == Side.SELL
    assert placed.qty == 5 and placed.client_order_id == "flat:paper:ADZ25:0"


async def test_flatten_live_bucket_noop_when_allow_live_false(monkeypatch):
    # allow_live=false(기본) → LIVE 청산 경로 닫힘 → 안전 no-op(어댑터 미진입). assert 대신
    # 게이트 진화(§17 L3-7): "거부"가 아니라 "실포지션 0이라 발사 0".
    svc, _repo, fake = await _svc(monkeypatch)
    out = await killswitch.flatten_all_positions(svc, bucket=BUCKET_LIVE)
    assert out == {"fired": [], "pending": [], "skipped": []}
    assert fake.calls == []  # LIVE 어댑터 미진입


# ── L2: 장마감 pending_flatten → 큐+critical, 발사 안 함 ─────────────────────
async def test_flatten_market_closed_queues_pending(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    await _hold(repo, side="long", qty=5)
    out = await killswitch.flatten_all_positions(svc, market_closed=True)
    assert out["pending"] == ["ADZ25"] and out["fired"] == []
    assert [c for c in fake.calls if c[0] == "place"] == []  # 발사 0
    assert await _count_events(repo, "pending_flatten") == 1


# ── L2: 개장 시 재개 — 현재 스냅에서 재계산(verbatim 금지·reduce-only 클램프) ─
async def test_resume_pending_recomputes_from_snapshot(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    await _hold(repo, side="long", qty=5)
    await killswitch.flatten_all_positions(svc, market_closed=True)
    # 마감~개장 사이 일부 체결로 보유 5→2 감소 → 개장 청산은 2(verbatim 5 아님).
    await _hold(repo, side="long", qty=2)
    out = await killswitch.resume_pending_flatten(svc, ["ADZ25"], run_seq=1)
    assert out["dropped"] == [] and len(out["fired"]) == 1
    assert out["fired"][0]["qty"] == 2 and out["fired"][0]["side"] == Side.SELL.value
    placed = [c[1] for c in fake.calls if c[0] == "place"][-1]
    assert placed.qty == 2 and placed.client_order_id == "flat:paper:ADZ25:1"


async def test_resume_drops_already_flat(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    await _hold(repo, side="long", qty=5)
    await killswitch.flatten_all_positions(svc, market_closed=True)
    # 개장 전 완전체결 → flat(qty 0) → 재발사 드롭.
    await _hold(repo, side="long", qty=0)
    out = await killswitch.resume_pending_flatten(svc, ["ADZ25"], run_seq=1)
    assert out["dropped"] == ["ADZ25"] and out["fired"] == []
    assert [c for c in fake.calls if c[0] == "place"] == []


# ── §12: 감사로그/메트릭 — 운영 액션(발동/해제) 트레일 누락 0 ─────────────────
async def test_engage_leaves_audit_and_metric(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    await killswitch.engage(svc, scope="global")
    # risk_event(critical) + audit_log(operator) + 카운터 모두 남는다.
    assert await _count_events(repo, "kill_switch") == 1
    assert await _count_audit(repo, "kill_switch.engage") == 1
    assert (await repo.get_metrics()).get("kill_switch_engaged") == 1


async def test_release_leaves_audit_and_risk_event(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    await killswitch.engage(svc, scope="global")
    await killswitch.release(svc, scope="global")
    # 이전엔 무흔적이던 해제도 risk_event + audit 트레일을 남긴다(§12 보강).
    assert await _count_events(repo, "kill_switch_release") == 1
    assert await _count_audit(repo, "kill_switch.release") == 1
