"""사전 위험 게이트 (M2 + M3a 확장).

설계: .claude/plans/2026-06-20-통합계획서.md M2 §4 + M3 §5.

순서(§5.1):
  0. 엔진상태(PAPER_TRADING; 런타임 ACTIVE 단일권위는 M3b 부트머신)
  EXIT reduce-only 사전분류(§5.5 — intent 신뢰 금지: 보유 반대방향·qty≤보유 만 EXIT,
  아니면 ENTRY 재분류)
  1. 킬스위치/halt + **HALTED_DAILY(risk_state)** — 신규 REJECT, 유효 EXIT(reduce-only) PASS
  2. 모드/HKEX 안전가드(하드) — ★EXIT 비우회★(시장가드는 청산도 통과 못 함)
  3. 과대주문(계약수)
  4. 명목 캡 — bucket_notional_cap(통화중립) + **per_order_cap_krw(FX ceil 환산, §5.2)**
  5. **노출/집중도 INV-7(projected-after-fill, §5.4)** — 매수 REJECT / 감축 skip
  6. max_positions / 동시 미체결 수
  7. orderable(best-effort)

REJECT>WARN>PASS. 모든 결정 audit_log, 위반 risk_events(DoD: 감사로그 누락 0).
fx 미주입(M2 단위테스트) 시 FX 캡·노출 단계는 안전하게 skip — 기존 동작 보존.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel

from app.core.engine_state import EngineState
from app.core.mode_matrix import (
    MARKET_OVERSEAS_FUTUREOPTION,
    TRADING_MODE_PAPER,
    bucket_of,
    markets_of,
    trading_mode_of,
)
from app.models.order_dto import IntentKind, OrderIntent, Side
from app.portfolio.metrics import exposure_violations, projected_weights
from app.repositories.orders_repo import OrdersRepo
from app.risk.caps import notional_in_ccy, notional_krw_ceil
from app.risk.halt import is_blocked
from app.risk.limits import RiskLimits


class RiskResult(StrEnum):
    PASS = "pass"
    WARN = "warn"
    REJECT = "reject"


class RiskDecision(BaseModel):
    result: RiskResult
    reasons: list[str] = []
    adjusted_qty: int | None = None
    reclassified_entry: bool = False  # EXIT 가 노출증가/미보유라 ENTRY 로 재분류됨(§5.5)

    @property
    def ok(self) -> bool:
        """주문 진행 허용 여부(REJECT 가 아니면 진행)."""
        return self.result != RiskResult.REJECT


@dataclass
class RiskContext:
    """게이트가 한도 판정에 쓰는 런타임 컨텍스트(서비스가 버킷-스코프로 채운다, §3)."""

    multiplier: float | None = None
    orderable_amount: float | None = None
    open_orders_count: int = 0
    # M3a — 버킷-스코프 보유/미체결(서비스가 positions_for(bucket) 등으로 채움)
    # {symbol, eval_krw, market, currency, position_side, qty}
    positions: list[dict] = field(default_factory=list)
    committed_krw: float = 0.0  # 살아있는 미체결 명목(KRW) — projected-after-fill 분모


class RiskGate:
    """주문 단일 진입 사전검증 게이트."""

    def __init__(self, repo: OrdersRepo, *, fx=None) -> None:
        self._repo = repo
        self._fx = fx  # FxRateProvider | None — None 이면 FX 캡·노출 단계 skip(M2 호환)

    async def pre_check(
        self,
        intent: OrderIntent,
        *,
        engine_state: str,
        ctx: RiskContext | None = None,
    ) -> RiskDecision:
        ctx = ctx or RiskContext()
        bucket = bucket_of(intent.market)

        # 0. 런타임 EngineState 단일 권위(§0.2-3/§7.1) — ACTIVE 가 아니면(READ_ONLY/RECONCILING)
        #    주문 금지. config 의도(hanbit_engine_state)는 더 이상 게이트가 보지 않는다.
        if engine_state != EngineState.ACTIVE:
            return await self._finalize(
                intent, RiskResult.REJECT, ["ENGINE_NOT_ACTIVE"], "critical"
            )

        # EXIT reduce-only 사전분류(§5.5) — intent 는 호출자 입력이라 신뢰하지 않는다.
        reclassified = False
        effective_exit = False
        if intent.intent == IntentKind.EXIT:
            effective_exit = self._reduce_only_ok(ctx.positions, intent)
            reclassified = not effective_exit  # 증가/미보유 EXIT → ENTRY 전체 게이트

        # 1. 킬스위치/halt + HALTED_DAILY — 신규/재분류 ENTRY 차단, 유효 EXIT 예외.
        if not effective_exit:
            rs = await self._repo.get_risk_state(bucket) if bucket else None
            daily = rs.get("halt_state") if rs else None
            if await is_blocked(self._repo, intent.market):
                reason = "HALTED_DAILY" if daily == "halted_daily" else "KILL_SWITCH"
                return await self._finalize(
                    intent, RiskResult.REJECT, [reason], "critical", reclassified
                )
            if daily == "halted_daily":
                return await self._finalize(
                    intent, RiskResult.REJECT, ["HALTED_DAILY"], "critical", reclassified
                )
            if daily == "killed":
                return await self._finalize(
                    intent, RiskResult.REJECT, ["KILL_SWITCH"], "critical", reclassified
                )
            # quarantine 노출 차단(§7.1): 미해소 격리 주문이 있으면 캡/일일손실이 그 명목을
            # 못 보므로 신규 ENTRY 금지(READ_ONLY 동급). 감축 EXIT 만 허용(위 effective_exit).
            if bucket and await self._repo.has_quarantined(markets_of(bucket)):
                return await self._finalize(
                    intent, RiskResult.REJECT, ["QUARANTINED"], "critical", reclassified
                )

        # 2. 모드/HKEX 안전가드 (하드, in-gate 이중방어) — ★EXIT 비우회★.
        if intent.market != MARKET_OVERSEAS_FUTUREOPTION:
            return await self._finalize(
                intent, RiskResult.REJECT, ["LIVE_DISABLED"], "critical", reclassified
            )
        if trading_mode_of(intent.market) != TRADING_MODE_PAPER:
            return await self._finalize(
                intent, RiskResult.REJECT, ["MODE_MISMATCH"], "critical", reclassified
            )
        if intent.exchange != "HKEX" or not await self._repo.is_whitelisted(
            intent.market, intent.symbol
        ):
            return await self._finalize(
                intent, RiskResult.REJECT, ["FUT_NOT_HKEX"], "critical", reclassified
            )

        # 유효 EXIT(reduce-only)는 위험감축 — 캡/노출/한도 skip(시장가드는 위에서 이미 통과).
        if effective_exit:
            return await self._finalize(intent, RiskResult.PASS, [], "info")

        # 3-7. 한도(ENTRY/재분류) — 누적 판정.
        limits = await RiskLimits.load(self._repo, intent.market)
        ccy = intent.currency or "USD"
        rejects: list[str] = []
        warns: list[str] = []

        # 3. 과대주문(계약수)
        if intent.qty > limits.max_contracts_per_order:
            rejects.append("MAX_CONTRACTS")

        # 4. 명목 캡 — 통화중립 bucket_notional_cap + FX ceil per_order_cap_krw(§5.2)
        notional = notional_in_ccy(intent.qty, intent.price, ctx.multiplier)
        notional_krw = None
        if notional is not None:
            if notional > limits.bucket_notional_cap:
                rejects.append("BUCKET_NOTIONAL_CAP")
            if self._fx is not None:
                if not self._fx.supports(ccy):
                    # 미지원 통화 → 1:1 환산은 캡 과소산정(누수) → 하드 거부(리뷰 #16).
                    rejects.append("FX_UNKNOWN_CCY")
                else:
                    notional_krw, est = notional_krw_ceil(notional, ccy, self._fx)
                    if notional_krw > limits.per_order_cap_krw:
                        rejects.append("PER_ORDER_CAP_KRW")
                    if est:
                        warns.append("FX_ESTIMATED")

        # 5. 노출/집중도 INV-7 (projected-after-fill, §5.4) — 빈 책이면 skip(부트스트랩).
        if self._fx is not None and ctx.positions and notional is not None:
            nrate, _ = self._fx.to_krw(ccy)  # add_eval = 중립 환율
            add_eval_krw = notional * nrate
            items = [
                {
                    "symbol": p.get("symbol"),
                    "eval_krw": p.get("eval_krw") or 0.0,
                    "market": p.get("market"),
                    "currency": p.get("currency"),
                }
                for p in ctx.positions
            ]
            weights = projected_weights(
                items,
                add_eval_krw=add_eval_krw,
                symbol=intent.symbol,
                market=intent.market,
                currency=ccy,
                committed_krw=ctx.committed_krw,
            )
            rejects.extend(exposure_violations(weights, limits))

        # 6. max_positions(신규 종목 추가 시) / 동시 미체결 수
        held = {p.get("symbol") for p in ctx.positions}
        if intent.symbol not in held and len(held) >= limits.max_positions:
            rejects.append("MAX_POSITIONS")
        if ctx.open_orders_count >= limits.max_open_orders:
            rejects.append("MAX_OPEN_ORDERS")

        # 7. orderable (best-effort) — 헤드룸은 KRW floor 환산(보수=작게, §6 방향표).
        if ctx.orderable_amount is not None and notional is not None:
            if self._fx is not None and self._fx.supports(ccy):
                # 가용잔고는 floor(작게), 주문 명목은 ceil(크게=위 notional_krw) → 이중 보수.
                frate, _ = self._fx.to_krw_floor(ccy)
                orderable_krw = ctx.orderable_amount * frate
                cost_krw = notional_krw if notional_krw is not None else notional
                if cost_krw > orderable_krw:
                    rejects.append("INSUFFICIENT_ORDERABLE")
            elif notional > ctx.orderable_amount:
                # FX 미주입(M2 단위테스트) → 동통화 가정 직접 비교(기존 동작 보존).
                rejects.append("INSUFFICIENT_ORDERABLE")
        elif ctx.orderable_amount is None:
            warns.append("ORDERABLE_UNKNOWN")  # 데이터 없음 → WARN 통과(M3b 강화)

        if rejects:
            return await self._finalize(
                intent, RiskResult.REJECT, rejects + warns, "high", reclassified
            )
        if warns:
            return await self._finalize(intent, RiskResult.WARN, warns, "low", reclassified)
        return await self._finalize(intent, RiskResult.PASS, [], "info", reclassified)

    # ── 정정 노출 재검증 (§7.1 — M3a 연기분 ②) ────────────────────────────
    async def check_amend(
        self,
        *,
        market: str,
        symbol: str,
        currency: str | None,
        new_qty: int,
        new_price: float | None,
        ctx: RiskContext | None = None,
    ) -> RiskDecision:
        """정정 후 명목/노출 재검증. 엔진/halt/모드는 OrderService._get_mutable 가 이미 확정.

        정정은 노출을 키울 수 있으므로 신규주문과 동일한 과대주문·명목 캡(per_order_cap_krw·
        bucket_notional_cap, FX ceil)·INV-7 노출캡을 **정정 후 값**으로 재적용한다.
        호출자는 ctx.committed_krw 에서 **이 주문의 기존 명목을 제외**해 이중계상을 막는다.
        fx 미주입 시 캡/노출 단계는 skip(M2 호환).
        """
        ctx = ctx or RiskContext()
        limits = await RiskLimits.load(self._repo, market)
        ccy = currency or "USD"
        rejects: list[str] = []
        warns: list[str] = []

        if new_qty > limits.max_contracts_per_order:
            rejects.append("MAX_CONTRACTS")

        notional = notional_in_ccy(new_qty, new_price, ctx.multiplier)
        if notional is not None:
            if notional > limits.bucket_notional_cap:
                rejects.append("BUCKET_NOTIONAL_CAP")
            if self._fx is not None:
                if not self._fx.supports(ccy):
                    rejects.append("FX_UNKNOWN_CCY")
                else:
                    notional_krw, est = notional_krw_ceil(notional, ccy, self._fx)
                    if notional_krw > limits.per_order_cap_krw:
                        rejects.append("PER_ORDER_CAP_KRW")
                    if est:
                        warns.append("FX_ESTIMATED")
            if self._fx is not None and ctx.positions:
                nrate, _ = self._fx.to_krw(ccy)
                add_eval_krw = notional * nrate
                items = [
                    {
                        "symbol": p.get("symbol"),
                        "eval_krw": p.get("eval_krw") or 0.0,
                        "market": p.get("market"),
                        "currency": p.get("currency"),
                    }
                    for p in ctx.positions
                ]
                weights = projected_weights(
                    items,
                    add_eval_krw=add_eval_krw,
                    symbol=symbol,
                    market=market,
                    currency=ccy,
                    committed_krw=ctx.committed_krw,
                )
                rejects.extend(exposure_violations(weights, limits))

        result = (
            RiskResult.REJECT if rejects else (RiskResult.WARN if warns else RiskResult.PASS)
        )
        reasons = rejects + warns
        await self._repo.insert_audit(
            actor="risk_gate",
            action="amend_check",
            target=symbol,
            detail={"market": market, "new_qty": new_qty, "new_price": new_price,
                    "result": result.value, "reasons": reasons},
        )
        if result != RiskResult.PASS:
            await self._repo.insert_risk_event(
                event_type=f"amend_check_{result.value}",
                severity="high" if rejects else "low",
                scope=market,
                scope_ref=symbol,
                message=",".join(reasons) or result.value,
                detail={"reasons": reasons},
            )
        return RiskDecision(result=result, reasons=reasons)

    @staticmethod
    def _reduce_only_ok(positions: list[dict], intent: OrderIntent) -> bool:
        """EXIT 가 진짜 위험감축인가 — 보유 반대방향 + qty≤보유(§5.5)."""
        held = next((p for p in positions if p.get("symbol") == intent.symbol), None)
        if held is None:
            return False
        hqty = abs(float(held.get("qty") or 0))
        if hqty <= 0:
            return False
        side = held.get("position_side") or held.get("side")
        if side == "long" and intent.side == Side.SELL and intent.qty <= hqty:
            return True
        if side == "short" and intent.side == Side.BUY and intent.qty <= hqty:
            return True
        return False

    async def _finalize(
        self,
        intent: OrderIntent,
        result: RiskResult,
        reasons: list[str],
        severity: str,
        reclassified: bool = False,
    ) -> RiskDecision:
        await self._repo.insert_audit(
            actor="risk_gate",
            action="pre_check",
            target=intent.symbol,
            detail={
                "intent": intent.model_dump(mode="json"),
                "result": result.value,
                "reasons": reasons,
                "reclassified_entry": reclassified,
            },
        )
        if result != RiskResult.PASS:
            await self._repo.insert_risk_event(
                event_type=f"pre_check_{result.value}",
                severity=severity,
                scope=intent.market,
                scope_ref=intent.symbol,
                message=",".join(reasons) or result.value,
                detail={"reasons": reasons, "reclassified_entry": reclassified},
            )
        return RiskDecision(result=result, reasons=reasons, reclassified_entry=reclassified)
