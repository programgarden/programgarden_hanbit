"""포트폴리오 집계 (M3a) — .claude/plans/2026-06-20-통합계획서.md M3 §4.

(bucket, market) 별 정규화 Snap(PositionSnap/BalanceSnap)을 받아, **consistent_tick**(§4.3)
으로 포지션·잔고·실현을 *한 순간*에 읽어 KRW 로 재집계한다. 핵심 불변식:

- **버킷 격리(§3)**: 상태는 `bucket → ...` 로만 키잉되고, 집계 함수는 자기 버킷만 본다.
  두 버킷 합산 경로 없음(합산은 §11 표시 라인에서만).
- **이중계상 방지(§5.3)**: realized·unrealized 를 같은 tick 에서 읽는다 — 포지션이 청산되어
  unrealized→realized 로 이동하는 경계에서 콜백을 따로 읽으면 누락/중복 → 단일 tick 으로 제거.
- **KRW 재집계(§1.2 ⚠)**: 트래커 헤드라인(USD 고정 의심)은 신뢰하지 않고, 포지션별 통화에
  우리 FxRateProvider 를 적용해 KRW 로 재합산한다. eval_krw 는 **중립 환율**(§6 호출부 방향표).
- **필드분할 영속(§4.2)**: 권위(qty/avg_price)는 reconcile 이 `upsert_position_authority` 로,
  보강(price/pnl/eval/fx)은 여기서 `upsert_position_marks` 로 — 두 SET 목록이 서로소.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.core.mode_matrix import markets_of
from app.models.portfolio_dto import BalanceSnap, BucketKpi, PositionSnap
from app.portfolio.fx import FxRateProvider
from app.portfolio.metrics import compute_concentration


@dataclass
class PortfolioTick:
    """한 순간의 일관 스냅(§4.3). realized/unrealized 가 같은 tick 에서 산출됨(이중계상 0)."""

    bucket: str
    positions: list[dict]  # {symbol, market, currency, side, qty, eval_krw, pnl_krw, ...}
    total_eval_krw: float
    total_buy_krw: float
    total_pnl_krw: float  # 미실현 합(KRW)
    # 일중 실현(KRW) — 잠정: balances.realized_pnl. 일일손실 권위는 fills(§5.3, M3b).
    realized_krw: float
    position_count: int
    fx_estimated: bool


class PortfolioAggregator:
    """정규화 Snap → 버킷 KPI. 버킷별 인메모리 상태 + consistent_tick + KRW 재집계."""

    def __init__(
        self,
        fx: FxRateProvider,
        *,
        repo=None,
        bus=None,
    ) -> None:
        self._fx = fx
        self._repo = repo
        self._bus = bus
        self._pos: dict[str, dict[str, PositionSnap]] = defaultdict(dict)  # bucket→symbol→snap
        self._bal: dict[str, dict[str, BalanceSnap]] = defaultdict(dict)  # bucket→ccy→snap

    # ── 입력: Snap apply (버킷 격리 — snap.bucket 자기 버킷에만 기록) ─────────
    def apply_position(self, snap: PositionSnap) -> None:
        book = self._pos[snap.bucket]
        if snap.qty == 0:
            book.pop(snap.symbol, None)  # 청산 → 포지션 제거(unrealized 에서 빠짐)
        else:
            book[snap.symbol] = snap

    def apply_balance(self, snap: BalanceSnap) -> None:
        self._bal[snap.bucket][snap.currency] = snap

    def apply(self, snap) -> None:
        """PositionSnap/BalanceSnap 자동 분기."""
        if isinstance(snap, PositionSnap):
            self.apply_position(snap)
        elif isinstance(snap, BalanceSnap):
            self.apply_balance(snap)
        else:  # pragma: no cover - 방어
            raise TypeError(f"unknown snap type: {type(snap)!r}")

    # ── 일관 tick: 포지션·잔고·실현을 한 순간에 (§4.3 이중계상 방지) ──────────
    def consistent_tick(self, bucket: str) -> PortfolioTick:
        rows: list[dict] = []
        total_eval = total_buy = total_pnl = 0.0
        est_any = False
        for snap in self._pos[bucket].values():
            rate, est = self._fx.to_krw(snap.currency)  # eval_krw=중립 환율(§6 표)
            est_any = est_any or est
            mult = float(snap.multiplier or 1)
            qty = float(snap.qty)
            price = float(
                snap.current_price if snap.current_price is not None else (snap.avg_price or 0)
            )
            avg = float(snap.avg_price or 0)
            eval_krw = qty * price * mult * rate
            buy_krw = qty * avg * mult * rate
            # 미실현: 트래커 pnl_amount 는 **통화단위 총손익**(qty·승수 이미 반영, 방향 포함) →
            # KRW 환산만(× mult 금지=이중계상). 미제공 시 eval−buy 로 근사하되 **방향 반영**
            # (숏은 가격 하락이 이익 → buy−eval). 부호 오염 방지(리뷰 #12).
            if snap.pnl_amount is not None:
                pnl_krw = float(snap.pnl_amount) * rate
            elif snap.side == "short":
                pnl_krw = buy_krw - eval_krw
            else:
                pnl_krw = eval_krw - buy_krw
            total_eval += eval_krw
            total_buy += buy_krw
            total_pnl += pnl_krw
            rows.append(
                {
                    "symbol": snap.symbol,
                    "market": snap.market,
                    "currency": snap.currency,
                    "side": snap.side,
                    "qty": qty,
                    "current_price": price,
                    "avg_price": avg,
                    "multiplier": mult,
                    "pnl_amount": (float(snap.pnl_amount) if snap.pnl_amount is not None else None),
                    "pnl_rate": snap.pnl_rate,
                    "eval_krw": eval_krw,
                    "pnl_krw": pnl_krw,
                    "fx_now": rate,
                    "fx_estimated": est,
                }
            )
        # 실현(KRW) — in-memory tick 은 balances.realized_pnl(트래커/잔고) 합산 **표시값**이다.
        # 일일손실/영속 KPI 의 realized 권위는 **fills 기반**(§5.3, M3b 흡수 ①):
        # persist_and_publish 가 repo.realized_pnl_krw 로 교체해 daily_realized_krw 에 쓴다.
        # 트래커 realized_pnl 의 일중/lifetime 의미가 라이브 미확정(§13-3)이라 체결 원장이 권위.
        realized_krw = 0.0
        for b in self._bal[bucket].values():
            if b.realized_pnl is not None:
                r, e = self._fx.to_krw(b.currency)
                est_any = est_any or e
                realized_krw += float(b.realized_pnl) * r
        return PortfolioTick(
            bucket=bucket,
            positions=rows,
            total_eval_krw=total_eval,
            total_buy_krw=total_buy,
            total_pnl_krw=total_pnl,
            realized_krw=realized_krw,
            position_count=len(rows),
            fx_estimated=est_any,
        )

    # ── KPI: 헤드라인 + 집중도(§5.4) — 모두 같은 tick 에서 ────────────────────
    def kpi(self, bucket: str) -> BucketKpi:
        tick = self.consistent_tick(bucket)
        conc = compute_concentration(tick.positions)
        return BucketKpi(
            bucket=bucket,
            account_pnl_rate=(
                tick.total_pnl_krw / tick.total_buy_krw if tick.total_buy_krw else None
            ),
            total_eval_krw=tick.total_eval_krw,
            total_buy_krw=tick.total_buy_krw,
            total_pnl_krw=tick.total_pnl_krw,
            position_count=tick.position_count,
            hhi=conc.hhi,
            norm_hhi=conc.norm_hhi,
            eff_n=conc.eff_n,
            top1_weight=conc.top1_weight,
            currency_hhi=conc.currency_hhi,
            daily_realized_krw=tick.realized_krw,
            daily_pnl_krw=tick.total_pnl_krw + tick.realized_krw,
            by_currency=conc.by_currency,
            by_market=conc.by_market,
        )

    async def _observe_live_fx(self, bucket: str) -> None:
        """라이브 스냅 exchange_rate(overseas) → fx 캐시(§6 우선순위 ①). futures 는 0 → 무시.

        이후 consistent_tick 이 고정환율 대신 라이브 환율을 쓰게 한다(리뷰 #6).
        """
        for snap in self._pos[bucket].values():
            if snap.exchange_rate:
                await self._fx.observe(snap.currency, float(snap.exchange_rate), source="tracker")
        for b in self._bal[bucket].values():
            if b.exchange_rate:
                await self._fx.observe(b.currency, float(b.exchange_rate), source="tracker")

    # ── 영속 + publish (필드분할 보강 writer·bucket_kpi·balances·WS) ──────────
    async def persist_and_publish(self, bucket: str) -> BucketKpi:
        await self._observe_live_fx(bucket)  # FX 우선순위 ①(§6) — 라이브 스냅 환율을 캐시에 주입
        tick = self.consistent_tick(bucket)
        kpi = self.kpi(bucket)
        if self._repo is not None:
            # 일일손실/영속 KPI 의 realized 권위 = 체결 원장(fills) 평균원가 실현(§5.3, 흡수 ①).
            # in-memory tick.realized_krw(balances 표시값) 대신 이 값을 KPI 에 쓴다.
            realized_auth = await self._repo.realized_pnl_krw(markets_of(bucket), self._fx)
            kpi.daily_realized_krw = realized_auth
            kpi.daily_pnl_krw = kpi.total_pnl_krw + realized_auth
            # 포지션 보강(marks) — 권위 컬럼(qty/avg)은 절대 안 건드림(§4.2).
            for p in tick.positions:
                acct = await self._repo.get_account_id(p["market"])
                if acct is None:
                    continue
                inst = await self._repo.ensure_instrument(p["market"], p["symbol"])
                await self._repo.upsert_position_marks(
                    acct,
                    inst,
                    current_price=p["current_price"],
                    pnl_amount=p["pnl_amount"],
                    pnl_rate=p["pnl_rate"],
                    fx_now=p["fx_now"],
                    # 취득시점 환율 고정(§4.3) — marks 가 COALESCE 로 최초값 유지.
                    fx_at_buy=p["fx_now"],
                    fx_estimated=int(p["fx_estimated"]),
                    eval_krw=p["eval_krw"],
                )
            # 통화별 잔고 스냅샷
            for ccy, b in self._bal[bucket].items():
                acct = await self._repo.get_account_id(b.market)
                if acct is None:
                    continue
                await self._repo.upsert_balance_snapshot(
                    acct,
                    ccy,
                    deposit=_f(b.deposit),
                    orderable_amount=_f(b.orderable_amount),
                    margin_total=_f(b.margin_total),
                    withdrawable=_f(b.withdrawable),
                    realized_pnl=_f(b.realized_pnl),
                    exchange_rate=_f(b.exchange_rate),
                )
            await self._repo.insert_bucket_kpi(
                bucket,
                account_pnl_rate=kpi.account_pnl_rate,
                total_eval_krw=kpi.total_eval_krw,
                total_buy_krw=kpi.total_buy_krw,
                total_pnl_krw=kpi.total_pnl_krw,
                position_count=kpi.position_count,
                hhi=kpi.hhi,
                norm_hhi=kpi.norm_hhi,
                eff_n=kpi.eff_n,
                top1_weight=kpi.top1_weight,
                currency_hhi=kpi.currency_hhi,
                daily_realized_krw=kpi.daily_realized_krw,
                daily_pnl_krw=kpi.daily_pnl_krw,
            )
            await self._repo.incr_metric("kpi_snapshots")
            if tick.positions:
                await self._repo.incr_metric("positions_synced", by=len(tick.positions))
        if self._bus is not None:
            await self._bus.publish("portfolio_snapshot", kpi.model_dump(mode="json"))
        return kpi


def _f(v) -> float | None:
    return float(v) if v is not None else None
