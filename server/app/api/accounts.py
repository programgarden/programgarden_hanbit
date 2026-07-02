"""계좌 라우터 (M3b §11) — 시장별 잔고 스냅샷(통화별) 실데이터.

집계기/reconcile 이 채운 `balances_snapshot`(account_id, currency)을 읽기만 한다 — API 가
직접 계좌 TR 을 호출하지 않는다(§11, 호출건수 보호).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import get_repo
from app.models.schemas import success
from app.repositories.orders_repo import OrdersRepo

router = APIRouter(prefix="/accounts", tags=["accounts"])

RepoDep = Annotated[OrdersRepo, Depends(get_repo)]


@router.get("")
async def list_accounts(repo: RepoDep) -> dict[str, Any]:
    """계좌 목록 + 시장별 통화별 잔고 스냅샷."""
    accounts = []
    for acct in await repo.list_accounts():
        accounts.append({**acct, "balances": await repo.list_balances(acct["id"])})
    return success({"accounts": accounts})
