"""포트폴리오 라우터 (M3b §11) — 버킷 KPI/집중도/포지션 실데이터.

집계기(`app/portfolio/aggregator.py`)·reconcile 이 채운 DB 행(`bucket_kpi`/`positions`)을
**읽기만** 한다 — API 는 직접 계좌 TR 을 호출하지 않는다(§11, 호출건수 보호). 버킷 합산은
표시용 참고 라인에서만(§3 — 위험/집계 경로는 버킷 격리).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.deps import get_repo
from app.core.mode_matrix import BUCKET_LIVE, BUCKET_PAPER
from app.models.schemas import failure, success
from app.repositories.orders_repo import OrdersRepo

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

RepoDep = Annotated[OrdersRepo, Depends(get_repo)]

_BUCKETS = (BUCKET_LIVE, BUCKET_PAPER)
# 표시용 참고 합산 대상(KRW 환산값만 — 버킷 간 합은 §3 상 표시 라인 전용).
_SUM_FIELDS = ("total_eval_krw", "total_buy_krw", "total_pnl_krw", "position_count")


@router.get("")
async def portfolio_root(repo: RepoDep) -> dict[str, Any]:
    """버킷별 최신 KPI(집중도 currency_hhi 포함) + 참고 합산."""
    buckets = {b: await repo.get_latest_bucket_kpi(b) for b in _BUCKETS}
    totals = {
        f: sum(float(k[f] or 0) for k in buckets.values() if k and k.get(f) is not None)
        for f in _SUM_FIELDS
    }
    # 합산은 표시 참고일 뿐(KRW 환산) — 위험/한도는 버킷 격리값을 쓴다.
    return success({"buckets": buckets, "totals": totals, "totals_note": "display-only sum (§3)"})


@router.get("/positions")
async def positions(repo: RepoDep, bucket: str = BUCKET_PAPER) -> Any:
    """버킷-스코프 포지션(태깅: bucket/market/currency/eval_krw/fx_now/fx_at_buy)."""
    if bucket not in _BUCKETS:
        return JSONResponse(
            status_code=422,
            content=failure("BAD_BUCKET", f"bucket must be one of {_BUCKETS}"),
        )
    rows = await repo.positions_for(bucket)
    return success({"bucket": bucket, "positions": rows})
