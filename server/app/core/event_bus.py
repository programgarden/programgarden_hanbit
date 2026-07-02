"""인프로세스 이벤트 버스 (M2) — order_service/risk → WebSocket 구독자.

asyncio 기반 pub/sub. 구독자는 큐를 받아 메시지를 소비한다. 토픽별 단조증가 seq 로
유실 감지를 돕는다(§6.4). account_tracker 기반 풀 push 는 M3/M5.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from app.models.schemas import utc_now_iso


class EventBus:
    """단순 fan-out 버스. 느린 구독자가 전체를 막지 않도록 put_nowait + 큐 한도."""

    def __init__(self, *, maxsize: int = 1000) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._seq: dict[str, int] = defaultdict(int)
        self._maxsize = maxsize

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def publish(self, topic: str, data: dict, *, msg_type: str = "event") -> None:
        self._seq[topic] += 1
        msg = {
            "topic": topic,
            "type": msg_type,
            "seq": self._seq[topic],
            "ts": utc_now_iso(),
            "data": data,
        }
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # 느린 구독자는 드롭(유실은 seq 갭으로 감지 → REST 재동기화)
                pass
