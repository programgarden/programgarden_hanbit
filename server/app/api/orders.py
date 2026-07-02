"""주문 라우터 (M2) — 해외선물 paper 한정. KR/OVS 는 403 LIVE_DISABLED.

quote(견적) → commit(발사) / amend / cancel / reconcile / open / history.
모든 경로는 order_service 단일 진입점(registry + 리스크 게이트 + 락) 을 거친다.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.adapters.order_base import OrderError
from app.api.deps import get_order_service, get_repo
from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION
from app.models.order_dto import IntentKind, OrderIntent, OrderType, Side
from app.models.schemas import failure, success
from app.repositories.orders_repo import OrdersRepo
from app.services.order_service import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])

_ERR_STATUS = {
    "LIVE_DISABLED": 403,
    "ENGINE_NOT_ACTIVE": 409,   # 런타임 EngineState != ACTIVE (M3b)
    "KILL_SWITCH": 409,
    "HALTED_DAILY": 409,
    "NOT_FOUND": 404,
    "NO_BROKER_ORDNO": 409,
    "AMEND_REJECTED": 422,      # 정정 노출 재검증 실패(per_order_cap/INV-7, M3b §7.1)
}


class CommitBody(BaseModel):
    market: str = MARKET_OVERSEAS_FUTUREOPTION
    symbol: str
    side: Side
    order_type: OrderType = OrderType.LIMIT
    qty: int = Field(gt=0)
    price: float | None = None
    exchange: str = "HKEX"
    due_yymm: str | None = None
    intent: IntentKind = IntentKind.ENTRY
    client_order_id: str | None = None


class AmendBody(BaseModel):
    qty: int = Field(gt=0)
    price: float


def _to_intent(b: CommitBody) -> OrderIntent:
    return OrderIntent(
        market=b.market,
        symbol=b.symbol,
        side=b.side,
        intent=b.intent,
        order_type=b.order_type,
        qty=b.qty,
        price=b.price,
        exchange=b.exchange,
        due_yymm=b.due_yymm,
        client_order_id=b.client_order_id,
    )


def _live_disabled(market: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content=failure("LIVE_DISABLED", f"order path for '{market}' is disabled until M4"),
    )


def _order_error(exc: OrderError) -> JSONResponse:
    return JSONResponse(
        status_code=_ERR_STATUS.get(exc.code, 400),
        content=failure(exc.code, exc.message),
    )


RepoDep = Annotated[OrdersRepo, Depends(get_repo)]
SvcDep = Annotated[OrderService, Depends(get_order_service)]


@router.get("/open")
async def list_open(repo: RepoDep) -> dict[str, Any]:
    return success({"orders": await repo.list_open_orders()})


@router.get("/history")
async def history(repo: RepoDep, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    return success({"orders": await repo.list_orders(limit=limit, offset=offset)})


@router.get("/whitelist")
async def whitelist(
    repo: RepoDep, market: str = MARKET_OVERSEAS_FUTUREOPTION
) -> dict[str, Any]:
    """주문 가능한 화이트리스트 심볼 목록(현재 FUT=HKEX 한정).

    리스크 게이트가 ``FUT_NOT_HKEX`` 로 강제하는 화이트리스트를 UI 가 **사전 검증**할 수
    있도록 노출한다. 새 주문 폼은 이 목록으로 심볼을 제한해, 잘못된 심볼이 서버 거부로 한
    번 왕복한 뒤에야 드러나는 것을 막는다. 계약 승수(multiplier)는 ``meta_json`` 에 들어
    있어 파싱해 함께 내려준다(명목 추정에 사용).
    """
    rows = await repo.list_whitelist(market)
    symbols = []
    for r in rows:
        meta: dict[str, Any] = {}
        if r.get("meta_json"):
            try:
                meta = json.loads(r["meta_json"])
            except (ValueError, TypeError):
                meta = {}
        symbols.append(
            {
                "symbol": r["symbol"],
                "name": r.get("name"),
                "exchange": r.get("exchange"),
                "multiplier": meta.get("multiplier"),
            }
        )
    return success({"market": market, "symbols": symbols})


@router.post("/quote")
async def quote(body: CommitBody, svc: SvcDep):
    if body.market != MARKET_OVERSEAS_FUTUREOPTION:
        return _live_disabled(body.market)
    decision = await svc.preview(_to_intent(body))
    return success(
        {
            "decision": decision.model_dump(mode="json"),
            "confirm_token": uuid.uuid4().hex if decision.ok else None,
        }
    )


@router.post("/commit")
async def commit(body: CommitBody, svc: SvcDep):
    if body.market != MARKET_OVERSEAS_FUTUREOPTION:
        return _live_disabled(body.market)
    try:
        result = await svc.place(_to_intent(body))
    except OrderError as exc:
        return _order_error(exc)
    return success(result) if result.get("ok") else JSONResponse(
        status_code=422, content=failure("ORDER_NOT_ACCEPTED", "order rejected", result)
    )


@router.post("/{order_id}/amend")
async def amend(order_id: int, body: AmendBody, svc: SvcDep):
    try:
        return success(await svc.amend(order_id, qty=body.qty, price=body.price))
    except OrderError as exc:
        return _order_error(exc)


@router.post("/{order_id}/cancel")
async def cancel(order_id: int, svc: SvcDep):
    try:
        return success(await svc.cancel(order_id))
    except OrderError as exc:
        return _order_error(exc)


@router.post("/reconcile")
async def reconcile(svc: SvcDep, market_closed: bool = False):
    return success(await svc.reconcile(scope="manual", market_closed=market_closed))
