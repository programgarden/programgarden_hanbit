"""시스템 엔드포인트 — health / modes / clock (전부 동작)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.config import get_settings
from app.core.mode_matrix import (
    BUCKET_LIVE,
    BUCKET_PAPER,
    get_mode_matrix,
    markets_of,
)
from app.models.order_dto import OrderState
from app.models.schemas import success, utc_now_iso

# 루트 라우터 (/healthz)
root_router = APIRouter(tags=["system"])

# /api/v1/system/* 라우터
router = APIRouter(prefix="/system", tags=["system"])


@root_router.get("/healthz")
async def healthz() -> dict[str, Any]:
    """라이브니스 프로브 — 항상 200."""
    return success({"status": "ok"})


@router.get("/health")
async def system_health(request: Request) -> dict[str, Any]:
    """서버 상태 + 모드/allow_live + 시장별 세션 상태 + 런타임 엔진 상태(M3b §11).

    `engine_state` 는 **런타임 부트머신값**(READ_ONLY/RECONCILING/ACTIVE) — config 의도가
    아니라 `OrderService.engine` 의 현재 권위값을 노출한다. `mode` 는 LIVE 기준 READ_ONLY
    유지(M2 호환). KR/OVS 주문 경로는 M4 까지 닫혀 있다.
    """
    settings = get_settings()
    sessions = getattr(request.app.state, "sessions", None)
    sessions_status = (
        sessions.status()
        if sessions is not None
        else {
            "korea_stock": None,
            "overseas_stock": None,
            "overseas_futureoption": None,
        }
    )
    order_service = getattr(request.app.state, "order_service", None)
    # engine_state = paper 버킷 값(하위호환, §17 L3-9). 버킷별은 engine_states 맵으로 신규 노출.
    engine_state = (
        order_service.engine.state
        if order_service is not None
        else settings.hanbit_engine_state
    )
    engine_states = (
        {b: order_service.engine_for(b).state for b in (BUCKET_PAPER, BUCKET_LIVE)}
        if order_service is not None
        else {BUCKET_PAPER: settings.hanbit_engine_state, BUCKET_LIVE: "READ_ONLY"}
    )
    return success(
        {
            "status": "ok",
            "mode": "READ_ONLY",
            "engine_state": engine_state,
            "engine_states": engine_states,
            "allow_live": settings.hanbit_allow_live,
            "realtime_fills": settings.realtime_fills_enabled,
            "sessions": sessions_status,
            "milestone": "M3",
        }
    )


@router.get("/quarantine")
async def system_quarantine(request: Request) -> dict[str, Any]:
    """격리(quarantined) 주문 목록(§7 노출 약속). 운영자가 브로커에서 직접 조치할 대상."""
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        return success({"orders": [], "count": 0})
    markets = markets_of(BUCKET_LIVE) + markets_of(BUCKET_PAPER)
    orders = await repo.list_by_status(OrderState.QUARANTINED, markets)
    return success({"orders": orders, "count": len(orders)})


@router.get("/metrics")
async def system_metrics(request: Request) -> dict[str, Any]:
    """M2 최소 메트릭(주문 placed/rejected/filled, reconcile diffs)."""
    repo = getattr(request.app.state, "repo", None)
    metrics = await repo.get_metrics() if repo is not None else {}
    return success({"metrics": metrics})


@router.get("/modes")
async def system_modes() -> dict[str, Any]:
    """INV-1 거래모드 매트릭스 반환."""
    return success({"markets": get_mode_matrix()})


@router.get("/clock")
async def system_clock() -> dict[str, Any]:
    """서버 시각 + (M0 placeholder) 시장 세션 상태 스텁."""
    return success(
        {
            "server_time": utc_now_iso(),
            "market_sessions": {
                "korea_stock": {"state": "unknown", "note": "stub (M0)"},
                "overseas_stock": {"state": "unknown", "note": "stub (M0)"},
                "overseas_futureoption": {"state": "unknown", "note": "stub (M0)"},
            },
        }
    )
