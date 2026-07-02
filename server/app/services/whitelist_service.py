"""HKEX 화이트리스트 적재 — o3101 마스터 → instruments upsert (M2).

o3101 OutBlock 리스트에서 ExchCd=='HKEX' 행만 골라 instruments 에 계약메타와 함께 적재하고
whitelisted=1 로 표시한다. 승수(CtrtPrAmt)·틱(UntPrc/MnChgAmt)·증거금(OpngMgn/MntncMgn)·
거래시간(DlStrtTm/DlEndTm)을 보관. ⚠ gubun 값/ExchCd 정확값/CtrtPrAmt 승수 의미는 라이브 확정 전제.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from programgarden_finance.ls.overseas_futureoption.market.o3101.blocks import O3101InBlock

from app.adapters.order_base import OrderError
from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION

if TYPE_CHECKING:
    from app.core.sessions import SessionManager
    from app.repositories.orders_repo import OrdersRepo

_HKEX = "HKEX"


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class WhitelistService:
    """o3101 마스터 기반 HKEX 화이트리스트 적재."""

    def __init__(self, repo: OrdersRepo, session: SessionManager) -> None:
        self._repo = repo
        self._session = session

    async def refresh(self, gubun: str = "1") -> dict:
        facade = self._session.client_for(MARKET_OVERSEAS_FUTUREOPTION)
        if facade is None:
            raise OrderError("MARKET_UNAUTHENTICATED", "overseas_futureoption not authenticated")
        tr = facade.market().o3101(
            body=O3101InBlock(gubun=gubun), options=self._session.quote_opts()
        )
        resp = await tr.req_async()
        rows = getattr(resp, "block", None) or []
        return await self.load_rows(rows)

    async def load_rows(self, rows: list) -> dict:
        """o3101 OutBlock 행 리스트 적재(테스트/재사용). HKEX 만 화이트리스트."""
        whitelisted = 0
        skipped = 0
        for r in rows:
            exch = str(getattr(r, "ExchCd", "") or "").upper()
            symbol = str(getattr(r, "Symbol", "") or "")
            if not symbol or exch != _HKEX:
                skipped += 1
                continue
            await self._repo.ensure_instrument(
                MARKET_OVERSEAS_FUTUREOPTION,
                symbol,
                exchange=_HKEX,
                name=str(getattr(r, "SymbolNm", "") or "") or None,
                asset_type="future",
                currency=str(getattr(r, "CrncyCd", "") or "") or None,
                multiplier=_f(getattr(r, "CtrtPrAmt", None)),
                tick_size=_f(getattr(r, "UntPrc", None)),
                tick_value=_f(getattr(r, "MnChgAmt", None)),
                init_margin=_f(getattr(r, "OpngMgn", None)),
                maint_margin=_f(getattr(r, "MntncMgn", None)),
                trading_start=str(getattr(r, "DlStrtTm", "") or "") or None,
                trading_end=str(getattr(r, "DlEndTm", "") or "") or None,
                whitelisted=1,
            )
            whitelisted += 1
        return {"whitelisted": whitelisted, "skipped": skipped}
