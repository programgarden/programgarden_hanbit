"""WebSocket 스트림 (M2 + M3b §11) — event_bus 구독 → 클라이언트 push.

연결 수락 후 event_bus 를 구독하고, 발행 메시지를 그대로 포워딩한다(토픽 무관 fan-out).
M3b 토픽: `portfolio_snapshot`(집계기 §4.3), `risk.halt_state`(킬스위치 engage/release).
M2 토픽: orders/fill/risk_event/mode. 동시에 클라이언트의 ping/제어 메시지를 받는다.
account_tracker 기반 positions/pnl/balance 풀 push 는 집계기 tick 이 portfolio_snapshot 으로.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.logging_setup import get_logger
from app.models.schemas import utc_now_iso

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/stream")
async def stream(websocket: WebSocket) -> None:
    """연결 수락 → event_bus 구독 포워딩 + ping/pong (M2)."""
    await websocket.accept()
    await websocket.send_json(
        {
            "type": "info",
            "milestone": "M3",
            "message": "stream connected (event_bus)",
            "topics": ["portfolio_snapshot", "risk.halt_state", "orders", "fill", "risk_event"],
            "server_time": utc_now_iso(),
        }
    )

    bus = getattr(websocket.app.state, "event_bus", None)
    queue = bus.subscribe() if bus is not None else None

    async def _forward() -> None:
        assert queue is not None
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)

    fwd_task = asyncio.create_task(_forward()) if queue is not None else None
    try:
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_json({"type": "pong", "server_time": utc_now_iso()})
            else:
                await websocket.send_json(
                    {"type": "ack", "received": msg, "server_time": utc_now_iso()}
                )
    except WebSocketDisconnect:
        logger.info("ws /stream disconnected")
    finally:
        if fwd_task is not None:
            fwd_task.cancel()
        if bus is not None and queue is not None:
            bus.unsubscribe(queue)
