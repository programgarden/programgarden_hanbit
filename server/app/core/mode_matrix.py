"""INV-1 거래모드 매트릭스 — Single Source of Truth.

시장별 거래모드/통화/제약을 여기 한 곳에서 정의한다.
이 값은 불변식(invariant)이며, 임의로 바꾸면 안전 경계가 깨진다.

- korea_stock          : live  / KRW / 소액주문 상한 100,000 KRW
- overseas_stock       : live  / USD / 소액주문 상한 50 USD
- overseas_futureoption: paper / 거래소 화이트리스트 HKEX
"""

from __future__ import annotations

from typing import Any, Final

# 거래모드 상수
TRADING_MODE_LIVE: Final[str] = "live"
TRADING_MODE_PAPER: Final[str] = "paper"

# 시장 식별자
MARKET_KOREA_STOCK: Final[str] = "korea_stock"
MARKET_OVERSEAS_STOCK: Final[str] = "overseas_stock"
MARKET_OVERSEAS_FUTUREOPTION: Final[str] = "overseas_futureoption"

# INV-1 매트릭스 (절대 값 — 변경 금지)
MODE_MATRIX: Final[list[dict[str, Any]]] = [
    {
        "market": MARKET_KOREA_STOCK,
        "trading_mode": TRADING_MODE_LIVE,
        "currency": "KRW",
        "small_amount_cap": {"currency": "KRW", "max_order": 100000},
        "constraints": {},
    },
    {
        "market": MARKET_OVERSEAS_STOCK,
        "trading_mode": TRADING_MODE_LIVE,
        "currency": "USD",
        "small_amount_cap": {"currency": "USD", "max_order": 50},
        "constraints": {},
    },
    {
        "market": MARKET_OVERSEAS_FUTUREOPTION,
        "trading_mode": TRADING_MODE_PAPER,
        "currency": None,
        "small_amount_cap": None,
        "constraints": {"exchange_whitelist": ["HKEX"]},
    },
]

# 버킷 (거래모드 격리의 최상위 분류 — .claude/plans/2026-06-20-통합계획서.md M3 §3)
BUCKET_LIVE: Final[str] = "live"
BUCKET_PAPER: Final[str] = "paper"

# 시장 → 버킷 (live = 국내+해외주식, paper = 해외선물)
_MARKET_TO_BUCKET: Final[dict[str, str]] = {
    MARKET_KOREA_STOCK: BUCKET_LIVE,
    MARKET_OVERSEAS_STOCK: BUCKET_LIVE,
    MARKET_OVERSEAS_FUTUREOPTION: BUCKET_PAPER,
}
_BUCKET_TO_MARKETS: Final[dict[str, tuple[str, ...]]] = {
    BUCKET_LIVE: (MARKET_KOREA_STOCK, MARKET_OVERSEAS_STOCK),
    BUCKET_PAPER: (MARKET_OVERSEAS_FUTUREOPTION,),
}

# 빠른 조회용 인덱스
_BY_MARKET: Final[dict[str, dict[str, Any]]] = {entry["market"]: entry for entry in MODE_MATRIX}


def bucket_of(market: str) -> str | None:
    """시장이 속한 버킷(live/paper). 모르는 시장이면 None."""
    return _MARKET_TO_BUCKET.get(market)


def markets_of(bucket: str) -> tuple[str, ...]:
    """버킷에 속한 시장 목록."""
    return _BUCKET_TO_MARKETS.get(bucket, ())


def get_mode_matrix() -> list[dict[str, Any]]:
    """전체 거래모드 매트릭스(깊은 복사본)를 반환한다."""
    return [
        {
            **entry,
            "small_amount_cap": (
                dict(entry["small_amount_cap"]) if entry["small_amount_cap"] else None
            ),
            "constraints": dict(entry["constraints"]),
        }
        for entry in MODE_MATRIX
    ]


def get_market_mode(market: str) -> dict[str, Any] | None:
    """단일 시장의 거래모드 항목을 반환한다. 없으면 None."""
    entry = _BY_MARKET.get(market)
    if entry is None:
        return None
    return {
        **entry,
        "small_amount_cap": (
            dict(entry["small_amount_cap"]) if entry["small_amount_cap"] else None
        ),
        "constraints": dict(entry["constraints"]),
    }


def trading_mode_of(market: str) -> str | None:
    """시장의 거래모드(live/paper)를 반환한다."""
    entry = _BY_MARKET.get(market)
    return entry["trading_mode"] if entry else None
