"""위험관리 라우터 (M2 + M3b §9/§11) — 한도/이벤트/킬스위치(L1·L2)/버킷 halt 상태.

킬스위치 발동/취소 오케스트레이션은 `app/risk/killswitch.py`(버킷 분기·격리 raw-cancel·
LIVE_DISABLED 미삼킴)에 위임한다. L2(flatten)는 **2단계 확인**(confirm_token) — 토큰 없이
요청하면 미실행 + 토큰 발급, 토큰을 실어 재요청해야 청산이 실행된다(§11).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.deps import get_order_service, get_repo
from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION, markets_of
from app.models.schemas import failure, success
from app.repositories.orders_repo import OrdersRepo
from app.risk import halt, killswitch
from app.risk.limits import RiskLimits
from app.services.order_service import OrderService

router = APIRouter(prefix="/risk", tags=["risk"])

RepoDep = Annotated[OrdersRepo, Depends(get_repo)]
SvcDep = Annotated[OrderService, Depends(get_order_service)]


class KillSwitchBody(BaseModel):
    scope: str = "global"  # global / overseas_futureoption / market
    action: str  # engage / release
    level: int = 1  # 1 = 일괄취소(L1), 2 = 취소+포지션 flatten(L2, paper 전용·2단계 확인)
    confirm_token: str | None = None  # L2 2단계 확인 — 없으면 미실행 + 토큰 발급


@router.get("/limits")
async def risk_limits(repo: RepoDep) -> dict[str, Any]:
    return success(
        {
            "limits": await repo.get_risk_limits(MARKET_OVERSEAS_FUTUREOPTION),
            "halt": {
                "global": await repo.get_halt_state("global"),
                "overseas_futureoption": await repo.get_halt_state(MARKET_OVERSEAS_FUTUREOPTION),
            },
        }
    )


@router.get("/events")
async def risk_events(repo: RepoDep, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    return success({"events": await repo.list_risk_events(limit=limit, offset=offset)})


@router.get("/halt_state")
async def halt_state(repo: RepoDep) -> dict[str, Any]:
    """버킷별 유효 halt 상태(active|halted_daily|killed) + 일일손실 진행."""
    buckets: dict[str, Any] = {}
    for b in halt.BUCKETS:
        state = await halt.bucket_state(repo, b)
        rs = await repo.get_risk_state(b) or {}
        kpi = await repo.get_latest_bucket_kpi(b) or {}
        scope_ref = (markets_of(b) or (b,))[0]
        limits = await RiskLimits.load(repo, scope_ref)
        base_r = rs.get("day_start_realized_krw")
        base_u = rs.get("day_start_unrealized_krw")
        now_r = kpi.get("daily_realized_krw")
        now_u = kpi.get("total_pnl_krw")
        buckets[b] = {
            **state,
            "daily_loss": {
                "day_start_realized_krw": base_r,
                "day_start_unrealized_krw": base_u,
                "day_start_equity_krw": rs.get("day_start_equity_krw"),
                "daily_notional_used_krw": rs.get("daily_notional_used_krw"),
                "last_reset_day": rs.get("last_reset_day"),
                "now_realized_krw": now_r,
                "now_unrealized_krw": now_u,
                "realized_loss_krw": _loss(base_r, now_r),
                "eval_loss_krw": _eval_loss(base_r, base_u, now_r, now_u),
                "max_daily_loss_realized": limits.max_daily_loss_realized,
                "max_daily_loss_eval": limits.max_daily_loss_eval,
            },
        }
    return success({"buckets": buckets})


@router.post("/killswitch")
async def killswitch_endpoint(body: KillSwitchBody, svc: SvcDep):
    scope = halt.normalize_scope(body.scope)
    if body.action == "release":
        await killswitch.release(svc, scope=scope)
        return success({"scope": scope, "state": "active"})
    if body.action != "engage":
        return JSONResponse(
            status_code=422, content=failure("BAD_ACTION", "action must be engage/release")
        )
    if body.level == 2:
        if not body.confirm_token:
            # 2단계 확인: 미실행 + 토큰 발급 → 같은 토큰을 실어 재요청해야 청산 실행(§11).
            return success(
                {
                    "scope": scope,
                    "level": 2,
                    "requires_confirm": True,
                    "confirm_token": uuid.uuid4().hex,
                    "warning": "level 2 flattens all paper positions — resend with confirm_token",
                }
            )
        result = await killswitch.engage_level2(svc, scope=scope)
        return success({"scope": scope, "state": "killed", **result})
    # 기본 — 레벨1(일괄취소 + 격리 raw-cancel).
    result = await killswitch.engage(svc, scope=scope)
    return success({"scope": scope, "state": "killed", **result})


def _loss(base: float | None, now: float | None) -> float | None:
    """일중 손실(양수) = max(0, baseline − 현재). 둘 중 하나라도 미확정이면 None."""
    if base is None or now is None:
        return None
    return max(0.0, float(base) - float(now))


def _eval_loss(
    base_r: float | None, base_u: float | None, now_r: float | None, now_u: float | None
) -> float | None:
    """평가손실(실현+미실현, §5.3 산식). 입력 미확정이면 None."""
    if None in (base_r, base_u, now_r, now_u):
        return None
    return max(0.0, (float(base_r) - float(now_r)) + (float(base_u) - float(now_u)))
