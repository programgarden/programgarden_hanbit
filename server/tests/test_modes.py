"""INV-1 거래모드 매트릭스 불변식 테스트."""

from __future__ import annotations

from httpx import AsyncClient


async def test_modes_invariants(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/system/modes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True

    markets = {m["market"]: m for m in body["data"]["markets"]}

    # 거래모드
    assert markets["korea_stock"]["trading_mode"] == "live"
    assert markets["overseas_stock"]["trading_mode"] == "live"
    assert markets["overseas_futureoption"]["trading_mode"] == "paper"

    # 해외선물 거래소 화이트리스트
    assert markets["overseas_futureoption"]["constraints"]["exchange_whitelist"] == ["HKEX"]

    # 소액주문 상한
    assert markets["overseas_stock"]["small_amount_cap"]["max_order"] == 50
    assert markets["korea_stock"]["small_amount_cap"]["max_order"] == 100000

    # 통화
    assert markets["korea_stock"]["currency"] == "KRW"
    assert markets["overseas_stock"]["currency"] == "USD"
