"""전략 라우터 — M0 스텁."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.models.schemas import stub

router = APIRouter(prefix="/strategy", tags=["strategy"])


@router.get("")
async def list_strategies() -> dict[str, Any]:
    """전략 목록 — M0 스텁."""
    return stub()


@router.get("/allocations")
async def allocations() -> dict[str, Any]:
    """전략 배분 — M0 스텁."""
    return stub()
