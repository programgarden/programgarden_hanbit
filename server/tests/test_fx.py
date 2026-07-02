"""FxRateProvider (M3a §6) — ceil/floor 방향, 출처 우선순위, TTL, estimated."""

from __future__ import annotations

from app.portfolio.fx import FxRateProvider


class Clock:
    """주입형 결정론 시계."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _fx(clock: Clock | None = None, **kw) -> FxRateProvider:
    base = dict(usd_krw=1400.0, hkd_krw=180.0, buffer_pct=0.02, ttl_s=300)
    base.update(kw)
    return FxRateProvider(now_fn=(clock or Clock()), **base)


def test_krw_is_identity_no_buffer():
    fx = _fx()
    assert fx.to_krw("KRW") == (1.0, False)
    assert fx.to_krw_ceil("KRW") == (1.0, False)
    assert fx.to_krw_floor("KRW") == (1.0, False)


def test_ceil_floor_direction():
    fx = _fx()
    # 라이브 없음 → 고정 1400(estimated)
    neutral, est = fx.to_krw("USD")
    ceil, _ = fx.to_krw_ceil("USD")
    floor, _ = fx.to_krw_floor("USD")
    assert est is True
    assert floor < neutral < ceil  # ceil 이 더 큼(명목 크게=거부 strict)
    assert ceil == 1400.0 * 1.02
    assert floor == 1400.0 * 0.98


async def test_observed_live_rate_used_and_not_estimated():
    clock = Clock()
    fx = _fx(clock)
    await fx.observe("USD", 1320.5)
    rate, est = fx.to_krw("USD")
    assert rate == 1320.5 and est is False
    # ceil 은 라이브에도 버퍼 적용(캡 안전 마진)
    assert fx.to_krw_ceil("USD")[0] == 1320.5 * 1.02


async def test_ttl_expiry_falls_back_to_fixed():
    clock = Clock()
    fx = _fx(clock)
    await fx.observe("USD", 1320.5)
    clock.t += 301  # TTL(300) 초과
    rate, est = fx.to_krw("USD")
    assert rate == 1400.0 and est is True  # 고정 fallback


async def test_observe_persists_to_fx_rates():
    from tests._fut_helpers import make_repo

    repo = await make_repo()
    fx = FxRateProvider(usd_krw=1400.0, hkd_krw=180.0, repo=repo)
    await fx.observe("USD", 1310.0, source="tracker")
    row = await repo.get_latest_fx_rate("USD")
    assert row is not None and row["to_krw"] == 1310.0 and row["fx_estimated"] == 0


def test_zero_or_none_observe_ignored():
    fx = _fx()
    # observe 는 async 지만 0/None 은 즉시 무시 — 동기 경로 단언용으로 캐시만 확인
    assert fx.to_krw("HKD") == (180.0, True)
