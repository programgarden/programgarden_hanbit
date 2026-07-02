"""주문 서비스 (M2) — place/amend/cancel/reconcile/killswitch 단일 진입점.

모든 주문 경로는 (1) registry(FUT 만) (2) RiskGate 사전검증 (3) order_id 단일 writer 락을
거친다(INV-5). reconcile(CIDBQ02400/CIDBQ01500)이 체결 추적 권위 경로. 실시간(TC1/2/3)은 미배선.
auto-retry 금지: place 예외는 in_doubt 로 두고 reconcile 로만 종결(PLAN §5.4).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.adapters.order_base import OrderError
from app.adapters.order_registry import make_order_adapter
from app.adapters.overseas_future_order import TR_AMEND, TR_CANCEL, TR_NEW
from app.core.engine_state import EngineState
from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION, bucket_of, markets_of
from app.core.tr_queue import AccountTrQueue, TrPriority
from app.models.order_dto import (
    TERMINAL_STATES,
    AmendRequest,
    CancelRequest,
    IntentKind,
    OrderIntent,
    OrderState,
    Side,
)
from app.orders.fill_tracker import open_order_to_fill
from app.orders.state_machine import OrderLocks
from app.portfolio.fx import FxRateProvider
from app.repositories.orders_repo import OrdersRepo
from app.risk.gate import RiskContext, RiskGate
from app.risk.halt import is_blocked

if TYPE_CHECKING:
    from app.config import Settings
    from app.core.event_bus import EventBus
    from app.core.sessions import SessionManager

_NON_TERMINAL_FOR_CANCEL = {
    OrderState.SUBMITTED,
    OrderState.ACCEPTED,
    OrderState.PARTIALLY_FILLED,
    OrderState.IN_DOUBT,
}


class OrderService:
    """주문 파이프라인 오케스트레이션."""

    def __init__(
        self,
        repo: OrdersRepo,
        session: SessionManager,
        settings: Settings,
        *,
        event_bus: EventBus | None = None,
        locks: OrderLocks | None = None,
        engine: EngineState | None = None,
        tr_queue: AccountTrQueue | None = None,
    ) -> None:
        self._repo = repo
        self._session = session
        self._settings = settings
        self._bus = event_bus
        self._locks = locks or OrderLocks()
        # 계좌-TR 직렬 큐(§8) — reconcile 의 계좌 TR(CIDBQ01500/02400)이 경유한다. 버킷별
        # 직렬 + 우선순위(킬스위치>부트>routine) + 호출건수초과 backoff. 향후 aggregator
        # 폴링 TR 도 반드시 이 큐를 경유해야 한다(tracker 콜백 push 경로는 호출건수 무관).
        self._tr = tr_queue or AccountTrQueue.from_settings(settings)
        self._fx = FxRateProvider.from_settings(settings, repo)
        self._gate = RiskGate(repo, fx=self._fx)
        # 런타임 EngineState 단일 권위(§0.2-3) — place/amend/cancel 이 이걸 본다. 초기값은
        # config 의도에서 도출(PAPER_TRADING→ACTIVE=빈 책 부트 결과). 운영 부팅은 boot_engine
        # 이 이 위에서 READ_ONLY→RECONCILING→ACTIVE/quarantine 를 재구동한다(app/orders/boot.py).
        self._engine = engine or EngineState.from_config(settings)

    @property
    def engine(self) -> EngineState:
        return self._engine

    @property
    def locks(self) -> OrderLocks:
        """order_id 단일 writer 락 — 실시간 체결 소스가 동일 인스턴스를 공유해야 한다(§10)."""
        return self._locks

    @property
    def tr_queue(self) -> AccountTrQueue:
        return self._tr

    @staticmethod
    def _tr_priority(scope: str) -> TrPriority:
        """reconcile scope → 계좌-TR 큐 우선순위(§8). 킬스위치>부트>routine."""
        if scope.startswith("kill"):
            return TrPriority.KILL
        if scope.startswith("boot"):
            return TrPriority.BOOT
        return TrPriority.ROUTINE

    # ── 견적/미리보기 (주문 미발사) ───────────────────────────────────────
    async def preview(self, intent: OrderIntent):
        """리스크 게이트만 돌려 판정 미리보기(주문 발사 안 함)."""
        ctx = await self._build_ctx(intent)
        return await self._gate.pre_check(
            intent, engine_state=self._engine.state, ctx=ctx
        )

    # ── 신규 ─────────────────────────────────────────────────────────────
    async def place(self, intent: OrderIntent) -> dict:
        ctx = await self._build_ctx(intent)
        decision = await self._gate.pre_check(
            intent, engine_state=self._engine.state, ctx=ctx
        )
        if not decision.ok:
            await self._repo.incr_metric("orders_rejected")
            await self._publish(
                "risk_event",
                {
                    "result": decision.result.value,
                    "reasons": decision.reasons,
                    "symbol": intent.symbol,
                },
            )
            return {"ok": False, "decision": decision.model_dump(mode="json")}

        acct = await self._repo.get_account_id(intent.market)
        inst = await self._repo.ensure_instrument(
            intent.market, intent.symbol, exchange=intent.exchange
        )
        cid = intent.client_order_id or self._gen_cid(intent)
        order_id, created = await self._repo.insert_order(
            idempotency_key=cid,
            account_id=acct,
            instrument_id=inst,
            market=intent.market,
            trading_mode="paper",
            side=intent.side.value,
            order_type=intent.order_type.value,
            qty=intent.qty,
            price=intent.price,
            exchange=intent.exchange,
            currency=intent.currency,
            # 재분류된 EXIT(증가/미보유 → ENTRY)는 'open'(리뷰 #8 — close 오기록 방지).
            position_effect=(
                "close"
                if intent.intent == IntentKind.EXIT and not decision.reclassified_entry
                else "open"
            ),
            tr_code=TR_NEW,
            relation="new",
            strategy_id=intent.strategy_id,
            status=OrderState.APPROVED.value,
        )
        if not created:
            # 멱등: 동일 client_order_id 재요청 → 기존 주문 반환(브로커 재전송 안 함).
            return {"ok": True, "idempotent": True, "order": await self._repo.get_order(order_id)}

        adapter = make_order_adapter(intent.market, self._session)
        async with self._locks.lock(order_id):
            await self._repo.transition(order_id, OrderState.SUBMITTED, "tr_response")
            try:
                ack = await adapter.place_order(intent)
            except Exception as exc:  # noqa: BLE001 — 미확정 → in_doubt(재시도 금지)
                await self._repo.transition(
                    order_id, OrderState.IN_DOUBT, "tr_response",
                    updates={"error_msg": str(exc)[:500]},
                )
                await self._publish("orders", {"order_id": order_id, "state": "in_doubt"})
                doubt = await self._repo.get_order(order_id)
                return {"ok": False, "in_doubt": True, "order": doubt}

            if ack.ok:
                await self._repo.transition(
                    order_id, OrderState.ACCEPTED, "tr_response",
                    updates={"broker_order_id": ack.broker_ord_no, "rsp_cd": ack.rsp_cd},
                )
                await self._repo.incr_metric("orders_placed")
            else:
                await self._repo.transition(
                    order_id, OrderState.REJECTED, "tr_response",
                    updates={
                        "reject_reason": ack.error_msg or ack.rsp_cd,
                        "rsp_cd": ack.rsp_cd,
                        "error_msg": ack.error_msg,
                    },
                )
                await self._repo.incr_metric("orders_rejected")

        order = await self._repo.get_order(order_id)
        await self._publish("orders", {"order_id": order_id, "state": order["status"]})
        return {"ok": ack.ok, "order": order, "ack": ack.model_dump(mode="json")}

    # ── 정정 / 취소 (게이트 경유) ─────────────────────────────────────────
    async def amend(self, order_id: int, *, qty: int, price: float) -> dict:
        # _get_mutable 가 시장/엔진(런타임 ACTIVE)/OrdNo + halt(killswitch+일일손실) 가드를,
        # check_amend 가 정정 후 명목 캡(per_order_cap_krw)/INV-7 노출을 재검증한다(§7.1, 리뷰 #7).
        order = await self._get_mutable(order_id, action="amend")
        symbol = await self._symbol_of(order)
        amend_ctx = await self._build_amend_ctx(order, symbol)
        decision = await self._gate.check_amend(
            market=order["market"],
            symbol=symbol,
            currency=order.get("currency"),
            new_qty=qty,
            new_price=price,
            ctx=amend_ctx,
        )
        if not decision.ok:
            raise OrderError(
                "AMEND_REJECTED", ",".join(decision.reasons) or "amend risk rejected"
            )
        adapter = make_order_adapter(order["market"], self._session)  # FUT 외면 LIVE_DISABLED
        ack = await adapter.amend_order(
            AmendRequest(
                org_ord_no=order["broker_order_id"],
                symbol=symbol,
                side=Side(order["side"]),
                qty=qty,
                price=price,
                exchange=order["exchange"] or "HKEX",
                due_yymm=order.get("due_yymm"),
                currency=order.get("currency"),
            )
        )
        child = await self._record_child(order, "modify", ack, qty=qty, price=price)
        if ack.ok:
            # 원주문이 새 OrdNo 로 working 유지(잔량 승계).
            await self._repo.update_order_fields(
                order_id, broker_order_id=ack.broker_ord_no, qty=qty, price=price
            )
        await self._publish("orders", {"order_id": order_id, "action": "amend", "ok": ack.ok})
        return {"ok": ack.ok, "ack": ack.model_dump(mode="json"), "child_order_id": child}

    async def cancel(self, order_id: int, *, risk_reduction: bool = False) -> dict:
        # risk_reduction=True 는 킬스위치 L1 의 위험감축 취소(§8 우선 레인) — 멱등·노출감소라
        # 런타임 ACTIVE 를 요구하지 않는다(boot 실패/RECONCILING 에도 취소가 막히면 안 됨).
        order = await self._get_mutable(order_id, action="cancel", risk_reduction=risk_reduction)
        adapter = make_order_adapter(order["market"], self._session)
        symbol = await self._symbol_of(order)
        ack = await adapter.cancel_order(
            CancelRequest(
                org_ord_no=order["broker_order_id"],
                symbol=symbol,
                exchange=order["exchange"] or "HKEX",
            )
        )
        await self._record_child(order, "cancel", ack)
        if ack.ok:
            async with self._locks.lock(order_id):
                cur = await self._repo.get_order(order_id)
                if OrderState(cur["status"]) in _NON_TERMINAL_FOR_CANCEL:
                    await self._repo.transition(order_id, OrderState.CANCELED, "manual")
        await self._publish("orders", {"order_id": order_id, "action": "cancel", "ok": ack.ok})
        return {"ok": ack.ok, "ack": ack.model_dump(mode="json")}

    # ── 킬스위치 레벨1: 미체결 일괄취소(guarded cancel 재사용) ─────────────
    async def cancel_all_open(self, *, reason: str = "kill_switch") -> dict:
        open_orders = [
            o
            for o in await self._repo.list_open_orders()
            if o["market"] == MARKET_OVERSEAS_FUTUREOPTION
        ]
        # in_doubt 는 취소 전 reconcile-우선(비존재/기체결 취소 방지)
        if any(o["status"] == OrderState.IN_DOUBT.value for o in open_orders):
            await self.reconcile(scope="kill_switch_precancel")
            open_orders = [
                o
                for o in await self._repo.list_open_orders()
                if o["market"] == MARKET_OVERSEAS_FUTUREOPTION
            ]
        canceled = 0
        for o in open_orders:
            if not o.get("broker_order_id"):
                continue  # OrdNo 없는 in_doubt → 취소 불가(reconcile 대상)
            try:
                # 위험감축 lane(§8 우선 레인) — 엔진상태 우회. boot 실패/RECONCILING 에도
                # 미체결 취소가 막히면 안 된다(단계6 연기분 흡수).
                res = await self.cancel(o["id"], risk_reduction=True)
                canceled += 1 if res["ok"] else 0
            except OrderError as exc:
                # §0.2-4: paper 취소 루프에서 LIVE_DISABLED 는 "스킵"이 아니라 라우팅
                # 버그(critical) — 삼키지 않고 전파한다. 그 외(NO_BROKER_ORDNO 등)는 다음으로.
                if exc.code == "LIVE_DISABLED":
                    raise
                continue
        return {"canceled": canceled, "reason": reason}

    # ── reconcile (CIDBQ02400 종목별 + CIDBQ01500 포지션) ───────────────────
    async def reconcile(self, *, scope: str = "manual", market_closed: bool = False) -> dict:
        run_id = await self._repo.start_reconcile_run(scope)
        adapter = make_order_adapter(MARKET_OVERSEAS_FUTUREOPTION, self._session)
        # 계좌 TR 은 §8 직렬 큐 경유 — boot(elevated)/kill(최상위)/routine 우선순위 분기.
        # ⚠ 락순서 불변식: 큐 submit 은 order 락 바깥에서만(아래 fetch 들은 전부 락 밖).
        bucket = bucket_of(MARKET_OVERSEAS_FUTUREOPTION)
        prio = self._tr_priority(scope)
        non_terminal = [
            o
            for o in await self._repo.list_open_orders()
            if o["market"] == MARKET_OVERSEAS_FUTUREOPTION
        ]
        acct = await self._repo.get_account_id(MARKET_OVERSEAS_FUTUREOPTION)

        found = resolved = unresolved = 0
        # 종목별 그룹 → 종목당 1회 조회(CIDBQ02400 은 IsuCodeVal 필수)
        by_symbol: dict[str, list[dict]] = {}
        for o in non_terminal:
            sym = await self._symbol_of(o)
            by_symbol.setdefault(sym, []).append(o)

        known_ordnos: set[str] = set()
        for sym, orders in by_symbol.items():
            broker_rows = await self._tr.submit(
                bucket, prio, lambda s=sym: adapter.get_open_orders(s),
                label=f"get_open_orders:{sym}",
            )
            rows_by_ord: dict[str, list] = {}
            for r in broker_rows:
                rows_by_ord.setdefault(r.broker_ord_no, []).append(r)
                known_ordnos.add(r.broker_ord_no)

            for o in orders:
                ordno = o.get("broker_order_id")
                if not ordno:
                    unresolved += 1  # OrdNo 없는 in_doubt — 매칭 불가
                    continue
                matched = rows_by_ord.get(ordno, [])
                if not matched:
                    # 브로커에 없음(소멸) → 장마감이면 expired, 아니면 canceled
                    found += 1
                    to = OrderState.EXPIRED if market_closed else OrderState.CANCELED
                    async with self._locks.lock(o["id"]):
                        cur = await self._repo.get_order(o["id"])
                        if OrderState(cur["status"]) not in TERMINAL_STATES:
                            await self._repo.transition(o["id"], to, "reconcile")
                            resolved += 1
                    continue
                # 체결 적용
                applied_any = False
                async with self._locks.lock(o["id"]):
                    for r in matched:
                        fill = open_order_to_fill(r)
                        if fill and await self._repo.apply_fill(o["id"], fill, trigger="reconcile"):
                            applied_any = True
                    # 무체결인데 장마감 → expired
                    if not applied_any and market_closed:
                        cur = await self._repo.get_order(o["id"])
                        if OrderState(cur["status"]) == OrderState.ACCEPTED:
                            await self._repo.transition(o["id"], OrderState.EXPIRED, "reconcile")
                            applied_any = True
                if applied_any:
                    found += 1
                    resolved += 1

            # orphan: 브로커엔 있는데 DB 가 모르는 OrdNo
            db_ordnos = {o.get("broker_order_id") for o in orders}
            for ordno, rows in rows_by_ord.items():
                if ordno in db_ordnos:
                    continue
                found += 1
                if await self._register_orphan(acct, sym, ordno, rows):
                    resolved += 1

        # 포지션 동기화 — reconcile=권위 writer(qty/avg/방향). 보강(가격/eval/fx)은 집계기(§4.2).
        # ⚠ boot(§7.1)는 이 동기화 성공을 ACTIVE 진입 전제로 본다 — 실패를 swallow 하되
        #   position_sync_ok 플래그로 호출자에 알린다(boot 는 실패 시 READ_ONLY 유지).
        position_sync_ok = True
        try:
            positions = await self._tr.submit(
                bucket, prio, adapter.get_positions, label="get_positions",
            )
            for p in positions:
                if acct is None:
                    break  # 계좌 앵커 없으면 FK 위반 — 동기화 skip(dev)
                inst = await self._repo.ensure_instrument(
                    MARKET_OVERSEAS_FUTUREOPTION, p.symbol, exchange="HKEX"
                )
                side = "long" if p.side == Side.BUY else ("short" if p.side == Side.SELL else None)
                await self._repo.upsert_position_authority(
                    acct,
                    inst,
                    bucket=bucket,
                    market=MARKET_OVERSEAS_FUTUREOPTION,
                    currency=p.currency or "USD",
                    position_side=side,
                    qty=p.qty,
                    avg_price=p.avg_price,
                )
        except OrderError:
            position_sync_ok = False

        await self._repo.finish_reconcile_run(
            run_id, found=found, resolved=resolved, unresolved=unresolved
        )
        if found:
            await self._repo.incr_metric("reconcile_diffs", by=found)
        await self._publish("orders", {"reconcile": scope, "found": found, "resolved": resolved})
        return {
            "found": found,
            "resolved": resolved,
            "unresolved": unresolved,
            "position_sync_ok": position_sync_ok,
        }

    async def _register_orphan(self, acct: int, symbol: str, ordno: str, rows: list) -> bool:
        inst = await self._repo.ensure_instrument(
            MARKET_OVERSEAS_FUTUREOPTION, symbol, exchange="HKEX"
        )
        first = rows[0]
        side = first.side.value if first.side else "buy"
        order_id, created = await self._repo.insert_order(
            idempotency_key=f"recon:{ordno}",  # NOT NULL + 멱등(재조회 시 충돌→기존)
            account_id=acct,
            instrument_id=inst,
            market=MARKET_OVERSEAS_FUTUREOPTION,
            trading_mode="paper",
            side=side,
            order_type="limit",
            qty=int(first.qty or 0) or 1,
            price=first.price,
            exchange="HKEX",
            tr_code=TR_NEW,
            relation="new",
            broker_order_id=ordno,
            reconcile_key=f"recon:{ordno}",
            status=OrderState.ACCEPTED.value,
        )
        if not created:
            return False
        # 체결분 반영
        async with self._locks.lock(order_id):
            for r in rows:
                fill = open_order_to_fill(r)
                if fill:
                    await self._repo.apply_fill(order_id, fill, trigger="reconcile")
        return True

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────
    async def _get_mutable(
        self, order_id: int, *, action: str, risk_reduction: bool = False
    ) -> dict:
        order = await self._repo.get_order(order_id)
        if order is None:
            raise OrderError("NOT_FOUND", f"order {order_id} not found")
        # 시장 가드 — paper FUT 만 mutate. 위험감축 경로라도 비우회(§0.2-4 LIVE_DISABLED 미삼킴).
        if order["market"] != MARKET_OVERSEAS_FUTUREOPTION:
            raise OrderError("LIVE_DISABLED", f"market '{order['market']}' order path disabled")
        # 런타임 EngineState 단일 권위(§7.1) — config 판독 폐기. ACTIVE 가 아니면 정정/취소 금지.
        # 단, 위험감축 취소(killswitch L1·§8 우선 레인)는 멱등·노출감소라 ACTIVE 비요구 —
        # boot 실패/RECONCILING 에도 취소가 막히면 안 된다(단계6 연기분 흡수).
        if not risk_reduction and self._engine.state != EngineState.ACTIVE:
            raise OrderError("ENGINE_NOT_ACTIVE", f"engine is {self._engine.state}")
        if not order.get("broker_order_id"):
            raise OrderError("NO_BROKER_ORDNO", "order has no OrdNo to amend/cancel")
        # 정정은 halt 시 차단(노출 증가 가능), 취소는 위험감축이라 허용.
        # killswitch(trading_halt) + **일일손실 halt(risk_state)** 둘 다 본다 — daily-loss 는
        # trading_halt 에 미러되지 않으므로 is_blocked 만으론 amend 우회 가능(리뷰 #1).
        if action == "amend":
            if await is_blocked(self._repo, order["market"]):
                raise OrderError("KILL_SWITCH", "bucket halted")
            bucket = bucket_of(order["market"])
            rs = await self._repo.get_risk_state(bucket) if bucket else None
            halt_state = rs.get("halt_state") if rs else None
            if halt_state == "halted_daily":
                raise OrderError("HALTED_DAILY", "bucket halted (daily loss)")
            if halt_state == "killed":
                raise OrderError("KILL_SWITCH", "bucket killed")
        return order

    async def _record_child(self, parent: dict, relation: str, ack, **fields) -> int | None:
        cid = f"{parent['idempotency_key']}:{relation}:{uuid.uuid4().hex[:8]}"
        order_id, _ = await self._repo.insert_order(
            idempotency_key=cid,
            account_id=parent["account_id"],
            instrument_id=parent["instrument_id"],
            market=parent["market"],
            trading_mode="paper",
            side=parent["side"],
            order_type=parent["order_type"],
            qty=fields.get("qty", parent["qty"]),
            price=fields.get("price", parent.get("price")),
            exchange=parent.get("exchange"),
            tr_code=TR_AMEND if relation == "modify" else TR_CANCEL,
            relation=relation,
            parent_order_id=parent["id"],
            # child 는 감사 행 — broker_order_id 는 비운다(작업대상 OrdNo 의 유니크는 부모 소유).
            broker_org_ord_no=parent.get("broker_order_id"),
            rsp_cd=ack.rsp_cd,
            error_msg=ack.error_msg,
            status=OrderState.ACCEPTED.value if ack.ok else OrderState.REJECTED.value,
        )
        return order_id

    async def _symbol_of(self, order: dict) -> str:
        inst = await self._repo.get_instrument_by_id(order["instrument_id"])
        if inst is None:
            raise OrderError("NO_INSTRUMENT", "instrument not found")
        return inst["symbol"]

    async def _build_ctx(self, intent: OrderIntent) -> RiskContext:
        # 버킷-스코프 조회(§3) — 책-전체 list_open_orders + 수동 필터 대신 markets 인자 필수화.
        bucket = bucket_of(intent.market)
        markets = markets_of(bucket) if bucket else (intent.market,)
        open_orders = await self._repo.open_orders_for(markets)
        inst = await self._repo.get_instrument(intent.market, intent.symbol)
        multiplier = inst.get("multiplier") if inst else None
        positions = await self._repo.positions_for(bucket) if bucket else []
        ccy = intent.currency or "USD"
        committed_krw = self._committed_krw(open_orders, multiplier, ccy)
        # orderable 은 place 단계에서 라이브 조회 안 함(best-effort) → reconcile/집계기가 채운
        # 잔고 스냅샷을 읽는다. 게이트가 KRW floor 환산해 헤드룸 비교(§6, item ③). 없으면 None.
        acct = await self._repo.get_account_id(intent.market)
        orderable = await self._repo.latest_orderable(acct, ccy) if acct is not None else None
        return RiskContext(
            multiplier=multiplier,
            orderable_amount=orderable,
            open_orders_count=len(open_orders),
            positions=positions,
            committed_krw=committed_krw,
        )

    async def _build_amend_ctx(self, order: dict, symbol: str) -> RiskContext:
        """정정 재검증용 컨텍스트(§7.1). committed_krw 에서 **이 주문의 기존 명목을 제외**해
        이중계상을 막는다(정정은 이 주문 명목을 새 값으로 대체하므로)."""
        market = order["market"]
        bucket = bucket_of(market)
        markets = markets_of(bucket) if bucket else (market,)
        open_orders = await self._repo.open_orders_for(markets)
        inst = await self._repo.get_instrument(market, symbol)
        multiplier = inst.get("multiplier") if inst else None
        positions = await self._repo.positions_for(bucket) if bucket else []
        ccy = order.get("currency") or "USD"
        others = [o for o in open_orders if o.get("id") != order["id"]]
        return RiskContext(
            multiplier=multiplier,
            open_orders_count=len(open_orders),
            positions=positions,
            committed_krw=self._committed_krw(others, multiplier, ccy),
        )

    def _committed_krw(self, open_orders: list[dict], multiplier, ccy: str) -> float:
        """살아있는 미체결 명목(KRW) — projected-after-fill 분모. 동질 paper 버킷이라 주문통화·
        승수 근사(중립 환율). LIVE 다시장 정밀화는 M4."""
        nrate, _ = self._fx.to_krw(ccy)
        return sum(
            float(o.get("qty") or 0) * float(o.get("price") or 0) * float(multiplier or 1) * nrate
            for o in open_orders
        )

    def _gen_cid(self, intent: OrderIntent) -> str:
        sid = intent.strategy_id or "manual"
        base = f"{sid}:{intent.market}:{intent.symbol}:{intent.side.value}:{intent.intent.value}"
        return f"{base}:{uuid.uuid4().hex[:10]}"

    async def _publish(self, topic: str, data: dict) -> None:
        if self._bus is not None:
            await self._bus.publish(topic, data)
