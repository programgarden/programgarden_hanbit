"""해외선물(HKEX, paper) 주문/조회 어댑터 — M2.

검증된 라이브러리 사실(.claude/plans/2026-06-20-통합계획서.md M2 §1):
  신규 CIDBT00100 / 정정 CIDBT00900 / 취소 CIDBT01000 — facade.order().<TR>(body=, options=).
  주문번호 = block2.OvrsFutsOrdNo. BnsTpCode '1'=매도/'2'=매수(⚠ 반대).
  AbrdFutsOrdPtnCode '1'=시장가(가격0)/'2'=지정가. FutsOrdTpCode 신규'1'/정정'2'/취소'3'.
  미체결/체결 조회 CIDBQ02400(종목+날짜창 필수): UnercQty/ExecQty/AbrdFutsExecPrc/OvrsFutsExecNo.
  미결제잔고 CIDBQ01500: BalQty/PchsPrc/AbrdFutsEvalPnlAmt/OrdAbleAmt(행 반복).
  성공판정: OrdNo 존재 AND status<400 AND error_msg None AND (rsp_cd 없거나 '00000').

⚠ 이 파일은 CIDBT*/CIDBQ* 주문계열 식별자가 등장하는 **유일한** app/ 파일이어야 한다
  (read-only 불변식 스코프 — tests/test_readonly_invariant.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from programgarden_finance.ls.overseas_futureoption.accno.CIDBQ01500.blocks import (
    CIDBQ01500InBlock1,
)
from programgarden_finance.ls.overseas_futureoption.accno.CIDBQ02400.blocks import (
    CIDBQ02400InBlock1,
)
from programgarden_finance.ls.overseas_futureoption.order.CIDBT00100.blocks import (
    CIDBT00100InBlock1,
)
from programgarden_finance.ls.overseas_futureoption.order.CIDBT00900.blocks import (
    CIDBT00900InBlock1,
)
from programgarden_finance.ls.overseas_futureoption.order.CIDBT01000.blocks import (
    CIDBT01000InBlock1,
)

from app.adapters.order_base import OrderError
from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION
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

_HKT = ZoneInfo("Asia/Hong_Kong")

# 해외선물 주문 TR 코드 — 리터럴은 이 파일에만 둔다(read-only 불변식 스코프).
# 다른 모듈(order_service 등)은 이 상수를 import 해 tr_code 라벨로 사용.
TR_NEW = "CIDBT00100"
TR_AMEND = "CIDBT00900"
TR_CANCEL = "CIDBT01000"

# 시장 무관 Side → LS BnsTpCode ('1'=매도, '2'=매수). 역매핑은 조회 파싱에 사용.
_SIDE_TO_BNS: dict[Side, str] = {Side.SELL: "1", Side.BUY: "2"}
_BNS_TO_SIDE: dict[str, Side] = {"1": Side.SELL, "2": Side.BUY}


def hkt_today() -> str:
    """HKEX 세션일 기준 오늘(YYYYMMDD). 컨테이너 UTC 자정 경계 오류 방지."""
    return datetime.now(_HKT).strftime("%Y%m%d")


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class OverseasFutureOrderAdapter:
    """해외선물(paper, HKEX) 주문/조회 어댑터."""

    market = MARKET_OVERSEAS_FUTUREOPTION
    tr_new = TR_NEW
    tr_amend = TR_AMEND
    tr_cancel = TR_CANCEL

    def __init__(self, session: SessionManager) -> None:
        self._session = session

    def _facade(self) -> Any:
        # 세션 바인딩 불변식: 반드시 FUT 세션 파사드만 취득(다른 시장 세션 거부).
        facade = self._session.client_for(self.market)
        if facade is None:
            raise OrderError(
                "MARKET_UNAUTHENTICATED",
                f"market '{self.market}' is not authenticated",
            )
        return facade

    def _opts(self) -> Any:
        # 주문/조회도 시세와 동일하게 on_rate_limit='wait' 적용(요청마다 주입).
        return self._session.quote_opts()

    # ── 주문 ──────────────────────────────────────────────────────────────
    async def place_order(self, intent: OrderIntent) -> OrderAck:
        is_market = intent.order_type == OrderType.MARKET
        if not is_market and intent.price is None:
            raise OrderError("INVALID_PRICE", "LIMIT order requires price")
        body = CIDBT00100InBlock1(
            OrdDt=hkt_today(),
            IsuCodeVal=intent.symbol,
            FutsOrdTpCode="1",  # 신규
            BnsTpCode=_SIDE_TO_BNS[intent.side],
            AbrdFutsOrdPtnCode="1" if is_market else "2",
            OvrsDrvtOrdPrc=0.0 if is_market else float(intent.price or 0.0),
            CndiOrdPrc=0.0,
            OrdQty=int(intent.qty),
            CrcyCode=intent.currency or "",
            DueYymm=intent.due_yymm or "000000",
            # ExchCode 미설정 — 공식 예제(HKEX 주문 test_order.py 포함)가 비워둠.
            # 브로커가 IsuCodeVal 로 거래소를 도출한다. (HKEX 강제는 리스크 게이트가 담당.)
        )
        tr = self._facade().order().CIDBT00100(body=body, options=self._opts())
        return self._ack(await tr.req_async())

    async def amend_order(self, req: AmendRequest) -> OrderAck:
        if req.price is None:
            raise OrderError("INVALID_PRICE", "amend requires limit price")
        body = CIDBT00900InBlock1(
            OrdDt=hkt_today(),
            OvrsFutsOrgOrdNo=req.org_ord_no,
            IsuCodeVal=req.symbol,
            FutsOrdTpCode="2",  # 정정
            BnsTpCode=_SIDE_TO_BNS[req.side],
            FutsOrdPtnCode="2",
            OvrsDrvtOrdPrc=float(req.price),
            CndiOrdPrc=0.0,
            OrdQty=int(req.qty),
            CrcyCodeVal=req.currency or "",
            DueYymm=req.due_yymm or "",
            # ExchCode 미설정 — 예제 정합(브로커가 심볼로 거래소 도출).
        )
        tr = self._facade().order().CIDBT00900(body=body, options=self._opts())
        return self._ack(await tr.req_async())

    async def cancel_order(self, req: CancelRequest) -> OrderAck:
        body = CIDBT01000InBlock1(
            OrdDt=hkt_today(),
            IsuCodeVal=req.symbol,
            OvrsFutsOrgOrdNo=req.org_ord_no,
            FutsOrdTpCode="3",  # 취소
            # ExchCode/PrdtTpCode 미설정 — 예제 정합(라이브러리 기본값).
        )
        tr = self._facade().order().CIDBT01000(body=body, options=self._opts())
        return self._ack(await tr.req_async())

    @staticmethod
    def _ack(resp: Any) -> OrderAck:
        block2 = getattr(resp, "block2", None)
        raw_ord_no = getattr(block2, "OvrsFutsOrdNo", None) if block2 is not None else None
        ord_no = (str(raw_ord_no).strip() or None) if raw_ord_no is not None else None
        error_msg = getattr(resp, "error_msg", None)
        status = getattr(resp, "status_code", None)
        rsp_cd = getattr(resp, "rsp_cd", None)
        rsp_msg = getattr(resp, "rsp_msg", None)

        ok = True
        if error_msg:
            ok = False
        elif status is not None and status >= 400:
            ok = False
        elif rsp_cd and rsp_cd != "00000":  # '00000'=성공(소스 문서화) 반례 가드
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

    # ── 조회(reconcile 소스) ───────────────────────────────────────────────
    async def get_open_orders(
        self, symbol: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[OpenOrder]:
        d = hkt_today()
        body = CIDBQ02400InBlock1(
            IsuCodeVal=symbol,
            QrySrtDt=start_date or d,
            QryEndDt=end_date or d,
            ThdayTpCode="1",  # 당일
            OrdStatCode="0",  # 전체
            BnsTpCode="0",  # 전체
            QryTpCode="1",  # 역순(newest first)
            OrdPtnCode="00",  # 전체
            OvrsDrvtFnoTpCode="F",  # 선물
        )
        tr = self._facade().accno().CIDBQ02400(body=body, options=self._opts())
        resp = await tr.req_async()
        rows = getattr(resp, "block2", None) or []
        return [self._to_open_order(r) for r in rows]

    @staticmethod
    def _to_open_order(r: Any) -> OpenOrder:
        return OpenOrder(
            broker_ord_no=str(getattr(r, "OvrsFutsOrdNo", "") or ""),
            org_ord_no=(str(getattr(r, "OvrsFutsOrgOrdNo", "") or "") or None),
            exec_no=(str(getattr(r, "OvrsFutsExecNo", "") or "") or None),
            symbol=str(getattr(r, "IsuCodeVal", "") or ""),
            side=_BNS_TO_SIDE.get(str(getattr(r, "BnsTpCode", "") or "")),
            qty=int(getattr(r, "OrdQty", 0) or 0),
            price=_to_float(getattr(r, "OvrsDrvtOrdPrc", None)),
            exec_qty=float(getattr(r, "ExecQty", 0) or 0),
            exec_price=_to_float(getattr(r, "AbrdFutsExecPrc", None)),
            remaining_qty=_to_float(getattr(r, "UnercQty", None)),
            ord_status_code=(str(getattr(r, "FutsOrdStatCode", "") or "") or None),
        )

    async def get_positions(self) -> list[Position]:
        body = CIDBQ01500InBlock1()  # 전부 기본값(당일/집계)
        tr = self._facade().accno().CIDBQ01500(body=body, options=self._opts())
        resp = await tr.req_async()
        rows = getattr(resp, "block2", None) or []
        out: list[Position] = []
        for r in rows:
            sym = str(getattr(r, "IsuCodeVal", "") or "")
            if not sym:
                continue  # 포지션 없는 행(계좌값만)은 스킵
            out.append(
                Position(
                    symbol=sym,
                    qty=float(getattr(r, "BalQty", 0) or 0),
                    avg_price=_to_float(getattr(r, "PchsPrc", None)),
                    current_price=_to_float(getattr(r, "OvrsDrvtNowPrc", None)),
                    pnl_amount=_to_float(getattr(r, "AbrdFutsEvalPnlAmt", None)),
                    side=_BNS_TO_SIDE.get(str(getattr(r, "BnsTpCode", "") or "")),
                    currency=(str(getattr(r, "CrcyCodeVal", "") or "") or None),
                    orderable_amount=_to_float(getattr(r, "OrdAbleAmt", None)),
                )
            )
        return out
