"""실시간 체결 소스(해외선물 TC2/TC3) — 스캐폴드 (M3b §10, flag off 기본).

M2 의 `RealtimeFillSource`(Protocol stub, `app/orders/fill_tracker.py`)를 구체화한다.
`.real()` 로 TC2(주문응답·접수/거부)+TC3(주문체결)을 구독하고, 체결분(TC3)을 시장 무관
`Fill` 로 정규화해 **reconcile 과 동일한 단일 writer 경로** `repo.apply_fill` 로 멱등 적재한다.

⚠ **체결 권위는 여전히 reconcile(CIDBQ02400)** — TC 는 보강이다(§1.5/§1.6). TC 라이브 값과
`OvrsFutsOrdNo` 매칭이 미검증(§13-5)이므로 기본 off(`HANBIT_REALTIME_FILLS`). TC1 은
존재하나 미사용(라이브 확정 후). overseas_stock 의 AS0 계열은 본 스캐폴드 범위 밖.

**writer 강제 off(검증 Lens2-M3)**: flag 가 켜져 있어도 런타임 `EngineState` 가
READ_ONLY/RECONCILING 이면 적재하지 않는다 — 거래가 안 도는데 상태를 변이시키지 않는다.
누락분은 boot/주기 reconcile 이 권위 경로로 흡수한다.

⚠ **이 파일은 발주 TR(CIDBT*) 리터럴을 포함하지 않는다**(read-only 불변식 스코프). 체결 적재는
order_id 단일 writer 락(OrderLocks) 안에서 `repo.apply_fill` 로만 수행한다(INV-5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION
from app.logging_setup import get_logger
from app.models.order_dto import Fill, Relation, Side
from app.orders.state_machine import OrderLocks

if TYPE_CHECKING:
    from app.config import Settings
    from app.core.engine_state import EngineState
    from app.core.event_bus import EventBus
    from app.core.sessions import SessionManager
    from app.repositories.orders_repo import OrdersRepo

logger = get_logger("app.realtime_future")

# §1.5 검증 필드 코드 — 주문 어댑터(BnsTpCode)와 동일한 매도/매수 반전에 주의.
_SB_TO_SIDE: dict[str, Side] = {"1": Side.SELL, "2": Side.BUY}  # s_b_ccd
_CCD_TO_RELATION: dict[str, Relation] = {  # ordr_ccd
    "1": Relation.NEW,
    "2": Relation.MODIFY,
    "3": Relation.CANCEL,
}


def _to_float(value: Any, default: float = 0.0) -> float:
    """LS 실시간 필드는 전부 문자열 — 공백/빈문자 안전 파싱."""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _clean(value: Any) -> str:
    return (str(value).strip() if value is not None else "").strip()


def _raw_of(body: Any) -> dict[str, Any] | None:
    """정규화 원본 보존 — pydantic 모델/SimpleNamespace 둘 다 흡수."""
    dump = getattr(body, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:  # 비-pydantic model_dump
            return dump()
    if hasattr(body, "__dict__"):
        return dict(vars(body))
    return None


def tc3_to_fill(body: Any) -> Fill | None:
    """TC3(주문체결) body → `Fill`. 체결분(ccls_q>0 + ordr_no)만 변환, 아니면 None.

    멱등키 `event_seq='tc:'+ccls_no`(거래소 체결식별자; 없으면 'tc:'+ordr_no fallback).
    reconcile 의 'recon:'+OvrsFutsExecNo 와는 별개 prefix — 교차-origin 중복제거(ccls_no==
    OvrsFutsExecNo 여부)는 라이브 미검증(§13-5)이라 flag off 상태에서만 안전하다.
    `remaining_qty` 는 TC3 미제공 → `apply_fill` 이 주문수량 대비 누적으로 산출한다.
    """
    ordno = _clean(getattr(body, "ordr_no", ""))
    if not ordno:
        return None
    qty = _to_float(getattr(body, "ccls_q", None))
    if qty <= 0:  # 접수/취소 ack 등 비-체결 이벤트 → apply_fill 대상 아님
        return None
    seq = _clean(getattr(body, "ccls_no", "")) or ordno
    return Fill(
        broker_ord_no=ordno,
        exec_qty=qty,
        exec_price=_to_float(getattr(body, "ccls_prc", None)),
        remaining_qty=None,
        ord_status_code=None,
        origin="tc",
        event_seq=f"tc:{seq}",
        raw=_raw_of(body),
    )


def tc2_to_event(
    body: Any,
    *,
    rsp_cd: Any = None,
    rsp_msg: Any = None,
    error_msg: Any = None,
) -> dict[str, Any]:
    """TC2(주문응답: 접수/거부) → 정규화 dict(관측용). DB 미변이(스캐폴드).

    §1.5 필드(ordr_no/s_b_ccd/ordr_ccd/ordr_q/cnfr_q/rfsl_cd)는 `TC2RealResponseBody` 에 있고,
    응답코드(rsp_cd/rsp_msg/error_msg)는 **엔벨로프 `TC2RealResponse`** 에 있다(라이브러리 소스
    확인, 어댑터 `overseas_future_order` 관례와 동일). 그래서 응답코드는 호출자(`_handle_tc2`)가
    resp 에서 읽어 주입한다 — body 에서 읽으면 라이브에선 항상 비게 된다.
    """
    side = _SB_TO_SIDE.get(_clean(getattr(body, "s_b_ccd", "")))
    rel = _CCD_TO_RELATION.get(_clean(getattr(body, "ordr_ccd", "")))
    return {
        "broker_ord_no": _clean(getattr(body, "ordr_no", "")),
        "org_ord_no": _clean(getattr(body, "orgn_ordr_no", "")) or None,
        "symbol": _clean(getattr(body, "is_cd", "")) or None,
        "side": side.value if side else None,
        "relation": rel.value if rel else None,
        "order_qty": _to_float(getattr(body, "ordr_q", None)),
        "confirmed_qty": _to_float(getattr(body, "cnfr_q", None)),
        "reject_code": _clean(getattr(body, "rfsl_cd", "")) or None,
        "rsp_cd": _clean(rsp_cd) or None,
        "rsp_msg": _clean(rsp_msg) or None,
        "error_msg": _clean(error_msg) or None,
    }


class RealtimeFutureFillSource:
    """해외선물 실시간 체결(TC2/TC3) 구독 + 보강 적재. RealtimeFillSource 구체화.

    start()/stop() 의 실제 WS 구독은 flag(`HANBIT_REALTIME_FILLS`) on + 세션 인증 시에만
    동작한다(기본 off → no-op). 적재(writer)는 추가로 런타임 ACTIVE 를 요구한다(writer_enabled).
    """

    market = MARKET_OVERSEAS_FUTUREOPTION

    def __init__(
        self,
        repo: OrdersRepo,
        session: SessionManager | None,
        settings: Settings,
        engine: EngineState,
        *,
        locks: OrderLocks | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._repo = repo
        self._session = session
        self._settings = settings
        self._engine = engine
        # ⚠ OrderService 와 **동일한** OrderLocks 인스턴스를 주입해야 reconcile/place 와의
        #   order_id 단일 writer 직렬화가 성립한다(별도 인스턴스면 락 분리 = race). 미주입 시
        #   독립 락(스캐폴드/단위테스트 전용).
        self._locks = locks or OrderLocks()
        self._bus = event_bus
        self._real: Any | None = None

    def _flag_enabled(self) -> bool:
        return bool(getattr(self._settings, "realtime_fills_enabled", False))

    @property
    def writer_enabled(self) -> bool:
        """TC 체결을 실제 적재할지: flag on **그리고** 런타임 ACTIVE(검증 Lens2-M3).

        READ_ONLY/RECONCILING 에서는 flag 가 켜져 있어도 적재 금지(상태 변이 차단).
        """
        return self._flag_enabled() and self._engine.can_trade

    # ── 라이브 구독 (flag off 면 no-op) ───────────────────────────────────
    async def start(self, symbols: list[str] | None = None) -> None:
        """TC2/TC3 구독 시작. TC 채널은 계좌 단위(tr_key="")라 symbols 는 미사용(Protocol 호환)."""
        if not self._flag_enabled():
            logger.info("realtime fills off (HANBIT_REALTIME_FILLS) — TC2/TC3 미구독(스캐폴드)")
            return
        if self._session is None:
            logger.warning("realtime fills: 세션 없음 — TC 미구독")
            return
        facade = self._session.client_for(self.market)
        if facade is None:
            logger.warning("realtime fills: FUT 세션 미인증 — TC 미구독")
            return
        try:
            real = facade.real()
            await real.connect(wait=True)
            # TC1/2/3 은 하나만 등록해도 증권사에서 전부 자동등록 — 우리는 TC2(접수/거부)+
            # TC3(체결) 핸들러를 단다(라이브러리가 코루틴 리스너를 create_task 로 await).
            real.TC2().on_tc2_message(self._handle_tc2)
            real.TC3().on_tc3_message(self._handle_tc3)
            self._real = real
            logger.info(
                "realtime fills started — TC2/TC3 구독(writer_enabled=%s)", self.writer_enabled
            )
        except Exception:  # noqa: BLE001 — 부팅/런타임 비차단
            logger.exception("realtime fills start 실패 — TC 미구독(권위는 reconcile)")
            self._real = None

    async def stop(self) -> None:
        real = self._real
        if real is None:
            return
        try:
            real.TC2().on_remove_tc2_message()
            real.TC3().on_remove_tc3_message()
            await real.close()
        except Exception:  # noqa: BLE001
            logger.exception("realtime fills stop 실패")
        finally:
            self._real = None

    # ── 이벤트 핸들러 ─────────────────────────────────────────────────────
    async def _handle_tc3(self, resp: Any) -> str:
        """TC3 체결 → Fill 정규화 → 가드 후 단일 writer 적재."""
        body = getattr(resp, "body", None)
        if body is None:
            body = resp  # 테스트가 body 를 직접 넘긴 경우
        fill = tc3_to_fill(body)
        if fill is None:
            return "ignored"
        return await self._apply_fill(fill)

    async def _handle_tc2(self, resp: Any) -> dict[str, Any]:
        """TC2 접수/거부 ack → 정규화(관측). reconcile 권위라 DB 변이는 안 한다(스캐폴드).

        §1.5 필드는 resp.body 에서, 응답코드(rsp_cd/rsp_msg/error_msg)는 엔벨로프 resp 에서 읽는다.
        """
        body = getattr(resp, "body", None)
        if body is None:
            body = resp
        event = tc2_to_event(
            body,
            rsp_cd=getattr(resp, "rsp_cd", None),
            rsp_msg=getattr(resp, "rsp_msg", None),
            error_msg=getattr(resp, "error_msg", None),
        )
        await self._incr("realtime_tc2_events")
        await self._publish("orders", {"tc2": event})
        return event

    # ── 단일 writer 적재 경로(reconcile 와 동일) ──────────────────────────
    async def _apply_fill(self, fill: Fill) -> str:
        if not self.writer_enabled:
            # flag off 또는 런타임 READ_ONLY/RECONCILING → 미적재. reconcile 이 흡수(권위).
            await self._incr("realtime_fills_skipped")
            return "skipped"
        acct = await self._repo.get_account_id(self.market)
        if acct is None:
            return "no_account"
        order = await self._repo.get_order_by_broker(acct, fill.broker_ord_no)
        if order is None:
            # DB 미등록 OrdNo(외부/누락 주문) → reconcile orphan 흡수에 위임(여기서 생성 안 함).
            await self._incr("realtime_fills_unmatched")
            return "no_order"
        async with self._locks.lock(order["id"]):
            applied = await self._repo.apply_fill(order["id"], fill, trigger="tc")
            # apply_fill==False 는 두 원인 — 멱등 중복(event_seq 기존) vs 터미널 주문 도착(진짜
            # 체결 누락). 둘을 같은 메트릭으로 뭉개지 않도록 락 안에서 멱등키 존재로 구분한다.
            already = (
                True if applied else await self._repo.fill_exists(order["id"], fill.event_seq)
            )
        if applied:
            await self._incr("realtime_fills_applied")
            await self._publish(
                "fill",
                {
                    "order_id": order["id"],
                    "broker_ord_no": fill.broker_ord_no,
                    "exec_qty": fill.exec_qty,
                    "exec_price": fill.exec_price,
                    "origin": "tc",
                },
            )
            return "applied"
        if already:
            await self._incr("realtime_fills_duplicate")  # 멱등(이미 적재된 event_seq)
            return "duplicate"
        # 신규 event_seq 인데 미적재 → 동시 reconcile 이 주문을 터미널화한 뒤 도착한 체결.
        # reconcile 권위가 다음 스냅샷(CIDBQ02400)에서 흡수하나, 누락을 관측 가능하게 남긴다(§13-5).
        await self._incr("realtime_fills_after_terminal")
        logger.warning(
            "TC 체결이 터미널 주문 도착 — 미적재(reconcile 흡수). order_id=%s seq=%s status=%s",
            order["id"],
            fill.event_seq,
            order.get("status"),
        )
        return "after_terminal"

    async def _incr(self, name: str) -> None:
        try:
            await self._repo.incr_metric(name)
        except Exception:  # noqa: BLE001 — 메트릭 실패가 적재를 막지 않는다
            logger.debug("incr_metric(%s) 실패", name)

    async def _publish(self, topic: str, data: dict[str, Any]) -> None:
        if self._bus is not None:
            await self._bus.publish(topic, data)
