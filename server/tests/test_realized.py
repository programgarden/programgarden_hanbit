"""체결 기반 실현손익 평균원가 매칭 (M3b §5.3, 흡수 ①) — app/portfolio/realized.py."""

from __future__ import annotations

import pytest

from app.portfolio.realized import realized_pnl_ccy


def test_long_round_trip_profit():
    # 매수 2@10 → 매도 2@12 : (12-10)*2 = +4
    assert realized_pnl_ccy([(2, 10.0), (-2, 12.0)]) == pytest.approx(4.0)


def test_long_round_trip_loss():
    # 매수 2@10 → 매도 2@8 : (8-10)*2 = -4 (손실=음수)
    assert realized_pnl_ccy([(2, 10.0), (-2, 8.0)]) == pytest.approx(-4.0)


def test_short_round_trip_profit():
    # 매도 2@12 → 매수 2@10 : 숏 진입 후 하락 청산 = +4
    assert realized_pnl_ccy([(-2, 12.0), (2, 10.0)]) == pytest.approx(4.0)


def test_partial_close_leaves_residual():
    # 매수 3@10 → 매도 1@13 : 1계약만 실현 (13-10)*1 = 3, 잔여 2계약 미실현
    assert realized_pnl_ccy([(3, 10.0), (-1, 13.0)]) == pytest.approx(3.0)


def test_weighted_average_entry():
    # 매수 1@10 + 매수 1@20 (평균 15) → 매도 2@18 : (18-15)*2 = 6
    assert realized_pnl_ccy([(1, 10.0), (1, 20.0), (-2, 18.0)]) == pytest.approx(6.0)


def test_flip_direction_realizes_only_closed():
    # 매수 2@10 → 매도 5@12 : 보유 2 청산 (12-10)*2=4, 잔여 -3 숏 신규(@12) 미실현
    assert realized_pnl_ccy([(2, 10.0), (-5, 12.0)]) == pytest.approx(4.0)


def test_multiplier_applied():
    # 선물 승수 100: 매수 1@0.65 → 매도 1@0.70 : (0.70-0.65)*1*100 = 5.0
    assert realized_pnl_ccy([(1, 0.65), (-1, 0.70)], 100) == pytest.approx(5.0)


def test_open_only_no_realized():
    # 청산 체결 없음 → 실현 0
    assert realized_pnl_ccy([(2, 10.0), (1, 11.0)]) == 0.0


def test_empty_fills():
    assert realized_pnl_ccy([]) == 0.0
