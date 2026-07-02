"""주문 어댑터 레지스트리 — 시장/모드 매트릭스를 코드로 못박는다.

``overseas_futureoption``(paper) 만 생성 가능. 국내주식·해외주식(실거래) 주문 경로는
M4까지 존재하지 않으므로 생성 자체를 거부한다(LIVE_DISABLED). place/amend/cancel/
killswitch 의 모든 어댑터 취득은 이 함수를 단일 관문으로 경유한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.order_base import BrokerOrderAdapter, OrderError
from app.adapters.overseas_future_order import OverseasFutureOrderAdapter
from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION

if TYPE_CHECKING:
    from app.core.sessions import SessionManager

# 주문 가능 시장 = 해외선물(paper) 단 하나. (KR/OVS 는 의도적으로 부재)
_ORDER_ADAPTERS = {
    MARKET_OVERSEAS_FUTUREOPTION: OverseasFutureOrderAdapter,
}


def make_order_adapter(market: str, session: SessionManager) -> BrokerOrderAdapter:
    """시장 키에 맞는 주문 어댑터 생성. FUT 외 시장은 LIVE_DISABLED 로 거부."""
    cls = _ORDER_ADAPTERS.get(market)
    if cls is None:
        raise OrderError(
            "LIVE_DISABLED",
            f"order path for market '{market}' is disabled until M4",
        )
    return cls(session)


__all__ = ["BrokerOrderAdapter", "OrderError", "make_order_adapter"]
