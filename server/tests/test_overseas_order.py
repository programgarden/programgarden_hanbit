"""해외주식 주문 어댑터(OverseasStockOrderAdapter) 단위 테스트 — M4c.

fake 세션/파사드로 COSAT00301(신규/취소)/COSAT00311(정정)·COSAQ00102/COSOQ00201 호출 시
InBlock 구성과 응답 정규화를 검증한다. 실주문은 발사되지 않는다(fake).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.adapters.order_base import OrderError
from app.adapters.overseas_stock_order import OverseasStockOrderAdapter
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


def _ok_resp(ord_no=98765, rsp_cd="00000"):
    return SimpleNamespace(
        block2=SimpleNamespace(OrdNo=ord_no),
        rsp_cd=rsp_cd,
        rsp_msg="",
        error_msg=None,
        status_code=200,
    )


def _adapter(order_resp=None, accno_resp=None):
    facade = _FakeFacade(order_resp=order_resp, accno_resp=accno_resp)
    return OverseasStockOrderAdapter(_FakeSession(facade)), facade


def _intent(**kw):
    base = dict(
        market="overseas_stock", symbol="82:TSLA", side=Side.BUY,
        order_type=OrderType.LIMIT, qty=1, price=250.0, exchange="HKEX",
    )
    base.update(kw)
    return OrderIntent(**base)


# ── place (COSAT00301, OrdPtnCode 01/02) ────────────────────────────────────
async def test_place_limit_buy_builds_inblock():
    adapter, facade = _adapter(order_resp=_ok_resp(ord_no=555))
    ack = await adapter.place_order(_intent(symbol="82:TSLA", side=Side.BUY, price=250.0))
    assert ack.ok is True
    assert ack.broker_ord_no == "555"
    name, body = facade.calls[-1]
    assert name == "cosat00301"
    assert body.OrdPtnCode == "02"  # BUY=02
    assert body.OrdMktCode == "82"  # 심볼 prefix 82=NASDAQ
    assert body.IsuNo == "TSLA"  # 티커만
    assert body.OvrsOrdPrc == 250.0
    assert body.OrdprcPtnCode == "00"


async def test_place_sell_uses_ptn_01_and_exchange_hint():
    adapter, facade = _adapter(order_resp=_ok_resp())
    await adapter.place_order(_intent(symbol="AAPL", side=Side.SELL, exchange="81"))
    _name, body = facade.calls[-1]
    assert body.OrdPtnCode == "01"  # SELL=01
    assert body.OrdMktCode == "81"  # exchange 힌트 우선(NYSE/AMEX)
    assert body.IsuNo == "AAPL"


async def test_place_market_order_price_zero_ptn_03():
    adapter, facade = _adapter(order_resp=_ok_resp())
    await adapter.place_order(_intent(order_type=OrderType.MARKET, price=None))
    _name, body = facade.calls[-1]
    assert body.OvrsOrdPrc == 0.0
    assert body.OrdprcPtnCode == "03"


async def test_place_nonzero_error_rsp_rejected():
    adapter, _facade = _adapter(order_resp=_ok_resp(ord_no=3, rsp_cd="99999"))
    ack = await adapter.place_order(_intent())
    assert ack.ok is False  # rsp_cd != '00000' 이고 명시적이면 실패


# ── amend (COSAT00311, OrdPtnCode 07) / cancel (COSAT00301, OrdPtnCode 08) ───
async def test_amend_uses_cosat00311_ptn07():
    adapter, facade = _adapter(order_resp=_ok_resp())
    await adapter.amend_order(
        AmendRequest(org_ord_no="42", symbol="82:TSLA", side=Side.BUY, qty=2, price=260.0)
    )
    name, body = facade.calls[-1]
    assert name == "cosat00311"
    assert body.OrdPtnCode == "07"  # 정정 고정
    assert body.OrgOrdNo == 42


async def test_cancel_uses_cosat00301_ptn08_with_qty():
    adapter, facade = _adapter(order_resp=_ok_resp())
    await adapter.cancel_order(CancelRequest(org_ord_no="42", symbol="82:TSLA", qty=3))
    name, body = facade.calls[-1]
    assert name == "cosat00301"
    assert body.OrdPtnCode == "08"  # 취소(현금)
    assert body.OrgOrdNo == 42
    assert body.OrdQty == 3


async def test_cancel_requires_qty():
    adapter, _facade = _adapter(order_resp=_ok_resp())
    with pytest.raises(OrderError) as ei:
        await adapter.cancel_order(CancelRequest(org_ord_no="1", symbol="82:TSLA"))
    assert ei.value.code == "INVALID_QTY"


# ── 조회(reconcile 소스) ────────────────────────────────────────────────────
async def test_get_open_orders_normalizes_rows():
    row = SimpleNamespace(
        OrdNo=111, OrgOrdNo="", ShtnIsuNo="TSLA", BnsTpCode="2",
        OrdQty=5, OvrsOrdPrc=250.0, ExecQty=2, OvrsExecPrc=250.0, UnercQty=3,
        OrdPtnCode="02",
    )
    adapter, _facade = _adapter(accno_resp=SimpleNamespace(block3=[row]))
    orders = await adapter.get_open_orders("")
    assert len(orders) == 1
    oo = orders[0]
    assert oo.broker_ord_no == "111"
    assert oo.symbol == "TSLA"
    assert oo.side == Side.BUY
    assert oo.remaining_qty == 3  # UnercQty


async def test_get_positions_from_block4():
    row = SimpleNamespace(
        ShtnIsuNo="TSLA", AstkBalQty=4, FcstckUprc=240.0, OvrsScrtsCurpri=250.0,
        FcurrEvalPnlAmt=40.0, CrcyCode="USD", BaseXchrat=1400.0,
    )
    adapter, _facade = _adapter(accno_resp=SimpleNamespace(block4=[row]))
    positions = await adapter.get_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.symbol == "TSLA"
    assert p.qty == 4
    assert p.avg_price == 240.0
    assert p.currency == "USD"
    assert p.side == Side.BUY
