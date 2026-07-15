"""해외주식(US, LIVE) 주문/조회 어댑터 — M4c.

검증된 라이브러리 사실(.claude/plans/2026-06-20-M4-계획서.md §1.2/§16 + 설치 소스 직접확인):
  신규+취소 COSAT00301(한 TR) / 정정 COSAT00311 —
    facade.order().cosat00301(body=, options=).req_async().
  ⚠ 플랜(통합계획서) 오기 정정(§16 C1/C2): 신규=COSAT00301(OrdPtnCode 01/02), 정정=COSAT00311(07),
    취소=COSAT00301 OrdPtnCode '08'(현금). COSMT00300(매도상환·margin)은 M4 범위 밖.
  OrdPtnCode '01'=매도/'02'=매수/'08'=취소. OrdMktCode '81'=NYSE/AMEX·'82'=NASDAQ.
  주문번호 = block2.OrdNo(int) → str. OvrsOrdPrc(float), OrdprcPtnCode '00'=지정가/'03'=시장가.
  성공 rsp_cd '00000'.
  미체결 조회 COSAQ00102(list-all, block3 rows): OrdNo/OrgOrdNo/ShtnIsuNo/BnsTpCode/OrdQty/
    OvrsOrdPrc/ExecQty/OvrsExecPrc/UnercQty(미체결 키)/CrcyCode.
  잔고 COSOQ00201(block4 rows): ShtnIsuNo/AstkBalQty/FcstckUprc(평단)/OvrsScrtsCurpri(현재가)/
    FcurrEvalPnlAmt/BaseXchrat(FX)/CrcyCode. (block3.FcurrOrdAbleAmt=통화별 주문가능)

⚠ 이 파일은 해외주식 주문 발주 TR(COSAT*) 리터럴이 등장하는 **유일한** app/ 파일이어야 한다
  (read-only 불변식 스코프 — tests/test_readonly_invariant.py). 시세 어댑터 overseas_stock.py 는
  g3101/g3103(시세)만. COSAQ*/COSOQ* 계좌-조회 TR 은 reconcile 읽기 경로.

⚠ 실주문 경로: HANBIT_ALLOW_LIVE=false 인 한 registry 가 이 어댑터 **생성 자체를 거부**한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from programgarden_finance.ls.overseas_stock.accno.COSAQ00102.blocks import (
    COSAQ00102InBlock1,
)
from programgarden_finance.ls.overseas_stock.accno.COSOQ00201.blocks import (
    COSOQ00201InBlock1,
)
from programgarden_finance.ls.overseas_stock.order.COSAT00301.blocks import (
    COSAT00301InBlock1,
)
from programgarden_finance.ls.overseas_stock.order.COSAT00311.blocks import (
    COSAT00311InBlock1,
)

from app.adapters.order_base import OrderError
from app.adapters.overseas_stock import _split_symbol  # 시세 어댑터의 거래소/티커 분해 재사용
from app.core.mode_matrix import MARKET_OVERSEAS_STOCK
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

_ET = ZoneInfo("America/New_York")

# 해외주식 주문 TR 코드 — 리터럴은 이 파일에만 둔다(read-only 불변식 스코프).
TR_NEW = "COSAT00301"
TR_AMEND = "COSAT00311"
TR_CANCEL = "COSAT00301"  # 취소도 COSAT00301(OrdPtnCode='08')

# 시장 무관 Side → OVS OrdPtnCode('01'=매도, '02'=매수). 취소는 '08'(별도).
_SIDE_TO_PTN: dict[Side, str] = {Side.SELL: "01", Side.BUY: "02"}
# 조회(COSAQ00102 block3)는 BnsTpCode('1'=매도/'2'=매수, 값 일관) — 역매핑.
_BNS_TO_SIDE: dict[str, Side] = {"1": Side.SELL, "2": Side.BUY}


def et_today() -> str:
    """미국 세션일 기준 오늘(YYYYMMDD, [L] — 계좌 TR 기준일 실측 후 확정)."""
    return datetime.now(_ET).strftime("%Y%m%d")


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class OverseasStockOrderAdapter:
    """해외주식(LIVE, US) 주문/조회 어댑터."""

    market = MARKET_OVERSEAS_STOCK
    tr_new = TR_NEW
    tr_amend = TR_AMEND
    tr_cancel = TR_CANCEL

    def __init__(self, session: SessionManager) -> None:
        self._session = session

    def _facade(self) -> Any:
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
    def _route(symbol: str, exchange: str | None) -> tuple[str, str]:
        """(OrdMktCode, IsuNo) 도출 — 심볼("82:TSLA"/"82TSLA"/"TSLA")·exchange 힌트 흡수.

        exchange 가 '81'/'82' 로 명시되면 우선, 아니면 심볼 prefix, 그래도 없으면 NASDAQ('82').
        """
        exchcd, ticker, _keysymbol = _split_symbol(symbol)
        if exchange in ("81", "82"):
            exchcd = exchange
        return exchcd, ticker

    # ── 주문 ──────────────────────────────────────────────────────────────
    async def place_order(self, intent: OrderIntent) -> OrderAck:
        is_market = intent.order_type == OrderType.MARKET
        if not is_market and intent.price is None:
            raise OrderError("INVALID_PRICE", "LIMIT order requires price")
        mkt, isu = self._route(intent.symbol, intent.exchange)
        body = COSAT00301InBlock1(
            OrdPtnCode=_SIDE_TO_PTN[intent.side],
            OrdMktCode=mkt,
            IsuNo=isu,
            OrdQty=int(intent.qty),
            OvrsOrdPrc=0.0 if is_market else float(intent.price or 0.0),
            OrdprcPtnCode="03" if is_market else "00",
        )
        tr = self._facade().order().cosat00301(body=body, options=self._opts())
        return self._ack(await tr.req_async())

    async def amend_order(self, req: AmendRequest) -> OrderAck:
        if req.price is None:
            raise OrderError("INVALID_PRICE", "amend requires limit price")
        mkt, isu = self._route(req.symbol, req.exchange)
        body = COSAT00311InBlock1(
            OrdPtnCode="07",  # 정정 고정
            OrgOrdNo=int(req.org_ord_no),
            OrdMktCode=mkt,
            IsuNo=isu,
            OrdQty=int(req.qty),
            OvrsOrdPrc=float(req.price),
            OrdprcPtnCode="00",
        )
        tr = self._facade().order().cosat00311(body=body, options=self._opts())
        return self._ack(await tr.req_async())

    async def cancel_order(self, req: CancelRequest) -> OrderAck:
        if req.qty is None:
            raise OrderError("INVALID_QTY", "OVS cancel requires qty (remaining)")
        mkt, isu = self._route(req.symbol, req.exchange)
        body = COSAT00301InBlock1(
            OrdPtnCode="08",  # 취소(현금)
            OrgOrdNo=int(req.org_ord_no),
            OrdMktCode=mkt,
            IsuNo=isu,
            OrdQty=int(req.qty),
            OvrsOrdPrc=0.0,
            OrdprcPtnCode="00",
        )
        tr = self._facade().order().cosat00301(body=body, options=self._opts())
        return self._ack(await tr.req_async())

    @staticmethod
    def _ack(resp: Any) -> OrderAck:
        block2 = getattr(resp, "block2", None)
        raw_ord_no = getattr(block2, "OrdNo", None) if block2 is not None else None
        ord_no = str(raw_ord_no).strip() if raw_ord_no else None
        error_msg = getattr(resp, "error_msg", None)
        status = getattr(resp, "status_code", None)
        rsp_cd = getattr(resp, "rsp_cd", None)
        rsp_msg = getattr(resp, "rsp_msg", None)

        # OVS 성공판정: OrdNo 존재 AND status<400 AND error_msg None AND rsp_cd ∈ {'00000', None}.
        ok = True
        if error_msg:
            ok = False
        elif status is not None and status >= 400:
            ok = False
        elif rsp_cd and rsp_cd != "00000":
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

    # ── 조회(reconcile 소스) — list-all ────────────────────────────────────
    async def get_open_orders(
        self, symbol: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[OpenOrder]:
        # COSAQ00102 list-all(종목 인자 무시). ⚠ 커서(SrtOrdNo)/tr_cont 2층 연속조회는 [L] 검증 후
        #   Gate B 확장(§7 L4-1) — 현재 단일 조회 → reconcile 이 소멸판정 보류(fail-safe).
        body = COSAQ00102InBlock1(OrdDt=start_date or et_today())
        tr = self._facade().accno().cosaq00102(body=body, options=self._opts())
        resp = await tr.req_async()
        rows = getattr(resp, "block3", None) or []
        return [self._to_open_order(r) for r in rows]

    @staticmethod
    def _to_open_order(r: Any) -> OpenOrder:
        return OpenOrder(
            broker_ord_no=str(getattr(r, "OrdNo", "") or ""),
            org_ord_no=(str(getattr(r, "OrgOrdNo", "") or "") or None),
            exec_no=None,  # COSAQ00102 체결번호 미제공 → event_seq fallback=OrdNo(멱등)
            symbol=str(getattr(r, "ShtnIsuNo", "") or ""),
            side=_BNS_TO_SIDE.get(str(getattr(r, "BnsTpCode", "") or "")),
            qty=int(_to_float(getattr(r, "OrdQty", 0)) or 0),
            price=_to_float(getattr(r, "OvrsOrdPrc", None)),
            exec_qty=_to_float(getattr(r, "ExecQty", 0)) or 0.0,
            exec_price=_to_float(getattr(r, "OvrsExecPrc", None)),
            remaining_qty=_to_float(getattr(r, "UnercQty", None)),
            ord_status_code=(str(getattr(r, "OrdPtnCode", "") or "") or None),
        )

    async def get_positions(self) -> list[Position]:
        body = COSOQ00201InBlock1(BaseDt=et_today())
        tr = self._facade().accno().cosoq00201(body=body, options=self._opts())
        resp = await tr.req_async()
        rows = getattr(resp, "block4", None) or []
        out: list[Position] = []
        for r in rows:
            sym = str(getattr(r, "ShtnIsuNo", "") or "")
            if not sym:
                continue
            qty = _to_float(getattr(r, "AstkBalQty", 0)) or 0.0
            if qty == 0:
                continue
            out.append(
                Position(
                    symbol=sym,
                    qty=qty,
                    avg_price=_to_float(getattr(r, "FcstckUprc", None)),
                    current_price=_to_float(getattr(r, "OvrsScrtsCurpri", None)),
                    pnl_amount=_to_float(getattr(r, "FcurrEvalPnlAmt", None)),
                    side=Side.BUY,  # 해외주식 현물은 롱 온리
                    currency=(str(getattr(r, "CrcyCode", "") or "") or "USD"),
                    # 통화별 주문가능(block3.FcurrOrdAbleAmt)은 집계기/balances 로 채운다(M4d).
                    orderable_amount=None,
                )
            )
        return out
