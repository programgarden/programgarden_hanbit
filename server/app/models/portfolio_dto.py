"""포트폴리오/위험 집계 정규화 DTO (M3a) — .claude/plans/2026-06-20-통합계획서.md M3 §4.1.

account_tracker 콜백(시장별 정규화 dataclass)과 reconcile 스냅샷(CIDBQ01500)을 하나의
표현으로 흡수한다. 라이브러리 금액은 korea=int(원)/overseas·futures=Decimal 이라(§1.3),
내부 계산은 Decimal, 저장/전송은 float 로 한다. `source` 로 권위(reconcile)/보강(tracker)/
테스트(fake)를 구분해 이중 writer 필드 분할(§4.2)을 강제한다.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class SnapSource(StrEnum):
    """스냅 출처 — 권위/보강/테스트."""

    RECONCILE = "reconcile"  # 권위: qty/avg_price/margin (CIDBQ01500)
    TRACKER = "tracker"      # 보강: current_price/pnl/fx (account_tracker 콜백)
    FAKE = "fake"            # 테스트


class PositionSnap(BaseModel):
    """포지션 스냅(시장 무관). 금액은 통화단위(KRW 환산 전)."""

    bucket: str
    market: str
    symbol: str
    currency: str = "USD"
    side: str = "long"                 # 'long' / 'short' (선물 is_long 매핑)
    qty: Decimal = Decimal(0)
    multiplier: Decimal = Decimal(1)   # 선물 승수(주식 1)
    avg_price: Decimal | None = None
    current_price: Decimal | None = None
    pnl_amount: Decimal | None = None  # 미실현(통화단위)
    pnl_rate: float | None = None
    margin_used: Decimal | None = None
    exchange_rate: Decimal | None = None  # 라이브러리 제공 KRW 환산율(overseas)
    source: SnapSource = SnapSource.FAKE

    model_config = {"arbitrary_types_allowed": True}


class BalanceSnap(BaseModel):
    """통화별 잔고 스냅."""

    bucket: str
    market: str
    currency: str
    deposit: Decimal | None = None
    orderable_amount: Decimal | None = None
    margin_total: Decimal | None = None
    withdrawable: Decimal | None = None
    realized_pnl: Decimal | None = None     # 일중 실현(있는 시장만 — overseas_stock 은 없음)
    exchange_rate: Decimal | None = None
    source: SnapSource = SnapSource.FAKE

    model_config = {"arbitrary_types_allowed": True}


class BucketKpi(BaseModel):
    """버킷 헤드라인 + 집중도 (KRW 환산). bucket_kpi 테이블/WS push 매핑."""

    bucket: str
    account_pnl_rate: float | None = None
    total_eval_krw: float = 0.0
    total_buy_krw: float = 0.0
    total_pnl_krw: float = 0.0
    position_count: int = 0
    hhi: float | None = None
    norm_hhi: float | None = None
    eff_n: float | None = None
    top1_weight: float | None = None
    currency_hhi: float | None = None
    daily_realized_krw: float | None = None
    daily_pnl_krw: float | None = None
    drawdown_pct: float | None = None
    risk_budget_left_krw: float | None = None
    halted: bool = False
    by_currency: dict[str, float] = Field(default_factory=dict)
    by_market: dict[str, float] = Field(default_factory=dict)
