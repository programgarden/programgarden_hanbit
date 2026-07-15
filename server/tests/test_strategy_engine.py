"""자동매매 전략 엔진 (M5) — 임계값 규칙 + 안전 파이프라인 라우팅.

- ThresholdStrategy: -3% 하락 매수 / +5% 수익 청산 신호(순수).
- StrategyEngine.run_once: 마스터 토글 off → 발주 0; on → 신호를 order_service.place 로 라우팅.
- **자동경로 LIVE 는 게이트가 강제**: allow_live=false → 어댑터 미진입(실주문 0).
"""

from __future__ import annotations

from app.core.engine_state import EngineState
from app.core.mode_matrix import (
    BUCKET_LIVE,
    BUCKET_PAPER,
    MARKET_KOREA_STOCK,
    MARKET_OVERSEAS_FUTUREOPTION,
)
from app.models.dto import Quote
from app.models.order_dto import IntentKind, Side
from app.services.order_service import OrderService
from app.strategies.engine import StrategyEngine
from app.strategies.threshold import ThresholdStrategy
from tests._fut_helpers import FakeOrderAdapter, fake_settings, make_repo, patch_adapter

FUT = MARKET_OVERSEAS_FUTUREOPTION


def _q(symbol, market, price, change_rate):
    return Quote(symbol=symbol, market=market, price=price, change_rate=change_rate)


# ── ThresholdStrategy: 순수 신호 산출 ────────────────────────────────────────
def test_buy_signal_on_big_drop_when_flat():
    strat = ThresholdStrategy("t", FUT, ["ADZ25"], qty=1, buy_drop_pct=3.0)
    sigs = strat.evaluate({"ADZ25": _q("ADZ25", FUT, 0.65, -4.0)}, {})
    assert len(sigs) == 1
    assert sigs[0].side == Side.BUY and sigs[0].intent == IntentKind.ENTRY
    assert sigs[0].price == 0.65 and sigs[0].qty == 1


def test_no_buy_when_drop_too_small():
    strat = ThresholdStrategy("t", FUT, ["ADZ25"], buy_drop_pct=3.0)
    assert strat.evaluate({"ADZ25": _q("ADZ25", FUT, 0.65, -1.0)}, {}) == []


def test_sell_signal_on_profit_when_held():
    strat = ThresholdStrategy("t", FUT, ["ADZ25"], sell_profit_pct=5.0)
    quotes = {"ADZ25": _q("ADZ25", FUT, 106.0, 1.0)}
    positions = {"ADZ25": {"symbol": "ADZ25", "qty": 3, "avg_price": 100.0}}
    sigs = strat.evaluate(quotes, positions)
    assert len(sigs) == 1
    assert sigs[0].side == Side.SELL and sigs[0].intent == IntentKind.EXIT
    assert sigs[0].qty == 3  # 보유 전량 청산


def test_no_sell_when_profit_below_target():
    strat = ThresholdStrategy("t", FUT, ["ADZ25"], sell_profit_pct=5.0)
    quotes = {"ADZ25": _q("ADZ25", FUT, 103.0, 1.0)}
    positions = {"ADZ25": {"symbol": "ADZ25", "qty": 3, "avg_price": 100.0}}
    assert strat.evaluate(quotes, positions) == []


# ── StrategyEngine.run_once: 안전 파이프라인 라우팅 ──────────────────────────
async def _engine(monkeypatch, *, enabled, allow_live, quotes):
    repo = await make_repo()
    fake = FakeOrderAdapter()
    patch_adapter(monkeypatch, fake)
    svc = OrderService(repo, session=None, settings=fake_settings(allow_live=allow_live))
    svc.engine_for(BUCKET_PAPER).set(EngineState.ACTIVE)
    svc.engine_for(BUCKET_LIVE).set(EngineState.ACTIVE)

    async def quote_fn(market, symbol):
        return quotes[symbol]

    return StrategyEngine(svc, repo, quote_fn, enabled=enabled), svc, repo, fake


async def test_run_once_disabled_fires_nothing(monkeypatch):
    eng, _svc, _repo, fake = await _engine(
        monkeypatch, enabled=False, allow_live=False,
        quotes={"ADZ25": _q("ADZ25", FUT, 0.65, -4.0)},
    )
    eng.add_strategy(ThresholdStrategy("t", FUT, ["ADZ25"]))
    out = await eng.run_once()
    assert out == {"enabled": False, "fired": []}
    assert fake.calls == []  # 토글 off → 어댑터 미진입


async def test_run_once_paper_buy_routes_through_place(monkeypatch):
    eng, _svc, _repo, fake = await _engine(
        monkeypatch, enabled=True, allow_live=False,
        quotes={"ADZ25": _q("ADZ25", FUT, 0.65, -4.0)},
    )
    eng.add_strategy(ThresholdStrategy("t", FUT, ["ADZ25"], qty=1, buy_drop_pct=3.0))
    out = await eng.run_once()
    assert out["enabled"] is True and len(out["fired"]) == 1
    assert out["fired"][0]["ok"] is True and out["fired"][0]["side"] == "buy"
    assert any(c[0] == "place" for c in fake.calls)  # 신호가 실제 발주로 라우팅


async def test_run_once_live_signal_gated_when_allow_live_false(monkeypatch):
    eng, _svc, _repo, fake = await _engine(
        monkeypatch, enabled=True, allow_live=False,
        quotes={"005930": _q("005930", MARKET_KOREA_STOCK, 50000, -4.0)},
    )
    eng.add_strategy(ThresholdStrategy("kr", MARKET_KOREA_STOCK, ["005930"], qty=1))
    out = await eng.run_once()
    # 전략은 신호를 냈지만 게이트가 LIVE_DISABLED 로 막는다 → 어댑터 미진입(실주문 0).
    assert len(out["fired"]) == 1 and out["fired"][0]["ok"] is False
    assert "LIVE_DISABLED" in out["fired"][0]["decision"]["reasons"]
    assert not any(c[0] == "place" for c in fake.calls)


async def test_list_and_toggle(monkeypatch):
    eng, _svc, _repo, _fake = await _engine(
        monkeypatch, enabled=False, allow_live=False, quotes={},
    )
    eng.add_strategy(ThresholdStrategy("t", FUT, ["ADZ25"]))
    assert eng.list_strategies()[0]["name"] == "t"
    assert eng.enabled is False
    eng.set_enabled(True)
    assert eng.enabled is True
