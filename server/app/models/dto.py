"""정규화 시세 DTO — 라이브러리 응답을 시장 무관 형태로 변환한다.

각 어댑터가 라이브러리 TR 응답(OutBlock)을 읽어 이 DTO 로 채운다.
필드 의미가 소스에서 불확실한 값(예: decimal scale)은 그대로(as-returned) 담는다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Quote(BaseModel):
    """단일 종목 현재가 스냅샷(정규화)."""

    symbol: str = Field(..., description="조회 종목코드/심볼")
    market: str = Field(..., description="시장 키(korea_stock 등)")
    price: float = Field(..., description="현재가")
    prev_close: float | None = Field(default=None, description="전일종가(있으면)")
    change: float | None = Field(default=None, description="전일대비")
    change_rate: float | None = Field(default=None, description="등락률(%)")
    volume: int | None = Field(default=None, description="누적거래량")
    ts: str | None = Field(default=None, description="응답 시각/관련 시각(있으면)")


class Candle(BaseModel):
    """단일 OHLCV 봉(정규화)."""

    date: str = Field(..., description="봉 기준일/시각(YYYYMMDD 등)")
    o: float = Field(..., description="시가")
    h: float = Field(..., description="고가")
    low: float = Field(..., alias="l", description="저가")
    c: float = Field(..., description="종가")
    v: int = Field(default=0, description="거래량")

    model_config = {"populate_by_name": True}
