"""2단계 확인 토큰 (M4d §10.1 / §17 L1-10) — one-time·TTL·intent 바인딩.

무상태 토큰(아무 값이나 통과)이 아니라 서버측 저장 토큰임을 증명한다.
"""

from __future__ import annotations

from app.core.mode_matrix import MARKET_KOREA_STOCK
from app.models.order_dto import OrderIntent, OrderType, Side
from app.services.confirm_token import ConfirmTokenStore
from app.services.order_service import OrderService
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter


def _intent(**kw):
    base = dict(
        market=MARKET_KOREA_STOCK, symbol="005930", side=Side.BUY,
        order_type=OrderType.LIMIT, qty=1, price=50000,
    )
    base.update(kw)
    return OrderIntent(**base)


def test_token_is_one_time():
    store = ConfirmTokenStore()
    intent = _intent()
    tok = store.issue(intent)
    assert store.consume(tok, intent) is True   # 최초 소모 성공
    assert store.consume(tok, intent) is False  # 재사용 불가(one-time)


def test_token_bound_to_intent():
    store = ConfirmTokenStore()
    tok = store.issue(_intent(qty=1, price=50000))
    # 다른 내용(수량 변경)으로 commit 시도 → 실패(intent 바인딩).
    assert store.consume(tok, _intent(qty=99, price=50000)) is False


def test_token_expires():
    store = ConfirmTokenStore(ttl_s=-1)  # 발급 즉시 만료
    intent = _intent()
    tok = store.issue(intent)
    assert store.consume(tok, intent) is False  # 만료 → 거부


def test_unknown_or_empty_token_rejected():
    store = ConfirmTokenStore()
    intent = _intent()
    assert store.consume(None, intent) is False
    assert store.consume("nope", intent) is False


async def test_service_issue_and_check(monkeypatch):
    repo = await make_repo()
    patch_adapter(monkeypatch, FakeOrderAdapter())
    svc = OrderService(repo, session=None, settings=fake_settings(allow_live=True))
    intent = _intent()
    tok = svc.issue_confirm_token(intent)
    assert svc.check_confirm_token(intent, tok) is True   # 유효
    assert svc.check_confirm_token(intent, tok) is False  # 소모됨(one-time)
    assert svc.check_confirm_token(intent, "bogus") is False
