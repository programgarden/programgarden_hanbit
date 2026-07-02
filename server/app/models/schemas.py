"""공통 응답 envelope 모델.

성공: {"ok": true, "data": {...}, "server_time": "<UTC ISO8601 Z>"}
실패: {"ok": false, "error": {"code","message","detail"}}
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    """현재 UTC 시각을 ISO8601 'Z' 포맷 문자열로 반환한다."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class SuccessEnvelope[T](BaseModel):
    """성공 응답 봉투."""

    ok: bool = True
    data: T | None = None
    server_time: str = Field(default_factory=utc_now_iso)


class ErrorDetail(BaseModel):
    """에러 본문."""

    code: str
    message: str
    detail: Any | None = None


class ErrorEnvelope(BaseModel):
    """실패 응답 봉투."""

    ok: bool = False
    error: ErrorDetail
    server_time: str = Field(default_factory=utc_now_iso)


def success(data: Any | None = None) -> dict[str, Any]:
    """성공 envelope dict 를 생성한다."""
    return {"ok": True, "data": data, "server_time": utc_now_iso()}


def failure(code: str, message: str, detail: Any | None = None) -> dict[str, Any]:
    """실패 envelope dict 를 생성한다."""
    return {
        "ok": False,
        "error": {"code": code, "message": message, "detail": detail},
        "server_time": utc_now_iso(),
    }


def stub(note: str = "stub (M0)") -> dict[str, Any]:
    """M0 스텁 응답: data=null + note."""
    return {"ok": True, "data": None, "note": note, "server_time": utc_now_iso()}
