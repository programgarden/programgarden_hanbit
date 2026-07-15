"""킬스위치 LIVE 실동작 (M4d §9) — allow_live=true 분기.

allow_live=false 는 test_killswitch/test_no_live_facade 가 no-op 을 커버. 여기서는 토글이
켜졌을 때 LIVE 버킷 L1(미체결 실취소)·L2(reduce-only flatten)가 실제로 동작함을 증명한다.
"""

from __future__ import annotations

from app.core.engine_state import EngineState
from app.core.mode_matrix import BUCKET_LIVE, MARKET_KOREA_STOCK
from app.models.order_dto import OrderState, Side
from app.risk import killswitch
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
    svc.engine_for(BUCKET_LIVE).set(EngineState.ACTIVE)  # 부트 성공 가정
    return svc, repo, fake


async def _insert_kr_order(repo, *, key, broker_ord_no, qty=5):
    inst = await repo.ensure_instrument(MARKET_KOREA_STOCK, "005930", exchange="KRX")
    acct = await repo.get_account_id(MARKET_KOREA_STOCK)
    oid, _ = await repo.insert_order(
        idempotency_key=key, account_id=acct, instrument_id=inst,
        market=MARKET_KOREA_STOCK, trading_mode="live", side="buy", order_type="limit",
        qty=qty, price=70000, exchange="KRX", relation="new",
        broker_order_id=broker_ord_no, status=OrderState.ACCEPTED.value,
    )
    return oid


# ── L1: allow_live=true → LIVE 미체결 실제 취소(어댑터 진입) ──────────────────
async def test_level1_live_cancels_when_allow_live(monkeypatch):
    svc, repo, fake = await _live_svc(monkeypatch)
    oid = await _insert_kr_order(repo, key="kr-1", broker_ord_no="111")
    report = await killswitch.level1(svc, bucket=BUCKET_LIVE)
    assert report.get("no_op") is not True  # no-op 아님
    assert report["canceled"] == 1
    assert any(c[0] == "cancel" for c in fake.calls)  # LIVE 어댑터 진입
    assert (await repo.get_order(oid))["status"] == OrderState.CANCELED.value


async def test_level1_live_still_noop_when_allow_live_false(monkeypatch):
    svc, repo, fake = await _live_svc(monkeypatch, allow_live=False)
    await _insert_kr_order(repo, key="kr-1", broker_ord_no="111")
    report = await killswitch.level1(svc, bucket=BUCKET_LIVE)
    assert report == {"bucket": BUCKET_LIVE, "no_op": True, "canceled": 0}
    assert fake.calls == []  # 토글 꺼짐 → 어댑터 미진입


# ── L2: allow_live=true → LIVE 포지션 reduce-only flatten 발사 ────────────────
async def test_flatten_live_fires_reduce_only_when_allow_live(monkeypatch):
    svc, repo, fake = await _live_svc(monkeypatch)
    acct = await repo.get_account_id(MARKET_KOREA_STOCK)
    inst = await repo.ensure_instrument(MARKET_KOREA_STOCK, "005930", exchange="KRX")
    await repo.upsert_position_authority(
        acct, inst, bucket=BUCKET_LIVE, market=MARKET_KOREA_STOCK, currency="KRW",
        position_side="long", qty=5, avg_price=70000,
    )
    out = await killswitch.flatten_all_positions(svc, bucket=BUCKET_LIVE, run_seq=0)
    assert out["pending"] == [] and len(out["fired"]) == 1
    fired = out["fired"][0]
    assert fired["ok"] and fired["side"] == Side.SELL.value and fired["qty"] == 5
    # reduce-only EXIT 이 게이트를 통과해 실제 발사(SELL, 멱등키 flat:live:...).
    placed = [c[1] for c in fake.calls if c[0] == "place"][-1]
    assert placed.side == Side.SELL and placed.client_order_id == "flat:live:005930:0"


# ── engage_level2(global) → paper+live 버킷 flatten 맵 ──────────────────────
async def test_engage_level2_flattens_all_buckets(monkeypatch):
    svc, _repo, _fake = await _live_svc(monkeypatch)
    result = await killswitch.engage_level2(svc, scope="global")
    assert result["level"] == 2
    # flatten 은 버킷별 맵 — paper/live 둘 다 키로 존재(빈 책이면 fired 0).
    assert set(result["flatten"]) == {"paper", "live"}
