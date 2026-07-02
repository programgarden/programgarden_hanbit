"""위험 한도 로딩 — risk_limits 테이블 + 안전 기본값 (M2 + M3a 확장).

승수/통화 미확정이라 일부 cap 은 보수 placeholder. 한도값은 설정 가능하되 상한선(여기 기본값)을
넘기지 않는 것이 원칙(교육용 과대주문 방지). M3a 가 집중도/FX/일일손실 한도를 추가한다 —
시드만 하고 판독 안 하면 inert 이므로(검증 [5]) 여기서 명시 판독한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.repositories.orders_repo import OrdersRepo

# 안전 기본값 (risk_limits 시드와 일치; 테이블에 없으면 이 값 사용)
DEFAULTS: dict[str, float] = {
    # M2 (과대주문 방지)
    "max_contracts_per_order": 10,
    "max_open_orders": 20,
    "bucket_notional_cap": 1_000_000,
    "order_ack_timeout_s": 30,
    # M3a (집중도 INV-7 · FX 캡 · 일일손실 — KRW 환산 기준)
    # market/currency 캡 1.0 = paper 버킷(단일 시장·단일 통화)에서 미적용. LIVE(M4)는 시드로
    # 다시장·다통화 분산 캡을 받는다(검증/사용자 결정 2026-06-19).
    "max_symbol_weight": 0.25,
    "max_market_weight": 1.0,
    "max_currency_weight": 1.0,
    "max_positions": 20,
    "per_order_cap_krw": 3_000_000,
    "max_daily_loss_realized": 1_000_000,
    "max_daily_loss_eval": 2_000_000,
}


@dataclass(frozen=True)
class RiskLimits:
    """버킷 위험 한도 스냅샷."""

    max_contracts_per_order: int
    max_open_orders: int
    bucket_notional_cap: float
    order_ack_timeout_s: int
    # M3a
    max_symbol_weight: float
    max_market_weight: float
    max_currency_weight: float
    max_positions: int
    per_order_cap_krw: float
    max_daily_loss_realized: float
    max_daily_loss_eval: float

    @classmethod
    async def load(cls, repo: OrdersRepo, scope_ref: str) -> RiskLimits:
        raw = await repo.get_risk_limits(scope_ref)
        merged = {**DEFAULTS, **raw}
        return cls(
            max_contracts_per_order=int(merged["max_contracts_per_order"]),
            max_open_orders=int(merged["max_open_orders"]),
            bucket_notional_cap=float(merged["bucket_notional_cap"]),
            order_ack_timeout_s=int(merged["order_ack_timeout_s"]),
            max_symbol_weight=float(merged["max_symbol_weight"]),
            max_market_weight=float(merged["max_market_weight"]),
            max_currency_weight=float(merged["max_currency_weight"]),
            max_positions=int(merged["max_positions"]),
            per_order_cap_krw=float(merged["per_order_cap_krw"]),
            max_daily_loss_realized=float(merged["max_daily_loss_realized"]),
            max_daily_loss_eval=float(merged["max_daily_loss_eval"]),
        )
