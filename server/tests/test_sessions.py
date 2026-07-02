"""SessionManager 단위 테스트 — LS 를 가짜로 monkeypatch (네트워크/키 없음).

검증:
  (a) 키 있는 시장마다 LS() 가 호출되고 LS.get_instance 는 호출 안 됨.
  (b) async_login 이 시장별 올바른 (appkey, paper) 로 await 됨
      (국내/해외주식 paper=False, 해외선물 paper=True).
  (c) 빈 키 시장은 unauthenticated.
"""

from __future__ import annotations

import pytest

import app.core.sessions as sessions_mod
from app.core.sessions import (
    MARKET_KOREA_STOCK,
    MARKET_OVERSEAS_FUTUREOPTION,
    MARKET_OVERSEAS_STOCK,
    SessionManager,
)


class _DummySettings:
    """더미 키 주입 settings."""

    def __init__(
        self,
        appkey_korea="",
        appsecret_korea="",
        appkey_overseas="",
        appsecret_overseas="",
        appkey_future="",
        appsecret_future="",
    ) -> None:
        self.appkey_korea = appkey_korea
        self.appsecret_korea = appsecret_korea
        self.appkey_overseas = appkey_overseas
        self.appsecret_overseas = appsecret_overseas
        self.appkey_future = appkey_future
        self.appsecret_future = appsecret_future


class _FakeLS:
    """가짜 LS — 생성/로그인 호출을 기록한다."""

    instances: list = []
    get_instance_calls = 0

    def __init__(self) -> None:
        self.login_calls: list[dict] = []
        type(self).instances.append(self)

    @classmethod
    def get_instance(cls, *args, **kwargs):  # 절대 호출되면 안 됨
        cls.get_instance_calls += 1
        raise AssertionError("get_instance must not be called")

    async def async_login(
        self, appkey: str, appsecretkey: str, paper_trading: bool = False
    ) -> bool:
        self.login_calls.append(
            {
                "appkey": appkey,
                "appsecretkey": appsecretkey,
                "paper_trading": paper_trading,
            }
        )
        return True


@pytest.fixture(autouse=True)
def _patch_ls(monkeypatch):
    _FakeLS.instances = []
    _FakeLS.get_instance_calls = 0
    monkeypatch.setattr(sessions_mod, "LS", _FakeLS)
    yield


async def test_start_creates_new_instance_per_market_no_singleton():
    settings = _DummySettings(
        appkey_korea="K_APP",
        appsecret_korea="K_SEC",
        appkey_overseas="O_APP",
        appsecret_overseas="O_SEC",
        appkey_future="F_APP",
        appsecret_future="F_SEC",
    )
    sm = SessionManager(settings)
    await sm.start()

    # (a) 세 시장 모두 키 있음 → LS() 세 번, get_instance 0 번
    assert len(_FakeLS.instances) == 3
    assert _FakeLS.get_instance_calls == 0

    status = sm.status()
    assert status[MARKET_KOREA_STOCK]["authenticated"] is True
    assert status[MARKET_OVERSEAS_STOCK]["authenticated"] is True
    assert status[MARKET_OVERSEAS_FUTUREOPTION]["authenticated"] is True


async def test_login_called_with_correct_appkey_and_paper():
    settings = _DummySettings(
        appkey_korea="K_APP",
        appsecret_korea="K_SEC",
        appkey_overseas="O_APP",
        appsecret_overseas="O_SEC",
        appkey_future="F_APP",
        appsecret_future="F_SEC",
    )
    sm = SessionManager(settings)
    await sm.start()

    # 각 인스턴스의 로그인 호출에서 (appkey, paper) 수집
    seen = {}
    for inst in _FakeLS.instances:
        call = inst.login_calls[0]
        seen[call["appkey"]] = call["paper_trading"]

    # (b) 국내/해외주식 paper=False, 해외선물 paper=True
    assert seen["K_APP"] is False
    assert seen["O_APP"] is False
    assert seen["F_APP"] is True

    # mode 도 일치
    assert sm.mode_of(MARKET_KOREA_STOCK) == "live"
    assert sm.mode_of(MARKET_OVERSEAS_STOCK) == "live"
    assert sm.mode_of(MARKET_OVERSEAS_FUTUREOPTION) == "paper"


async def test_empty_keys_unauthenticated_no_login():
    # 국내만 키 있음, 나머지 빈 키
    settings = _DummySettings(appkey_korea="K_APP", appsecret_korea="K_SEC")
    sm = SessionManager(settings)
    await sm.start()

    # (c) LS() 는 한 번만(국내), 빈 키 시장은 unauthenticated
    assert len(_FakeLS.instances) == 1
    assert _FakeLS.get_instance_calls == 0

    status = sm.status()
    assert status[MARKET_KOREA_STOCK]["status"] == "authenticated"
    assert status[MARKET_OVERSEAS_STOCK]["status"] == "unauthenticated"
    assert status[MARKET_OVERSEAS_FUTUREOPTION]["status"] == "unauthenticated"
    assert status[MARKET_OVERSEAS_STOCK]["authenticated"] is False


async def test_login_failure_marks_failed_not_crash():
    class _FailLS(_FakeLS):
        async def async_login(self, appkey, appsecretkey, paper_trading=False):
            return False

    import app.core.sessions as mod

    settings = _DummySettings(appkey_korea="K_APP", appsecret_korea="K_SEC")
    sm = SessionManager(settings)
    # 이 테스트만 실패 LS 사용
    mod.LS = _FailLS
    try:
        await sm.start()  # 예외 없이 끝나야 함
    finally:
        mod.LS = _FakeLS

    status = sm.status()
    assert status[MARKET_KOREA_STOCK]["status"] == "failed"
    assert status[MARKET_KOREA_STOCK]["authenticated"] is False
