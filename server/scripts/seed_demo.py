"""교육용 데모 시드 — web 대시보드가 비어 보이지 않게 대표 샘플 데이터를 SQLite 에 적재.

⚠️ 교육용 데모 시드, 실데이터 아님, dev 전용. 실거래 주문을 발사하지 않는다(브로커 미접속).
   주말/실계좌 공란이라 화면(포지션/주문/포트폴리오/계좌/리스크)을 의미있게 채우려는 용도.

용도:
    - accounts/instruments/balances_snapshot/positions/bucket_kpi/orders/
      order_state_transitions/fills/risk_events/fx_rates 에 대표 행을 넣는다.
    - 통화·실거래(live)/모의(paper) 격리 원칙 준수 — 절대 통화 합산 가정 금지.
      버킷: live={korea_stock(KRW), overseas_stock(USD)}, paper={overseas_futureoption(HKD)}.

실행:
    cd /workspace/server && uv run python scripts/seed_demo.py

멱등:
    이 스크립트가 만든 행만 'demo:' idempotency_key / label '[DEMO]' / event detail {"demo":true}
    같은 안정 마커로 식별한다. 재실행 시 그 마커 행만 지우고 다시 넣으므로 실제(비-demo)
    데이터(이미 있는 rejected 주문/risk_event/PAPER-FUT 앵커 계좌 등)는 건드리지 않는다.

주의(스키마 제약 — 추측 금지로 마이그레이션 정독 후 반영):
    - orders: idempotency_key UNIQUE NOT NULL / market·trading_mode·side·order_type·qty·status
      ·relation 모두 NOT NULL / filled_qty NOT NULL DEFAULT 0.
    - fills: (order_id,event_seq) UNIQUE, origin NOT NULL, qty·price NOT NULL.
    - balances_snapshot/positions: PK·UNIQUE(account_id,...) 라 upsert 헬퍼로 멱등.
    - 금액은 minor-unit 정수 아님 → Decimal/REAL. FX 는 KRW base(to_krw).
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

# scripts/ 에서 직접 실행 시 'app' 모듈이 sys.path 에 없다 → server 루트를 자체 주입
# (live_e2e 는 PYTHONPATH 의존이라 그냥 실행하면 ModuleNotFoundError. 이 스크립트는 self-heal).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.core.mode_matrix import (  # noqa: E402
    BUCKET_LIVE,
    BUCKET_PAPER,
    MARKET_KOREA_STOCK,
    MARKET_OVERSEAS_FUTUREOPTION,
    MARKET_OVERSEAS_STOCK,
)
from app.models.schemas import utc_now_iso  # noqa: E402
from app.repositories.db import init_db  # noqa: E402
from app.repositories.orders_repo import OrdersRepo  # noqa: E402

# 데모 행 식별 마커 — 멱등 cleanup 에 쓰는 안정 키 ----------------------------
DEMO_PREFIX = "demo:"            # orders.idempotency_key 접두
DEMO_LABEL = "[DEMO]"           # accounts.label 접두 (데모 계좌만 삭제)
DEMO_DETAIL = '"demo": true'    # risk_events.detail_json 부분일치 (데모 이벤트만 삭제)

# 데모 계좌 번호(placeholder — 실계좌번호 저장/출력 금지). ensure_account 의 유니크 앵커.
ACCT_KR = "DEMO-KR-STOCK"
ACCT_OS = "DEMO-OS-STOCK"
ACCT_FUT = "DEMO-PAPER-FUT"

# 환율(KRW base). config 기본값과 맞춰 화면 환산이 일관되게.
FX_USD = 1400.0
FX_HKD = 180.0
FX_KRW = 1.0


# ── instruments (이미 있으면 멱등 upsert) ────────────────────────────────────
async def _seed_instruments(repo: OrdersRepo) -> dict[str, int]:
    """KR/OS 주식 + FUT 1종(HBIM26 은 이미 있을 것). symbol→instrument_id 반환."""
    ids: dict[str, int] = {}
    # 국내주식 (KRW, KRX)
    ids["005930"] = await repo.ensure_instrument(
        MARKET_KOREA_STOCK, "005930", exchange="KRX",
        name="삼성전자", asset_type="stock", currency="KRW", multiplier=1.0,
    )
    ids["035720"] = await repo.ensure_instrument(
        MARKET_KOREA_STOCK, "035720", exchange="KRX",
        name="카카오", asset_type="stock", currency="KRW", multiplier=1.0,
    )
    # 해외주식 (USD, NASDAQ)
    ids["AAPL"] = await repo.ensure_instrument(
        MARKET_OVERSEAS_STOCK, "AAPL", exchange="NASDAQ",
        name="Apple Inc.", asset_type="stock", currency="USD", multiplier=1.0,
    )
    ids["NVDA"] = await repo.ensure_instrument(
        MARKET_OVERSEAS_STOCK, "NVDA", exchange="NASDAQ",
        name="NVIDIA Corp.", asset_type="stock", currency="USD", multiplier=1.0,
    )
    # 해외선물 (HKD, HKEX) — HBIM26 은 0002 시드/마스터로 보통 이미 있다. 멱등 보장.
    ids["HBIM26"] = await repo.ensure_instrument(
        MARKET_OVERSEAS_FUTUREOPTION, "HBIM26", exchange="HKEX",
        name="Hang Seng Biotech Fut(6월물)", asset_type="future", currency="HKD",
        multiplier=50.0, whitelisted=1,
    )
    return ids


# ── accounts (시장별, ensure_account 멱등) ───────────────────────────────────
async def _seed_accounts(repo: OrdersRepo) -> dict[str, int]:
    ids: dict[str, int] = {}
    ids[MARKET_KOREA_STOCK] = await repo.ensure_account(
        MARKET_KOREA_STOCK, ACCT_KR, trading_mode="live",
        currency="KRW", label=f"{DEMO_LABEL} 국내주식(실거래)",
    )
    ids[MARKET_OVERSEAS_STOCK] = await repo.ensure_account(
        MARKET_OVERSEAS_STOCK, ACCT_OS, trading_mode="live",
        currency="USD", label=f"{DEMO_LABEL} 해외주식(실거래)",
    )
    ids[MARKET_OVERSEAS_FUTUREOPTION] = await repo.ensure_account(
        MARKET_OVERSEAS_FUTUREOPTION, ACCT_FUT, trading_mode="paper",
        currency="HKD", label=f"{DEMO_LABEL} 해외선물 모의(HKEX)",
    )
    return ids


# ── balances_snapshot (계좌별·통화별 잔고) ───────────────────────────────────
async def _seed_balances(repo: OrdersRepo, acct: dict[str, int]) -> int:
    # (account_id, currency) upsert → 재실행 시 갱신만(중복 폭증 없음).
    await repo.upsert_balance_snapshot(
        acct[MARKET_KOREA_STOCK], "KRW",
        deposit=53_000_000, orderable_amount=21_400_000, margin_total=0,
        withdrawable=21_400_000, realized_pnl=1_280_000, exchange_rate=FX_KRW,
    )
    await repo.upsert_balance_snapshot(
        acct[MARKET_OVERSEAS_STOCK], "USD",
        deposit=8_500.0, orderable_amount=3_120.0, margin_total=0,
        withdrawable=3_120.0, realized_pnl=-145.0, exchange_rate=FX_USD,
    )
    await repo.upsert_balance_snapshot(
        acct[MARKET_OVERSEAS_FUTUREOPTION], "HKD",
        deposit=82_000.0, orderable_amount=61_500.0, margin_total=20_500.0,
        withdrawable=61_500.0, realized_pnl=3_400.0, exchange_rate=FX_HKD,
    )
    return 3


# ── positions (버킷별 보유, 권위+보강 두 upsert 로 전 컬럼 채움) ──────────────
async def _seed_positions(
    repo: OrdersRepo, acct: dict[str, int], inst: dict[str, int]
) -> int:
    # 한 종목 = (권위: qty/avg_price/bucket/market/통화/방향/multiplier/margin)
    #         + (보강: current_price/pnl/fx/eval_krw). 부호 섞어 up/down 색상 둘 다 보이게.
    rows = [
        # (acct_market, symbol, bucket, market, ccy, side, qty, avg, cur, mult, margin, fx)
        # --- live: 국내주식 2 (KRW, fx=1) ---
        (MARKET_KOREA_STOCK, "005930", BUCKET_LIVE, MARKET_KOREA_STOCK, "KRW",
         "long", 50, 71_500, 78_200, 1.0, None, FX_KRW),       # +이익
        (MARKET_KOREA_STOCK, "035720", BUCKET_LIVE, MARKET_KOREA_STOCK, "KRW",
         "long", 80, 58_400, 51_300, 1.0, None, FX_KRW),       # -손실
        # --- live: 해외주식 2 (USD) ---
        (MARKET_OVERSEAS_STOCK, "AAPL", BUCKET_LIVE, MARKET_OVERSEAS_STOCK, "USD",
         "long", 12, 188.40, 205.10, 1.0, None, FX_USD),       # +이익
        (MARKET_OVERSEAS_STOCK, "NVDA", BUCKET_LIVE, MARKET_OVERSEAS_STOCK, "USD",
         "long", 6, 132.10, 121.55, 1.0, None, FX_USD),        # -손실
        # --- paper: 해외선물 2 (HKD, 승수 50) ---
        (MARKET_OVERSEAS_FUTUREOPTION, "HBIM26", BUCKET_PAPER,
         MARKET_OVERSEAS_FUTUREOPTION, "HKD",
         "long", 2, 10_850, 11_240, 50.0, 14_000.0, FX_HKD),   # +이익
    ]
    for (am, sym, bucket, market, ccy, side, qty, avg, cur, mult, margin, fx) in rows:
        aid = acct[am]
        iid = inst[sym]
        # 통화단위 미실현 = (현재가-평단)*qty*승수 (long 기준)
        pnl_ccy = (cur - avg) * qty * mult
        pnl_rate = (cur - avg) / avg if avg else 0.0
        # KRW 환산 평가액 = 현재가*qty*승수*환율
        eval_krw = cur * qty * mult * fx
        # 권위(reconcile) 컬럼
        await repo.upsert_position_authority(
            aid, iid, bucket=bucket, market=market, currency=ccy,
            position_side=side, qty=float(qty), avg_price=float(avg),
            multiplier=mult, margin_used=margin,
        )
        # 보강(tracker) 컬럼 — 가격/미실현/환산
        await repo.upsert_position_marks(
            aid, iid, current_price=float(cur), pnl_amount=pnl_ccy,
            pnl_rate=pnl_rate, fx_now=fx, fx_at_buy=fx, fx_estimated=0,
            eval_krw=eval_krw,
        )
    return len(rows)


# ── bucket_kpi (live/paper 헤드라인 — positions 합과 대략 일관) ───────────────
async def _seed_bucket_kpi(repo: OrdersRepo, acct: dict[str, int], inst: dict[str, int]) -> int:
    # positions 의 eval_krw / 매수원가 KRW 를 버킷별로 직접 합산해 KPI 를 정합화.
    live_aids = (acct[MARKET_KOREA_STOCK], acct[MARKET_OVERSEAS_STOCK])
    paper_aids = (acct[MARKET_OVERSEAS_FUTUREOPTION],)

    async def _agg(aids: tuple[int, ...]) -> dict[str, float]:
        eval_sum = buy_sum = 0.0
        cnt = 0
        evals: list[float] = []
        for aid in aids:
            for p in await repo.list_positions(aid):
                if not p.get("qty"):
                    continue
                ek = float(p.get("eval_krw") or 0.0)
                mult = float(p.get("multiplier") or 1.0)
                fx = float(p.get("fx_at_buy") or 1.0)
                buy = float(p["avg_price"] or 0.0) * float(p["qty"]) * mult * fx
                eval_sum += ek
                buy_sum += buy
                evals.append(ek)
                cnt += 1
        # 집중도(HHI) — 평가액 비중 제곱합
        tot = sum(evals) or 1.0
        weights = [e / tot for e in evals]
        hhi = sum(w * w for w in weights)
        n = len(evals) or 1
        norm_hhi = (hhi - 1.0 / n) / (1.0 - 1.0 / n) if n > 1 else 0.0
        eff_n = (1.0 / hhi) if hhi else float(n)
        top1 = max(weights) if weights else 0.0
        return {
            "eval": eval_sum, "buy": buy_sum, "pnl": eval_sum - buy_sum, "count": cnt,
            "hhi": hhi, "norm_hhi": norm_hhi, "eff_n": eff_n, "top1": top1,
        }

    live = await _agg(live_aids)
    paper = await _agg(paper_aids)

    await repo.insert_bucket_kpi(
        BUCKET_LIVE,
        account_pnl_rate=(live["pnl"] / live["buy"]) if live["buy"] else 0.0,
        total_eval_krw=live["eval"], total_buy_krw=live["buy"], total_pnl_krw=live["pnl"],
        position_count=live["count"], hhi=live["hhi"], norm_hhi=live["norm_hhi"],
        eff_n=live["eff_n"], top1_weight=live["top1"], currency_hhi=0.51,
        daily_realized_krw=1_280_000.0, daily_pnl_krw=live["pnl"] * 0.12,
        drawdown_pct=-3.4, risk_budget_left_krw=720_000.0, halted=0,
    )
    await repo.insert_bucket_kpi(
        BUCKET_PAPER,
        account_pnl_rate=(paper["pnl"] / paper["buy"]) if paper["buy"] else 0.0,
        total_eval_krw=paper["eval"], total_buy_krw=paper["buy"], total_pnl_krw=paper["pnl"],
        position_count=paper["count"], hhi=paper["hhi"], norm_hhi=paper["norm_hhi"],
        eff_n=paper["eff_n"], top1_weight=paper["top1"], currency_hhi=1.0,
        daily_realized_krw=612_000.0, daily_pnl_krw=paper["pnl"] * 0.20,
        drawdown_pct=-1.1, risk_budget_left_krw=388_000.0, halted=0,
    )
    return 2


# ── orders + transitions + fills (직접 SQL — 임의 상태 세팅, 상태머신 우회) ────
def _seed_orders_sync(db_path: str, acct: dict[str, int], inst: dict[str, int]) -> dict[str, int]:
    """orders/order_state_transitions/fills 를 직접 INSERT OR IGNORE 로 멱등 적재.

    상태머신을 거치지 않고 (accepted/filled/rejected) 임의 상태를 만들기 위해 직접 SQL.
    멱등키는 'demo:' 접두 idempotency_key, transitions/fills 는 부모 order_id 재조회 후
    DELETE→재삽입(부모가 demo 일 때만)로 깔끔히 재현.
    """
    now = utc_now_iso()
    counts = {"orders": 0, "transitions": 0, "fills": 0}
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON;")

    fut_aid = acct[MARKET_OVERSEAS_FUTUREOPTION]
    kr_aid = acct[MARKET_KOREA_STOCK]
    os_aid = acct[MARKET_OVERSEAS_STOCK]

    # (key, account_id, instrument_id, market, mode, side, otype, qty, price, status,
    #  broker_order_id, filled_qty, remaining_qty, avg_fill_price, reject_reason, ccy, exchange,
    #  transitions[list of (from,to,trigger)], fills[list of (exec_qty,exec_price,event_seq)])
    specs = [
        # paper FUT — 열린 주문(accepted, broker_order_id 채움)
        dict(key="demo:fut:HBIM26:buy:open1", aid=fut_aid, iid=inst["HBIM26"],
             market=MARKET_OVERSEAS_FUTUREOPTION, mode="paper", side="buy", otype="limit",
             qty=2, price=10_900.0, status="accepted", broker="DEMO000101",
             filled=0, remaining=2, avg_fill=None, reject=None, ccy="HKD", exch="HKEX",
             trans=[("approved", "submitted", "tr_response"),
                    ("submitted", "accepted", "tr_response")],
             fills=[]),
        # paper FUT — 열린 주문2 (accepted)
        dict(key="demo:fut:HBIN26:sell:open2", aid=fut_aid, iid=inst["HBIM26"],
             market=MARKET_OVERSEAS_FUTUREOPTION, mode="paper", side="sell", otype="limit",
             qty=1, price=11_500.0, status="accepted", broker="DEMO000102",
             filled=0, remaining=1, avg_fill=None, reject=None, ccy="HKD", exch="HKEX",
             trans=[("approved", "submitted", "tr_response"),
                    ("submitted", "accepted", "tr_response")],
             fills=[]),
        # paper FUT — 체결 완료(filled) + fills 1건
        dict(key="demo:fut:HBIM26:buy:filled", aid=fut_aid, iid=inst["HBIM26"],
             market=MARKET_OVERSEAS_FUTUREOPTION, mode="paper", side="buy", otype="limit",
             qty=2, price=10_850.0, status="filled", broker="DEMO000103",
             filled=2, remaining=0, avg_fill=10_850.0, reject=None, ccy="HKD", exch="HKEX",
             trans=[("approved", "submitted", "tr_response"),
                    ("submitted", "accepted", "tr_response"),
                    ("accepted", "filled", "reconcile")],
             fills=[(2, 10_850.0, "demo:exec:103:1")]),
        # paper FUT — 거부(rejected) + reject_reason
        dict(key="demo:fut:HBIM26:buy:rejected", aid=fut_aid, iid=inst["HBIM26"],
             market=MARKET_OVERSEAS_FUTUREOPTION, mode="paper", side="buy", otype="limit",
             qty=5, price=12_000.0, status="rejected", broker=None,
             filled=0, remaining=5, avg_fill=None,
             reject="PER_ORDER_CAP_KRW 초과(데모)", ccy="HKD", exch="HKEX",
             trans=[("approved", "submitted", "tr_response"),
                    ("submitted", "rejected", "tr_response")],
             fills=[]),
        # live KR — 과거 체결(filled, history 용)
        dict(key="demo:kr:005930:buy:filled", aid=kr_aid, iid=inst["005930"],
             market=MARKET_KOREA_STOCK, mode="live", side="buy", otype="limit",
             qty=50, price=71_500.0, status="filled", broker="DEMOKR0001",
             filled=50, remaining=0, avg_fill=71_500.0, reject=None, ccy="KRW", exch="KRX",
             trans=[("approved", "submitted", "tr_response"),
                    ("submitted", "accepted", "tr_response"),
                    ("accepted", "filled", "reconcile")],
             fills=[(50, 71_500.0, "demo:exec:kr1:1")]),
        # live OS — 과거 체결(filled, history 용)
        dict(key="demo:os:AAPL:buy:filled", aid=os_aid, iid=inst["AAPL"],
             market=MARKET_OVERSEAS_STOCK, mode="live", side="buy", otype="limit",
             qty=12, price=188.40, status="filled", broker="DEMOOS0001",
             filled=12, remaining=0, avg_fill=188.40, reject=None, ccy="USD", exch="NASDAQ",
             trans=[("approved", "submitted", "tr_response"),
                    ("submitted", "accepted", "tr_response"),
                    ("accepted", "filled", "reconcile")],
             fills=[(12, 188.40, "demo:exec:os1:1")]),
    ]

    for s in specs:
        cur = con.execute(
            "INSERT OR IGNORE INTO orders "
            "(idempotency_key, account_id, instrument_id, market, trading_mode, side, "
            " order_type, qty, price, status, broker_order_id, filled_qty, remaining_qty, "
            " avg_fill_price, reject_reason, currency, exchange, relation, "
            " created_at, updated_at, submitted_at, accepted_at, terminal_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'new', ?,?,?,?,?)",
            (
                s["key"], s["aid"], s["iid"], s["market"], s["mode"], s["side"],
                s["otype"], float(s["qty"]), s["price"], s["status"], s["broker"],
                float(s["filled"]), float(s["remaining"]), s["avg_fill"], s["reject"],
                s["ccy"], s["exch"], now, now, now,
                now if s["status"] in ("accepted", "filled") else None,
                now if s["status"] in ("filled", "rejected", "canceled", "expired") else None,
            ),
        )
        if cur.rowcount:
            counts["orders"] += 1
        # 부모 order_id 재조회(멱등 — IGNORE 됐어도 존재)
        oid = con.execute(
            "SELECT id FROM orders WHERE idempotency_key=?", (s["key"],)
        ).fetchone()["id"]

        # transitions: demo 부모만 정리 후 재삽입(append-only 라 중복 방지용 DELETE)
        con.execute("DELETE FROM order_state_transitions WHERE order_id=?", (oid,))
        for (frm, to, trig) in s["trans"]:
            con.execute(
                "INSERT INTO order_state_transitions "
                "(order_id, from_state, to_state, trigger, event_ref, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (oid, frm, to, trig, "demo", now),
            )
            counts["transitions"] += 1

        # fills: (order_id,event_seq) UNIQUE 라 OR IGNORE 로 멱등
        for (q, px, seq) in s["fills"]:
            fc = con.execute(
                "INSERT OR IGNORE INTO fills "
                "(order_id, broker_ord_no, qty, price, fee, exec_qty, exec_price, "
                " remaining_qty, ord_status_code, origin, event_seq, raw_json, filled_at) "
                "VALUES (?,?,?,?,0,?,?,0,'2','reconcile',?,?,?)",
                (oid, s["broker"], float(q), float(px), float(q), float(px),
                 seq, '{"demo": true}', now),
            )
            if fc.rowcount:
                counts["fills"] += 1

    con.commit()
    con.close()
    return counts


# ── risk_events (severity 섞어서 — Risk 화면 위반로그) ───────────────────────
async def _seed_risk_events(repo: OrdersRepo) -> int:
    # detail 에 {"demo": true} 마커 → 멱등 cleanup 이 이 행들만 지운다.
    events = [
        ("pre_check_warn", "info", MARKET_OVERSEAS_FUTUREOPTION,
         "ORDERABLE_UNKNOWN 잔고 스냅샷 부재(데모)"),
        ("warn", "warn", MARKET_KOREA_STOCK, "max_symbol_weight 75% 근접 — 005930 집중(데모)"),
        ("breach", "warn", MARKET_OVERSEAS_STOCK, "FX_ESTIMATED 추정환율 사용 경고(데모)"),
        ("pre_check_reject", "critical", MARKET_OVERSEAS_FUTUREOPTION,
         "PER_ORDER_CAP_KRW 초과로 주문 거부(데모)"),
        ("breach", "critical", BUCKET_PAPER, "max_daily_loss_eval 한도의 80% 소진(데모)"),
        ("kill_switch", "info", "global", "킬스위치 release — 정상 운용 복귀(데모)"),
    ]
    for (etype, sev, scope, msg) in events:
        await repo.insert_risk_event(
            event_type=etype, severity=sev, scope=scope, scope_ref=scope,
            message=msg, detail={"demo": True},
        )
    return len(events)


# ── fx_rates (KRW base — 화면 환산 표시) ──────────────────────────────────────
async def _seed_fx(repo: OrdersRepo) -> int:
    await repo.upsert_fx_rate("USD", FX_USD, source="demo", fx_estimated=0)
    await repo.upsert_fx_rate("HKD", FX_HKD, source="demo", fx_estimated=0)
    return 2


# ── 멱등 cleanup — 이전 데모 행만 제거(실데이터 보존) ─────────────────────────
def _clean_demo_sync(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON;")
    # 데모 주문의 자식(fills/transitions) 먼저 → orders → 나머지.
    demo_oids = [
        r[0] for r in con.execute(
            "SELECT id FROM orders WHERE idempotency_key LIKE ?", (DEMO_PREFIX + "%",)
        ).fetchall()
    ]
    if demo_oids:
        ph = ",".join("?" * len(demo_oids))
        con.execute(f"DELETE FROM fills WHERE order_id IN ({ph})", demo_oids)
        con.execute(f"DELETE FROM order_state_transitions WHERE order_id IN ({ph})", demo_oids)
        con.execute(f"DELETE FROM orders WHERE id IN ({ph})", demo_oids)
    # 데모 risk_events (detail_json 마커)
    con.execute("DELETE FROM risk_events WHERE detail_json LIKE ?", ("%" + DEMO_DETAIL + "%",))
    # 데모 fx_rates — source='demo' 마커로 이전 실행분만 정리(재실행 누적 방지).
    con.execute("DELETE FROM fx_rates WHERE source=?", ("demo",))
    # bucket_kpi: 마커 컬럼이 없어 비-demo 집계기 행과 구분 불가 → 안전 삭제 불가.
    #   append-only(최신 1행만 API 가 읽음, ORDER BY id DESC LIMIT 1)라 재실행당 버킷별 1행만
    #   추가될 뿐 화면엔 항상 최신만 보인다(중복 폭증 아님, 설계상 이력 누적).
    # 데모 계좌/잔고/포지션은 ensure/upsert 멱등이라 cleanup 불필요(중복 안 생김).
    con.commit()
    con.close()


async def main() -> None:
    s = Settings()
    db_path = s.hanbit_db_path
    print(f"[seed_demo] DB = {db_path}")
    await init_db(db_path)  # 마이그레이션 적용 보장

    # 1) 이전 데모 행 정리(실데이터 보존)
    _clean_demo_sync(db_path)

    repo = OrdersRepo(db_path)

    # 2) 적재
    inst = await _seed_instruments(repo)
    acct = await _seed_accounts(repo)
    n_bal = await _seed_balances(repo, acct)
    n_pos = await _seed_positions(repo, acct, inst)
    n_kpi = await _seed_bucket_kpi(repo, acct, inst)
    ord_counts = _seed_orders_sync(db_path, acct, inst)
    n_risk = await _seed_risk_events(repo)
    n_fx = await _seed_fx(repo)

    print("[seed_demo] 적재 완료 (이번 실행 신규 행 기준):")
    print(f"  instruments(upsert) : {len(inst)} (KR2/OS2/FUT1)")
    print(f"  accounts(upsert)    : {len(acct)} (KR/OS/FUT)")
    print(f"  balances_snapshot   : {n_bal}")
    print(f"  positions           : {n_pos}")
    print(f"  bucket_kpi          : {n_kpi} (live/paper)")
    print(f"  orders              : {ord_counts['orders']} (신규)")
    print(f"  order_transitions   : {ord_counts['transitions']}")
    print(f"  fills               : {ord_counts['fills']} (신규)")
    print(f"  risk_events         : {n_risk}")
    print(f"  fx_rates            : {n_fx}")
    print("[seed_demo] done — 교육용 데모 시드(실데이터 아님).")


if __name__ == "__main__":
    asyncio.run(main())
