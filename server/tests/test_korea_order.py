"""국내주식 주문 어댑터(KoreaStockOrderAdapter) 단위 테스트 — M4b.

fake 세션/파사드로 CSPAT00601/00701/00801·CSPAQ13700/12300 호출 시 InBlock 구성과 응답
정규화(_ack, OpenOrder/Position)를 검증한다. 실주문은 발사되지 않는다(fake).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.adapters.korea_stock_order import KoreaStockOrderAdapter
from app.adapters.order_base import OrderError
from app.models.order_dto import (
    AmendRequest,
    CancelRequest,
    OrderIntent,
    OrderType,
    Side,
)


class _FakeTR:
    def __init__(self, resp):
        self._resp = resp

    async def req_async(self):
        return self._resp


class _FakeSubFacade:
    """order()/accno() 파사드 — 호출한 메서드명+body 를 기록하고 고정 resp 를 돌려준다."""

    def __init__(self, resp, calls):
        self._resp = resp
        self._calls = calls

    def __getattr__(self, name):
        def _method(body, options=None):
            self._calls.append((name, body))
            return _FakeTR(self._resp)

        return _method


class _FakeFacade:
    def __init__(self, order_resp=None, accno_resp=None):
        self.calls: list[tuple] = []
        self._order = _FakeSubFacade(order_resp, self.calls)
        self._accno = _FakeSubFacade(accno_resp, self.calls)

    def order(self):
        return self._order

    def accno(self):
        return self._accno


class _FakeSession:
    def __init__(self, facade):
        self._facade = facade

    def client_for(self, market):
        return self._facade

    def quote_opts(self):
        return None


def _ok_resp(ord_no=12345, rsp_cd="00040"):
    return SimpleNamespace(
        block2=SimpleNamespace(OrdNo=ord_no),
        rsp_cd=rsp_cd,
        rsp_msg="",
        error_msg=None,
        status_code=200,
    )


def _adapter(order_resp=None, accno_resp=None):
    facade = _FakeFacade(order_resp=order_resp, accno_resp=accno_resp)
    return KoreaStockOrderAdapter(_FakeSession(facade)), facade


# ── place ─────────────────────────────────────────────────────────────────
async def test_place_limit_buy_builds_inblock_and_acks():
    adapter, facade = _adapter(order_resp=_ok_resp(ord_no=777, rsp_cd="00040"))
    ack = await adapter.place_order(
        OrderIntent(
            market="korea_stock", symbol="005930", side=Side.BUY,
            order_type=OrderType.LIMIT, qty=1, price=70000,
        )
    )
    assert ack.ok is True
    assert ack.broker_ord_no == "777"  # int OrdNo → str 정규화
    name, body = facade.calls[-1]
    assert name == "cspat00601"
    assert body.BnsTpCode == "2"  # BUY=2
    assert body.OrdprcPtnCode == "00"  # 지정가
    assert body.OrdPrc == 70000
    assert body.MbrNo == ""  # 기본 KRX


async def test_place_market_sell_sets_price_zero_and_ptn_03():
    adapter, facade = _adapter(order_resp=_ok_resp(rsp_cd="00039"))
    await adapter.place_order(
        OrderIntent(
            market="korea_stock", symbol="005930", side=Side.SELL,
            order_type=OrderType.MARKET, qty=2,
        )
    )
    _name, body = facade.calls[-1]
    assert body.BnsTpCode == "1"  # SELL=1
    assert body.OrdprcPtnCode == "03"  # 시장가
    assert body.OrdPrc == 0.0


async def test_place_nxt_venue_routes_mbrno():
    adapter, facade = _adapter(order_resp=_ok_resp())
    await adapter.place_order(
        OrderIntent(
            market="korea_stock", symbol="005930", side=Side.BUY,
            order_type=OrderType.LIMIT, qty=1, price=70000, exchange="NXT",
        )
    )
    _name, body = facade.calls[-1]
    assert body.MbrNo == "NXT"  # 정규장 종료 후 대체거래소 라우팅


async def test_place_zero_ordno_is_rejected():
    adapter, _facade = _adapter(order_resp=_ok_resp(ord_no=0))
    ack = await adapter.place_order(
        OrderIntent(
            market="korea_stock", symbol="005930", side=Side.BUY,
            order_type=OrderType.LIMIT, qty=1, price=70000,
        )
    )
    assert ack.ok is False  # OrdNo 미발급 → 실패
    assert ack.broker_ord_no is None


async def test_place_error_msg_rejected_even_with_success_rsp():
    resp = _ok_resp(ord_no=5, rsp_cd="00040")
    resp.error_msg = "boom"
    adapter, _facade = _adapter(order_resp=resp)
    ack = await adapter.place_order(
        OrderIntent(
            market="korea_stock", symbol="005930", side=Side.BUY,
            order_type=OrderType.LIMIT, qty=1, price=70000,
        )
    )
    assert ack.ok is False


# ── amend / cancel ─────────────────────────────────────────────────────────
async def test_amend_requires_price():
    adapter, _facade = _adapter(order_resp=_ok_resp())
    with pytest.raises(OrderError) as ei:
        await adapter.amend_order(
            AmendRequest(org_ord_no="1", symbol="005930", side=Side.BUY, qty=1, price=None)
        )
    assert ei.value.code == "INVALID_PRICE"


async def test_cancel_requires_qty():
    adapter, _facade = _adapter(order_resp=_ok_resp())
    with pytest.raises(OrderError) as ei:
        await adapter.cancel_order(CancelRequest(org_ord_no="1", symbol="005930"))
    assert ei.value.code == "INVALID_QTY"


async def test_cancel_builds_inblock_with_qty():
    adapter, facade = _adapter(order_resp=_ok_resp())
    await adapter.cancel_order(CancelRequest(org_ord_no="42", symbol="005930", qty=7))
    name, body = facade.calls[-1]
    assert name == "cspat00801"
    assert body.OrgOrdNo == 42  # str → int
    assert body.OrdQty == 7


# ── 조회(reconcile 소스) ────────────────────────────────────────────────────
async def test_get_open_orders_normalizes_rows():
    row = SimpleNamespace(
        OrdNo=111, OrgOrdNo="", IsuNo="005930", BnsTpCode="2",
        OrdQty=10, OrdPrc=70000, ExecQty=3, ExecPrc=70000, RmnOrdQty=7,
        OrdprcPtnCode="00",
    )
    adapter, _facade = _adapter(accno_resp=SimpleNamespace(block3=[row]))
    orders = await adapter.get_open_orders("")  # list-all(종목 무시)
    assert len(orders) == 1
    oo = orders[0]
    assert oo.broker_ord_no == "111"
    assert oo.side == Side.BUY
    assert oo.exec_qty == 3
    assert oo.remaining_qty == 7


async def test_get_positions_normalizes_rows_and_skips_zero():
    held = SimpleNamespace(IsuNo="005930", BalQty=10, AvrUprc=68000, NowPrc=70000, EvalPnl=20000)
    empty = SimpleNamespace(IsuNo="000660", BalQty=0, AvrUprc=0, NowPrc=0, EvalPnl=0)
    resp = SimpleNamespace(block3=[held, empty], block2=SimpleNamespace(MnyOrdAbleAmt=22334))
    adapter, _facade = _adapter(accno_resp=resp)
    positions = await adapter.get_positions()
    assert len(positions) == 1  # 잔고 0 행 스킵
    p = positions[0]
    assert p.symbol == "005930"
    assert p.qty == 10
    assert p.avg_price == 68000
    assert p.side == Side.BUY  # 국내주식 롱 온리
    assert p.currency == "KRW"
    assert p.orderable_amount == 22334  # block2.MnyOrdAbleAmt
