"""주문 어댑터 레지스트리 — 시장/모드 매트릭스를 코드로 못박는다.

``overseas_futureoption``(paper)은 항상 생성 가능. 국내주식·해외주식(LIVE, M4b/M4c)은
**HANBIT_ALLOW_LIVE=true 일 때만** 생성 가능(allow_live 단일 관문 §4.4) — 마스터 토글이
false 면 LIVE_DISABLED. place/amend/cancel/killswitch 의 모든 어댑터 취득은 이 함수를 단일
관문으로 경유한다(3중 방어 중 한 겹 — 나머지는 게이트 step2·부트 boot_engine).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.korea_stock_order import KoreaStockOrderAdapter
from app.adapters.order_base import BrokerOrderAdapter, OrderError
from app.adapters.overseas_future_order import OverseasFutureOrderAdapter
from app.adapters.overseas_stock_order import OverseasStockOrderAdapter
from app.core.mode_matrix import (
    MARKET_KOREA_STOCK,
    MARKET_OVERSEAS_FUTUREOPTION,
    MARKET_OVERSEAS_STOCK,
)

if TYPE_CHECKING:
    from app.core.sessions import SessionManager

# 주문 가능 시장: 해외선물(paper) + 국내/해외주식(LIVE, M4b/M4c). LIVE 시장은 allow_live 게이트가
# 생성 자체를 막는다(아래 make_order_adapter) — 클래스 등록 ≠ 생성 허용.
_ORDER_ADAPTERS = {
    MARKET_OVERSEAS_FUTUREOPTION: OverseasFutureOrderAdapter,
    MARKET_KOREA_STOCK: KoreaStockOrderAdapter,
    MARKET_OVERSEAS_STOCK: OverseasStockOrderAdapter,
}
# LIVE 버킷 시장 — allow_live 마스터 토글의 게이트 대상(§4.4).
_LIVE_MARKETS = frozenset({MARKET_KOREA_STOCK, MARKET_OVERSEAS_STOCK})


def make_order_adapter(
    market: str, session: SessionManager, *, allow_live: bool = False
) -> BrokerOrderAdapter:
    """시장 키에 맞는 주문 어댑터 생성.

    allow_live 게이트를 registry 에 박아 단일 관문 유지(§4.4) — place/amend/cancel/
    killswitch 의 모든 어댑터 취득이 이 함수를 경유한다. 기본값 False(fail-closed):
    FUT 는 allow_live 무관(paper), LIVE 시장(KR/OVS)은 allow_live=false → LIVE_DISABLED.
    allow_live=true 면 KR/OVS 어댑터를 생성한다(M4b/M4c).
    """
    if market in _LIVE_MARKETS and not allow_live:
        raise OrderError(
            "LIVE_DISABLED",
            f"live order path for '{market}' is closed (HANBIT_ALLOW_LIVE=false)",
        )
    cls = _ORDER_ADAPTERS.get(market)
    if cls is None:
        raise OrderError(
            "LIVE_DISABLED",
            f"order path for market '{market}' is disabled",
        )
    return cls(session)


def order_tr_labels(market: str) -> tuple[str | None, str | None, str | None]:
    """시장의 (신규, 정정, 취소) TR 코드 라벨 — order_service 의 tr_code 감사기록용.

    실제 TR 리터럴은 각 어댑터 파일에만 존재(read-only 불변식 스코프). order_service 는 이
    헬퍼로 라벨만 얻어 orders.tr_code 컬럼에 기록한다(리터럴을 직접 담지 않는다). 미지 시장은
    (None, None, None).
    """
    cls = _ORDER_ADAPTERS.get(market)
    if cls is None:
        return (None, None, None)
    return (
        getattr(cls, "tr_new", None),
        getattr(cls, "tr_amend", None),
        getattr(cls, "tr_cancel", None),
    )


__all__ = [
    "BrokerOrderAdapter",
    "OrderError",
    "make_order_adapter",
    "order_tr_labels",
]
