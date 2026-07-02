"""부트 스테이트머신 (M3b §7) — 분류·boot reconcile·quarantine·READ_ONLY→ACTIVE(+부정 케이스).

DoD: reconcile in-flight/quarantine + READ_ONLY→ACTIVE 전이(+부정 케이스).
M2 4분기 회귀는 test_reconcile_future.py 가 계속 green 으로 커버.
"""

from __future__ import annotations

from app.adapters.order_base import OrderError
from app.core.engine_state import EngineState
from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION
from app.models.order_dto import IntentKind, OrderIntent, OrderState, OrderType, Side
from app.orders.boot import boot_engine
from app.services.order_service import OrderService
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter

FUT = MARKET_OVERSEAS_FUTUREOPTION


def _intent(**kw):
    base = dict(symbol="ADZ25", side=Side.BUY, order_type=OrderType.LIMIT, qty=2, price=0.65)
    base.update(kw)
    return OrderIntent(**base)


def _exec_row(ordno, *, exec_no, exec_qty, exec_price, remaining):
    from app.models.order_dto import OpenOrder

    return OpenOrder(
        broker_ord_no=ordno, exec_no=exec_no, symbol="ADZ25", side=Side.BUY,
        qty=2, price=0.65, exec_qty=exec_qty, exec_price=exec_price, remaining_qty=remaining,
        ord_status_code="2",
    )


async def _svc(monkeypatch, *, engine="PAPER_TRADING"):
    repo = await make_repo()
    fake = FakeOrderAdapter()
    patch_adapter(monkeypatch, fake)
    return OrderService(repo, session=None, settings=fake_settings(engine)), repo, fake


async def _count_risk_events(repo, event_type):
    async with repo._connect() as db:
        await repo._prep(db)
        async with db.execute(
            "SELECT count(*) c FROM risk_events WHERE event_type=?", (event_type,)
        ) as cur:
            return (await cur.fetchone())["c"]


# ── 정상 부팅 ─────────────────────────────────────────────────────────────
async def test_boot_empty_book_reaches_active(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    report = await boot_engine(svc)
    assert report.engine_state == EngineState.ACTIVE
    assert report.position_sync_ok and not report.entry_blocked and report.quarantined == []
    # ACTIVE → 신규 주문 통과
    res = await svc.place(_intent())
    assert res["ok"] and res["order"]["status"] == OrderState.ACCEPTED.value


async def test_boot_classifies_and_resolves_working_order(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    placed = await svc.place(_intent())  # accepted, O-1 (working)
    oid = placed["order"]["id"]
    # 브로커가 완전체결 보고 → boot reconcile 이 해소
    fake.open_orders["ADZ25"] = [
        _exec_row("O-1", exec_no="E1", exec_qty=2, exec_price=0.65, remaining=0)
    ]
    report = await boot_engine(svc)
    assert oid in report.classified["working"]
    assert (await repo.get_order(oid))["status"] == OrderState.FILLED.value
    assert report.engine_state == EngineState.ACTIVE and not report.entry_blocked


# ── 부정 케이스: RECONCILING 중 주문 거부 ─────────────────────────────────
async def test_reconciling_blocks_new_orders(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    svc._engine.set(EngineState.RECONCILING)
    res = await svc.place(_intent())
    assert res["ok"] is False
    assert "ENGINE_NOT_ACTIVE" in res["decision"]["reasons"]
    assert fake.calls == []  # 주문 미생성


# ── quarantine: OrdNo 없는 in_doubt → 격리 + ENTRY 차단 ───────────────────
async def test_boot_quarantines_unresolved_in_doubt(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    fake.place_raises = RuntimeError("network down")
    placed = await svc.place(_intent())  # in_doubt, OrdNo 없음
    oid = placed["order"]["id"]
    assert placed["order"]["status"] == OrderState.IN_DOUBT.value
    fake.place_raises = None

    report = await boot_engine(svc)
    # 격리 상태 + critical risk_event
    assert oid in report.quarantined
    assert (await repo.get_order(oid))["status"] == OrderState.QUARANTINED.value
    assert await _count_risk_events(repo, "quarantined") == 1
    assert (await repo.get_metrics()).get("quarantined") == 1  # §12 메트릭 노출
    # 엔진은 ACTIVE 지만 ENTRY 차단(감축 EXIT/취소는 가능, §7.1)
    assert report.engine_state == EngineState.ACTIVE and report.entry_blocked
    # 신규 ENTRY 거부
    entry = await svc.preview(_intent())
    assert entry.result.value == "reject" and "QUARANTINED" in entry.reasons


async def test_quarantine_allows_reduce_only_exit(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    # OrdNo 없는 in_doubt 격리 유발(포지션 추가 전 — INV-7 트립 회피).
    fake.place_raises = RuntimeError("net down")
    await svc.place(_intent())
    fake.place_raises = None
    # 보유 포지션(long 5) — 감축 EXIT 대상
    acct = await repo.get_account_id(FUT)
    inst = await repo.ensure_instrument(FUT, "ADZ25", exchange="HKEX")
    await repo.upsert_position_authority(
        acct, inst, bucket="paper", market=FUT, currency="USD",
        position_side="long", qty=5, avg_price=0.65,
    )
    report = await boot_engine(svc)
    assert report.entry_blocked

    # reduce-only EXIT(보유 long 5, SELL 2) → quarantine 우회 PASS
    ex = await svc.preview(
        OrderIntent(symbol="ADZ25", side=Side.SELL, intent=IntentKind.EXIT, qty=2, price=0.65)
    )
    assert ex.ok and "QUARANTINED" not in ex.reasons


# ── 부정 케이스: 포지션 동기화 실패 → ACTIVE 불가(READ_ONLY 유지) ─────────
async def test_position_sync_failure_keeps_read_only(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch)
    fake.positions_raises = OrderError("ACCT_TR", "호출 거래건수를 초과")
    report = await boot_engine(svc)
    assert report.position_sync_ok is False
    assert report.engine_state == EngineState.READ_ONLY
    # READ_ONLY → 신규 주문 거부
    res = await svc.place(_intent())
    assert res["ok"] is False and "ENGINE_NOT_ACTIVE" in res["decision"]["reasons"]


# ── 부정 케이스: config 의도 READ_ONLY → 깨끗해도 ACTIVE 안 됨 ─────────────
async def test_config_read_only_intent_stays_read_only(monkeypatch):
    svc, repo, fake = await _svc(monkeypatch, engine="READ_ONLY")
    report = await boot_engine(svc)
    assert report.config_intent_active is False
    assert report.engine_state == EngineState.READ_ONLY
