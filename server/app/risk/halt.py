"""킬스위치 / halt 상태 (M2) — trading_halt 테이블 영속.

scope: 'global'(전역) / 'overseas_futureoption'(버킷). state: active / halted / killed.
신규주문은 global 또는 해당 버킷이 active 가 아니면 차단(청산/EXIT 는 게이트에서 예외).
"""

from __future__ import annotations

from app.core.mode_matrix import (
    BUCKET_LIVE,
    BUCKET_PAPER,
    MARKET_OVERSEAS_FUTUREOPTION,
    markets_of,
)
from app.repositories.orders_repo import OrdersRepo

GLOBAL_SCOPE = "global"
_BLOCKING = {"halted", "killed"}

# 버킷별 halt 상태(§11 /risk/halt_state · WS risk.halt_state).
BUCKETS = (BUCKET_LIVE, BUCKET_PAPER)


async def is_blocked(repo: OrdersRepo, market: str) -> bool:
    """전역 또는 해당 시장 버킷이 차단(halted/killed) 상태인가."""
    if (await repo.get_halt_state(GLOBAL_SCOPE)) in _BLOCKING:
        return True
    return (await repo.get_halt_state(market)) in _BLOCKING


async def engage(repo: OrdersRepo, scope: str, *, reason: str | None = None) -> None:
    """킬스위치 발동(killed). scope='global' 또는 시장 키."""
    await repo.set_halt_state(scope, "killed", reason)


async def release(repo: OrdersRepo, scope: str) -> None:
    """킬스위치 해제(active 복귀)."""
    await repo.set_halt_state(scope, "active", None)


def normalize_scope(scope: str) -> str:
    """API scope 입력 정규화. 'market'/'overseas_futureoption' → 버킷, 그 외 global."""
    if scope in (MARKET_OVERSEAS_FUTUREOPTION, "market", "bucket"):
        return MARKET_OVERSEAS_FUTUREOPTION
    return GLOBAL_SCOPE


async def bucket_state(repo: OrdersRepo, bucket: str) -> dict:
    """버킷의 유효 halt 상태(§11). 킬스위치(trading_halt: global+버킷 시장)가 최상위로
    'killed' 을 강제하고, 아니면 일일손실 상태(risk_state.halt_state: active|halted_daily)."""
    rs = await repo.get_risk_state(bucket) or {}
    daily_state = rs.get("halt_state") or "active"
    killed = (await repo.get_halt_state(GLOBAL_SCOPE)) in _BLOCKING
    if not killed:
        for m in markets_of(bucket):
            if (await repo.get_halt_state(m)) in _BLOCKING:
                killed = True
                break
    return {
        "bucket": bucket,
        "state": "killed" if killed else daily_state,  # active|halted_daily|killed
        "kill_switch": "killed" if killed else "active",
        "daily_loss_state": daily_state,
    }


async def states_snapshot(repo: OrdersRepo) -> dict:
    """버킷별 유효 halt 상태 스냅(WS risk.halt_state push 페이로드)."""
    return {b: await bucket_state(repo, b) for b in BUCKETS}
