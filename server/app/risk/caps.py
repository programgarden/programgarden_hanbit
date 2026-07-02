"""FX 환산 명목 캡 헬퍼 (M3a) — .claude/plans/2026-06-20-통합계획서.md M3 §5.2.

명목(notional): 주식 qty*price, 선물 qty*price*multiplier (통화단위). 캡 비교는 KRW 환산값으로,
**환율 올림**(to_krw_ceil — 명목 크게 = 거부 strict)을 쓴다. 추정 환율(fx_estimated)이면 호출부가
risk_event(warn). 방향(올림/내림) 혼동이 곧 한도 무력화라 게이트가 명시적으로 ceil 을 호출한다.
"""

from __future__ import annotations


def notional_in_ccy(qty, price, multiplier: float | None = 1.0) -> float | None:
    """통화단위 명목. price None(시장가 등)이면 None(캡 판정 skip → 다른 단계가 잡음)."""
    if price is None:
        return None
    return float(qty) * float(price) * float(multiplier or 1.0)


def notional_krw_ceil(notional: float, ccy: str, fx) -> tuple[float, bool]:
    """명목(통화) → KRW 환산(올림). (krw, fx_estimated)."""
    rate, est = fx.to_krw_ceil(ccy)
    return notional * rate, est
