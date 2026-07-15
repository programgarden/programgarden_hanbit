"""per-bucket EngineState (M4a §3.2 / §17 L3-11) — 단일 글로벌 폐기 증명.

M4a 는 단일 `EngineState` 를 **버킷→EngineState 맵**으로 리팩터한다. 이 테스트는:
- allow_live=false(기본) → live 버킷 영구 READ_ONLY, paper 는 정상 ACTIVE.
- **버킷 격리(핵심 DoD)**: live 부트 실패(reconcile 예외/미구현)가 paper ACTIVE 전이를
  막지 않는다 — live READ_ONLY ∧ paper ACTIVE 로 독립(§17 L3-11).
- 미지 시장(bucket None) → `engine_for` 가 LIVE_DISABLED 로 거부(§17 L3-4).
- 하위호환: `engine`(단수)/`_engine` = paper 버킷, registry allow_live 게이트, DTO 보강.

paper 동작 무변은 test_reconcile_boot.py(191 회귀)가 커버 — 여기서는 리팩터 신규 계약만 본다.
"""

from __future__ import annotations

import pytest

from app.adapters.order_base import OrderError
from app.adapters.order_registry import make_order_adapter
from app.core.engine_state import EngineState
from app.core.mode_matrix import (
    BUCKET_LIVE,
    BUCKET_PAPER,
    MARKET_KOREA_STOCK,
    MARKET_OVERSEAS_STOCK,
)
from app.models.order_dto import CancelRequest
from app.orders.boot import boot_engine
from app.services.order_service import OrderService
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter


async def _svc(monkeypatch, *, engine="PAPER_TRADING", allow_live=False):
    repo = await make_repo()
    fake = FakeOrderAdapter()
    patch_adapter(monkeypatch, fake)
    svc = OrderService(
        repo, session=None, settings=fake_settings(engine, allow_live=allow_live)
    )
    return svc, repo, fake


# ── allow_live=false(기본): live 버킷 영구 READ_ONLY, paper 정상 ACTIVE ──────────
async def test_live_bucket_read_only_when_allow_live_false(monkeypatch):
    svc, _repo, _fake = await _svc(monkeypatch)  # allow_live=False
    report = await boot_engine(svc)
    assert report.engine_states == {
        BUCKET_PAPER: EngineState.ACTIVE,
        BUCKET_LIVE: EngineState.READ_ONLY,
    }
    # 런타임 접근자도 일치
    assert svc.engine_for(BUCKET_PAPER).state == EngineState.ACTIVE
    assert svc.engine_for(BUCKET_LIVE).state == EngineState.READ_ONLY
    # 하위호환 최상위 필드 = paper
    assert report.engine_state == EngineState.ACTIVE


# ── M4b: allow_live=true + live reconcile 성공(빈 책) → live ACTIVE (paper 와 독립) ──────
async def test_live_boot_reaches_active_when_allow_live_and_reconcile_ok(monkeypatch):
    """M4b 에서 live reconcile(KR/OVS list-all)이 구현되어, allow_live=true + 포지션 동기화
    성공(빈 책)이면 live 버킷이 ACTIVE 에 도달한다. paper 는 독립적으로 ACTIVE.
    (M4a 에선 live reconcile 미구현이라 여기서 READ_ONLY 에 남았다 — 이제 진화.)"""
    svc, _repo, _fake = await _svc(monkeypatch, allow_live=True)
    report = await boot_engine(svc)
    assert report.engine_states[BUCKET_LIVE] == EngineState.ACTIVE  # live 부트 성공
    assert report.engine_states[BUCKET_PAPER] == EngineState.ACTIVE  # paper 독립
    assert report.engine_state == EngineState.ACTIVE


async def test_live_reconcile_arbitrary_exception_does_not_block_paper(monkeypatch):
    """임의 예외(RuntimeError)를 live reconcile 이 던져도 버킷별 독립 try/except 가 흡수 —
    paper 는 ACTIVE, live 는 READ_ONLY. (단일 try 였다면 paper 전이가 함께 죽는다.)"""
    svc, _repo, _fake = await _svc(monkeypatch, allow_live=True)
    real_reconcile = svc.reconcile

    async def flaky(*, scope="manual", market_closed=False, bucket=None):
        if bucket == BUCKET_LIVE:
            raise RuntimeError("live reconcile boom")
        return await real_reconcile(scope=scope, market_closed=market_closed, bucket=bucket)

    monkeypatch.setattr(svc, "reconcile", flaky)
    report = await boot_engine(svc)
    assert report.engine_states[BUCKET_PAPER] == EngineState.ACTIVE
    assert report.engine_states[BUCKET_LIVE] == EngineState.READ_ONLY


# ── 미지 시장(bucket None) → LIVE_DISABLED 거부(§17 L3-4, engines[None] KeyError 방지) ──
async def test_engine_for_unknown_bucket_rejects(monkeypatch):
    svc, _repo, _fake = await _svc(monkeypatch)
    with pytest.raises(OrderError) as ei:
        svc.engine_for(None)  # bucket_of(미지 시장) = None
    assert ei.value.code == "LIVE_DISABLED"
    with pytest.raises(OrderError):
        svc.engine_for("no_such_bucket")


# ── 하위호환: engine/_engine = paper 버킷, 버킷 엔진은 서로 다른 객체 ──────────────
async def test_engine_compat_alias_and_distinct_buckets(monkeypatch):
    svc, _repo, _fake = await _svc(monkeypatch)
    assert svc.engine is svc.engine_for(BUCKET_PAPER)
    assert svc._engine is svc.engine_for(BUCKET_PAPER)  # deprecated 별칭도 paper
    assert svc.engine_for(BUCKET_PAPER) is not svc.engine_for(BUCKET_LIVE)


# ── 독립-레이어 누수 가드(§17 L1-4 registry 레이어): allow_live 게이트 단독 거부 ─────
def test_registry_allow_live_gate_rejects_live_markets():
    for market in (MARKET_KOREA_STOCK, MARKET_OVERSEAS_STOCK):
        with pytest.raises(OrderError) as ei:
            make_order_adapter(market, None, allow_live=False)
        assert ei.value.code == "LIVE_DISABLED"
        # M4b/M4c: allow_live=true → 어댑터 생성 성공. registry 는 마스터 토글 단일 관문.
        adapter = make_order_adapter(market, None, allow_live=True)
        assert adapter.market == market


# ── DTO 보강(§4.3 / §17 L4-4): CancelRequest.qty Optional(기본 None) ───────────────
def test_cancel_request_qty_optional_default_none():
    r = CancelRequest(org_ord_no="O-1", symbol="ADZ25")
    assert r.qty is None  # None = 전량(FUT), 기존 호출부 무파괴
    r2 = CancelRequest(org_ord_no="O-1", symbol="005930", qty=3)
    assert r2.qty == 3
