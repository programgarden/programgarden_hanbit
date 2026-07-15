"""FastAPI 의존성 — app.state 에서 세션/서비스 주입."""

from __future__ import annotations

from fastapi import Request

from app.core.event_bus import EventBus
from app.core.sessions import SessionManager
from app.repositories.orders_repo import OrdersRepo
from app.services.market_service import MarketService
from app.services.order_service import OrderService
from app.services.whitelist_service import WhitelistService


def get_sessions(request: Request) -> SessionManager:
    """app.state.sessions 에 보관된 SessionManager 를 반환한다."""
    return request.app.state.sessions


def get_market_service(request: Request) -> MarketService:
    """요청별 MarketService 를 생성해 반환한다(세션은 공유)."""
    return MarketService(get_sessions(request))


def get_repo(request: Request) -> OrdersRepo:
    """app.state.repo (OrdersRepo) 반환."""
    return request.app.state.repo


def get_event_bus(request: Request) -> EventBus:
    """app.state.event_bus 반환."""
    return request.app.state.event_bus


def get_order_service(request: Request) -> OrderService:
    """app.state.order_service 반환."""
    return request.app.state.order_service


def get_strategy_engine(request: Request):
    """app.state.strategy_engine(StrategyEngine) 반환 — M5 자동매매 엔진."""
    return request.app.state.strategy_engine


def get_whitelist_service(request: Request) -> WhitelistService:
    """요청별 WhitelistService 생성(repo/세션 공유)."""
    return WhitelistService(get_repo(request), get_sessions(request))
