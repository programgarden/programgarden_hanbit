"""/api/v1/system/health 의 allow_live 기본값 테스트 (안전 토글)."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_allow_live_false(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/system/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["allow_live"] is False
    assert body["data"]["mode"] == "READ_ONLY"
