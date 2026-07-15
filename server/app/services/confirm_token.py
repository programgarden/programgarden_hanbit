"""2단계 확인 토큰 저장소 (M4d §10.1 / §17 L1-10).

LIVE 사람경로(quote→commit)의 확인 토큰을 **서버측**에서 관리한다. M3b 킬스위치 L2 의
confirm_token 은 무상태(아무 값이나 통과)였다 — LIVE 실주문에는 부족하다. 이 저장소는:

- **one-time**: consume 시 즉시 소모(pop) — 같은 토큰 재사용 불가(중복 발사 차단).
- **TTL**: 짧은 만료(기본 120s) — 오래된 quote 로 commit 우회 차단.
- **intent 바인딩**: 토큰이 발급된 주문 내용(시장/종목/방향/수량/가격/유형) 해시에 묶인다 —
  quote 후 내용을 바꿔 commit 하는 우회 차단(다른 intent 로는 통과 못 함).

프로세스 인메모리(단일 OrderService 인스턴스 = app.state.order_service 에 보관)라 quote↔commit
두 요청 사이에 유지된다. 재기동 시 소실은 무해(사람이 다시 quote 하면 됨).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.order_dto import OrderIntent


def _intent_hash(intent: OrderIntent) -> str:
    """주문 핵심 필드 해시 — 토큰 바인딩 키(발급↔소모 intent 동일성 증명)."""
    key = json.dumps(
        {
            "market": intent.market,
            "symbol": intent.symbol,
            "side": intent.side.value,
            "qty": intent.qty,
            "price": intent.price,
            "order_type": intent.order_type.value,
        },
        sort_keys=True,
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ConfirmTokenStore:
    """짧은 TTL·one-time·intent 바인딩 확인 토큰(인메모리)."""

    def __init__(self, ttl_s: int = 120) -> None:
        self._ttl = ttl_s
        self._store: dict[str, tuple[str, float]] = {}  # token -> (intent_hash, expiry_monotonic)

    def issue(self, intent: OrderIntent) -> str:
        self._gc()
        token = secrets.token_urlsafe(16)
        self._store[token] = (_intent_hash(intent), time.monotonic() + self._ttl)
        return token

    def consume(self, token: str | None, intent: OrderIntent) -> bool:
        """토큰 검증 + 소모(one-time). 유효(미만료·intent 일치)면 True 반환하고 삭제한다."""
        self._gc()
        if not token:
            return False
        rec = self._store.pop(token, None)  # one-time: 존재하면 즉시 제거
        if rec is None:
            return False
        intent_hash, expiry = rec
        if time.monotonic() > expiry:
            return False
        return intent_hash == _intent_hash(intent)  # intent 바인딩 확인

    def _gc(self) -> None:
        now = time.monotonic()
        for t in [t for t, (_, e) in self._store.items() if now > e]:
            self._store.pop(t, None)
