"""LIVE 파사드 부재 (M3b §12) — INV-1 의 **행동 증명**.

`test_readonly_invariant` 가 정규식으로 LIVE 주문 TR/경로의 *정적* 부재를 박는다면, 여기서는
포트폴리오·위험의 전 흐름(aggregator/fx/killswitch/flatten)을 fake-LS 로 실제로 돌려
**KR/OVS(LIVE) 주문이 한 번도 발사되지 않음**을 부작용 레벨에서 확인한다(§12).

- killswitch L1(LIVE 버킷, allow_live=false) → 어댑터 미진입(no-op-with-warning).
- flatten(LIVE, allow_live=false) → 청산 경로 닫힘 → 어댑터 미진입 안전 no-op(§17 L3-7).
  (allow_live=true LIVE 실동작은 test_killswitch_live 가 커버.)
- killswitch engage(global) → LIVE 버킷에 place/amend 0(위험감축 cancel 만 허용).
- aggregator + LIVE tracker 콜백 → 주문 0(LIVE tracker read-only — 집계 상태만 갱신).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.mode_matrix import (
    BUCKET_LIVE,
    BUCKET_PAPER,
    MARKET_KOREA_STOCK,
    MARKET_OVERSEAS_FUTUREOPTION,
)
from app.models.order_dto import OrderAck, OrderIntent, OrderType, Side
from app.portfolio.aggregator import PortfolioAggregator
from app.portfolio.fx import FxRateProvider
from app.portfolio.sources import make_tracker_callbacks
from app.risk import killswitch
from app.services.order_service import OrderService
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter

FUT = MARKET_OVERSEAS_FUTUREOPTION


async def _svc(monkeypatch):
    repo = await make_repo()
    fake = FakeOrderAdapter()
    patch_adapter(monkeypatch, fake)
    return OrderService(repo, session=None, settings=fake_settings()), repo, fake


def _intent(**kw):
    base = dict(symbol="ADZ25", side=Side.BUY, order_type=OrderType.LIMIT, qty=2, price=0.65)
    base.update(kw)
    return OrderIntent(**base)


async def _place(svc, fake, ordno, **kw):
    fake.place_ack = OrderAck(ok=True, broker_ord_no=ordno, rsp_cd="00000")
    return (await svc.place(_intent(**kw)))["order"]["id"]


# ── killswitch: LIVE 버킷은 어댑터에 닿지 않는다 ──────────────────────────────
async def test_killswitch_level1_live_never_touches_adapter(monkeypatch):
    svc, _repo, fake = await _svc(monkeypatch)
    report = await killswitch.level1(svc, bucket=BUCKET_LIVE)
    assert report["no_op"] is True
    assert fake.calls == []  # place/amend/cancel 어느 것도 LIVE 에서 발사되지 않음


async def test_flatten_live_bucket_is_noop_when_allow_live_false(monkeypatch):
    # allow_live=false → LIVE 청산 경로 닫힘 → 어댑터 미진입 안전 no-op(§17 L3-7 진화).
    svc, _repo, fake = await _svc(monkeypatch)
    out = await killswitch.flatten_all_positions(svc, bucket=BUCKET_LIVE)
    assert out == {"fired": [], "pending": [], "skipped": []}
    assert fake.calls == []


async def test_engage_global_places_no_live_orders(monkeypatch):
    svc, _repo, fake = await _svc(monkeypatch)
    await _place(svc, fake, "O-1")  # paper working order
    fake.calls.clear()
    await killswitch.engage(svc, scope="global")
    kinds = {c[0] for c in fake.calls}
    # 위험감축 취소만 어댑터에 닿는다 — 신규/정정(노출 증가) 발사는 0.
    assert "place" not in kinds and "amend" not in kinds
    assert kinds <= {"cancel"}


# ── aggregator + LIVE tracker: read-only(주문 0) ─────────────────────────────
async def test_live_tracker_callbacks_are_read_only():
    """LIVE 트래커 콜백은 집계기 상태만 갱신 — 주문 메서드는 호출되지 않는다(부재)."""
    calls: list[str] = []

    class SpyAggregator:
        # 주문 계열 메서드가 아예 없다 — 콜백이 그런 걸 부르려 하면 AttributeError 로 즉시 실패.
        def apply_position(self, snap):
            calls.append("apply_position")

        def apply_balance(self, snap):
            calls.append("apply_balance")

    cbs = make_tracker_callbacks(BUCKET_LIVE, MARKET_KOREA_STOCK, SpyAggregator())
    await cbs["on_position_change"](
        [{"symbol": "005930", "quantity": 10, "current_price": 70000, "currency_code": "KRW"}]
    )
    # korea 잔고는 단일 객체(dict 아님) 경로.
    await cbs["on_balance_change"](SimpleNamespace(deposit=1_000_000, currency_code="KRW"))
    assert calls == ["apply_position", "apply_balance"]  # read-only 갱신만


async def test_aggregator_tick_places_no_orders(monkeypatch):
    """집계기는 주문 어댑터 참조 자체가 없다 — LIVE 스냅을 먹여 KPI 를 뽑아도 주문 0.

    혹시라도 집계 경로에서 OrderService 가 만들어지면(=주문 경로 누수) 즉시 실패하도록 가드.
    """
    import app.services.order_service as os_mod

    def _boom(*_a, **_k):
        raise AssertionError("order adapter must not be built from the aggregation path")

    monkeypatch.setattr(os_mod, "make_order_adapter", _boom)

    repo = await make_repo()
    fx = FxRateProvider(usd_krw=1400.0, hkd_krw=180.0, repo=repo)
    agg = PortfolioAggregator(fx, repo=repo)
    cbs = make_tracker_callbacks(BUCKET_LIVE, MARKET_KOREA_STOCK, agg)
    await cbs["on_position_change"](
        [{"symbol": "005930", "quantity": 10, "current_price": 70000, "currency_code": "KRW"}]
    )
    kpi = agg.kpi(BUCKET_LIVE)
    assert kpi.position_count == 1
    # 집계 흐름은 주문 행을 만들지 않는다.
    async with repo._connect() as db:
        await repo._prep(db)
        async with db.execute("SELECT count(*) c FROM orders") as cur:
            assert (await cur.fetchone())["c"] == 0


# paper 격리(스모크) — facade 테스트가 paper 정상 경로를 막지 않는지(대조군).
async def test_paper_bucket_flatten_path_is_allowed(monkeypatch):
    svc, _repo, fake = await _svc(monkeypatch)
    out = await killswitch.flatten_all_positions(svc, bucket=BUCKET_PAPER)
    assert out == {"fired": [], "pending": [], "skipped": []}  # 빈 책 → 발사 0, 예외 없음
