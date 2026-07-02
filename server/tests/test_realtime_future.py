"""실시간 TC2/TC3 스캐폴드 (M3b §10) — fake TC 이벤트 결정론 테스트.

검증: TC3→Fill 정규화 · TC2 코드 매핑 · 단일 writer 적재(reconcile 와 동일 경로) ·
flag off / 런타임 READ_ONLY·RECONCILING 시 writer 강제 off(Lens2-M3) · 멱등 · 미매칭 위임.
DoD 아님(스캐폴드, flag off). 라이브 값/OvrsFutsOrdNo 매칭은 §13-5 open question.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.adapters.realtime_future import (
    RealtimeFutureFillSource,
    tc2_to_event,
    tc3_to_fill,
)
from app.core.engine_state import EngineState
from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION
from app.models.order_dto import OrderState
from tests._fut_helpers import fake_settings, make_repo

FUT = MARKET_OVERSEAS_FUTUREOPTION


def _tc3(ordr_no="O-1", *, ccls_q="2", ccls_prc="0.65", ccls_no="E1", s_b_ccd="2", ordr_ccd="1"):
    return SimpleNamespace(
        ordr_no=ordr_no, orgn_ordr_no="0", is_cd="ADZ25", s_b_ccd=s_b_ccd, ordr_ccd=ordr_ccd,
        ccls_q=ccls_q, ccls_prc=ccls_prc, ccls_no=ccls_no, ccls_tm="120000", svc_id="CH01",
    )


def _tc2_body(ordr_no="O-1", *, s_b_ccd="2", ordr_ccd="1", ordr_q="2", cnfr_q="0", rfsl_cd=""):
    """TC2RealResponseBody 형태 — §1.5 필드만(rsp_cd 는 body 에 없음)."""
    return SimpleNamespace(
        ordr_no=ordr_no, orgn_ordr_no="0", is_cd="ADZ25", s_b_ccd=s_b_ccd, ordr_ccd=ordr_ccd,
        ordr_q=ordr_q, cnfr_q=cnfr_q, rfsl_cd=rfsl_cd,
    )


def _tc2(rsp_cd="00000", *, rsp_msg="", error_msg=None, **body_kw):
    """TC2RealResponse 엔벨로프 — body + 응답코드(엔벨로프 레벨)."""
    return SimpleNamespace(
        body=_tc2_body(**body_kw), rsp_cd=rsp_cd, rsp_msg=rsp_msg, error_msg=error_msg,
    )


async def _seed_order(repo, *, broker_ord_no="O-1", qty=2):
    """accepted 주문(broker_order_id) 시드 — TC 매칭 전제(reconcile 와 동일).

    계좌 앵커는 migration 0002 가 시드한 기본 FUT 계좌(get_account_id)를 쓴다 —
    source._apply_fill 이 동일하게 get_account_id 로 해석하므로 일치해야 한다.
    """
    acct = await repo.get_account_id(FUT)
    assert acct is not None
    inst = await repo.ensure_instrument(FUT, "ADZ25", exchange="HKEX")
    oid, created = await repo.insert_order(
        idempotency_key=f"seed:{broker_ord_no}", account_id=acct, instrument_id=inst,
        market=FUT, trading_mode="paper", side="buy", order_type="limit", qty=qty,
        price=0.65, broker_order_id=broker_ord_no, status=OrderState.ACCEPTED.value,
    )
    assert created
    return oid


def _source(repo, *, engine=EngineState.ACTIVE, realtime_fills=True):
    return RealtimeFutureFillSource(
        repo, session=None, settings=fake_settings(realtime_fills=realtime_fills),
        engine=EngineState(engine),
    )


# ── 순수 정규화 ────────────────────────────────────────────────────────────
def test_tc3_to_fill_normalizes_fields():
    fill = tc3_to_fill(_tc3(ccls_q="2", ccls_prc="0.65", ccls_no="E9"))
    assert fill is not None
    assert fill.broker_ord_no == "O-1"
    assert fill.exec_qty == 2.0 and fill.exec_price == 0.65
    assert fill.event_seq == "tc:E9"  # 멱등키 prefix 'tc:'
    assert fill.origin == "tc" and fill.remaining_qty is None
    assert fill.raw and fill.raw["ccls_no"] == "E9"


def test_tc3_event_seq_fallbacks_to_ordno():
    fill = tc3_to_fill(_tc3(ccls_no=""))  # 체결식별자 없으면 ordr_no fallback
    assert fill is not None and fill.event_seq == "tc:O-1"


def test_tc3_non_fill_returns_none():
    assert tc3_to_fill(_tc3(ccls_q="0")) is None        # 미체결
    assert tc3_to_fill(_tc3(ordr_no="")) is None         # OrdNo 없음
    assert tc3_to_fill(_tc3(ccls_q="")) is None          # 빈 문자열


def test_tc2_to_event_maps_codes():
    # §1.5 필드는 body, 응답코드(rsp_cd)는 엔벨로프 → kwarg 로 주입(라이브 형태).
    ev = tc2_to_event(_tc2_body(s_b_ccd="1", ordr_ccd="2", rfsl_cd="X1"), rsp_cd="00000")
    assert ev["broker_ord_no"] == "O-1"
    assert ev["side"] == "sell"          # s_b_ccd '1'=매도(어댑터와 동일 반전)
    assert ev["relation"] == "modify"    # ordr_ccd '2'=정정
    assert ev["reject_code"] == "X1" and ev["rsp_cd"] == "00000"


async def test_tc2_handler_reads_rsp_cd_from_envelope():
    """라이브 형태(rsp_cd 는 엔벨로프, §1.5 필드는 body)에서 핸들러가 응답코드를 읽어내는가."""
    repo = await make_repo()
    src = _source(repo)
    ev = await src._handle_tc2(_tc2(rsp_cd="00000", s_b_ccd="1", ordr_ccd="2"))
    assert ev["rsp_cd"] == "00000"   # body 가 아니라 엔벨로프에서
    assert ev["side"] == "sell" and ev["relation"] == "modify"  # body 필드 매핑 유지


# ── writer 적재(단일 경로) ─────────────────────────────────────────────────
async def test_writer_applies_when_active_and_flag_on():
    repo = await make_repo()
    oid = await _seed_order(repo, qty=2)
    src = _source(repo, engine=EngineState.ACTIVE, realtime_fills=True)
    assert src.writer_enabled is True

    res = await src._handle_tc3(SimpleNamespace(body=_tc3(ccls_q="2", ccls_no="E1")))
    assert res == "applied"
    o = await repo.get_order(oid)
    assert o["status"] == OrderState.FILLED.value and o["filled_qty"] == 2.0


async def test_partial_then_full_fill():
    repo = await make_repo()
    oid = await _seed_order(repo, qty=3)
    src = _source(repo)
    assert await src._handle_tc3(_tc3(ccls_q="1", ccls_no="E1")) == "applied"
    assert (await repo.get_order(oid))["status"] == OrderState.PARTIALLY_FILLED.value
    assert await src._handle_tc3(_tc3(ccls_q="2", ccls_no="E2")) == "applied"
    o = await repo.get_order(oid)
    assert o["status"] == OrderState.FILLED.value and o["filled_qty"] == 3.0


async def test_idempotent_duplicate_tc():
    repo = await make_repo()
    oid = await _seed_order(repo, qty=2)
    src = _source(repo)
    assert await src._handle_tc3(_tc3(ccls_q="2", ccls_no="E1")) == "applied"
    # 동일 ccls_no 재수신 → 멱등(event_seq 기존)
    assert await src._handle_tc3(_tc3(ccls_q="2", ccls_no="E1")) == "duplicate"
    assert (await repo.get_order(oid))["filled_qty"] == 2.0  # 이중계상 0
    assert (await repo.get_metrics()).get("realtime_fills_duplicate") == 1


async def test_new_fill_after_terminal_is_observable():
    """동시 reconcile 이 주문을 터미널화한 뒤 도착한 '신규' TC 체결은 멱등중복과 구분돼야 한다.

    apply_fill 은 둘 다 False 지만 fill_exists 로 분기 → after_terminal 전용 메트릭(§13-5).
    """
    repo = await make_repo()
    oid = await _seed_order(repo, qty=2)
    src = _source(repo)
    assert await src._handle_tc3(_tc3(ccls_q="2", ccls_no="E1")) == "applied"  # → FILLED(터미널)
    # 신규 체결식별자(E2)인데 주문은 이미 터미널 → 미적재지만 '중복'이 아님
    assert await src._handle_tc3(_tc3(ccls_q="1", ccls_no="E2")) == "after_terminal"
    o = await repo.get_order(oid)
    assert o["filled_qty"] == 2.0  # 이중계상 0(reconcile 권위로 흡수)
    m = await repo.get_metrics()
    assert m.get("realtime_fills_after_terminal") == 1 and "realtime_fills_duplicate" not in m


async def test_unmatched_ordno_no_write():
    repo = await make_repo()
    await _seed_order(repo, broker_ord_no="O-1")
    src = _source(repo)
    # DB 미등록 OrdNo → reconcile orphan 흡수에 위임(여기선 미생성)
    assert await src._handle_tc3(_tc3(ordr_no="O-999", ccls_no="E1")) == "no_order"


# ── writer 강제 off (Lens2-M3) ─────────────────────────────────────────────
async def test_writer_off_when_flag_off():
    repo = await make_repo()
    oid = await _seed_order(repo)
    src = _source(repo, engine=EngineState.ACTIVE, realtime_fills=False)
    assert src.writer_enabled is False
    assert await src._handle_tc3(_tc3()) == "skipped"
    assert (await repo.get_order(oid))["status"] == OrderState.ACCEPTED.value  # 미변이


async def test_writer_off_when_read_only_or_reconciling():
    repo = await make_repo()
    oid = await _seed_order(repo)
    for state in (EngineState.READ_ONLY, EngineState.RECONCILING):
        src = _source(repo, engine=state, realtime_fills=True)  # flag on 이어도
        assert src.writer_enabled is False
        assert await src._handle_tc3(_tc3()) == "skipped"
    assert (await repo.get_order(oid))["status"] == OrderState.ACCEPTED.value


# ── reconcile 권위 유지(교차-origin 키 분리, §13-5) ─────────────────────────
async def test_tc_key_distinct_from_reconcile_prefix():
    """현재 스캐폴드: TC='tc:'+ccls_no, reconcile='recon:'+ExecNo 로 prefix 가 분리된다.

    ccls_no==OvrsFutsExecNo 여부가 라이브 미검증(§13-5)이라 flag off 가 안전 기본.
    교차-origin 중복제거는 라이브 확정 후 도입(여기선 prefix 분리만 박제).
    """
    from app.models.order_dto import OpenOrder
    from app.orders.fill_tracker import open_order_to_fill

    recon = open_order_to_fill(
        OpenOrder(broker_ord_no="O-1", exec_no="E1", symbol="ADZ25", exec_qty=2, exec_price=0.65)
    )
    tc = tc3_to_fill(_tc3(ccls_no="E1"))
    assert recon is not None and tc is not None
    assert recon.event_seq == "recon:E1" and tc.event_seq == "tc:E1"
    assert recon.event_seq != tc.event_seq
