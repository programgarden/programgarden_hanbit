"""PortfolioAggregator (M3a §4) — 다통화 KRW 재집계·버킷 격리·필드분할 권위·청산경계 이중계상 0."""

from __future__ import annotations

from decimal import Decimal

from app.core.event_bus import EventBus
from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION
from app.models.portfolio_dto import BalanceSnap, PositionSnap, SnapSource
from app.portfolio.aggregator import PortfolioAggregator
from app.portfolio.fx import FxRateProvider
from app.portfolio.sources import fake_balances, fake_multi_ccy_book, make_tracker_callbacks
from tests._fut_helpers import make_repo

FUT = MARKET_OVERSEAS_FUTUREOPTION


def _fx(repo=None) -> FxRateProvider:
    # 라이브 없음 → 고정 1400/180(estimated). 결정론.
    return FxRateProvider(usd_krw=1400.0, hkd_krw=180.0, buffer_pct=0.02, ttl_s=300, repo=repo)


def test_multi_currency_krw_reaggregation():
    agg = PortfolioAggregator(_fx())
    for snap in fake_multi_ccy_book(bucket="paper"):
        agg.apply_position(snap)
    tick = agg.consistent_tick("paper")
    # ADZ25(USD): 2*0.70*100*1400=196000 ; HSIQ25(HKD): 1*178*10*180=320400
    assert round(tick.total_eval_krw) == 196000 + 320400
    # buy: 2*0.65*100*1400=182000 ; 1*180*10*180=324000
    assert round(tick.total_buy_krw) == 182000 + 324000
    # pnl_amount 권위(통화 총손익): USD 10*1400=14000 + HKD 20*180=3600 (숏 부호 보존)
    assert round(tick.total_pnl_krw) == 14000 + 3600
    assert tick.fx_estimated is True  # 고정환율 fallback
    assert tick.position_count == 2


def test_kpi_concentration_and_currency_split():
    agg = PortfolioAggregator(_fx())
    for snap in fake_multi_ccy_book("paper"):
        agg.apply_position(snap)
    kpi = agg.kpi("paper")
    assert set(kpi.by_currency) == {"USD", "HKD"}
    # eval 196000 vs 320400 → 합 516400. 비중 합 ≈ 1.
    assert abs(sum(kpi.by_currency.values()) - 1.0) < 1e-9
    assert kpi.currency_hhi is not None and 0 < kpi.currency_hhi <= 1.0


def test_bucket_isolation_no_cross_leak():
    agg = PortfolioAggregator(_fx())
    agg.apply_position(
        PositionSnap(
            bucket="paper",
            market=FUT,
            symbol="ADZ25",
            currency="USD",
            qty=Decimal(1),
            multiplier=Decimal(100),
            current_price=Decimal("0.70"),
            avg_price=Decimal("0.70"),
            source=SnapSource.FAKE,
        )
    )
    agg.apply_position(
        PositionSnap(
            bucket="live",
            market="korea_stock",
            symbol="005930",
            currency="KRW",
            qty=Decimal(10),
            multiplier=Decimal(1),
            current_price=Decimal("70000"),
            avg_price=Decimal("70000"),
            source=SnapSource.FAKE,
        )
    )
    paper = agg.consistent_tick("paper")
    live = agg.consistent_tick("live")
    assert paper.position_count == 1 and live.position_count == 1
    # paper KPI 분모에 live 가 안 섞임
    assert {p["symbol"] for p in paper.positions} == {"ADZ25"}
    assert {p["symbol"] for p in live.positions} == {"005930"}


def test_liquidation_boundary_no_double_count():
    """포지션 청산(qty→0)이 unrealized 에서 빠지고 realized(balance)에 한 번만 — 같은 tick."""
    agg = PortfolioAggregator(_fx())
    # 보유 + 미실현
    agg.apply_position(
        PositionSnap(
            bucket="paper",
            market=FUT,
            symbol="ADZ25",
            currency="USD",
            qty=Decimal(1),
            multiplier=Decimal(100),
            avg_price=Decimal("0.65"),
            current_price=Decimal("0.70"),
            pnl_amount=Decimal("5"),
            source=SnapSource.FAKE,
        )
    )
    t1 = agg.consistent_tick("paper")
    assert t1.position_count == 1 and round(t1.total_pnl_krw) == 5 * 1400
    # 청산: qty=0 포지션 snap + 실현 5 USD 잔고 반영
    agg.apply_position(
        PositionSnap(
            bucket="paper",
            market=FUT,
            symbol="ADZ25",
            currency="USD",
            qty=Decimal(0),
            source=SnapSource.FAKE,
        )
    )
    # 중간 tick(청산 적용·잔고 미적용): unrealized 빠지고 realized 아직 0 → phantom 합산 0(리뷰 #9)
    mid = agg.consistent_tick("paper")
    assert mid.position_count == 0
    assert round(mid.total_pnl_krw) == 0 and round(mid.realized_krw) == 0
    agg.apply_balance(
        BalanceSnap(
            bucket="paper",
            market=FUT,
            currency="USD",
            realized_pnl=Decimal("5"),
            source=SnapSource.FAKE,
        )
    )
    t2 = agg.consistent_tick("paper")
    assert t2.position_count == 0  # unrealized 에서 빠짐(중복 0)
    assert round(t2.total_pnl_krw) == 0
    assert round(t2.realized_krw) == 5 * 1400  # realized 한 번만


async def test_persist_and_publish_writes_kpi_marks_and_bus():
    repo = await make_repo()  # FUT 계좌 시드 + ADZ25 instrument
    bus = EventBus()
    q = bus.subscribe()
    agg = PortfolioAggregator(_fx(repo), repo=repo, bus=bus)
    agg.apply_position(
        PositionSnap(
            bucket="paper",
            market=FUT,
            symbol="ADZ25",
            currency="USD",
            qty=Decimal(2),
            multiplier=Decimal(100),
            avg_price=Decimal("0.65"),
            current_price=Decimal("0.70"),
            pnl_amount=Decimal("10"),
            source=SnapSource.FAKE,
        )
    )
    for b in fake_balances("paper"):
        agg.apply_balance(b)
    kpi = await agg.persist_and_publish("paper")
    # bucket_kpi 영속
    row = await repo.get_latest_bucket_kpi("paper")
    assert row is not None and round(row["total_eval_krw"]) == 196000
    # 포지션 보강(marks) 영속 — eval_krw 채워짐, qty 는 권위(reconcile) 부재라 0(보강만 들어옴).
    # 그래서 positions_for(qty!=0 필터)가 아니라 list_positions 로 확인.
    positions = await repo.list_positions(await repo.get_account_id(FUT))
    p = next(r for r in positions if r["eval_krw"] is not None)
    assert round(p["eval_krw"]) == 196000 and p["current_price"] == 0.70
    assert p["fx_estimated"] == 1  # 고정환율 fallback 전파(리뷰 #18)
    assert p["fx_at_buy"] == p["fx_now"]  # 취득시점 환율 고정(리뷰 #13)
    # 잔고 스냅샷
    bals = await repo.list_balances(await repo.get_account_id(FUT))
    assert any(b["currency"] == "USD" and b["orderable_amount"] == 8000 for b in bals)
    # WS publish
    msg = q.get_nowait()
    assert msg["topic"] == "portfolio_snapshot" and msg["data"]["bucket"] == "paper"
    assert kpi.position_count == 1


async def test_tracker_callbacks_value_binding():
    """make_tracker_callbacks 가 (bucket,market) 을 값으로 고정 — 늦은바인딩 버그 0(§3)."""
    agg = PortfolioAggregator(_fx())
    cbs = {}
    # 루프 안에서 팩토리로 콜백 생성 — 늦은바인딩이면 둘 다 마지막 버킷(live)에 기록됨.
    for bucket, market in [("paper", FUT), ("live", "korea_stock")]:
        cbs[bucket] = make_tracker_callbacks(bucket, market, agg)
    await cbs["paper"]["on_position_change"](
        [
            {
                "symbol": "ADZ25",
                "quantity": 1,
                "is_long": True,
                "currency": "USD",
                "current_price": 0.7,
                "buy_price": 0.65,
            }
        ]
    )
    await cbs["live"]["on_position_change"](
        [
            {
                "symbol": "005930",
                "quantity": 5,
                "currency": "KRW",
                "current_price": 70000,
                "buy_price": 70000,
            }
        ]
    )
    assert {p["symbol"] for p in agg.consistent_tick("paper").positions} == {"ADZ25"}
    assert {p["symbol"] for p in agg.consistent_tick("live").positions} == {"005930"}


async def test_persist_observes_live_fx_priority_one():
    """overseas 스냅 exchange_rate → persist 시 fx 캐시 주입(우선순위 ①) → 라이브 채택(리뷰 #6)."""
    repo = await make_repo()
    fx = _fx(repo)
    assert fx.to_krw("USD") == (1400.0, True)  # 처음엔 고정환율(estimated)
    agg = PortfolioAggregator(fx, repo=repo)
    agg.apply_position(
        PositionSnap(
            bucket="live",
            market="overseas_stock",
            symbol="AAPL",
            currency="USD",
            qty=Decimal(1),
            multiplier=Decimal(1),
            avg_price=Decimal("100"),
            current_price=Decimal("100"),
            exchange_rate=Decimal("1320"),  # 라이브 제공 환율
            source=SnapSource.TRACKER,
        )
    )
    await agg.persist_and_publish("live")
    rate, est = fx.to_krw("USD")
    assert rate == 1320.0 and est is False  # 우선순위 ① 라이브 환율 채택
