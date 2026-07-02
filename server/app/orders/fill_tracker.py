"""체결 추적 — 소스 추상화 (M2: reconcile 권위, 실시간 준비).

M2 의 권위 소스는 reconcile(CIDBQ02400) 다. 브로커 미체결/체결 행(OpenOrder)을 정규화
Fill 로 변환해 repository.apply_fill 로 멱등 적재한다(event_seq='recon:'+OvrsFutsExecNo).

실시간 소스(TC1 주문접수/TC2 주문응답·체결/TC3 주문체결, 하나 등록 시 자동 동시등록)는
**미배선** — 라이브 값/포맷 의미 미검증. 활성화 시 아래 RealtimeFillSource 한 곳만 채운다.
TC2/TC3 응답 필드(소스 문서화됨, 값 미검증): ordr_no(주문번호), orgn_ordr_no(원주문번호),
is_cd(종목), s_b_ccd(매도/매수), ordr_ccd(신규/정정/취소), 체결수량/체결가/미체결.
"""

from __future__ import annotations

from typing import Protocol

from app.models.order_dto import Fill, OpenOrder


def open_order_to_fill(oo: OpenOrder) -> Fill | None:
    """CIDBQ02400 행 → Fill. 체결분(exec_qty>0 + exec_no)만 변환, 아니면 None.

    event_seq = 'recon:'+OvrsFutsExecNo (체결번호 단위 멱등; 다중 체결 충돌 방지).
    체결번호가 없으면 'recon:'+OrdNo (체결 1건 가정 fallback).
    """
    if not oo.exec_qty or oo.exec_qty <= 0:
        return None
    seq = oo.exec_no or oo.broker_ord_no
    return Fill(
        broker_ord_no=oo.broker_ord_no,
        exec_qty=float(oo.exec_qty),
        exec_price=float(oo.exec_price or 0.0),
        remaining_qty=oo.remaining_qty,
        ord_status_code=oo.ord_status_code,
        origin="reconcile",
        event_seq=f"recon:{seq}",
        raw=oo.model_dump(mode="json"),
    )


class RealtimeFillSource(Protocol):
    """실시간 체결 소스(TC1/2/3) 인터페이스.

    M3b §10 스캐폴드에서 `app/adapters/realtime_future.RealtimeFutureFillSource` 가 구체화한다
    (TC2/TC3 구독 → `tc3_to_fill` 정규화 → `repo.apply_fill` 단일 writer). flag off 기본 ·
    런타임 READ_ONLY/RECONCILING 시 writer 강제 off. 체결 권위는 여전히 reconcile.
    """

    async def start(self, symbols: list[str] | None = None) -> None: ...
    async def stop(self) -> None: ...
