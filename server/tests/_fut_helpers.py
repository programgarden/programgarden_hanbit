"""해외선물 주문 서비스 테스트용 공용 fake/헬퍼."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION
from app.models.order_dto import OpenOrder, OrderAck, Position
from app.repositories.db import init_db
from app.repositories.orders_repo import OrdersRepo


class FakeOrderAdapter:
    """make_order_adapter 를 대체하는 가짜 어댑터(응답 주입 가능)."""

    market = MARKET_OVERSEAS_FUTUREOPTION

    def __init__(self) -> None:
        self.place_ack = OrderAck(ok=True, broker_ord_no="O-1", rsp_cd="00000")
        self.place_raises: Exception | None = None
        self.amend_ack = OrderAck(ok=True, broker_ord_no="O-2", rsp_cd="00000")
        self.cancel_ack = OrderAck(ok=True, broker_ord_no="O-1", rsp_cd="00000")
        self.cancel_raises: Exception | None = None  # 취소 경로 예외 주입(LIVE_DISABLED 미삼킴 등)
        self.open_orders: dict[str, list[OpenOrder]] = {}
        self.positions: list[Position] = []
        self.positions_raises: Exception | None = None  # boot 포지션 동기화 실패 시뮬레이션
        self.calls: list[tuple] = []

    async def place_order(self, intent):
        self.calls.append(("place", intent))
        if self.place_raises is not None:
            raise self.place_raises
        return self.place_ack

    async def amend_order(self, req):
        self.calls.append(("amend", req))
        return self.amend_ack

    async def cancel_order(self, req):
        self.calls.append(("cancel", req))
        if self.cancel_raises is not None:
            raise self.cancel_raises
        return self.cancel_ack

    async def get_open_orders(self, symbol, *, start_date=None, end_date=None):
        self.calls.append(("get_open_orders", symbol))
        return self.open_orders.get(symbol, [])

    async def get_positions(self):
        self.calls.append(("get_positions",))
        if self.positions_raises is not None:
            raise self.positions_raises
        return self.positions


async def make_repo() -> OrdersRepo:
    path = Path(tempfile.mkdtemp(prefix="hanbit-os-")) / "t.db"
    await init_db(str(path))
    repo = OrdersRepo(str(path))
    await repo.ensure_instrument(MARKET_OVERSEAS_FUTUREOPTION, "ADZ25", exchange="HKEX")
    await repo.set_whitelisted(MARKET_OVERSEAS_FUTUREOPTION, "ADZ25", True)
    return repo


def fake_settings(
    engine: str = "PAPER_TRADING", *, realtime_fills: bool = False, allow_live: bool = False
) -> SimpleNamespace:
    return SimpleNamespace(
        hanbit_engine_state=engine,
        engine_trading_enabled=(engine == "PAPER_TRADING"),
        # M4a §2 LIVE 마스터 토글 + 소액 캡/가드(registry allow_live 게이트가 읽는다).
        hanbit_allow_live=allow_live,
        hanbit_live_per_order_cap_krw=100_000,
        hanbit_live_per_order_cap_usd=50.0,
        hanbit_live_first_order_guard=True,
        hanbit_live_daily_notional_cap_krw=300_000,
        # M3a FX (FxRateProvider.from_settings)
        hanbit_fx_usd_krw=1400.0,
        hanbit_fx_hkd_krw=180.0,
        hanbit_fx_buffer_pct=0.02,
        hanbit_fx_ttl_s=300,
        # M3b §8 계좌-TR 직렬 큐 — 테스트는 간격 강제 없이 직렬만(슬립 회피)
        hanbit_tr_min_interval_ms=0,
        # M3b §10 실시간 체결 스캐폴드 flag(기본 off)
        hanbit_realtime_fills=realtime_fills,
        realtime_fills_enabled=realtime_fills,
    )


def patch_adapter(monkeypatch, fake: FakeOrderAdapter) -> None:
    # M4a §3.4/§17 L3-1: make_order_adapter 시그니처에 keyword-only `allow_live` 가 추가됐다.
    # 대체 람다가 **kwargs 로 이를 수용하지 않으면 기존 테스트가 TypeError 로 깨진다.
    monkeypatch.setattr(
        "app.services.order_service.make_order_adapter",
        lambda market, session, **kwargs: fake,
    )
