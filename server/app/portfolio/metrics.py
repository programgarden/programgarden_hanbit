"""분산/집중도 지표 + 사전 노출 캡 (M3a, INV-7) — .claude/plans/2026-06-20-통합계획서.md M3 §5.4.

모든 지표는 **버킷 내부**에서만 산출(통화/시장 교차 합산 금지). weight = eval_krw / Σ eval_krw.
사전 노출 캡은 **projected-after-fill**(현재 포지션 + 살아있는 미체결 + 이번 주문)로 판정해
여러 주문 동시통과 후 전부 체결 시 한도 초과를 막는다(PLAN §4.2).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConcentrationReport:
    """버킷 집중도 스냅(게이지/배지용)."""

    n: int
    hhi: float
    norm_hhi: float
    eff_n: float
    top1_weight: float
    currency_hhi: float
    by_symbol: dict[str, float] = field(default_factory=dict)
    by_market: dict[str, float] = field(default_factory=dict)
    by_currency: dict[str, float] = field(default_factory=dict)


def _weights(amounts: dict[str, float], total: float) -> dict[str, float]:
    return {k: (v / total) for k, v in amounts.items()} if total > 0 else {}


def _hhi(weights: dict[str, float]) -> float:
    return sum(w * w for w in weights.values())


def compute_concentration(items: list[dict]) -> ConcentrationReport:
    """items: [{eval_krw, symbol, market, currency}] (eval_krw>0 인 보유). 버킷 내부."""
    by_symbol: dict[str, float] = defaultdict(float)
    by_market: dict[str, float] = defaultdict(float)
    by_currency: dict[str, float] = defaultdict(float)
    for it in items:
        ev = float(it.get("eval_krw") or 0.0)
        if ev <= 0:
            continue
        by_symbol[it["symbol"]] += ev
        by_market[it.get("market") or "?"] += ev
        by_currency[it.get("currency") or "?"] += ev

    total = sum(by_symbol.values())
    n = len(by_symbol)
    sym_w = _weights(dict(by_symbol), total)
    cur_w = _weights(dict(by_currency), total)
    hhi = _hhi(sym_w)
    # 정규화 HHI: (HHI − 1/N)/(1 − 1/N), N≤1 이면 0(분산 정의 불가).
    norm = ((hhi - 1.0 / n) / (1.0 - 1.0 / n)) if n > 1 else 0.0
    eff_n = (1.0 / hhi) if hhi > 0 else 0.0
    top1 = max(sym_w.values(), default=0.0)
    return ConcentrationReport(
        n=n, hhi=hhi, norm_hhi=norm, eff_n=eff_n, top1_weight=top1,
        currency_hhi=_hhi(cur_w),
        by_symbol=sym_w, by_market=_weights(dict(by_market), total), by_currency=cur_w,
    )


def projected_weights(
    items: list[dict],
    *,
    add_eval_krw: float,
    symbol: str,
    market: str,
    currency: str,
    committed_krw: float = 0.0,
) -> dict[str, float]:
    """이번 주문 체결 후 예상 비중(symbol/market/currency). committed_krw=살아있는 미체결 명목.

    분모 = 현재 평가 합 + 미체결 명목 + 이번 주문 명목. 분자도 같은 가산(매수형 보수).
    """
    cur_symbol = sum(float(i.get("eval_krw") or 0.0) for i in items if i["symbol"] == symbol)
    cur_market = sum(
        float(i.get("eval_krw") or 0.0) for i in items if (i.get("market") or "?") == market
    )
    cur_ccy = sum(
        float(i.get("eval_krw") or 0.0) for i in items if (i.get("currency") or "?") == currency
    )
    total_now = sum(float(i.get("eval_krw") or 0.0) for i in items)
    denom = total_now + committed_krw + add_eval_krw
    if denom <= 0:
        return {"symbol": 0.0, "market": 0.0, "currency": 0.0}
    return {
        "symbol": (cur_symbol + add_eval_krw) / denom,
        "market": (cur_market + add_eval_krw) / denom,
        "currency": (cur_ccy + add_eval_krw) / denom,
    }


def exposure_violations(weights: dict[str, float], limits) -> list[str]:
    """projected weight 가 max_*_weight 초과 시 위반 코드 목록(INV-7)."""
    out: list[str] = []
    if weights.get("symbol", 0.0) > limits.max_symbol_weight:
        out.append("MAX_SYMBOL_WEIGHT")
    if weights.get("market", 0.0) > limits.max_market_weight:
        out.append("MAX_MARKET_WEIGHT")
    if weights.get("currency", 0.0) > limits.max_currency_weight:
        out.append("MAX_CURRENCY_WEIGHT")
    return out
