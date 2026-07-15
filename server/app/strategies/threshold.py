"""임계값 규칙 전략 (M5 예제) — 지표 없이 가장 단순한 자동매매 규칙.

규칙(교육용·명확, 롱 온리):
  - **매수(ENTRY)**: 미보유 종목의 전일대비 등락률 ≤ -buy_drop_pct(예 -3%) → 현재가 지정가 매수.
  - **청산(EXIT)**: 보유 종목의 평가수익률 ≥ sell_profit_pct(예 +5%) → 보유 전량 현재가 지정가 매도.

지정가(현재가)로 내보내 **명목이 정의**되게 한다 → 소액캡·누적캡이 자동 적용되고, LIVE 시장가
금지(NO_NOTIONAL_FOR_LIVE) 도 피한다. 안전(캡·집중도·킬스위치·allow_live)은 게이트가 강제하므로
이 전략은 규칙만 담는다.

⚠ 자동경로는 사람 확인이 없다 — 이 규칙이 곧 발주다. 그래서 엔진 마스터 토글(기본 off)과
게이트(캡/킬스위치/allow_live)가 유일한 방어선이다.
"""

from __future__ import annotations

from app.models.dto import Quote
from app.models.order_dto import IntentKind, Side
from app.strategies.base import Signal


class ThresholdStrategy:
    """전일대비 하락 매수 / 평가수익 청산 규칙(롱 온리)."""

    def __init__(
        self,
        name: str,
        market: str,
        symbols: list[str],
        *,
        qty: int = 1,
        buy_drop_pct: float = 3.0,
        sell_profit_pct: float = 5.0,
        enabled: bool = True,
    ) -> None:
        self.name = name
        self.market = market
        self.symbols = list(symbols)
        self.qty = int(qty)
        self.buy_drop_pct = float(buy_drop_pct)
        self.sell_profit_pct = float(sell_profit_pct)
        self.enabled = enabled

    def evaluate(self, quotes: dict[str, Quote], positions: dict[str, dict]) -> list[Signal]:
        out: list[Signal] = []
        for sym in self.symbols:
            q = quotes.get(sym)
            if q is None or q.price is None:
                continue  # 시세 없으면 조용히 건너뛴다(자동경로)
            pos = positions.get(sym)
            held_qty = int(abs(float(pos.get("qty") or 0))) if pos else 0

            if held_qty <= 0:
                # 미보유 → 큰 하락에 매수(ENTRY).
                if q.change_rate is not None and q.change_rate <= -self.buy_drop_pct:
                    out.append(
                        Signal(
                            market=self.market, symbol=sym, side=Side.BUY,
                            intent=IntentKind.ENTRY, qty=self.qty, price=q.price,
                            reason=f"drop {q.change_rate:.2f}% <= -{self.buy_drop_pct}%",
                        )
                    )
                continue

            # 보유 중 → 평가수익률이 목표 이상이면 전량 청산(EXIT, reduce-only).
            avg = float(pos.get("avg_price") or 0) if pos else 0.0
            if avg <= 0:
                continue
            profit_pct = (q.price - avg) / avg * 100.0
            if profit_pct >= self.sell_profit_pct:
                out.append(
                    Signal(
                        market=self.market, symbol=sym, side=Side.SELL,
                        intent=IntentKind.EXIT, qty=held_qty, price=q.price,
                        reason=f"profit {profit_pct:.2f}% >= {self.sell_profit_pct}%",
                    )
                )
        return out
