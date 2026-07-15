"""주문 서비스 (M2) — place/amend/cancel/reconcile/killswitch 단일 진입점.

모든 주문 경로는 (1) registry(FUT 만) (2) RiskGate 사전검증 (3) order_id 단일 writer 락을
거친다(INV-5). reconcile(CIDBQ02400/CIDBQ01500)이 체결 추적 권위 경로. 실시간(TC1/2/3)은 미배선.
auto-retry 금지: place 예외는 in_doubt 로 두고 reconcile 로만 종결(PLAN §5.4).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.adapters.order_base import OrderError
from app.adapters.order_registry import make_order_adapter, order_tr_labels
from app.core.engine_state import EngineState
from app.core.mode_matrix import (
    BUCKET_LIVE,
    BUCKET_PAPER,
    MARKET_KOREA_STOCK,
    MARKET_OVERSEAS_FUTUREOPTION,
    MARKET_OVERSEAS_STOCK,
    bucket_of,
    markets_of,
    trading_mode_of,
)
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

# 주문/정정/취소가 허용되는 시장 — paper FUT + LIVE 주식(KR/OVS). LIVE 는 allow_live 마스터
# 토글이 registry/게이트/_get_mutable 3중으로 추가 게이팅(§0.2). 그 외 시장은 LIVE_DISABLED.
_ORDERABLE_MARKETS = frozenset(
    {MARKET_OVERSEAS_FUTUREOPTION, MARKET_KOREA_STOCK, MARKET_OVERSEAS_STOCK}
)

# 시장별 기본 거래소/venue — 정정/취소 시 CancelRequest/AmendRequest 라우팅 메타 채움.
#   FUT=HKEX(어댑터가 심볼로 도출), KR=KRX(정규장), OVS=""(심볼 prefix 로 거래소 도출).
_DEFAULT_EXCHANGE = {
    MARKET_OVERSEAS_FUTUREOPTION: "HKEX",
    MARKET_KOREA_STOCK: "KRX",
    MARKET_OVERSEAS_STOCK: "",
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
        # 게이트에 settings 주입(§6/§17 L1-4): LIVE allow_live 마스터 + 소액 per-order 캡 단일출처.
        self._gate = RiskGate(repo, fx=self._fx, settings=settings)
        # 런타임 EngineState — **버킷별**(M4a §3.2, 단일 글로벌 폐기). place/amend/cancel/
        # 게이트 step0/실시간 writer 가 `engine_for(bucket_of(market))` 로 해당 버킷 상태를 읽는다.
        #   - paper 버킷 = 기존 config 의도(PAPER_TRADING→ACTIVE=빈 책 부트 결과). 주입된
        #     `engine`(단수)은 하위호환으로 paper 버킷에 매핑한다.
        #   - live 버킷  = READ_ONLY 로 시작(allow_live=true AND live reconcile 성공일 때만
        #     boot_engine 이 ACTIVE 전이 — M4a 기본 allow_live=false 면 영구 READ_ONLY).
        paper_engine = engine or EngineState.from_paper_config(settings)
        self._engines: dict[str, EngineState] = {
            BUCKET_PAPER: paper_engine,
            BUCKET_LIVE: EngineState.live_initial(settings),
        }
        # 하위호환 별칭 — `_engine`(단수)/`engine` property 는 paper 버킷을 가리킨다(deprecated).
        # 기존 코드/테스트(`svc._engine.set(...)`, main.py `order_service.engine`)가 계속 동작.
        self._engine = paper_engine

    def _allow_live(self) -> bool:
        """HANBIT_ALLOW_LIVE 마스터 토글(§2) — registry allow_live 게이트에 전달."""
        return bool(getattr(self._settings, "hanbit_allow_live", False))

    def engine_for(self, bucket: str | None) -> EngineState:
        """버킷→EngineState 단일 접근자(§3.2). 미지 시장(bucket None) → LIVE_DISABLED 거부.

        `engines.get(bucket)` None-가드로 `engines[None]` KeyError 를 방지한다(§17 L3-4,
        기존 `if bucket else` 관용구와 정합).
        """
        eng = self._engines.get(bucket) if bucket else None
        if eng is None:
            raise OrderError("LIVE_DISABLED", f"no engine for bucket {bucket!r}")
        return eng

    @property
    def engine(self) -> EngineState:
        """하위호환 property — paper 버킷 엔진(deprecated, `engine_for(bucket)` 사용)."""
        return self._engines[BUCKET_PAPER]

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
            intent,
            engine_state=self.engine_for(bucket_of(intent.market)).state,
            ctx=ctx,
        )

    # ── 신규 ─────────────────────────────────────────────────────────────
    async def place(self, intent: OrderIntent) -> dict:
        ctx = await self._build_ctx(intent)
        decision = await self._gate.pre_check(
            intent,
            engine_state=self.engine_for(bucket_of(intent.market)).state,
            ctx=ctx,
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
            trading_mode=trading_mode_of(intent.market) or "paper",
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
            tr_code=order_tr_labels(intent.market)[0],
            relation="new",
            strategy_id=intent.strategy_id,
            status=OrderState.APPROVED.value,
        )
        if not created:
            # 멱등: 동일 client_order_id 재요청 → 기존 주문 반환(브로커 재전송 안 함).
            return {"ok": True, "idempotent": True, "order": await self._repo.get_order(order_id)}

        adapter = make_order_adapter(intent.market, self._session, allow_live=self._allow_live())
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
        adapter = make_order_adapter(  # LIVE 는 allow_live 없으면 LIVE_DISABLED(registry 관문)
            order["market"], self._session, allow_live=self._allow_live()
        )
        ack = await adapter.amend_order(
            AmendRequest(
                org_ord_no=order["broker_order_id"],
                symbol=symbol,
                side=Side(order["side"]),
                qty=qty,
                price=price,
                exchange=order.get("exchange") or _DEFAULT_EXCHANGE.get(order["market"], "HKEX"),
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
        adapter = make_order_adapter(order["market"], self._session, allow_live=self._allow_live())
        symbol = await self._symbol_of(order)
        ack = await adapter.cancel_order(
            CancelRequest(
                org_ord_no=order["broker_order_id"],
                symbol=symbol,
                exchange=order.get("exchange") or _DEFAULT_EXCHANGE.get(order["market"], "HKEX"),
                # KR/OVS 취소는 수량 필수 → 원주문 미체결 잔량으로 채운다(over-cancel 회피, §4.3).
                # FUT 취소 TR 은 수량 미사용이라 None 이어도 무방.
                qty=self._remaining_qty(order),
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
    async def reconcile(
        self,
        *,
        scope: str = "manual",
        market_closed: bool = False,
        bucket: str | None = None,
    ) -> dict:
        # per-bucket 부트(§3.2): live 버킷은 KR/OVS list-all reconcile(§7), 그 외(paper/미지정)는
        # 기존 FUT 종목별 reconcile 을 수행한다.
        if bucket == BUCKET_LIVE:
            return await self._reconcile_live(scope=scope, market_closed=market_closed)
        return await self._reconcile_paper(scope=scope, market_closed=market_closed)

    async def _reconcile_paper(
        self, *, scope: str = "manual", market_closed: bool = False
    ) -> dict:
        run_id = await self._repo.start_reconcile_run(scope)
        adapter = make_order_adapter(
            MARKET_OVERSEAS_FUTUREOPTION, self._session, allow_live=self._allow_live()
        )
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
            tr_code=order_tr_labels(MARKET_OVERSEAS_FUTUREOPTION)[0],
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

    # ── live reconcile (KR/OVS list-all + 버킷별, §7) ──────────────────────
    async def _reconcile_live(self, *, scope: str = "manual", market_closed: bool = False) -> dict:
        """LIVE 버킷(KR/OVS) reconcile — 시장별 list-all(§7 C7).

        ⚠ **소멸판정 보류 fail-safe(§7 L4-1)**: KR/OVS 미체결 조회는 list-all(페이지네이션)이라
          단일 조회로는 전 페이지를 봤다고 보장할 수 없다. 따라서 브로커 스냅에 없는 DB 주문을
          **CANCELED/EXPIRED 로 전이하지 않는다**(살아있는 working 주문 오소멸 차단). 체결적용·
          orphan 등록·포지션 동기화만 수행한다. 2층 커서 페이지네이션 + 소멸판정은 라이브 검증
          ([L]) 후 Gate B 에서 확장한다.
        ⚠ 계좌 TR 은 §8 직렬 큐 경유 + 락순서 불변식(submit 은 order 락 밖).
        """
        if not self._allow_live():
            # 방어: allow_live=false 면 boot 이 애초에 호출하지 않지만, 직접 호출도 하드 차단.
            raise OrderError("LIVE_DISABLED", "live reconcile requires HANBIT_ALLOW_LIVE=true")
        run_id = await self._repo.start_reconcile_run(scope)
        prio = self._tr_priority(scope)
        bucket = BUCKET_LIVE
        found = resolved = unresolved = 0
        position_sync_ok = True

        for market in markets_of(bucket):
            adapter = make_order_adapter(market, self._session, allow_live=True)
            acct = await self._repo.get_account_id(market)
            non_terminal = [o for o in await self._repo.list_open_orders() if o["market"] == market]

            try:
                broker_rows = await self._tr.submit(
                    bucket, prio, lambda a=adapter: a.get_open_orders(""),
                    label=f"get_open_orders:{market}",
                )
            except OrderError:
                # 미인증/조회 실패 → 이 시장은 소멸판정 불가 → incomplete(READ_ONLY 유지 유도).
                position_sync_ok = False
                continue

            rows_by_ord: dict[str, list] = {}
            for r in broker_rows:
                if r.broker_ord_no:
                    rows_by_ord.setdefault(r.broker_ord_no, []).append(r)

            # 매칭된 DB 주문 체결적용(소멸판정은 fail-safe 로 skip).
            for o in non_terminal:
                ordno = o.get("broker_order_id")
                if not ordno:
                    unresolved += 1  # OrdNo 없는 in_doubt — 매칭 불가
                    continue
                matched = rows_by_ord.get(ordno, [])
                if not matched:
                    continue  # ★ fail-safe: 소멸 전이 안 함(단일 페이지 미완결 가능)
                async with self._locks.lock(o["id"]):
                    for r in matched:
                        fill = open_order_to_fill(r)
                        if fill and await self._repo.apply_fill(o["id"], fill, trigger="reconcile"):
                            found += 1
                            resolved += 1

            # orphan: 브로커엔 있는데 DB 가 모르는 OrdNo. 단, **정정 후속(OrgOrdNo 체인)은 제외**
            #   (§7 L4-3 — list-all 은 정정 직후 구/신 OrdNo 공존 → 구 OrdNo 오등록 방지).
            if acct is not None:
                db_ordnos = {o.get("broker_order_id") for o in non_terminal}
                for ordno, rows in rows_by_ord.items():
                    if ordno in db_ordnos:
                        continue
                    org = rows[0].org_ord_no
                    if org and await self._repo.get_order_by_broker(acct, org) is not None:
                        continue  # 정정 후속(부모가 DB 에 있음) — orphan 아님
                    found += 1
                    if await self._register_orphan_live(acct, market, ordno, rows):
                        resolved += 1

            # 포지션 동기화 — reconcile=권위 writer(시장/버킷/통화 라우팅, 교차 upsert 금지).
            try:
                positions = await self._tr.submit(
                    bucket, prio, adapter.get_positions, label=f"get_positions:{market}",
                )
                for p in positions:
                    if acct is None:
                        break
                    exchange = _DEFAULT_EXCHANGE.get(market) or None
                    inst = await self._repo.ensure_instrument(market, p.symbol, exchange=exchange)
                    side = (
                        "long" if p.side == Side.BUY
                        else ("short" if p.side == Side.SELL else None)
                    )
                    await self._repo.upsert_position_authority(
                        acct,
                        inst,
                        bucket=bucket,
                        market=market,
                        currency=p.currency or ("KRW" if market == MARKET_KOREA_STOCK else "USD"),
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

    async def _register_orphan_live(
        self, acct: int, market: str, ordno: str, rows: list
    ) -> bool:
        """LIVE(KR/OVS) orphan 등록 — 시장 스코프 멱등키(교차 충돌 방지)."""
        exchange = _DEFAULT_EXCHANGE.get(market) or None
        inst = await self._repo.ensure_instrument(market, rows[0].symbol, exchange=exchange)
        first = rows[0]
        side = first.side.value if first.side else "buy"
        key = f"recon:{market}:{ordno}"  # ⚠ 시장 스코프 — OrdNo 일자 리셋 date-scope 는 [L]/Gate B
        order_id, created = await self._repo.insert_order(
            idempotency_key=key,
            account_id=acct,
            instrument_id=inst,
            market=market,
            trading_mode=trading_mode_of(market) or "live",
            side=side,
            order_type="limit",
            qty=int(first.qty or 0) or 1,
            price=first.price,
            exchange=exchange or "",
            currency=("KRW" if market == MARKET_KOREA_STOCK else "USD"),
            tr_code=order_tr_labels(market)[0],
            relation="new",
            broker_order_id=ordno,
            reconcile_key=key,
            status=OrderState.ACCEPTED.value,
        )
        if not created:
            return False
        async with self._locks.lock(order_id):
            for r in rows:
                fill = open_order_to_fill(r)
                if fill:
                    await self._repo.apply_fill(order_id, fill, trigger="reconcile")
        return True

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────
    @staticmethod
    def _remaining_qty(order: dict) -> int | None:
        """취소 수량 = 원주문 미체결 잔량(over-cancel 회피, §4.3). None=전량(FUT 무시)."""
        rem = order.get("remaining_qty")
        if rem is None:
            rem = float(order.get("qty") or 0) - float(order.get("filled_qty") or 0)
        try:
            r = int(rem)
        except (TypeError, ValueError):
            return None
        return r if r > 0 else None

    async def _get_mutable(
        self, order_id: int, *, action: str, risk_reduction: bool = False
    ) -> dict:
        order = await self._repo.get_order(order_id)
        if order is None:
            raise OrderError("NOT_FOUND", f"order {order_id} not found")
        market = order["market"]
        # 시장 가드 — paper FUT + LIVE 주식(KR/OVS)만 mutate. 위험감축 경로라도 비우회
        # (§0.2-4 LIVE_DISABLED 미삼킴). 그 외 시장은 주문 경로 부재.
        if market not in _ORDERABLE_MARKETS:
            raise OrderError("LIVE_DISABLED", f"market '{market}' order path disabled")
        # LIVE 시장(KR/OVS)은 allow_live 마스터 토글 없으면 정정/취소도 불가(registry 와 3중 방어).
        # 위험감축(killswitch) 경로라도 allow_live 없이 LIVE 어댑터를 못 만들므로 여기서 선차단.
        if bucket_of(market) == BUCKET_LIVE and not self._allow_live():
            raise OrderError("LIVE_DISABLED", f"live order path for '{market}' is closed")
        # 런타임 EngineState 단일 권위(§7.1) — config 판독 폐기. ACTIVE 가 아니면 정정/취소 금지.
        # 단, 위험감축 취소(killswitch L1·§8 우선 레인)는 멱등·노출감소라 ACTIVE 비요구 —
        # boot 실패/RECONCILING 에도 취소가 막히면 안 된다(단계6 연기분 흡수).
        # 버킷별 EngineState(§3.2) — 위 시장가드로 FUT(=paper 버킷)만 도달하므로 실질 동작은
        # 기존과 동일하되, 접근을 engine_for 로 일원화한다(단일 글로벌 폐기).
        engine = self.engine_for(bucket_of(order["market"]))
        if not risk_reduction and engine.state != EngineState.ACTIVE:
            raise OrderError("ENGINE_NOT_ACTIVE", f"engine is {engine.state}")
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
            trading_mode=trading_mode_of(parent["market"]) or "paper",
            side=parent["side"],
            order_type=parent["order_type"],
            qty=fields.get("qty", parent["qty"]),
            price=fields.get("price", parent.get("price")),
            exchange=parent.get("exchange"),
            tr_code=order_tr_labels(parent["market"])[1 if relation == "modify" else 2],
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
