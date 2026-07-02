"""TrackerSource (M3a, opt-in) — .claude/plans/2026-06-20-통합계획서.md M3 §1.2/§4.1.

account_tracker 콜백 페이로드(시장별 정규화 dataclass)를 단일 Snap 으로 변환 + 콜백 팩토리.
라이브러리 dataclass 와 dict 둘 다 받도록 getattr/dict 양용(`_g`). LIVE 트래커는 기본 off
(§0.2-5) — 여기는 변환·배선 헬퍼만 제공하고 실제 등록/실행은 M3b 가 런타임 가드와 함께 한다.

⚠ 값 바인딩(§3): (bucket, market) 을 **팩토리 인자**로 받아 클로저에 값으로 고정한다 —
루프 안에서 루프 변수를 직접 참조하는 늦은바인딩 버그를 구조적으로 차단.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.models.portfolio_dto import BalanceSnap, PositionSnap, SnapSource


def _g(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _float(v) -> float | None:
    return float(v) if v is not None else None


def position_item_to_snap(bucket: str, market: str, item) -> PositionSnap:
    """PositionItem(주식 long 고정 / 선물 is_long) → PositionSnap."""
    is_long = _g(item, "is_long", None)
    side = "long" if is_long is None else ("long" if is_long else "short")
    return PositionSnap(
        bucket=bucket,
        market=market,
        symbol=_g(item, "symbol"),
        currency=(_g(item, "currency_code") or _g(item, "currency") or "USD"),
        side=side,
        qty=_dec(_g(item, "quantity", 0)) or Decimal(0),
        multiplier=Decimal(1),  # 승수는 권위(reconcile/instrument) 보유 — 보강 snap 은 1
        avg_price=_dec(_g(item, "buy_price") or _g(item, "entry_price")),
        current_price=_dec(_g(item, "current_price")),
        pnl_amount=_dec(_g(item, "pnl_amount")),
        pnl_rate=_float(_g(item, "pnl_rate")),
        margin_used=_dec(_g(item, "opening_margin")),
        exchange_rate=_dec(_g(item, "exchange_rate")),
        source=SnapSource.TRACKER,
    )


def balance_to_snap(bucket: str, market: str, bal, *, currency: str | None = None) -> BalanceSnap:
    """BalanceInfo → BalanceSnap. currency 는 overseas dict 키에서 받거나 페이로드에서."""
    ccy = currency or _g(bal, "currency_code") or _g(bal, "currency") or "USD"
    return BalanceSnap(
        bucket=bucket,
        market=market,
        currency=ccy,
        deposit=_dec(_g(bal, "deposit")),
        orderable_amount=_dec(_g(bal, "orderable_amount")),
        margin_total=_dec(_g(bal, "total_margin")),
        withdrawable=_dec(_g(bal, "withdrawable")),
        realized_pnl=_dec(_g(bal, "realized_pnl")),
        exchange_rate=_dec(_g(bal, "exchange_rate")),
        source=SnapSource.TRACKER,
    )


def make_tracker_callbacks(bucket: str, market: str, aggregator) -> dict:
    """(bucket, market) 을 **값으로 고정**한 콜백 묶음(§3 값바인딩). 등록은 M3b."""
    b, m = bucket, market  # 값 바인딩 — 늦은바인딩 금지

    async def on_position_change(items) -> None:
        seq = items.values() if isinstance(items, dict) else items
        for item in seq:
            aggregator.apply_position(position_item_to_snap(b, m, item))

    async def on_balance_change(balances) -> None:
        if isinstance(balances, dict):  # overseas: Dict[currency, BalanceInfo]
            for ccy, bal in balances.items():
                aggregator.apply_balance(balance_to_snap(b, m, bal, currency=ccy))
        else:  # korea/futures: 단일
            aggregator.apply_balance(balance_to_snap(b, m, balances))

    return {"on_position_change": on_position_change, "on_balance_change": on_balance_change}
