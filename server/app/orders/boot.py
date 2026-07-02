"""부트 스테이트머신 (M3b §7) — READ_ONLY → RECONCILING → ACTIVE / quarantine.

기동 시 비터미널 주문을 **전수 분류**(검증 Lens3-C1, 4상태 망라)하고 boot reconcile 로
미확정을 해소한다. 해소 불가(OrdNo 없는 in_doubt/submitted)는 `quarantined` 상태로 격리하고
critical risk_event 를 남긴다. ACTIVE 진입 조건(§7.1):

    config 의도 == PAPER_TRADING  AND  포지션/잔고 동기화 성공

quarantine 이 있어도 엔진은 ACTIVE 로 둔다 — 감축 EXIT/취소가 위험감축으로 필요하기 때문.
대신 게이트가 그 버킷의 **신규 ENTRY 만 차단**한다(§7.1 has_quarantined, "full ACTIVE 금지"의
구현). 포지션 동기화가 실패하면 책을 신뢰할 수 없으므로 READ_ONLY 를 유지한다.

런타임 권위는 `EngineState`(app/core/engine_state.py) — 게이트 step0 와 _get_mutable 이 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.engine_state import EngineState
from app.core.mode_matrix import BUCKET_PAPER, markets_of
from app.models.order_dto import OrderState
from app.orders.state_machine import StateMachineError

# 부트 분류 4상태(§7.1): 브로커 미확정(in_flight) vs 브로커 확정·작동중(working).
_IN_FLIGHT = {OrderState.SUBMITTED.value, OrderState.IN_DOUBT.value}
_WORKING = {OrderState.ACCEPTED.value, OrderState.PARTIALLY_FILLED.value}


@dataclass
class BootReport:
    """부트 결과 — 운영/테스트 가시화."""

    engine_state: str
    config_intent_active: bool
    position_sync_ok: bool
    classified: dict[str, list[int]]
    reconcile: dict
    quarantined: list[int] = field(default_factory=list)
    entry_blocked: bool = False  # quarantine 존재 → 신규 ENTRY 차단(엔진은 ACTIVE 가능)


def _classify(orders: list[dict]) -> dict[str, list[int]]:
    """비터미널 주문을 in_flight / working / other 로 전수 분류(§7.1)."""
    out: dict[str, list[int]] = {"in_flight": [], "working": [], "other": []}
    for o in orders:
        st = o.get("status")
        if st in _IN_FLIGHT:
            out["in_flight"].append(o["id"])
        elif st in _WORKING:
            out["working"].append(o["id"])
        else:
            out["other"].append(o["id"])
    return out


def _is_unresolved(o: dict) -> bool:
    """boot reconcile 후에도 매칭 불가한 미확정 주문 — OrdNo 없는 in_doubt/submitted.

    (OrdNo 보유분은 reconcile 이 OrdNo 로 매칭해 해소; orphan 은 reconcile 이 등록한다.)
    """
    return o.get("status") in _IN_FLIGHT and not o.get("broker_order_id")


async def boot_engine(service, *, market_closed: bool = False) -> BootReport:
    """부트 시퀀스 구동 — service 의 런타임 EngineState 를 전이시킨다.

    service: OrderService (repo/engine/reconcile/settings 를 제공). 순환 import 회피 위해
             타입 주석 없이 덕타이핑으로 받는다.
    """
    repo = service._repo
    engine: EngineState = service._engine
    intent_active = bool(getattr(service._settings, "engine_trading_enabled", False))
    markets = markets_of(BUCKET_PAPER)

    # [READ_ONLY] 주문 전면 금지 + 비터미널 주문 전수 분류.
    engine.set(EngineState.READ_ONLY)
    pre = await repo.open_orders_for(markets)
    classification = _classify(pre)

    # [RECONCILING] boot reconcile — in_doubt 4분기 + accepted/partial 해소 + 포지션 동기화.
    engine.set(EngineState.RECONCILING)
    recon = await service.reconcile(scope="boot", market_closed=market_closed)
    position_sync_ok = bool(recon.get("position_sync_ok", True))

    # 미해소(OrdNo 없는 in_doubt/submitted) → quarantined(상태) + critical risk_event.
    remaining = await repo.open_orders_for(markets)
    quarantined: list[int] = []
    for o in remaining:
        if not _is_unresolved(o):
            continue
        try:
            await repo.transition(o["id"], OrderState.QUARANTINED, "boot")
        except StateMachineError:  # pragma: no cover - 경쟁/이미 전이됨
            continue
        await repo.insert_risk_event(
            event_type="quarantined",
            severity="critical",
            scope=o["market"],
            scope_ref=str(o.get("broker_order_id") or o["id"]),
            message="boot reconcile could not resolve in-flight order",
            detail={"order_id": o["id"], "status_before": o.get("status")},
        )
        await repo.incr_metric("quarantined")  # §12: 격리 건수 /system/metrics 노출
        quarantined.append(o["id"])

    entry_blocked = await repo.has_quarantined(markets)

    # [ACTIVE] 조건: config 의도 PAPER_TRADING + 포지션 동기화 성공. quarantine 은 ENTRY 만 막고
    #   엔진은 ACTIVE(감축 EXIT/취소 허용). 동기화 실패면 책 불신 → READ_ONLY 유지.
    if intent_active and position_sync_ok:
        engine.set(EngineState.ACTIVE)
    else:
        engine.set(EngineState.READ_ONLY)

    return BootReport(
        engine_state=engine.state,
        config_intent_active=intent_active,
        position_sync_ok=position_sync_ok,
        classified=classification,
        reconcile=recon,
        quarantined=quarantined,
        entry_blocked=entry_blocked,
    )
