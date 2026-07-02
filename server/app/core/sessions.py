"""SessionManager — 시장별 LS 세션 수명주기 관리 (M1 read-only).

각 시장(국내주식/해외주식/해외선물)마다 **독립된 `LS()` 인스턴스**를 만들고
로그인한다. 절대 `LS.get_instance()` 를 쓰지 않는다 — 그것은 프로세스 싱글톤이라
두 번째 시장 로그인이 첫 번째 토큰을 덮어쓰기 때문이다(검증된 라이브러리 사실).

paper_trading 플래그는 INV-1 mode_matrix 의 trading_mode 에서 도출한다
(live→False, paper→True). 하드코딩하지 않는다.

키가 비어 있는 시장은 로그인을 시도하지 않고 ``unauthenticated`` 상태로 둔다.
로그인 실패도 부팅을 막지 않는다(경고 로그 + ``failed`` 상태). 서버는 키가 없거나
일부 시장이 실패해도 떠야 한다(M1 read-only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from programgarden_finance import LS
from programgarden_finance.ls.models import SetupOptions

from app.core.mode_matrix import (
    MARKET_KOREA_STOCK,
    MARKET_OVERSEAS_FUTUREOPTION,
    MARKET_OVERSEAS_STOCK,
    TRADING_MODE_PAPER,
    trading_mode_of,
)
from app.logging_setup import get_logger

if TYPE_CHECKING:
    from app.config import Settings

logger = get_logger("app.sessions")

# 지원 시장 키 (mode_matrix 식별자와 동일)
MARKETS: tuple[str, ...] = (
    MARKET_KOREA_STOCK,
    MARKET_OVERSEAS_STOCK,
    MARKET_OVERSEAS_FUTUREOPTION,
)

# 세션 상태 리터럴
STATUS_UNAUTHENTICATED = "unauthenticated"
STATUS_AUTHENTICATED = "authenticated"
STATUS_FAILED = "failed"


class SessionManager:
    """시장별 LS 세션을 생성/로그인/조회/정리한다."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

        # 시장 → (appkey, appsecret, paper, facade_method_name) 매핑.
        # paper 는 mode_matrix 의 trading_mode 에서 도출(INV-1 일치).
        self._config: dict[str, dict[str, Any]] = {
            MARKET_KOREA_STOCK: {
                "appkey": settings.appkey_korea,
                "appsecret": settings.appsecret_korea,
                "paper": self._is_paper(MARKET_KOREA_STOCK),
                "facade": "korea_stock",
            },
            MARKET_OVERSEAS_STOCK: {
                "appkey": settings.appkey_overseas,
                "appsecret": settings.appsecret_overseas,
                "paper": self._is_paper(MARKET_OVERSEAS_STOCK),
                "facade": "overseas_stock",
            },
            MARKET_OVERSEAS_FUTUREOPTION: {
                "appkey": settings.appkey_future,
                "appsecret": settings.appsecret_future,
                "paper": self._is_paper(MARKET_OVERSEAS_FUTUREOPTION),
                "facade": "overseas_futureoption",
            },
        }

        # 시장 → LS 인스턴스(로그인 성공/실패와 무관하게 생성된 경우 보관)
        self._clients: dict[str, LS] = {}
        # 시장 → 상태 문자열
        self._status: dict[str, str] = {m: STATUS_UNAUTHENTICATED for m in MARKETS}

    @staticmethod
    def _is_paper(market: str) -> bool:
        """mode_matrix 의 trading_mode 로부터 paper_trading 플래그를 도출한다."""
        return trading_mode_of(market) == TRADING_MODE_PAPER

    async def start(self) -> None:
        """키가 있는 시장마다 독립 LS() 를 만들고 로그인한다.

        - 빈 키 → 로그인 생략, ``unauthenticated`` 유지.
        - 로그인 실패/예외 → 경고 로그 + ``failed`` (부팅 중단 금지).
        - 성공 → ``authenticated``.
        """
        for market in MARKETS:
            cfg = self._config[market]
            appkey: str = (cfg["appkey"] or "").strip()
            appsecret: str = (cfg["appsecret"] or "").strip()
            paper: bool = cfg["paper"]

            if not appkey or not appsecret:
                self._status[market] = STATUS_UNAUTHENTICATED
                logger.info(
                    "session %s — no credentials, skipping login (unauthenticated)",
                    market,
                )
                continue

            # 반드시 새 인스턴스. get_instance() 절대 금지(프로세스 싱글톤).
            client = LS()
            self._clients[market] = client
            try:
                ok = await client.async_login(
                    appkey=appkey,
                    appsecretkey=appsecret,
                    paper_trading=paper,
                )
            except Exception as exc:  # noqa: BLE001 — 부팅을 막지 않는다
                self._status[market] = STATUS_FAILED
                logger.warning(
                    "session %s — login raised (%s); marked failed", market, exc
                )
                continue

            if ok:
                self._status[market] = STATUS_AUTHENTICATED
                logger.info(
                    "session %s — login ok (paper=%s)", market, paper
                )
            else:
                self._status[market] = STATUS_FAILED
                logger.warning(
                    "session %s — login returned False; marked failed", market
                )

    def get(self, market: str) -> LS | None:
        """해당 시장의 LS 인스턴스(인증 성공한 경우만) 반환."""
        if self._status.get(market) != STATUS_AUTHENTICATED:
            return None
        return self._clients.get(market)

    def is_authenticated(self, market: str) -> bool:
        """시장이 인증되었는지 여부."""
        return self._status.get(market) == STATUS_AUTHENTICATED

    def client_for(self, market: str) -> Any | None:
        """해당 시장의 facade 객체(korea_stock()/overseas_stock()/
        overseas_futureoption()) 반환. 미인증이면 None."""
        ls = self.get(market)
        if ls is None:
            return None
        facade_name: str = self._config[market]["facade"]
        return getattr(ls, facade_name)()

    def quote_opts(self) -> SetupOptions:
        """시세 조회용 SetupOptions. rate limit 초과 시 대기 후 재시도."""
        return SetupOptions(
            rate_limit_count=2,
            rate_limit_seconds=1,
            on_rate_limit="wait",
        )

    def mode_of(self, market: str) -> str:
        """시장의 거래모드 문자열('live'|'paper')을 반환."""
        return "paper" if self._config[market]["paper"] else "live"

    def status(self) -> dict[str, dict[str, Any]]:
        """시장별 상태 요약 dict."""
        return {
            market: {
                "authenticated": self.is_authenticated(market),
                "mode": self.mode_of(market),
                "status": self._status.get(market, STATUS_UNAUTHENTICATED),
            }
            for market in MARKETS
        }

    async def close(self) -> None:
        """세션 정리. LS 는 명시적 close 메서드가 없어 참조만 해제한다."""
        self._clients.clear()
        for market in MARKETS:
            self._status[market] = STATUS_UNAUTHENTICATED
