"""전략 엔진 (M5) — 전략 신호를 **기존 안전 파이프라인**으로 라우팅한다.

`run_once()`: 마스터 토글이 켜져 있을 때, 각 활성 전략을 평가해 Signal 을 얻고, 각 Signal 을
OrderIntent 로 바꿔 `order_service.place()` 로 보낸다. 캡·집중도·킬스위치·엔진상태·allow_live
는 **전부 기존 게이트가 강제** — 엔진은 안전을 재구현하지 않는다.

자동경로 안전 모델(사람 확인 없음 → 게이트가 유일 방어선):
  - 마스터 토글 `hanbit_strategies_enabled` 기본 off — 켜야만 발주한다.
  - LIVE(KR/OVS)는 allow_live=false 면 게이트가 거부(실주문 0). 켜도 소액캡·누적캡·첫주문
    가드(단일종목)로 강하게 제한된다.
  - 버킷 halt/killed(킬스위치·일일손실)면 게이트가 거부 → 전략이 자동으로 멈춘다.
  - 시세 조회 실패는 조용히 스킵(자동 루프가 예외로 죽지 않게).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.core.mode_matrix import bucket_of
from app.logging_setup import get_logger
from app.models.dto import Quote
from app.models.order_dto import OrderIntent
from app.strategies.base import Signal, Strategy

if TYPE_CHECKING:
    from app.repositories.orders_repo import OrdersRepo
    from app.services.order_service import OrderService

logger = get_logger("app.strategies")

# 시세 소스 주입 — (market, symbol) → Quote. 실서버=MarketService.get_quote, 테스트=fake.
QuoteFn = Callable[[str, str], Awaitable[Quote]]


class StrategyEngine:
    """전략 평가 → 안전 파이프라인 발주 오케스트레이션."""

    def __init__(
        self,
        order_service: OrderService,
        repo: OrdersRepo,
        quote_fn: QuoteFn,
        *,
        enabled: bool = False,
        strategies: list[Strategy] | None = None,
    ) -> None:
        self._svc = order_service
        self._repo = repo
        self._quote_fn = quote_fn
        self._enabled = enabled
        self._strategies: list[Strategy] = list(strategies or [])

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)

    def add_strategy(self, strategy: Strategy) -> None:
        self._strategies.append(strategy)

    def list_strategies(self) -> list[dict]:
        return [
            {"name": s.name, "market": s.market, "symbols": list(s.symbols), "enabled": s.enabled}
            for s in self._strategies
        ]

    async def run_once(self) -> dict:
        """전체 활성 전략 1회 평가·발주. 마스터 토글 off 면 아무것도 하지 않는다."""
        if not self._enabled:
            return {"enabled": False, "fired": []}
        fired: list[dict] = []
        for strat in self._strategies:
            if not getattr(strat, "enabled", True):
                continue
            fired.extend(await self._run_strategy(strat))
        return {"enabled": True, "fired": fired}

    async def _run_strategy(self, strat: Strategy) -> list[dict]:
        quotes: dict[str, Quote] = {}
        for sym in strat.symbols:
            try:
                quotes[sym] = await self._quote_fn(strat.market, sym)
            except Exception:  # noqa: BLE001 — 시세 실패는 스킵(자동 루프 보호)
                logger.warning("strategy %s quote failed for %s", strat.name, sym)
        bucket = bucket_of(strat.market)
        positions = {
            p.get("symbol"): p
            for p in (await self._repo.positions_for(bucket) if bucket else [])
        }
        results: list[dict] = []
        for sig in strat.evaluate(quotes, positions):
            res = await self._svc.place(self._to_intent(strat, sig))
            results.append(
                {
                    "strategy": strat.name,
                    "symbol": sig.symbol,
                    "side": sig.side.value,
                    "intent": sig.intent.value,
                    "qty": sig.qty,
                    "reason": sig.reason,
                    "ok": bool(res.get("ok")),
                    "decision": res.get("decision"),  # 게이트 거부 사유(있으면)
                }
            )
        return results

    @staticmethod
    def _to_intent(strat: Strategy, sig: Signal) -> OrderIntent:
        return OrderIntent(
            market=sig.market,
            symbol=sig.symbol,
            side=sig.side,
            intent=sig.intent,
            order_type=sig.order_type,
            qty=sig.qty,
            price=sig.price,
            reason=f"[{strat.name}] {sig.reason}",
        )

    async def run_loop(self, interval_s: float, *, stop: asyncio.Event | None = None) -> None:
        """주기적 자동 실행(백그라운드 태스크용). stop 이벤트로 종료. 발주 여부는 토글이 통제."""
        while stop is None or not stop.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 — 한 사이클 실패가 루프를 죽이지 않게
                logger.exception("strategy run_once failed")
            if stop is None:
                await asyncio.sleep(interval_s)
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)  # 조기 종료 지원
            except TimeoutError:
                pass  # 정상 — 다음 사이클로
