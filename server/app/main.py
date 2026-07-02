"""FastAPI 앱 팩토리 + lifespan.

create_app(): 설정 로드 → 로깅 → CORS → 라우터 등록 → lifespan(DB ensure).
M0 에서는 세션/엔진을 기동하지 않고 "READ_ONLY, LIVE disabled" 만 로그한다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.realtime_future import RealtimeFutureFillSource
from app.api import (
    accounts,
    market,
    orders,
    portfolio,
    risk,
    strategy,
    system,
    ws,
)
from app.config import get_settings
from app.core.event_bus import EventBus
from app.core.sessions import SessionManager
from app.logging_setup import get_logger, setup_logging
from app.orders.boot import boot_engine
from app.repositories.db import init_db
from app.repositories.orders_repo import OrdersRepo
from app.services.order_service import OrderService

API_V1_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """기동 시 설정 로드 + DB 보장 + 세션 시작 + 로그.

    M1 도 READ_ONLY(주문 엔진 미기동). 세션은 시세 조회용으로만 띄운다.
    키가 없거나 일부 로그인이 실패해도 서버는 떠야 한다.
    """
    settings = get_settings()
    setup_logging(settings.hanbit_log_level)
    logger = get_logger("app.lifespan")

    await init_db(settings.hanbit_db_path)

    # 시장별 LS 세션 시작(read-only 시세 + paper FUT 주문). 실패해도 부팅 중단하지 않음.
    sessions = SessionManager(settings)
    await sessions.start()
    app.state.sessions = sessions

    # M2: 주문 도메인 — repo / event bus / order service.
    repo = OrdersRepo(settings.hanbit_db_path)
    event_bus = EventBus()
    app.state.repo = repo
    app.state.event_bus = event_bus
    order_service = OrderService(repo, sessions, settings, event_bus=event_bus)
    app.state.order_service = order_service

    # M3b 부트 스테이트머신(§7) — 비터미널 주문 분류 → boot reconcile → quarantine →
    # READ_ONLY/ACTIVE 결정. 라이브 세션/계좌 TR 실패가 기동을 막지 않도록 방어한다(엔진은
    # from_config 초기값 유지). 게이트/_get_mutable 가 런타임 EngineState 를 단일 권위로 읽는다.
    try:
        report = await boot_engine(order_service)
        logger.info(
            "boot complete — engine_state=%s entry_blocked=%s quarantined=%d reconcile=%s",
            report.engine_state,
            report.entry_blocked,
            len(report.quarantined),
            report.reconcile,
        )
    except Exception:  # noqa: BLE001 — 부트 실패가 서버 기동을 막지 않는다.
        logger.exception("boot_engine failed — engine left at %s", order_service.engine.state)

    # M3b §10 실시간 체결(TC2/TC3) 스캐폴드 — flag off 기본이라 start() 는 no-op.
    # OrderService 와 동일한 OrderLocks/EngineState/event_bus 를 공유해 단일 writer 직렬화 유지.
    realtime_fills = RealtimeFutureFillSource(
        repo, sessions, settings, order_service.engine,
        locks=order_service.locks, event_bus=event_bus,
    )
    app.state.realtime_fills = realtime_fills
    try:
        await realtime_fills.start()
    except Exception:  # noqa: BLE001 — 구독 실패가 기동을 막지 않는다(권위는 reconcile)
        logger.exception("realtime fills start failed")

    logger.info(
        "startup complete — mode=READ_ONLY engine_state=%s allow_live=%s db=%s sessions=%s "
        "realtime_fills=%s",
        order_service.engine.state,
        settings.hanbit_allow_live,
        settings.hanbit_db_path,
        sessions.status(),
        settings.realtime_fills_enabled,
    )
    yield
    await realtime_fills.stop()
    await sessions.close()
    logger.info("shutdown complete")


def create_app() -> FastAPI:
    """앱 인스턴스를 생성/구성해 반환한다."""
    settings = get_settings()
    setup_logging(settings.hanbit_log_level)

    app = FastAPI(
        title="programgarden_hanbit — Trading Backend",
        version="0.1.0",
        description="자동화매매 트레이딩 백엔드 (M0 스캐폴딩, READ_ONLY).",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 루트 라우터: /healthz
    app.include_router(system.root_router)

    # /api/v1 라우터들
    app.include_router(system.router, prefix=API_V1_PREFIX)
    app.include_router(market.router, prefix=API_V1_PREFIX)
    app.include_router(orders.router, prefix=API_V1_PREFIX)
    app.include_router(portfolio.router, prefix=API_V1_PREFIX)
    app.include_router(accounts.router, prefix=API_V1_PREFIX)
    app.include_router(risk.router, prefix=API_V1_PREFIX)
    app.include_router(strategy.router, prefix=API_V1_PREFIX)
    app.include_router(ws.router, prefix=API_V1_PREFIX)

    return app


# ASGI 진입점: `uvicorn app.main:app`
app = create_app()
