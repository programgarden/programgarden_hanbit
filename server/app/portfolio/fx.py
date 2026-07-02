"""환율 제공자 (M3a) — .claude/plans/2026-06-20-통합계획서.md M3 §6. base = KRW.

방향별 메서드(검증 Lens2-H3 — generic boolean footgun 금지):
- `to_krw_ceil`  환율 올림 → 명목/매수 캡(거부 strict). per_order_cap/bucket_notional_cap.
- `to_krw_floor` 환율 내림 → 가용잔고/헤드룸/매도대금(보수=작게).
- `to_krw`       중립 → 표시/취득환율(fx_at_buy).

출처 우선순위(§1.4): ① 최신 라이브 스냅 exchange_rate(overseas; futures 는 미제공=0 → 건너뜀)
→ ② 캐시(TTL 내) → ③ 설정 고정환율(estimated=1). ceil/floor 는 buffer_pct 로 안전 마진.
각 메서드는 `(rate: float, estimated: bool)` 반환 — estimated=True 면 캡 판정 시 risk_event(warn).
"""

from __future__ import annotations

import time
from collections.abc import Callable

from app.repositories.orders_repo import OrdersRepo

BASE_CCY = "KRW"


class FxRateProvider:
    """KRW 기준 환율 캐시 + 고정 fallback."""

    def __init__(
        self,
        *,
        usd_krw: float,
        hkd_krw: float,
        buffer_pct: float = 0.02,
        ttl_s: int = 300,
        repo: OrdersRepo | None = None,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self._fixed: dict[str, float] = {"USD": float(usd_krw), "HKD": float(hkd_krw)}
        self._buffer = float(buffer_pct)
        self._ttl = float(ttl_s)
        self._repo = repo
        self._now = now_fn
        self._cache: dict[str, tuple[float, float]] = {}  # ccy -> (rate, observed_ts)

    @classmethod
    def from_settings(cls, settings, repo: OrdersRepo | None = None, **kw) -> FxRateProvider:
        return cls(
            usd_krw=settings.hanbit_fx_usd_krw,
            hkd_krw=settings.hanbit_fx_hkd_krw,
            buffer_pct=settings.hanbit_fx_buffer_pct,
            ttl_s=settings.hanbit_fx_ttl_s,
            repo=repo,
            **kw,
        )

    async def observe(self, ccy: str, rate: float | None, *, source: str = "tracker") -> None:
        """라이브러리 제공 환율 관측 → 캐시 + fx_rates 영속(estimated=0)."""
        if ccy == BASE_CCY or not rate or float(rate) <= 0:
            return
        self._cache[ccy] = (float(rate), self._now())
        if self._repo is not None:
            await self._repo.upsert_fx_rate(ccy, float(rate), source=source, fx_estimated=0)

    def _base(self, ccy: str) -> tuple[float, bool]:
        """기준 환율(rate, estimated). 라이브(신선) → 고정 fallback 순."""
        if ccy == BASE_CCY:
            return (1.0, False)
        cached = self._cache.get(ccy)
        if cached is not None and (self._now() - cached[1]) <= self._ttl:
            return (cached[0], False)
        fixed = self._fixed.get(ccy)
        if fixed is not None:
            return (fixed, True)
        # 미지원 통화 — 도메인(KRW/USD/HKD)에 없음. 보수적으로 estimated 표시.
        return (1.0, True)

    def supports(self, ccy: str) -> bool:
        """캡 환산이 가능한 통화인가(KRW 또는 고정환율 보유). 미지원이면 호출부가 거부(§16)."""
        return ccy == BASE_CCY or ccy in self._fixed

    def to_krw(self, ccy: str) -> tuple[float, bool]:
        """중립 환율 — 표시/취득환율용(버퍼 미적용)."""
        return self._base(ccy)

    def to_krw_ceil(self, ccy: str) -> tuple[float, bool]:
        """올림 환율 — 명목/매수 캡(명목 크게 = 거부 strict)."""
        if ccy == BASE_CCY:
            return (1.0, False)
        rate, est = self._base(ccy)
        return (rate * (1.0 + self._buffer), est)

    def to_krw_floor(self, ccy: str) -> tuple[float, bool]:
        """내림 환율 — 가용잔고/헤드룸(작게 = 보수)."""
        if ccy == BASE_CCY:
            return (1.0, False)
        rate, est = self._base(ccy)
        return (rate * (1.0 - self._buffer), est)
