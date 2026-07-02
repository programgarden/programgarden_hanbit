"""FakeSource (M3a 테스트) — .claude/plans/2026-06-20-통합계획서.md M3 §4.1. 다통화 결정론 픽스처.

KRW 재집계/집중도/이중계상 검증용. paper 버킷은 실제로 단일 통화(USD)지만, 집계기는 버킷
무관하므로 합성 다통화 책(USD+HKD)으로 환산 수학을 검증한다(LIVE 통화 혼합은 M4).
"""

from __future__ import annotations

from decimal import Decimal

from app.models.portfolio_dto import BalanceSnap, PositionSnap, SnapSource

_M = "overseas_futureoption"


def fake_multi_ccy_book(bucket: str = "paper", market: str = _M) -> list[PositionSnap]:
    """결정론 2종(USD long + HKD short) 포지션. eval = qty*price*mult*fx(통화별)."""
    return [
        PositionSnap(
            bucket=bucket, market=market, symbol="ADZ25", currency="USD", side="long",
            qty=Decimal(2), multiplier=Decimal(100),
            avg_price=Decimal("0.65"), current_price=Decimal("0.70"),
            pnl_amount=Decimal("10"), pnl_rate=7.6, source=SnapSource.FAKE,
        ),
        PositionSnap(
            bucket=bucket, market=market, symbol="HSIQ25", currency="HKD", side="short",
            qty=Decimal(1), multiplier=Decimal(10),
            avg_price=Decimal("180"), current_price=Decimal("178"),
            pnl_amount=Decimal("20"), pnl_rate=1.1, source=SnapSource.FAKE,
        ),
    ]


def fake_balances(bucket: str = "paper", market: str = _M) -> list[BalanceSnap]:
    return [
        BalanceSnap(
            bucket=bucket, market=market, currency="USD",
            deposit=Decimal("10000"), orderable_amount=Decimal("8000"),
            margin_total=Decimal("1200"), realized_pnl=Decimal("5"),
            source=SnapSource.FAKE,
        )
    ]
