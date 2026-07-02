"""o3101 화이트리스트 적재 테스트 — HKEX 만 화이트리스트, 계약메타 보관."""

from __future__ import annotations

from types import SimpleNamespace

from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION
from app.services.whitelist_service import WhitelistService
from tests._fut_helpers import make_repo


def _row(symbol, exch, **kw):
    base = dict(
        Symbol=symbol, SymbolNm=f"{symbol} name", ExchCd=exch, CrncyCd="HKD",
        CtrtPrAmt="50", UntPrc="1", MnChgAmt="50", OpngMgn="1000", MntncMgn="800",
        DlStrtTm="0915", DlEndTm="1600",
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def test_load_rows_whitelists_only_hkex():
    repo = await make_repo()
    svc = WhitelistService(repo, session=None)
    res = await svc.load_rows(
        [
            _row("HSIZ25", "HKEX"),
            _row("ESZ25", "CME"),  # 비-HKEX → 스킵
            _row("", "HKEX"),  # 빈 심볼 → 스킵
        ]
    )
    assert res["whitelisted"] == 1 and res["skipped"] == 2
    assert await repo.is_whitelisted(MARKET_OVERSEAS_FUTUREOPTION, "HSIZ25")
    assert not await repo.is_whitelisted(MARKET_OVERSEAS_FUTUREOPTION, "ESZ25")
    inst = await repo.get_instrument(MARKET_OVERSEAS_FUTUREOPTION, "HSIZ25")
    assert inst["multiplier"] == 50.0 and inst["exchange"] == "HKEX"
    assert inst["init_margin"] == 1000.0
