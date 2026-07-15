"""국내주식(KRX, LIVE) 주문/조회 어댑터 — M4b.

검증된 라이브러리 사실(.claude/plans/2026-06-20-M4-계획서.md §1.1 + 설치 소스 직접확인):
  신규 CSPAT00601 / 정정 CSPAT00701 / 취소 CSPAT00801 —
    facade.order().cspat00601(body=CSPAT00601InBlock1, options=).req_async().
  주문번호 = block2.OrdNo(int) → str 정규화. BnsTpCode '1'=매도/'2'=매수(FUT 동일).
  OrdprcPtnCode '00'=지정가/'03'=시장가(시장가는 OrdPrc=0).
  성공 rsp_cd: 매수 '00040' / 매도 '00039' (⚠ '00000' 아님). 정정/취소 rsp_cd 는 소스 미선언 →
    OrdNo 존재 + status<400 + error_msg None 으로 판정('00000' 강제 금지).
  미체결 조회 CSPAQ13700(list-all, block3 rows): OrdNo/OrgOrdNo/IsuNo/BnsTpCode/OrdQty/OrdPrc/
    ExecQty/ExecPrc/RmnOrdQty(미체결 잔량 키).
  종목별 잔고 CSPAQ12300(block3 rows): IsuNo/BalQty/AvrUprc(평단)/NowPrc/EvalPnl.
    (block2.MnyOrdAbleAmt = 주문가능액)

⚠ 이 파일은 국내주식 주문 발주 TR(CSPAT*) 리터럴이 등장하는 **유일한** app/ 파일이어야 한다
  (read-only 불변식 스코프 — tests/test_readonly_invariant.py). 시세 어댑터 korea_stock.py 는
  t1102/t8451(시세)만 — 주문 TR 부재. CSPAQ* 계좌-조회 TR 은 reconcile 읽기 경로.

⚠ 실주문 경로: HANBIT_ALLOW_LIVE=false 인 한 registry 가 이 어댑터 **생성 자체를 거부**한다
  (allow_live 단일 관문 §4.4). 아래 코드는 allow_live=true + 버킷 ACTIVE 일 때만 실행된다.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from programgarden_finance.ls.korea_stock.accno.CSPAQ12300.blocks import (
    CSPAQ12300InBlock1,
)
from programgarden_finance.ls.korea_stock.accno.CSPAQ13700.blocks import (
    CSPAQ13700InBlock1,
)
from programgarden_finance.ls.korea_stock.order.CSPAT00601.blocks import (
    CSPAT00601InBlock1,
)
from programgarden_finance.ls.korea_stock.order.CSPAT00701.blocks import (
    CSPAT00701InBlock1,
)
from programgarden_finance.ls.korea_stock.order.CSPAT00801.blocks import (
    CSPAT00801InBlock1,
)

from app.adapters.order_base import OrderError
from app.core.mode_matrix import MARKET_KOREA_STOCK
from app.models.order_dto import (
    AmendRequest,
    CancelRequest,
    OpenOrder,
    OrderAck,
    OrderIntent,
    OrderType,
    Position,
    Side,
)

if TYPE_CHECKING:
    from app.core.sessions import SessionManager

_KST = ZoneInfo("Asia/Seoul")

# 국내주식 주문 TR 코드 — 리터럴은 이 파일에만 둔다(read-only 불변식 스코프).
# 다른 모듈(order_service 등)은 registry.order_tr_labels() 로 이 값을 얻어 tr_code 라벨에 쓴다.
TR_NEW = "CSPAT00601"
TR_AMEND = "CSPAT00701"
TR_CANCEL = "CSPAT00801"

# 시장 무관 Side → LS BnsTpCode ('1'=매도, '2'=매수). 역매핑은 조회 파싱에 사용.
_SIDE_TO_BNS: dict[Side, str] = {Side.SELL: "1", Side.BUY: "2"}
_BNS_TO_SIDE: dict[str, Side] = {"1": Side.SELL, "2": Side.BUY}

# KR 신규주문 성공 rsp_cd — 매수 '00040' / 매도 '00039'(참고용; 판정은 OrdNo 존재 기준).
_KR_NEW_SUCCESS_RSP = frozenset({"00040", "00039"})

# 체결 venue(회원사) 라우팅 — CSPAT00601.MbrNo Literal("", "KRX", "NXT").
#   정규장(~15:30 KST)=KRX, 대체거래소(NXT) 연장/경쟁매매는 MbrNo="NXT".
#   intent.exchange 로 호출자(주문티켓/전략)가 지정, 미지정/기타는 "" (브로커 기본=KRX).
_VENUES = frozenset({"KRX", "NXT"})


def _venue(exchange: str | None) -> str:
    v = (exchange or "").strip().upper()
    return v if v in _VENUES else ""


def kst_today() -> str:
    """KRX 세션일 기준 오늘(YYYYMMDD). 컨테이너 UTC 자정 경계 오류 방지."""
    return datetime.now(_KST).strftime("%Y%m%d")


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class KoreaStockOrderAdapter:
    """국내주식(LIVE, KRX) 주문/조회 어댑터."""

    market = MARKET_KOREA_STOCK
    tr_new = TR_NEW
    tr_amend = TR_AMEND
    tr_cancel = TR_CANCEL

    def __init__(self, session: SessionManager) -> None:
        self._session = session

    def _facade(self) -> Any:
        # 세션 바인딩 불변식: 반드시 KR 세션 파사드만 취득(다른 시장 세션 거부).
        facade = self._session.client_for(self.market)
        if facade is None:
            raise OrderError(
                "MARKET_UNAUTHENTICATED",
                f"market '{self.market}' is not authenticated",
            )
        return facade

    def _opts(self) -> Any:
        return self._session.quote_opts()

    @staticmethod
    def _isu(symbol: str) -> str:
        """종목코드 정규화 — 6자리 또는 'A'+6자리 양형식 수용(모의는 항상 'A'+code). 그대로 전달."""
        return (symbol or "").strip().upper()

    # ── 주문 ──────────────────────────────────────────────────────────────
    async def place_order(self, intent: OrderIntent) -> OrderAck:
        is_market = intent.order_type == OrderType.MARKET
        if not is_market and intent.price is None:
            raise OrderError("INVALID_PRICE", "LIMIT order requires price")
        body = CSPAT00601InBlock1(
            IsuNo=self._isu(intent.symbol),
            OrdQty=int(intent.qty),
            OrdPrc=0.0 if is_market else float(intent.price or 0.0),
            BnsTpCode=_SIDE_TO_BNS[intent.side],
            OrdprcPtnCode="03" if is_market else "00",
            MbrNo=_venue(intent.exchange),  # KRX/NXT 대체거래소 라우팅(정규장 종료 후 NXT)
        )
        tr = self._facade().order().cspat00601(body=body, options=self._opts())
        return self._ack(await tr.req_async())

    async def amend_order(self, req: AmendRequest) -> OrderAck:
        if req.price is None:
            raise OrderError("INVALID_PRICE", "amend requires limit price")
        body = CSPAT00701InBlock1(
            OrgOrdNo=int(req.org_ord_no),
            IsuNo=self._isu(req.symbol),
            OrdQty=int(req.qty),
            OrdprcPtnCode="00",
            OrdPrc=float(req.price),
        )
        tr = self._facade().order().cspat00701(body=body, options=self._opts())
        return self._ack(await tr.req_async())

    async def cancel_order(self, req: CancelRequest) -> OrderAck:
        if req.qty is None:
            # KR 취소는 수량 필수 — order_service.cancel 이 원주문 미체결 잔량을 채워 전달한다.
            raise OrderError("INVALID_QTY", "KR cancel requires qty (remaining)")
        body = CSPAT00801InBlock1(
            OrgOrdNo=int(req.org_ord_no),
            IsuNo=self._isu(req.symbol),
            OrdQty=int(req.qty),
        )
        tr = self._facade().order().cspat00801(body=body, options=self._opts())
        return self._ack(await tr.req_async())

    @staticmethod
    def _ack(resp: Any) -> OrderAck:
        block2 = getattr(resp, "block2", None)
        raw_ord_no = getattr(block2, "OrdNo", None) if block2 is not None else None
        # OrdNo 는 int — 0/None 은 실패(주문번호 미발급). str 정규화.
        ord_no = str(raw_ord_no).strip() if raw_ord_no else None
        error_msg = getattr(resp, "error_msg", None)
        status = getattr(resp, "status_code", None)
        rsp_cd = getattr(resp, "rsp_cd", None)
        rsp_msg = getattr(resp, "rsp_msg", None)

        # KR 성공판정: OrdNo 존재 AND status<400 AND error_msg None.
        # ⚠ '00000' 강제 금지 — KR 신규는 side별 코드('00040'/'00039'), 정정/취소는 rsp_cd 미선언.
        ok = True
        if error_msg:
            ok = False
        elif status is not None and status >= 400:
            ok = False
        elif not ord_no:
            ok = False
        return OrderAck(
            ok=ok,
            broker_ord_no=ord_no,
            rsp_cd=rsp_cd,
            rsp_msg=rsp_msg,
            error_msg=error_msg,
            status_code=status,
        )

    # ── 조회(reconcile 소스) — list-all(종목 불요) ─────────────────────────
    async def get_open_orders(
        self, symbol: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[OpenOrder]:
        # CSPAQ13700 은 list-all(종목 인자 무시). 미체결/체결 당일 전체를 block3 로 반환.
        # ⚠ 커서 페이지네이션(SrtOrdNo2)·tr_cont 2층 연속조회는 라이브([L]) 검증 후 Gate B 에서
        #   확장(§7 L4-1). 현재는 단일 조회 → reconcile 이 소멸판정을 보류(fail-safe)한다.
        body = CSPAQ13700InBlock1(OrdDt=start_date or kst_today())
        tr = self._facade().accno().cspaq13700(body=body, options=self._opts())
        resp = await tr.req_async()
        rows = getattr(resp, "block3", None) or []
        return [self._to_open_order(r) for r in rows]

    @staticmethod
    def _to_open_order(r: Any) -> OpenOrder:
        return OpenOrder(
            broker_ord_no=str(getattr(r, "OrdNo", "") or ""),
            org_ord_no=(str(getattr(r, "OrgOrdNo", "") or "") or None),
            exec_no=None,  # CSPAQ13700 은 체결번호 미제공 → event_seq fallback=OrdNo(멱등)
            symbol=str(getattr(r, "IsuNo", "") or ""),
            side=_BNS_TO_SIDE.get(str(getattr(r, "BnsTpCode", "") or "")),
            qty=int(_to_float(getattr(r, "OrdQty", 0)) or 0),
            price=_to_float(getattr(r, "OrdPrc", None)),
            exec_qty=_to_float(getattr(r, "ExecQty", 0)) or 0.0,
            exec_price=_to_float(getattr(r, "ExecPrc", None)),
            remaining_qty=_to_float(getattr(r, "RmnOrdQty", None)),
            ord_status_code=(str(getattr(r, "OrdprcPtnCode", "") or "") or None),
        )

    async def get_positions(self) -> list[Position]:
        body = CSPAQ12300InBlock1()  # 전부 기본값(당일 잔고)
        tr = self._facade().accno().cspaq12300(body=body, options=self._opts())
        resp = await tr.req_async()
        rows = getattr(resp, "block3", None) or []
        # 주문가능액은 계좌집계 block2.MnyOrdAbleAmt(행 아님) — 포지션 행에 실어 orderable 로 전달.
        block2 = getattr(resp, "block2", None)
        orderable = _to_float(getattr(block2, "MnyOrdAbleAmt", None)) if block2 else None
        out: list[Position] = []
        for r in rows:
            sym = str(getattr(r, "IsuNo", "") or "")
            if not sym:
                continue
            qty = _to_float(getattr(r, "BalQty", 0)) or 0.0
            if qty == 0:
                continue  # 잔고 0 행 스킵
            out.append(
                Position(
                    symbol=sym,
                    qty=qty,
                    avg_price=_to_float(getattr(r, "AvrUprc", None)),
                    current_price=_to_float(getattr(r, "NowPrc", None)),
                    pnl_amount=_to_float(getattr(r, "EvalPnl", None)),
                    side=Side.BUY,  # 국내주식 현물은 롱 온리
                    currency="KRW",
                    orderable_amount=orderable,
                )
            )
        return out
