"""해외선물 주문 어댑터 단위 테스트 — fake LS facade.

검증: OrderIntent→CIDBT00100 매핑(BnsTpCode 반전, MARKET/LIMIT, OrdNo=block2.OvrsFutsOrdNo),
성공판정(rsp_cd '00000'/비-00000/error_msg/status/무 OrdNo), 정정/취소 매핑, 조회 파싱,
세션 바인딩, registry 의 KR/OVS 차단(no-live-facade).
"""

from __future__ import annotations

import re
import types

import pytest
from programgarden_finance.ls.models import SetupOptions

from app.adapters.order_base import OrderError
from app.adapters.order_registry import make_order_adapter
from app.adapters.overseas_future_order import OverseasFutureOrderAdapter
from app.core.mode_matrix import (
    MARKET_KOREA_STOCK,
    MARKET_OVERSEAS_FUTUREOPTION,
    MARKET_OVERSEAS_STOCK,
)
from app.models.order_dto import (
    AmendRequest,
    CancelRequest,
    OrderIntent,
    OrderType,
    Side,
)


# ── fake LS ────────────────────────────────────────────────────────────────
class _FakeTr:
    def __init__(self, response):
        self._response = response

    async def req_async(self):
        return self._response


class _Recorder:
    """order()/accno() TR 호출을 기록하고 정해진 응답을 돌려준다."""

    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def __getattr__(self, name):
        def _method(*, body, options=None, header=None):
            self.calls.append({"name": name, "body": body, "options": options})
            return _FakeTr(self._response)

        return _method


class _FakeFacade:
    def __init__(self, order_resp=None, accno_resp=None):
        self._order = _Recorder(order_resp)
        self._accno = _Recorder(accno_resp)

    def order(self):
        return self._order

    def accno(self):
        return self._accno


class _FakeSession:
    def __init__(self, market, facade):
        self._market = market
        self._facade = facade

    def client_for(self, market):
        assert market == self._market
        return self._facade

    def quote_opts(self):
        return SetupOptions(on_rate_limit="wait", rate_limit_count=2, rate_limit_seconds=1)


def _resp(*, ord_no="ORD123", rsp_cd="00000", status_code=200, error_msg=None, block2=None):
    if block2 is None and ord_no is not None:
        block2 = types.SimpleNamespace(OvrsFutsOrdNo=ord_no)
    return types.SimpleNamespace(
        block2=block2,
        rsp_cd=rsp_cd,
        rsp_msg="ok",
        status_code=status_code,
        error_msg=error_msg,
    )


def _adapter(order_resp=None, accno_resp=None):
    facade = _FakeFacade(order_resp=order_resp, accno_resp=accno_resp)
    session = _FakeSession(MARKET_OVERSEAS_FUTUREOPTION, facade)
    return OverseasFutureOrderAdapter(session), facade


# ── place ────────────────────────────────────────────────────────────────
async def test_place_limit_buy_maps_cidbt00100():
    adapter, facade = _adapter(order_resp=_resp(ord_no="O-1"))
    ack = await adapter.place_order(
        OrderIntent(symbol="ADZ25", side=Side.BUY, order_type=OrderType.LIMIT, qty=2, price=0.65)
    )
    call = facade.order().calls[0]
    body = call["body"]
    assert call["name"] == "CIDBT00100"
    assert body.FutsOrdTpCode == "1"
    assert body.BnsTpCode == "2"  # BUY → 2
    assert body.AbrdFutsOrdPtnCode == "2"  # LIMIT
    assert body.OvrsDrvtOrdPrc == 0.65
    assert body.OrdQty == 2
    assert body.IsuCodeVal == "ADZ25"
    assert re.fullmatch(r"\d{8}", body.OrdDt)
    assert call["options"].on_rate_limit == "wait"
    assert ack.ok is True and ack.broker_ord_no == "O-1"


async def test_place_market_sell_sets_price_zero_and_bns_one():
    adapter, facade = _adapter(order_resp=_resp())
    await adapter.place_order(
        OrderIntent(symbol="ADZ25", side=Side.SELL, order_type=OrderType.MARKET, qty=1)
    )
    body = facade.order().calls[0]["body"]
    assert body.BnsTpCode == "1"  # SELL → 1
    assert body.AbrdFutsOrdPtnCode == "1"  # MARKET
    assert body.OvrsDrvtOrdPrc == 0.0


async def test_place_limit_without_price_raises():
    adapter, _ = _adapter(order_resp=_resp())
    with pytest.raises(OrderError) as ei:
        await adapter.place_order(
            OrderIntent(symbol="ADZ25", side=Side.BUY, order_type=OrderType.LIMIT, qty=1)
        )
    assert ei.value.code == "INVALID_PRICE"


@pytest.mark.parametrize(
    "kwargs,expected_ok",
    [
        ({"rsp_cd": "00000", "ord_no": "X"}, True),
        ({"rsp_cd": "0040", "ord_no": "X"}, False),  # 비-00000 반례
        ({"rsp_cd": "00000", "error_msg": "boom", "ord_no": "X"}, False),
        ({"rsp_cd": "00000", "status_code": 500, "ord_no": "X"}, False),
        ({"rsp_cd": "00000", "ord_no": None}, False),  # OrdNo 없음
    ],
)
async def test_success_judgment(kwargs, expected_ok):
    adapter, _ = _adapter(order_resp=_resp(**kwargs))
    ack = await adapter.place_order(
        OrderIntent(symbol="ADZ25", side=Side.BUY, order_type=OrderType.MARKET, qty=1)
    )
    assert ack.ok is expected_ok


# ── amend / cancel ─────────────────────────────────────────────────────────
async def test_amend_maps_cidbt00900():
    adapter, facade = _adapter(order_resp=_resp(ord_no="O-2"))
    ack = await adapter.amend_order(
        AmendRequest(org_ord_no="O-1", symbol="ADZ25", side=Side.BUY, qty=3, price=0.7)
    )
    call = facade.order().calls[0]
    body = call["body"]
    assert call["name"] == "CIDBT00900"
    assert body.FutsOrdTpCode == "2"
    assert body.OvrsFutsOrgOrdNo == "O-1"
    assert body.OvrsDrvtOrdPrc == 0.7
    assert body.OrdQty == 3
    assert ack.broker_ord_no == "O-2"


async def test_cancel_maps_cidbt01000():
    adapter, facade = _adapter(order_resp=_resp(ord_no="O-3"))
    await adapter.cancel_order(CancelRequest(org_ord_no="O-1", symbol="ADZ25"))
    call = facade.order().calls[0]
    body = call["body"]
    assert call["name"] == "CIDBT01000"
    assert body.FutsOrdTpCode == "3"
    assert body.OvrsFutsOrgOrdNo == "O-1"


# ── 조회 파싱 ────────────────────────────────────────────────────────────────
async def test_get_open_orders_parses_cidbq02400():
    row = types.SimpleNamespace(
        OvrsFutsOrdNo="O-1",
        OvrsFutsOrgOrdNo="",
        OvrsFutsExecNo="E-9",
        IsuCodeVal="ADZ25",
        BnsTpCode="2",
        OrdQty=5,
        OvrsDrvtOrdPrc=0.65,
        ExecQty=2,
        AbrdFutsExecPrc=0.64,
        UnercQty=3,
        FutsOrdStatCode="1",
    )
    resp = types.SimpleNamespace(block2=[row])
    adapter, facade = _adapter(accno_resp=resp)
    orders = await adapter.get_open_orders("ADZ25")
    call = facade.accno().calls[0]
    assert call["name"] == "CIDBQ02400"
    assert call["body"].IsuCodeVal == "ADZ25"
    assert re.fullmatch(r"\d{8}", call["body"].QrySrtDt)
    assert len(orders) == 1
    oo = orders[0]
    assert oo.broker_ord_no == "O-1" and oo.exec_no == "E-9"
    assert oo.side == Side.BUY
    assert oo.exec_qty == 2 and oo.remaining_qty == 3


async def test_get_positions_parses_cidbq01500_and_skips_empty_symbol():
    rows = [
        types.SimpleNamespace(
            IsuCodeVal="ADZ25", BalQty=2, PchsPrc=0.64, OvrsDrvtNowPrc=0.66,
            AbrdFutsEvalPnlAmt=40.0, BnsTpCode="2", CrcyCodeVal="USD", OrdAbleAmt=5000.0,
        ),
        types.SimpleNamespace(  # 포지션 없는 계좌-only 행 → 스킵
            IsuCodeVal="", BalQty=0, PchsPrc=0, OvrsDrvtNowPrc=0,
            AbrdFutsEvalPnlAmt=0, BnsTpCode="", CrcyCodeVal="USD", OrdAbleAmt=5000.0,
        ),
    ]
    resp = types.SimpleNamespace(block2=rows)
    adapter, _ = _adapter(accno_resp=resp)
    positions = await adapter.get_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.symbol == "ADZ25" and p.qty == 2 and p.side == Side.BUY
    assert p.orderable_amount == 5000.0 and p.currency == "USD"


# ── registry: KR/OVS 주문 경로 부재 ─────────────────────────────────────────
def test_registry_allows_only_overseas_future():
    session = _FakeSession(MARKET_OVERSEAS_FUTUREOPTION, _FakeFacade())
    adapter = make_order_adapter(MARKET_OVERSEAS_FUTUREOPTION, session)
    assert isinstance(adapter, OverseasFutureOrderAdapter)


@pytest.mark.parametrize("market", [MARKET_KOREA_STOCK, MARKET_OVERSEAS_STOCK])
def test_registry_blocks_live_markets(market):
    with pytest.raises(OrderError) as ei:
        make_order_adapter(market, _FakeSession(market, _FakeFacade()))
    assert ei.value.code == "LIVE_DISABLED"
