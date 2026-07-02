"""주문 도메인 데이터 계층 (M2) — orders/fills/transitions/positions + 리스크/메트릭/화이트리스트.

aiosqlite 연결을 작업 단위로 열고(FK on), 다단계 원자 작업(fill 적용 등)은 한 트랜잭션으로 묶는다.
상태 전이는 state_machine 의 합법성 판정을 거쳐 orders.status + order_state_transitions 에 기록한다.
호출자는 order_id 단일 writer 락(OrderLocks) 안에서 mutate 를 직렬화한다.
"""

from __future__ import annotations

import json

import aiosqlite

from app.models.order_dto import TERMINAL_STATES, Fill, OrderState
from app.models.schemas import utc_now_iso
from app.orders.state_machine import StateMachineError, assert_transition, can_transition


class OrdersRepo:
    """주문/체결/포지션/리스크/메트릭/화이트리스트 영속 계층."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def _connect(self) -> aiosqlite.Connection:
        return aiosqlite.connect(self._db_path)

    @staticmethod
    async def _prep(db: aiosqlite.Connection) -> None:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON;")

    # ── 계좌 / 종목 ──────────────────────────────────────────────────────
    async def get_account_id(self, market: str) -> int | None:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT id FROM accounts WHERE market=? ORDER BY id LIMIT 1", (market,)
            ) as cur:
                row = await cur.fetchone()
        return row["id"] if row else None

    async def list_accounts(self) -> list[dict]:
        """전체 계좌 행(시장/모드/통화) — `/accounts` API 가 시장별 잔고 스냅샷에 앵커로 쓴다."""
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT id, account_no, market, trading_mode, currency, label "
                "FROM accounts ORDER BY id"
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def ensure_account(
        self,
        market: str,
        account_no: str,
        *,
        trading_mode: str,
        currency: str | None = None,
        label: str | None = None,
    ) -> int:
        """accounts upsert(account_no 유니크) 후 id 반환. balances/positions FK 앵커(M3a)."""
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT id FROM accounts WHERE account_no=?", (account_no,)
            ) as cur:
                row = await cur.fetchone()
            if row is not None:
                return row["id"]
            cur = await db.execute(
                "INSERT INTO accounts (account_no, market, trading_mode, currency, label) "
                "VALUES (?,?,?,?,?)",
                (account_no, market, trading_mode, currency, label),
            )
            await db.commit()
            return cur.lastrowid

    async def ensure_instrument(
        self, market: str, symbol: str, *, exchange: str | None = None, **meta
    ) -> int:
        """instruments upsert 후 id 반환. meta: multiplier/tick_size/.../whitelisted 등."""
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT id FROM instruments WHERE market=? AND symbol=?", (market, symbol)
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                cols = ["market", "symbol", "exchange", *meta.keys()]
                vals = [market, symbol, exchange, *meta.values()]
                ph = ",".join("?" * len(cols))
                cur = await db.execute(
                    f"INSERT INTO instruments ({','.join(cols)}) VALUES ({ph})", tuple(vals)
                )
                await db.commit()
                return cur.lastrowid
            if meta or exchange is not None:
                sets = {"exchange": exchange, **meta} if exchange is not None else dict(meta)
                if sets:
                    assign = ",".join(f"{k}=?" for k in sets)
                    await db.execute(
                        f"UPDATE instruments SET {assign} WHERE id=?",
                        (*sets.values(), row["id"]),
                    )
                    await db.commit()
            return row["id"]

    async def get_instrument(self, market: str, symbol: str) -> dict | None:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT * FROM instruments WHERE market=? AND symbol=?", (market, symbol)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def get_instrument_by_id(self, instrument_id: int) -> dict | None:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT * FROM instruments WHERE id=?", (instrument_id,)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    # ── 주문 ─────────────────────────────────────────────────────────────
    async def insert_order(self, **fields) -> tuple[int, bool]:
        """주문 INSERT. idempotency_key 충돌 시 (기존 id, False) 반환(멱등)."""
        cols = ",".join(fields)
        ph = ",".join("?" * len(fields))
        async with self._connect() as db:
            await self._prep(db)
            try:
                cur = await db.execute(
                    f"INSERT INTO orders ({cols}) VALUES ({ph})", tuple(fields.values())
                )
                await db.commit()
                return cur.lastrowid, True
            except aiosqlite.IntegrityError:
                async with db.execute(
                    "SELECT id FROM orders WHERE idempotency_key=?",
                    (fields["idempotency_key"],),
                ) as cur:
                    row = await cur.fetchone()
                if row is None:
                    raise
                return row["id"], False

    async def get_order(self, order_id: int) -> dict | None:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute("SELECT * FROM orders WHERE id=?", (order_id,)) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def get_order_by_broker(self, account_id: int, broker_ord_no: str) -> dict | None:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT * FROM orders WHERE account_id=? AND broker_order_id=?",
                (account_id, broker_ord_no),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def transition(
        self,
        order_id: int,
        to_state: OrderState,
        trigger: str,
        *,
        event_ref: str | None = None,
        updates: dict | None = None,
    ) -> OrderState:
        """합법성 검증 후 orders.status 갱신 + 전이 이력 기록(단일 트랜잭션)."""
        now = utc_now_iso()
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute("SELECT status FROM orders WHERE id=?", (order_id,)) as cur:
                row = await cur.fetchone()
            if row is None:
                raise StateMachineError(to_state, to_state, f"order {order_id} not found")
            frm = OrderState(row["status"])
            assert_transition(frm, to_state)

            sets: dict = {"status": to_state.value, "updated_at": now}
            if to_state == OrderState.SUBMITTED:
                sets["submitted_at"] = now
            elif to_state == OrderState.ACCEPTED:
                sets["accepted_at"] = now
            if to_state in TERMINAL_STATES:
                sets["terminal_at"] = now
            if updates:
                sets.update(updates)
            assign = ",".join(f"{k}=?" for k in sets)
            await db.execute(
                f"UPDATE orders SET {assign} WHERE id=?", (*sets.values(), order_id)
            )
            await db.execute(
                "INSERT INTO order_state_transitions "
                "(order_id, from_state, to_state, trigger, event_ref) VALUES (?,?,?,?,?)",
                (order_id, frm.value, to_state.value, trigger, event_ref),
            )
            await db.commit()
        return frm

    async def update_order_fields(self, order_id: int, **updates) -> None:
        if not updates:
            return
        updates["updated_at"] = utc_now_iso()
        assign = ",".join(f"{k}=?" for k in updates)
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                f"UPDATE orders SET {assign} WHERE id=?", (*updates.values(), order_id)
            )
            await db.commit()

    async def list_open_orders(self) -> list[dict]:
        non_terminal = tuple(
            s.value for s in OrderState if s not in TERMINAL_STATES
        )
        ph = ",".join("?" * len(non_terminal))
        async with self._connect() as db:
            await self._prep(db)
            # working 주문(relation='new')만 — modify/cancel 은 감사 child 라 제외.
            async with db.execute(
                f"SELECT * FROM orders WHERE status IN ({ph}) AND relation='new' ORDER BY id DESC",
                non_terminal,
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_orders(self, *, limit: int = 50, offset: int = 0) -> list[dict]:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT * FROM orders ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_transitions(self, order_id: int) -> list[dict]:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT * FROM order_state_transitions WHERE order_id=? ORDER BY id", (order_id,)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── 체결 적용 (멱등 + 집계 + 상태전이, 단일 트랜잭션) ──────────────────
    async def apply_fill(self, order_id: int, fill: Fill, trigger: str = "reconcile") -> bool:
        """fill 멱등 적재 후 누적 체결/잔량/평균가 갱신 + 상태 전이. 적용되면 True."""
        now = utc_now_iso()
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute("SELECT status, qty FROM orders WHERE id=?", (order_id,)) as cur:
                o = await cur.fetchone()
            if o is None:
                return False
            frm = OrderState(o["status"])
            if frm in TERMINAL_STATES:
                return False  # 터미널 주문엔 체결 적용 금지

            cur = await db.execute(
                "INSERT OR IGNORE INTO fills "
                "(order_id, broker_ord_no, qty, price, fee, exec_qty, exec_price, remaining_qty, "
                " ord_status_code, origin, event_seq, raw_json, filled_at) "
                "VALUES (?,?,?,?,0,?,?,?,?,?,?,?,?)",
                (
                    order_id, fill.broker_ord_no, fill.exec_qty, fill.exec_price,
                    fill.exec_qty, fill.exec_price, fill.remaining_qty, fill.ord_status_code,
                    fill.origin, fill.event_seq, json.dumps(fill.raw or {}), now,
                ),
            )
            if cur.rowcount == 0:  # 중복 이벤트 — 멱등 무시
                await db.commit()
                return False

            async with db.execute(
                "SELECT COALESCE(SUM(exec_qty),0) sq, COALESCE(SUM(exec_qty*exec_price),0) sv "
                "FROM fills WHERE order_id=? AND exec_qty IS NOT NULL",
                (order_id,),
            ) as cur2:
                agg = await cur2.fetchone()
            filled = float(agg["sq"] or 0)
            avg = (agg["sv"] / filled) if filled else None
            ord_qty = float(o["qty"] or 0)
            remaining = (
                float(fill.remaining_qty)
                if fill.remaining_qty is not None
                else max(ord_qty - filled, 0.0)
            )

            sets: dict = {
                "filled_qty": filled,
                "remaining_qty": remaining,
                "avg_fill_price": avg,
                "updated_at": now,
            }
            # 목표 상태 판정
            if remaining <= 0 and filled > 0:
                to = OrderState.FILLED
            elif filled > 0:
                to = OrderState.PARTIALLY_FILLED
            else:
                to = frm

            if to != frm and can_transition(frm, to):
                if to in TERMINAL_STATES:
                    sets["terminal_at"] = now
                sets["status"] = to.value
                assign = ",".join(f"{k}=?" for k in sets)
                await db.execute(
                    f"UPDATE orders SET {assign} WHERE id=?", (*sets.values(), order_id)
                )
                await db.execute(
                    "INSERT INTO order_state_transitions "
                    "(order_id, from_state, to_state, trigger, event_ref) VALUES (?,?,?,?,?)",
                    (order_id, frm.value, to.value, trigger, fill.event_seq),
                )
            else:
                assign = ",".join(f"{k}=?" for k in sets)
                await db.execute(
                    f"UPDATE orders SET {assign} WHERE id=?", (*sets.values(), order_id)
                )
            await db.commit()
        return True

    async def fill_exists(self, order_id: int, event_seq: str) -> bool:
        """해당 event_seq 가 이미 적재됐는지(멱등키 존재 여부).

        apply_fill==False 의 두 원인(터미널 주문 도착 vs 멱등 중복)을 호출자가 구분할 때 쓴다
        (실시간 TC 보강 경로의 관측, M3b §10). UNIQUE(order_id,event_seq) 기준.
        """
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT 1 FROM fills WHERE order_id=? AND event_seq=? LIMIT 1",
                (order_id, event_seq),
            ) as cur:
                return await cur.fetchone() is not None

    # ── 체결 기반 실현손익 (M3b §5.3 — 일일손실 realized 권위) ──────────────
    async def realized_pnl_krw(self, markets: tuple[str, ...], fx) -> float:
        """버킷 fills 평균원가 매칭 → 누적 실현손익(KRW, 부호; 음수=손실).

        일일손실 한도의 realized 권위(§5.3): 트래커/잔고 realized_pnl 이 lifetime 인지 일중인지
        라이브 미확정(§13-3)이므로 우리 체결 원장에서 직접 산출한다. 통화별 KRW 환산은 중립
        환율(fx.to_krw). 거래일 경계내 실현은 DailyLossMonitor 가 baseline 차분으로 추출한다.
        """
        if not markets:
            return 0.0
        from app.portfolio.realized import realized_pnl_ccy

        ph = ",".join("?" * len(markets))
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                f"SELECT o.instrument_id AS iid, o.side AS side, o.currency AS o_ccy, "
                f"       i.currency AS i_ccy, i.multiplier AS mult, "
                f"       f.exec_qty AS q, f.exec_price AS p "
                f"FROM fills f JOIN orders o ON o.id = f.order_id "
                f"JOIN instruments i ON i.id = o.instrument_id "
                f"WHERE o.market IN ({ph}) AND f.exec_qty IS NOT NULL AND f.exec_qty > 0 "
                f"ORDER BY o.instrument_id, f.filled_at, f.id",
                tuple(markets),
            ) as cur:
                rows = await cur.fetchall()
        by_inst: dict[int, dict] = {}
        for r in rows:
            d = by_inst.setdefault(
                r["iid"],
                {"fills": [], "mult": r["mult"], "ccy": r["o_ccy"] or r["i_ccy"] or "USD"},
            )
            signed = float(r["q"]) if r["side"] == "buy" else -float(r["q"])
            d["fills"].append((signed, float(r["p"])))
        total_krw = 0.0
        for d in by_inst.values():
            realized_ccy = realized_pnl_ccy(d["fills"], d["mult"])
            rate, _ = fx.to_krw(d["ccy"])
            total_krw += realized_ccy * rate
        return total_krw

    # ── 포지션 ───────────────────────────────────────────────────────────
    async def upsert_position(
        self,
        account_id: int,
        instrument_id: int,
        *,
        qty: float,
        avg_price: float | None = None,
        realized_pnl: float | None = None,
    ) -> None:
        now = utc_now_iso()
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                "INSERT INTO positions "
                "(account_id, instrument_id, qty, avg_price, realized_pnl, updated_at) "
                "VALUES (?,?,?,?,COALESCE(?,0),?) "
                "ON CONFLICT(account_id, instrument_id) DO UPDATE SET "
                "qty=excluded.qty, avg_price=excluded.avg_price, updated_at=excluded.updated_at",
                (account_id, instrument_id, qty, avg_price, realized_pnl, now),
            )
            await db.commit()

    async def list_positions(self, account_id: int) -> list[dict]:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT * FROM positions WHERE account_id=?", (account_id,)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── 포지션 이중 writer 필드 분할 (M3a §4.2) ───────────────────────────
    async def upsert_position_authority(
        self,
        account_id: int,
        instrument_id: int,
        *,
        bucket: str,
        market: str,
        currency: str | None = None,
        position_side: str | None = None,
        qty: float,
        avg_price: float | None = None,
        multiplier: float | None = None,
        margin_used: float | None = None,
    ) -> None:
        """권위 소스(reconcile) — qty/avg_price/margin/bucket/market/통화/방향만 SET.

        보강 컬럼(current_price/pnl/fx/eval_krw)은 절대 건드리지 않는다(tracker 권위 보존).
        """
        now = utc_now_iso()
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                "INSERT INTO positions "
                "(account_id, instrument_id, qty, avg_price, bucket, market, currency, "
                " position_side, multiplier, margin_used, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(account_id, instrument_id) DO UPDATE SET "
                "qty=excluded.qty, avg_price=excluded.avg_price, bucket=excluded.bucket, "
                "market=excluded.market, currency=excluded.currency, "
                "position_side=excluded.position_side, multiplier=excluded.multiplier, "
                "margin_used=excluded.margin_used, updated_at=excluded.updated_at",
                (account_id, instrument_id, qty, avg_price, bucket, market, currency,
                 position_side, multiplier, margin_used, now),
            )
            await db.commit()

    async def upsert_position_marks(
        self,
        account_id: int,
        instrument_id: int,
        *,
        current_price: float | None = None,
        pnl_amount: float | None = None,
        pnl_rate: float | None = None,
        fx_now: float | None = None,
        fx_at_buy: float | None = None,
        fx_estimated: int = 0,
        eval_krw: float | None = None,
    ) -> None:
        """보강 소스(tracker) — 가격/미실현/환산만 SET. 권위 컬럼(qty/avg_price) 불변.

        행이 없으면(account_id, instrument_id) 신규 — qty 는 DEFAULT 0 으로 들어가고
        권위 upsert 가 나중에 채운다.
        """
        now = utc_now_iso()
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                "INSERT INTO positions "
                "(account_id, instrument_id, current_price, pnl_amount, pnl_rate, fx_now, "
                " fx_at_buy, fx_estimated, eval_krw, pos_updated_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(account_id, instrument_id) DO UPDATE SET "
                "current_price=excluded.current_price, pnl_amount=excluded.pnl_amount, "
                "pnl_rate=excluded.pnl_rate, fx_now=excluded.fx_now, "
                "fx_at_buy=COALESCE(positions.fx_at_buy, excluded.fx_at_buy), "
                "fx_estimated=excluded.fx_estimated, eval_krw=excluded.eval_krw, "
                "pos_updated_at=excluded.pos_updated_at",
                (account_id, instrument_id, current_price, pnl_amount, pnl_rate, fx_now,
                 fx_at_buy, fx_estimated, eval_krw, now, now),
            )
            await db.commit()

    async def positions_for(self, bucket: str) -> list[dict]:
        """버킷-스코프 포지션(보유 qty != 0) + 종목코드(instruments JOIN).

        책-전체 조회 금지 — 버킷 격리를 구조로 강제(§3). reduce-only/노출 판정이 symbol 로
        매칭하므로 instruments.symbol 을 함께 노출한다.
        """
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT p.*, i.symbol AS symbol FROM positions p "
                "JOIN instruments i ON i.id = p.instrument_id "
                "WHERE p.bucket=? AND p.qty != 0",
                (bucket,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def equity_for(self, bucket: str) -> float:
        """버킷 총평가액(KRW 환산 합) — 집중도 분모/일일손실 baseline."""
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT COALESCE(SUM(eval_krw),0) e FROM positions "
                "WHERE bucket=? AND eval_krw IS NOT NULL AND qty != 0",
                (bucket,),
            ) as cur:
                row = await cur.fetchone()
        return float(row["e"] or 0.0)

    async def open_orders_for(self, markets: tuple[str, ...]) -> list[dict]:
        """버킷(=시장 집합)의 working 미체결 행. 버킷-스코프(§3) — 책-전체 조회 금지.

        relation='new'(modify/cancel 감사 child 제외) + 비터미널만. 미체결 수·명목 산출에 쓴다.
        """
        if not markets:
            return []
        non_terminal = tuple(s.value for s in OrderState if s not in TERMINAL_STATES)
        ph_s = ",".join("?" * len(non_terminal))
        ph_m = ",".join("?" * len(markets))
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                f"SELECT * FROM orders WHERE status IN ({ph_s}) "
                f"AND relation='new' AND market IN ({ph_m})",
                (*non_terminal, *markets),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def has_quarantined(self, markets: tuple[str, ...]) -> bool:
        """버킷(=시장 집합)에 격리(quarantined) 주문이 있는가 (M3b §7.1 ENTRY 차단용)."""
        if not markets:
            return False
        ph = ",".join("?" * len(markets))
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                f"SELECT 1 FROM orders WHERE status=? AND market IN ({ph}) LIMIT 1",
                (OrderState.QUARANTINED.value, *markets),
            ) as cur:
                row = await cur.fetchone()
        return row is not None

    async def list_by_status(self, status: OrderState, markets: tuple[str, ...]) -> list[dict]:
        """버킷-스코프 특정 상태 주문 목록(격리 조회/킬스위치 raw-cancel 대상 등)."""
        if not markets:
            return []
        ph = ",".join("?" * len(markets))
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                f"SELECT * FROM orders WHERE status=? AND market IN ({ph}) ORDER BY id DESC",
                (status.value, *markets),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── 잔고 / KPI / 환율 / 위험상태 스냅샷 (M3a) ──────────────────────────
    async def upsert_balance_snapshot(
        self, account_id: int, currency: str, **fields
    ) -> None:
        cols = ["account_id", "currency", *fields.keys(), "updated_at"]
        vals = [account_id, currency, *fields.values(), utc_now_iso()]
        ph = ",".join("?" * len(cols))
        assign = ",".join(f"{k}=excluded.{k}" for k in (*fields.keys(), "updated_at"))
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                f"INSERT INTO balances_snapshot ({','.join(cols)}) VALUES ({ph}) "
                f"ON CONFLICT(account_id, currency) DO UPDATE SET {assign}",
                tuple(vals),
            )
            await db.commit()

    async def list_balances(self, account_id: int) -> list[dict]:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT * FROM balances_snapshot WHERE account_id=?", (account_id,)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def latest_orderable(self, account_id: int, currency: str) -> float | None:
        """통화별 가용주문금액(잔고 스냅샷) — 게이트 orderable 헤드룸(§6 floor 환산)용.

        place 는 라이브 조회 안 함(best-effort): reconcile/집계기가 채운 스냅샷을 읽는다.
        없으면 None → 게이트가 ORDERABLE_UNKNOWN(WARN)로 통과(M2 동작 보존).
        """
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT orderable_amount FROM balances_snapshot "
                "WHERE account_id=? AND currency=?",
                (account_id, currency),
            ) as cur:
                row = await cur.fetchone()
        if row is None or row["orderable_amount"] is None:
            return None
        return float(row["orderable_amount"])

    async def insert_bucket_kpi(self, bucket: str, **fields) -> None:
        cols = ["bucket", *fields.keys()]
        ph = ",".join("?" * len(cols))
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                f"INSERT INTO bucket_kpi ({','.join(cols)}) VALUES ({ph})",
                (bucket, *fields.values()),
            )
            await db.commit()

    async def get_latest_bucket_kpi(self, bucket: str) -> dict | None:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT * FROM bucket_kpi WHERE bucket=? ORDER BY id DESC LIMIT 1", (bucket,)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def upsert_fx_rate(
        self, quote_ccy: str, to_krw: float, *, source: str, fx_estimated: int = 0
    ) -> None:
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                "INSERT OR REPLACE INTO fx_rates (quote_ccy, to_krw, source, fx_estimated, as_of) "
                "VALUES (?,?,?,?,?)",
                (quote_ccy, to_krw, source, fx_estimated, utc_now_iso()),
            )
            await db.commit()

    async def get_latest_fx_rate(self, quote_ccy: str) -> dict | None:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT * FROM fx_rates WHERE quote_ccy=? ORDER BY as_of DESC LIMIT 1",
                (quote_ccy,),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def get_risk_state(self, bucket: str) -> dict | None:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT * FROM risk_state WHERE bucket=?", (bucket,)
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def set_risk_state(self, bucket: str, **fields) -> None:
        """risk_state upsert(halt_state/baseline/daily_notional/last_reset_day)."""
        fields["updated_at"] = utc_now_iso()
        cols = ["bucket", *fields.keys()]
        ph = ",".join("?" * len(cols))
        assign = ",".join(f"{k}=excluded.{k}" for k in fields)
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                f"INSERT INTO risk_state ({','.join(cols)}) VALUES ({ph}) "
                f"ON CONFLICT(bucket) DO UPDATE SET {assign}",
                (bucket, *fields.values()),
            )
            await db.commit()

    # ── reconcile 감사 ────────────────────────────────────────────────────
    async def start_reconcile_run(self, scope: str) -> int:
        async with self._connect() as db:
            await self._prep(db)
            cur = await db.execute("INSERT INTO reconcile_runs (scope) VALUES (?)", (scope,))
            await db.commit()
            return cur.lastrowid

    async def finish_reconcile_run(
        self, run_id: int, *, found: int, resolved: int, unresolved: int, detail: dict | None = None
    ) -> None:
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                "UPDATE reconcile_runs SET finished_at=?, diffs_found=?, diffs_resolved=?, "
                "unresolved=?, detail_json=? WHERE id=?",
                (utc_now_iso(), found, resolved, unresolved, json.dumps(detail or {}), run_id),
            )
            await db.commit()

    # ── 리스크: 한도 / halt / 감사 / 이벤트 ────────────────────────────────
    async def get_risk_limits(self, scope_ref: str) -> dict[str, float]:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT limit_type, value FROM risk_limits WHERE scope_ref=? AND enabled=1",
                (scope_ref,),
            ) as cur:
                rows = await cur.fetchall()
        return {r["limit_type"]: r["value"] for r in rows}

    async def get_halt_state(self, scope: str) -> str:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT state FROM trading_halt WHERE scope=?", (scope,)
            ) as cur:
                row = await cur.fetchone()
        return row["state"] if row else "active"

    async def set_halt_state(self, scope: str, state: str, reason: str | None = None) -> None:
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                "INSERT INTO trading_halt (scope, state, reason, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(scope) DO UPDATE SET state=excluded.state, reason=excluded.reason, "
                "updated_at=excluded.updated_at",
                (scope, state, reason, utc_now_iso()),
            )
            await db.commit()

    async def insert_audit(
        self, *, actor: str, action: str, target: str | None = None, detail: dict | None = None
    ) -> None:
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                "INSERT INTO audit_log (actor, action, target, detail_json) VALUES (?,?,?,?)",
                (actor, action, target, json.dumps(detail or {})),
            )
            await db.commit()

    async def insert_risk_event(
        self,
        *,
        event_type: str,
        severity: str,
        scope: str | None = None,
        scope_ref: str | None = None,
        message: str | None = None,
        detail: dict | None = None,
    ) -> None:
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                "INSERT INTO risk_events "
                "(event_type, severity, scope, scope_ref, message, detail_json) "
                "VALUES (?,?,?,?,?,?)",
                (event_type, severity, scope, scope_ref, message, json.dumps(detail or {})),
            )
            await db.commit()

    async def list_risk_events(self, *, limit: int = 50, offset: int = 0) -> list[dict]:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT * FROM risk_events ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── 메트릭 ───────────────────────────────────────────────────────────
    async def incr_metric(self, name: str, by: int = 1) -> None:
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                "INSERT INTO metrics_counter (name, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "value=value+excluded.value, updated_at=excluded.updated_at",
                (name, by, utc_now_iso()),
            )
            await db.commit()

    async def get_metrics(self) -> dict[str, int]:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute("SELECT name, value FROM metrics_counter") as cur:
                rows = await cur.fetchall()
        return {r["name"]: r["value"] for r in rows}

    # ── 화이트리스트 ──────────────────────────────────────────────────────
    async def set_whitelisted(self, market: str, symbol: str, whitelisted: bool) -> None:
        async with self._connect() as db:
            await self._prep(db)
            await db.execute(
                "UPDATE instruments SET whitelisted=? WHERE market=? AND symbol=?",
                (1 if whitelisted else 0, market, symbol),
            )
            await db.commit()

    async def is_whitelisted(self, market: str, symbol: str) -> bool:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT whitelisted FROM instruments WHERE market=? AND symbol=?", (market, symbol)
            ) as cur:
                row = await cur.fetchone()
        return bool(row and row["whitelisted"])

    async def list_whitelist(self, market: str) -> list[dict]:
        async with self._connect() as db:
            await self._prep(db)
            async with db.execute(
                "SELECT * FROM instruments WHERE market=? AND whitelisted=1 ORDER BY symbol",
                (market,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]
