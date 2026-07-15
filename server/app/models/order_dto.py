"""주문 도메인 DTO/enum — 시장 무관 정규화 (M2).

어댑터(시장별 LS 주문 TR)와 서비스/상태머신/리스크 게이트 사이에서 주고받는
시장 무관 표현. 라이브러리 OutBlock 의 시장별 차이는 어댑터가 흡수해 이 DTO 로 채운다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION


class OrderState(StrEnum):
    """주문 상태머신 상태 (.claude/plans/2026-06-20-통합계획서.md M2 §3 + M3b §7.2)."""

    APPROVED = "approved"
    SUBMITTED = "submitted"
    IN_DOUBT = "in_doubt"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELED = "canceled"
    EXPIRED = "expired"
    # M3b 부트 격리(§7.2): reconcile 로 해소 불가한 미확정 주문. 운영 수동 resolve 전까지
    # 자동 전이 없이 '비활성' 으로 묶인다 — list_open_orders(비터미널)에서 자연 제외.
    QUARANTINED = "quarantined"


#: 터미널 상태(이후 자동 전이 불가). quarantined 는 운영 수동 resolve 만 가능한 사실상의
#: sink 라 여기 포함해 list_open_orders/reconcile/apply_fill 이 자연 제외/스킵하도록 한다(§7.2).
TERMINAL_STATES: frozenset[OrderState] = frozenset(
    {
        OrderState.FILLED,
        OrderState.REJECTED,
        OrderState.CANCELED,
        OrderState.EXPIRED,
        OrderState.QUARANTINED,
    }
)


class Side(StrEnum):
    """매매 방향(시장 무관). 시장별 코드 매핑은 어댑터가 수행."""

    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class IntentKind(StrEnum):
    """주문 의도 — 노출 증가(ENTRY) vs 감축(EXIT). 청산은 킬스위치/halt 우회 허용."""

    ENTRY = "entry"
    EXIT = "exit"


class Relation(StrEnum):
    """주문 행 관계 — 정정/취소는 원주문에 연결된 새 행으로 기록."""

    NEW = "new"
    MODIFY = "modify"
    CANCEL = "cancel"


class OrderIntent(BaseModel):
    """주문 의향 — 리스크 게이트 입력 + 어댑터 place 입력."""

    market: str = Field(default=MARKET_OVERSEAS_FUTUREOPTION, description="시장 키")
    symbol: str = Field(..., description="종목코드(IsuCodeVal, 예 'ADZ25')")
    side: Side
    intent: IntentKind = Field(default=IntentKind.ENTRY)
    order_type: OrderType = Field(default=OrderType.LIMIT)
    qty: int = Field(..., gt=0, description="계약수")
    price: float | None = Field(default=None, description="지정가(LIMIT). MARKET 이면 무시")
    exchange: str = Field(default="HKEX", description="거래소(HKEX 강제)")
    due_yymm: str | None = Field(default=None, description="만기 YYMM(없으면 어댑터 기본)")
    currency: str | None = Field(default=None)
    client_order_id: str | None = Field(
        default=None, description="멱등키. 미지정 시 서비스가 결정적 생성"
    )
    strategy_id: int | None = Field(default=None)
    reason: str | None = Field(default=None, description="감사/로그용")


class OrderAck(BaseModel):
    """주문/정정/취소 TR 응답 정규화."""

    ok: bool = Field(..., description="성공판정(OrdNo 존재 + status<400 + error_msg None)")
    broker_ord_no: str | None = Field(default=None, description="OvrsFutsOrdNo")
    rsp_cd: str | None = Field(default=None)
    rsp_msg: str | None = Field(default=None)
    error_msg: str | None = Field(default=None)
    status_code: int | None = Field(default=None)


class Fill(BaseModel):
    """체결 이벤트 정규화 (reconcile 또는 실시간)."""

    broker_ord_no: str
    exec_qty: float
    exec_price: float
    remaining_qty: float | None = Field(default=None, description="UnercQty")
    ord_status_code: str | None = Field(default=None, description="FutsOrdStatCode")
    origin: str = Field(default="reconcile", description="reconcile / sc_event")
    event_seq: str = Field(..., description="멱등키(비-NULL 필수): 'recon:'+OvrsFutsExecNo 등")
    raw: dict[str, Any] | None = Field(default=None)


class OpenOrder(BaseModel):
    """미체결 주문 스냅샷 (CIDBQ02400)."""

    broker_ord_no: str
    org_ord_no: str | None = Field(default=None)
    exec_no: str | None = Field(default=None, description="OvrsFutsExecNo")
    symbol: str
    side: Side | None = Field(default=None)
    qty: int = 0
    price: float | None = Field(default=None)
    exec_qty: float = 0
    exec_price: float | None = Field(default=None)
    remaining_qty: float | None = Field(default=None, description="UnercQty")
    ord_status_code: str | None = Field(default=None)


class Position(BaseModel):
    """포지션 스냅샷 (CIDBQ01500)."""

    symbol: str
    qty: float = 0
    avg_price: float | None = Field(default=None, description="PchsPrc")
    current_price: float | None = Field(default=None, description="OvrsDrvtNowPrc")
    pnl_amount: float | None = Field(default=None, description="AbrdFutsEvalPnlAmt")
    side: Side | None = Field(default=None)
    currency: str | None = Field(default=None)
    orderable_amount: float | None = Field(default=None, description="OrdAbleAmt(행 반복)")


class AmendRequest(BaseModel):
    """정정 요청 — 원주문번호 + 신규 가격/수량 + 라우팅 메타."""

    org_ord_no: str
    symbol: str
    side: Side
    order_type: OrderType = Field(default=OrderType.LIMIT)
    qty: int = Field(..., gt=0)
    price: float | None = Field(default=None)
    exchange: str = Field(default="HKEX")
    due_yymm: str | None = Field(default=None)
    currency: str | None = Field(default=None)


class CancelRequest(BaseModel):
    """취소 요청 — 원주문번호 + 라우팅 메타."""

    org_ord_no: str
    symbol: str
    exchange: str = Field(default="HKEX")
    # 취소 수량 — **Optional, 기본 None**(§4.3 / §17 L4-4). 어댑터별로 None=전량(FUT),
    # 필수채움(KR/OVS). 필수(gt=0)로 두면 기존 FUT cancel 호출부가 ValidationError 로 깨진다.
    # order_service.cancel 이 원주문의 remaining_qty(미체결 잔량)를 채워 전달(over-cancel 회피).
    qty: int | None = Field(default=None, ge=1, description="취소 수량(None=전량, KR/OVS 필수)")
