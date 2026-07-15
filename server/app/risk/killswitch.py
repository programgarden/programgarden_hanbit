"""킬스위치 고도화 (M3b §9) — 레벨1 일괄취소 + 레벨2 청산(fake-테스트만).

M2 의 인라인 `cancel_all_open` 를 버킷-인지 오케스트레이션으로 끌어올린다. 핵심 안전 규율:

- **버킷 분기 선행**(§9, Lens2-M2): LIVE 버킷 L1 은 **명시적 no-op-with-warning** — LIVE 는
  주문 경로가 부재하므로 cancel 루프에 LIVE open order 를 절대 들이지 않는다(전수 순회 금지).
- **위험감축 lane = 엔진상태 우회**(§8 우선 레인): 취소는 멱등·노출감소라 런타임 ACTIVE 를
  요구하지 않는다(`OrderService.cancel(risk_reduction=True)`). boot 실패/RECONCILING 에도 막히면
  안 된다(단계6 연기분 흡수). 신규/정정·**L2 flatten 의 place** 는 여전히 guarded(ACTIVE 필요).
- **quarantine 노출 분리**(§7.2): 격리 주문은 OrdNo 보유 → raw cancel-by-OrdNo(상태 유지),
  OrdNo 없음 → 진짜 수동(excluded 로 명시 보고).
- **`LIVE_DISABLED` 미삼킴**(§0.2-4): paper 경로의 LIVE_DISABLED 는 스킵이 아니라 라우팅
  버그(critical) — 전파한다.

레벨2(`flatten_all_positions`)는 **M3 에선 fake-테스트만**(§0.3, [검증 3]) — paper 모의 체결
semantics 미검증이라 라이브 청산은 M4 이후. `assert bucket=='paper'` + `positions_for('paper')`
만 순회(전수 순회 금지), reduce-only EXIT(§5.5)를 guarded order_service 로 발사한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.order_base import OrderError
from app.core.mode_matrix import (
    BUCKET_LIVE,
    BUCKET_PAPER,
    MARKET_OVERSEAS_FUTUREOPTION,
    bucket_of,
    markets_of,
)
from app.models.order_dto import IntentKind, OrderIntent, OrderState, OrderType, Side
from app.risk import halt

if TYPE_CHECKING:  # 순환 import 회피 — 런타임은 덕타이핑(boot.py 와 동일 패턴)
    from app.services.order_service import OrderService


# ── 레벨1: 미체결 일괄취소 + 격리 raw-cancel (버킷 분기) ──────────────────────
async def engage(service: OrderService, *, scope: str) -> dict:
    """킬스위치 발동(L1). halt set → 신규 0단계 차단 + 미체결 일괄취소 + 격리 raw-cancel.

    scope 는 `halt.normalize_scope` 산출(global 또는 시장 키). L2(flatten)는 2단계 확인 +
    API(step10)에서 노출 — L1 이 M3 kill-switch DoD 바닥(§9).
    """
    repo = service._repo
    await halt.engage(repo, scope, reason="api")
    buckets: dict[str, dict] = {}
    canceled = 0
    for b in _target_buckets(scope):
        report = await level1(service, bucket=b)
        buckets[b] = report
        canceled += report.get("canceled", 0)
    await repo.insert_risk_event(
        event_type="kill_switch", severity="critical", scope=scope, message="engaged"
    )
    # 운영 액션 추적(§12 감사로그/메트릭): risk_event 와 별개로 audit_log + 카운터를 남긴다 —
    # 게이트는 주문 결정만 audit 하므로 킬스위치 발동/해제는 별도로 박제해야 누락 0.
    await repo.incr_metric("kill_switch_engaged")
    await repo.insert_audit(
        actor="operator",
        action="kill_switch.engage",
        target=scope,
        detail={"canceled": canceled, "buckets": list(buckets)},
    )
    await _publish_halt(service)
    return {"level": 1, "canceled": canceled, "buckets": buckets}


async def engage_level2(service: OrderService, *, scope: str, run_seq: int = 0) -> dict:
    """레벨2 — L1(halt+일괄취소) 후 대상 버킷 포지션 reduce-only **flatten**(2단계 확인은 API).

    `_target_buckets(scope)` 의 각 버킷을 flatten 한다(§9 진화). LIVE 는 allow_live=false 면
    `flatten_all_positions` 가 안전 no-op(실포지션 0)을 반환하므로 무해하다. `flatten` 은
    버킷별 결과 맵(`{bucket: {fired,pending,skipped}}`).
    """
    l1 = await engage(service, scope=scope)
    flats: dict[str, dict] = {}
    for b in _target_buckets(scope):
        flats[b] = await flatten_all_positions(service, bucket=b, run_seq=run_seq)
    return {**l1, "level": 2, "flatten": flats}


async def release(service: OrderService, *, scope: str) -> None:
    """킬스위치 해제(active 복귀). HALTED_DAILY 는 일일경계 자동, KILLED 는 이 수동 해제."""
    repo = service._repo
    await halt.release(repo, scope)
    # 해제는 거래 재개라는 중대한 상태변화 — 이전엔 무흔적이었다(§12 누락 보강). risk_event +
    # audit_log 로 발동/해제 대칭 트레일을 남긴다.
    await repo.insert_risk_event(
        event_type="kill_switch_release", severity="warning", scope=scope, message="released"
    )
    await repo.insert_audit(actor="operator", action="kill_switch.release", target=scope)
    await _publish_halt(service)


async def _publish_halt(service: OrderService) -> None:
    """버킷별 유효 halt 상태를 WS(risk.halt_state)로 push — engage/release 상태변화 반영."""
    bus = getattr(service, "_bus", None)
    if bus is not None:
        await bus.publish("risk.halt_state", await halt.states_snapshot(service._repo))


async def level1(service: OrderService, *, bucket: str) -> dict:
    """버킷 단위 L1 미체결 일괄취소 + 격리 raw-cancel(§7.2/§9).

    **LIVE 버킷 allow_live 분기(M4d §9)**:
    - allow_live=false → 주문 경로 부재 → **명시적 no-op-with-warning**(실주문 0이라 취소할
      것도 없음; cancel 루프에 LIVE open order 진입 0).
    - allow_live=true  → LIVE 미체결도 **실제 취소**(위험감축 lane — 끄려면 켜져 있어야 하고,
      취소는 멱등·노출감소라 allow_live 와 무관하게 허용되어야 안전).
    """
    repo = service._repo
    if bucket == BUCKET_LIVE and not service._allow_live():
        # allow_live=false → LIVE 구조적 no-op(§0.2-2/§9). 방어는 침묵하지 않는다(§0.2-4).
        await repo.insert_risk_event(
            event_type="kill_switch_live_noop",
            severity="warning",
            scope=BUCKET_LIVE,
            message="LIVE order path closed (allow_live=false) — kill-switch L1 is a no-op",
        )
        return {"bucket": BUCKET_LIVE, "no_op": True, "canceled": 0}

    # paper(항상) / LIVE(allow_live=true) — 미체결 일괄취소(위험감축 lane) + 격리 raw-cancel.
    cancel_res = await service.cancel_all_open(reason="kill_switch", bucket=bucket)
    q_canceled, q_excluded = await _raw_cancel_quarantined(service, markets_of(bucket))
    return {
        "bucket": bucket,
        "canceled": cancel_res.get("canceled", 0),
        "quarantine_canceled": q_canceled,
        # OrdNo 없음/unknown → 진짜 수동. 운영자가 브로커에서 직접 조치(§7.2 노출 약속).
        "quarantine_excluded": q_excluded,
    }


async def _raw_cancel_quarantined(
    service: OrderService, markets: tuple[str, ...]
) -> tuple[int, list[int]]:
    """격리(quarantined) 주문 노출 분리(§7.2): OrdNo 보유 → raw cancel-by-OrdNo(위험감축·멱등,
    상태는 quarantined 유지 → 운영 수동 resolve). OrdNo 없음 → excluded 로 명시 보고."""
    repo = service._repo
    quarantined = await repo.list_by_status(OrderState.QUARANTINED, markets)
    canceled = 0
    excluded: list[int] = []
    for o in quarantined:
        if not o.get("broker_order_id"):
            excluded.append(o["id"])  # 수동 — 응답에 명시
            continue
        try:
            # risk_reduction → ACTIVE 우회. quarantined 는 터미널이라 취소 전송 후에도 상태
            # 유지(cancel() 의 _NON_TERMINAL_FOR_CANCEL 가드) — 운영 수동 resolve 까지 격리.
            res = await service.cancel(o["id"], risk_reduction=True)
        except OrderError as exc:
            if exc.code == "LIVE_DISABLED":
                raise  # §0.2-4 미삼킴
            excluded.append(o["id"])
            continue
        canceled += 1 if res.get("ok") else 0
    return canceled, excluded


def _target_buckets(scope: str) -> tuple[str, ...]:
    """halt scope → L1 대상 버킷. global 은 paper(실취소) + live(no-op 가드 명시)."""
    if scope == halt.GLOBAL_SCOPE:
        return (BUCKET_PAPER, BUCKET_LIVE)
    b = bucket_of(scope)  # scope = 시장 키(normalize_scope 산출)
    return (b,) if b else (BUCKET_PAPER,)


# ── 레벨2: 전 포지션 청산 (paper-only, fake-테스트만 — §0.3/§9/[검증 3]) ──────
async def flatten_all_positions(
    service: OrderService,
    *,
    bucket: str = BUCKET_PAPER,
    run_seq: int = 0,
    market_closed: bool = False,
) -> dict:
    """보유 포지션 전부 reduce-only EXIT 로 청산(guarded order_service). **버킷별**(§9 진화).

    `positions_for(bucket)` **만** 순회(전수 순회 금지, Lens2-C1). LIVE 는 allow_live=false 면
    청산 경로가 닫혀 있으므로 **안전 no-op**(청산할 실포지션 0). allow_live=true 면 LIVE 포지션도
    reduce-only EXIT 로 청산한다 — reduce-only 는 게이트 effective_exit 로 캡/notional 우회
    통과(§5.5)라 킬스위치가 자기 가드에 막히지 않는다. LIVE 청산은 LIMIT(avg/현재가 참조)로
    명목을 정의해 발사하며, 시장가+슬리피지 fallback 은 [L]/Gate B 후속(§9 a~d).
    장마감(`market_closed`)이면 청산 불가 → pending 큐(symbol) + critical, 개장 시
    `resume_pending_flatten` 가 **현재 스냅에서 재계산**(verbatim 재발사 금지, Lens2-M1).
    """
    if bucket == BUCKET_LIVE and not service._allow_live():
        # LIVE 닫힘 → 청산할 실포지션 0. 어댑터 미진입 안전 no-op(assert 대신 게이트 진화 §17 L3-7).
        return {"fired": [], "pending": [], "skipped": []}
    repo = service._repo
    fired, pending, skipped = [], [], []
    for p in await repo.positions_for(bucket):
        qty = _held_qty(p)
        side = _exit_side(p.get("position_side") or p.get("side"))
        if qty <= 0 or side is None:
            skipped.append(p.get("symbol"))
            continue
        if market_closed:
            pending.append(p.get("symbol"))
            await repo.insert_risk_event(
                event_type="pending_flatten",
                severity="critical",
                scope=p.get("market"),
                scope_ref=p.get("symbol"),
                message="market closed — flatten queued for reopen",
                detail={"qty": qty, "side": side.value},
            )
            continue
        fired.append(await _flatten_one(service, bucket=bucket, p=p, run_seq=run_seq))
    return {"fired": fired, "pending": pending, "skipped": skipped}


async def resume_pending_flatten(
    service: OrderService,
    symbols,
    *,
    bucket: str = BUCKET_PAPER,
    run_seq: int = 1,
) -> dict:
    """개장 시 pending_flatten 재개 — **현재 포지션 스냅에서 side/qty 재계산**(verbatim 금지).

    이미 flat 인 symbol 은 드롭, reduce-only 클램프(현재 보유 qty). run_seq 는 멱등키를
    이전 청산 시도와 분리해 재발사를 허용한다(`flat:{bucket}:{symbol}:{seq}`).
    """
    if bucket == BUCKET_LIVE and not service._allow_live():
        return {"fired": [], "dropped": list(symbols)}
    repo = service._repo
    positions = {p.get("symbol"): p for p in await repo.positions_for(bucket)}
    fired, dropped = [], []
    for sym in symbols:
        p = positions.get(sym)
        if p is None or _held_qty(p) <= 0:
            dropped.append(sym)  # 이미 flat → 드롭(verbatim 재발사 금지)
            continue
        fired.append(await _flatten_one(service, bucket=bucket, p=p, run_seq=run_seq))
    return {"fired": fired, "dropped": dropped}


async def _flatten_one(
    service: OrderService, *, bucket: str, p: dict, run_seq: int
) -> dict:
    """단일 포지션 reduce-only 청산 주문(guarded place). 게이트가 §5.5 reduce-only 재검증."""
    qty = _held_qty(p)
    symbol = p.get("symbol")
    side = _exit_side(p.get("position_side") or p.get("side"))
    # 청산 주문유형(open_question §13.8): FUT 시장가 모의 수용 미확정 → 지정가(avg_price 참조)
    # fallback. 라이브 슬리피지/시장가는 M4. 멱등키 flat:{bucket}:{symbol}:{seq}.
    intent = OrderIntent(
        market=p.get("market") or MARKET_OVERSEAS_FUTUREOPTION,
        symbol=symbol,
        side=side,
        intent=IntentKind.EXIT,
        order_type=OrderType.LIMIT,
        qty=qty,
        price=p.get("avg_price"),
        currency=p.get("currency"),
        client_order_id=f"flat:{bucket}:{symbol}:{run_seq}",
        reason="killswitch_flatten",
    )
    res = await service.place(intent)
    return {
        "symbol": symbol,
        "qty": qty,
        "side": side.value if side else None,
        "ok": bool(res.get("ok")),
    }


def _held_qty(p: dict) -> int:
    return int(abs(float(p.get("qty") or 0)))


def _exit_side(position_side: str | None) -> Side | None:
    """보유 방향 → 청산(반대) 방향. 미상이면 None(스킵)."""
    if position_side == "long":
        return Side.SELL
    if position_side == "short":
        return Side.BUY
    return None
