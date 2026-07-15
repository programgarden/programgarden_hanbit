"""전략 라우터 (M5) — 자동매매 전략 엔진 조회/제어.

- GET  /strategy         : 전략 목록 + 엔진 마스터 토글 상태.
- POST /strategy/toggle  : 마스터 토글 on/off(런타임). 켜야만 발주.
- POST /strategy/run     : 지금 1회 평가·발주(run_once) 수동 트리거 — 결과 반환.

발주는 전부 order_service.place(리스크 게이트) 경유 — LIVE 는 allow_live 로 잠김(실주문 0).
자동 주기 실행은 lifespan 백그라운드 루프(config HANBIT_STRATEGY_INTERVAL_S)가 담당한다.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_strategy_engine
from app.models.schemas import success
from app.strategies.engine import StrategyEngine

router = APIRouter(prefix="/strategy", tags=["strategy"])

EngineDep = Annotated[StrategyEngine, Depends(get_strategy_engine)]


class ToggleBody(BaseModel):
    enabled: bool


@router.get("")
async def list_strategies(engine: EngineDep) -> dict[str, Any]:
    """전략 목록 + 엔진 상태(마스터 토글)."""
    return success({"enabled": engine.enabled, "strategies": engine.list_strategies()})


@router.post("/toggle")
async def toggle(body: ToggleBody, engine: EngineDep) -> dict[str, Any]:
    """마스터 토글 on/off(런타임). off 면 run_once/자동루프가 발주하지 않는다."""
    engine.set_enabled(body.enabled)
    return success({"enabled": engine.enabled})


@router.post("/run")
async def run_now(engine: EngineDep) -> dict[str, Any]:
    """지금 1회 전략 평가·발주(수동 트리거). 토글 off 면 발주 0."""
    return success(await engine.run_once())
