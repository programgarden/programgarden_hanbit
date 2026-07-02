"""집중도/분산 지표 + projected-after-fill 노출 캡 (M3a, INV-7) — §5.4."""

from __future__ import annotations

from dataclasses import dataclass

from app.portfolio.metrics import (
    compute_concentration,
    exposure_violations,
    projected_weights,
)


def _items(*rows):
    # rows: (symbol, eval_krw, market, currency)
    return [
        {"symbol": s, "eval_krw": e, "market": m, "currency": c} for s, e, m, c in rows
    ]


def test_hhi_single_position_is_full_concentration():
    r = compute_concentration(_items(("A", 100.0, "m", "USD")))
    assert r.hhi == 1.0 and r.eff_n == 1.0 and r.top1_weight == 1.0
    assert r.norm_hhi == 0.0  # N=1 → 정규화 정의상 0


def test_hhi_even_split_minimizes_concentration():
    r = compute_concentration(
        _items(("A", 50.0, "m", "USD"), ("B", 50.0, "m", "USD"))
    )
    assert r.hhi == 0.5 and r.eff_n == 2.0 and r.norm_hhi == 0.0
    assert r.top1_weight == 0.5


def test_currency_hhi_buckets_by_currency():
    r = compute_concentration(
        _items(
            ("A", 60.0, "kr", "KRW"),
            ("B", 20.0, "os", "USD"),
            ("C", 20.0, "os", "HKD"),
        )
    )
    # 통화 비중 0.6/0.2/0.2 → 0.36+0.04+0.04 = 0.44
    assert abs(r.currency_hhi - 0.44) < 1e-9
    assert r.by_market["os"] == 0.4


def test_skips_zero_eval():
    r = compute_concentration(_items(("A", 100.0, "m", "USD"), ("B", 0.0, "m", "USD")))
    assert r.n == 1


@dataclass
class _Limits:
    # paper 버킷 기본값 — 단일 시장·단일 통화라 market/currency 캡은 1.0(미적용).
    # symbol 캡만 의미(0.25). LIVE 버킷(M4)은 다시장·다통화 캡을 받는다.
    max_symbol_weight: float = 0.25
    max_market_weight: float = 1.0
    max_currency_weight: float = 1.0


def test_projected_weight_includes_new_order():
    items = _items(("A", 100.0, "fut", "USD"))
    # 새 주문 B 명목 50 → B 비중 = 50/150 = 0.333 > 0.25 위반
    w = projected_weights(items, add_eval_krw=50.0, symbol="B", market="fut", currency="USD")
    assert abs(w["symbol"] - (50.0 / 150.0)) < 1e-9
    assert "MAX_SYMBOL_WEIGHT" in exposure_violations(w, _Limits())


def test_projected_includes_committed_open_orders():
    items = _items(("A", 100.0, "fut", "USD"))
    # 살아있는 미체결 명목 200 → 분모 = 100+200+10 = 310; B 비중 = 10/310 작음
    w = projected_weights(
        items, add_eval_krw=10.0, symbol="B", market="fut", currency="USD",
        committed_krw=200.0,
    )
    assert abs(w["symbol"] - (10.0 / 310.0)) < 1e-9
    # 통화는 USD 단일 → currency 비중 = (100+10)/310 (committed 는 통화 미상이라 분모에만)
    assert w["currency"] == (100.0 + 10.0) / 310.0


def test_no_violation_within_limits():
    items = _items(("A", 100.0, "fut", "USD"), ("B", 100.0, "fut", "USD"))
    w = projected_weights(items, add_eval_krw=20.0, symbol="C", market="fut", currency="USD")
    assert exposure_violations(w, _Limits()) == []  # symbol 20/220 < 0.25


@dataclass
class _LiveLimits:
    # LIVE 버킷처럼 market/currency 캡 < 1.0 — 죽은 분기 아님을 검증(리뷰 #3).
    max_symbol_weight: float = 1.0
    max_market_weight: float = 0.5
    max_currency_weight: float = 0.5


def test_market_currency_caps_fire_when_below_one():
    items = _items(("A", 100.0, "fut", "USD"))
    # 단일 시장/통화 → market·currency 비중 1.0 > 0.5 → 둘 다 발화. symbol 캡 1.0 은 미발화.
    w = projected_weights(items, add_eval_krw=50.0, symbol="A", market="fut", currency="USD")
    v = exposure_violations(w, _LiveLimits())
    assert "MAX_MARKET_WEIGHT" in v and "MAX_CURRENCY_WEIGHT" in v
    assert "MAX_SYMBOL_WEIGHT" not in v
