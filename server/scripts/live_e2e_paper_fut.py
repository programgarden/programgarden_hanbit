"""해외선물 HKEX 모의 주문 라이브 e2e — 수동 실행 스크립트 (pytest 아님, CI 미실행).

용도: M2 파이프라인을 실제 LS 모의 브로커에 대해 검증(신규→reconcile→정정→취소).
주의: 실제(모의) 주문을 발사한다. 체결 안 되도록 시장가 아래 매수 지정가(잔존)로 두고 취소한다.

실행:
    cd server && uv run python scripts/live_e2e_paper_fut.py [SYMBOL] [LIMIT_PRICE] [QTY]
기본값: HBIM26 11000 1  (Hang Seng Biotech 6월물, 승수 50/HKD)

전제(2026-06-18 기준 미해소 블로커):
    LS 모의 계좌가 해외선물 "모의투자 주문권한" 미활성 → 주문 발사 시 rsp_cd '01491'
    "모의투자 주문이 불가한 계좌입니다." (로그인·시세·계좌조회는 정상). 권한 활성화되면 통과.
키:
    루트 /workspace/.env 의 APPKEY_FUTURE_FAKE / APPSECRET_FUTURE_FAKE 를 FUTURE 슬롯에 주입.
    (server/.env 의 APPKEY_FUTURE 도 동일 '01491' 이었음.)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_ROOT_ENV = Path("/workspace/.env")


def _inject_fake_keys() -> None:
    if not _ROOT_ENV.exists():
        return
    env: dict[str, str] = {}
    for ln in _ROOT_ENV.read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    fk, fs = env.get("APPKEY_FUTURE_FAKE"), env.get("APPSECRET_FUTURE_FAKE")
    if fk and fs:
        os.environ["APPKEY_FUTURE"] = fk  # os.environ 가 server/.env 보다 우선
        os.environ["APPSECRET_FUTURE"] = fs
        print("using FAKE futures key:", fk[:4] + "…(masked)")
    os.environ["HANBIT_ENGINE_STATE"] = "PAPER_TRADING"


async def main(symbol: str, price: float, qty: int) -> None:
    _inject_fake_keys()
    from app.config import Settings
    from app.core.event_bus import EventBus
    from app.core.mode_matrix import MARKET_OVERSEAS_FUTUREOPTION as FUT
    from app.core.sessions import SessionManager
    from app.models.order_dto import OrderIntent, OrderType, Side
    from app.repositories.db import init_db
    from app.repositories.orders_repo import OrdersRepo
    from app.services.order_service import OrderService
    from app.services.whitelist_service import WhitelistService

    s = Settings()
    await init_db(s.hanbit_db_path)
    sm = SessionManager(s)
    await sm.start()
    print("FUT session:", sm.status().get(FUT))
    if not sm.is_authenticated(FUT):
        print("FUT not authenticated — abort")
        return

    repo = OrdersRepo(s.hanbit_db_path)
    if not await repo.is_whitelisted(FUT, symbol):
        print("whitelist refresh:", await WhitelistService(repo, sm).refresh(gubun="1"))
    svc = OrderService(repo, sm, s, event_bus=EventBus())

    print(f"\n[1] PLACE buy limit {symbol} {price} x {qty}")
    r = await svc.place(
        OrderIntent(symbol=symbol, side=Side.BUY, order_type=OrderType.LIMIT,
                    qty=qty, price=price, exchange="HKEX")
    )
    if not r.get("order"):
        print("  REJECTED decision:", r.get("decision"))
        await sm.close()
        return
    oid = r["order"]["id"]
    print("  ok=", r.get("ok"), "status=", r["order"]["status"],
          "ordno=", r["order"].get("broker_order_id"), "ack=", r.get("ack"))

    await asyncio.sleep(2.5)
    print("\n[2] RECONCILE:", await svc.reconcile(scope="live-e2e"))
    o = await repo.get_order(oid)
    print("  status=", o["status"], "filled=", o["filled_qty"], "rem=", o["remaining_qty"])

    if o.get("broker_order_id") and o["status"] in ("accepted", "partially_filled"):
        await asyncio.sleep(2.5)
        try:
            am = await svc.amend(oid, qty=qty, price=price + 50)
            print("\n[3] AMEND ok=", am.get("ok"), "ack=", am.get("ack"))
        except Exception as e:  # noqa: BLE001
            print("[3] AMEND ERR", type(e).__name__, str(e)[:140])

    o = await repo.get_order(oid)
    if o.get("broker_order_id") and o["status"] in (
        "accepted", "partially_filled", "submitted", "in_doubt"
    ):
        await asyncio.sleep(2.5)
        for _ in range(2):
            try:
                cx = await svc.cancel(oid)
                print("\n[4] CANCEL ok=", cx.get("ok"), "ack=", cx.get("ack"))
                break
            except Exception as e:  # noqa: BLE001
                print("[4] CANCEL ERR", type(e).__name__, str(e)[:140])
                await asyncio.sleep(3)

    o = await repo.get_order(oid)
    print("\nFINAL status=", o["status"])
    print("transitions:", [t["to_state"] for t in await repo.list_transitions(oid)])
    await sm.close()


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "HBIM26"
    px = float(sys.argv[2]) if len(sys.argv) > 2 else 11000.0
    q = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    asyncio.run(main(sym, px, q))
