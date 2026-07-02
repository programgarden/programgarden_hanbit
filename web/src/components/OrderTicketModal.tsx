"use client";

/**
 * Shared order-confirmation modal (wireframe screen 7).
 *
 * Renders the action described by the order-ticket store:
 *   • new / liquidate → quote (risk-gate dry run) → commit
 *   • amend           → edit qty/price → amend (re-validated by the gate)
 *   • cancel          → confirm → cancel
 *
 * Faithful to the M3 backend: only FUT (paper) order paths are open, so a LIVE
 * (KR/OVS) ticket surfaces the 403 LIVE_DISABLED as an "M4 예정" notice with the
 * commit button disabled. Dim backdrop does not close (ESC / 취소 only).
 */

import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api/client";
import {
  useAmendOrder,
  useCancelOrder,
  useCommitOrder,
  useQuoteOrder,
  useWhitelist,
} from "@/lib/query/hooks";
import { useOrderTicket, type OrderTicket } from "@/lib/order-ticket/store";
import type { CommitResponse, OrderIntentBody, QuoteResponse } from "@/lib/api/types";
import { fmtMoney } from "@/lib/format";
import { ModeBadge } from "@/components/ModeBadge";

const MARKET_CCY: Record<string, string> = {
  korea_stock: "KRW",
  overseas_stock: "USD",
  overseas_futureoption: "HKD",
};

export function OrderTicketModal() {
  const ticket = useOrderTicket((s) => s.ticket);
  const close = useOrderTicket((s) => s.close);
  if (!ticket) return null;
  const key = `${ticket.kind}:${ticket.symbol}:${ticket.orderId ?? ""}:${ticket.side}:${ticket.qty}`;
  return <TicketDialog key={key} ticket={ticket} onClose={close} />;
}

function TicketDialog({ ticket, onClose }: { ticket: OrderTicket; onClose: () => void }) {
  const isLive = ticket.mode === "live";
  const ccy = MARKET_CCY[ticket.market] ?? null;

  // ESC closes; backdrop click is intentionally inert.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div
        role="dialog"
        aria-modal
        className={`w-full max-w-md rounded-lg border bg-surface p-4 shadow-xl ${
          isLive ? "border-live/60" : "border-paper/60"
        }`}
      >
        <header className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <ModeBadge mode={ticket.mode} /> {ticket.title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-muted hover:text-foreground"
            aria-label="닫기"
          >
            ✕
          </button>
        </header>

        {ticket.kind === "cancel" ? (
          <CancelBody ticket={ticket} ccy={ccy} onClose={onClose} />
        ) : ticket.kind === "amend" ? (
          <AmendBodyForm ticket={ticket} ccy={ccy} onClose={onClose} />
        ) : (
          <QuoteCommitBody ticket={ticket} ccy={ccy} isLive={isLive} onClose={onClose} />
        )}
      </div>
    </div>
  );
}

// ── new / liquidate: quote → commit ──────────────────────────────────────────
function QuoteCommitBody({
  ticket,
  ccy,
  isLive,
  onClose,
}: {
  ticket: OrderTicket;
  ccy: string | null;
  isLive: boolean;
  onClose: () => void;
}) {
  const quoteM = useQuoteOrder();
  const commitM = useCommitOrder();
  const [quote, setQuote] = useState<QuoteResponse | null>(null);
  const [quoteErr, setQuoteErr] = useState<ApiError | null>(null);
  const [result, setResult] = useState<CommitResponse | null>(null);
  const [resultErr, setResultErr] = useState<ApiError | null>(null);
  const [ack, setAck] = useState(false);
  const ran = useRef(false);

  // A brand-new order is editable (the user picks symbol/side/qty/price and
  // must request a quote); a liquidation is a fixed reduce-only auto-quote.
  const editable = ticket.kind === "new";
  const [symbol, setSymbol] = useState(ticket.symbol);
  const [side, setSide] = useState(ticket.side);
  const [qty, setQty] = useState(ticket.qty);
  const [price, setPrice] = useState(ticket.price ?? 0);

  // HKEX 화이트리스트 사전 검증: 새 주문(FUT)만 해당. 서버 게이트가 FUT_NOT_HKEX 로
  // 최종 강제하지만, 폼에서 미리 막아 잘못된 심볼의 서버 왕복을 없앤다.
  const needsWhitelist = editable && ticket.market === "overseas_futureoption";
  const wl = useWhitelist("overseas_futureoption", needsWhitelist);
  const wlSymbols = wl.data?.symbols ?? [];
  const wlReady = needsWhitelist && wlSymbols.length > 0;
  const wlEntry = wlSymbols.find((s) => s.symbol === symbol);
  // 선택 심볼의 계약 승수(있으면 명목 추정에 사용; 없으면 티켓 기본값).
  const effMultiplier = wlEntry?.multiplier ?? ticket.multiplier ?? 1;

  const body: OrderIntentBody = {
    market: ticket.market,
    symbol: editable ? symbol : ticket.symbol,
    side: editable ? side : ticket.side,
    order_type: ticket.orderType,
    qty: editable ? qty : ticket.qty,
    price: editable ? price : ticket.price,
    exchange: ticket.exchange,
    intent: ticket.intent,
  };
  const estNotional =
    (editable ? qty : ticket.qty) *
    (editable ? price : ticket.price ?? 0) *
    (editable ? effMultiplier : ticket.multiplier ?? 1);

  // 폼 사전 검증: 빈 심볼·수량≤0·지정가 가격≤0·화이트리스트 외 심볼이면 견적 차단.
  // wlReady 가 아니면(로딩/조회불가) 심볼 검증은 보류 — 서버 게이트가 최종 검증.
  const symbolValid = !needsWhitelist || !wlReady || wlSymbols.some((s) => s.symbol === symbol);
  const inputValid =
    symbol.trim() !== "" && qty > 0 && (ticket.orderType === "market" || price > 0);
  const formValid = inputValid && symbolValid;
  const formError = !inputValid
    ? symbol.trim() === ""
      ? "심볼을 입력하세요"
      : qty <= 0
        ? "수량은 1 이상이어야 합니다"
        : "지정가 가격을 입력하세요"
    : !symbolValid
      ? "HKEX 화이트리스트에 없는 심볼입니다"
      : null;

  function runQuote() {
    setQuote(null);
    setQuoteErr(null);
    setResult(null);
    setResultErr(null);
    quoteM.mutate(body, {
      onSuccess: setQuote,
      onError: (e) => setQuoteErr(e as ApiError),
    });
  }

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    // Liquidation auto-quotes on open. We call mutate directly (not runQuote)
    // so there's no synchronous setState in the effect body — the result lands
    // via the async onSuccess/onError callbacks.
    if (!editable) {
      quoteM.mutate(body, {
        onSuccess: setQuote,
        onError: (e) => setQuoteErr(e as ApiError),
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const liveDisabled = quoteErr?.code === "LIVE_DISABLED" || isLive;
  const decisionOk = quote?.decision.result !== "reject" && !!quote;
  const canCommit = decisionOk && !liveDisabled && (!isLive || ack) && !result;

  function onCommit() {
    commitM.mutate(body, {
      onSuccess: setResult,
      onError: (e) => setResultErr(e as ApiError),
    });
  }

  return (
    <div className="space-y-3 text-sm">
      {editable ? (
        <div className="grid grid-cols-2 gap-2 rounded border border-border bg-surface-2 p-2">
          <label className="col-span-2 block text-xs">
            <span className="text-muted">심볼 (HKEX 화이트리스트)</span>
            {needsWhitelist && wlReady ? (
              <select
                value={symbol}
                onChange={(e) => {
                  setSymbol(e.target.value);
                  setQuote(null);
                }}
                className="mt-1 w-full rounded border border-border bg-surface px-2 py-1 text-foreground"
              >
                {/* 티켓 기본 심볼이 화이트리스트에 없을 수도 있어(데모 시드) 보강 노출. */}
                {!wlSymbols.some((s) => s.symbol === symbol) && (
                  <option value={symbol}>{symbol} (목록 외)</option>
                )}
                {wlSymbols.map((s) => (
                  <option key={s.symbol} value={s.symbol}>
                    {s.symbol}
                    {s.name ? ` · ${s.name}` : ""}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={symbol}
                onChange={(e) => {
                  setSymbol(e.target.value);
                  setQuote(null);
                }}
                className="num mt-1 w-full rounded border border-border bg-surface px-2 py-1 text-foreground"
              />
            )}
            {needsWhitelist && (
              <span className="mt-0.5 block text-[10px] text-muted">
                {wl.isLoading
                  ? "화이트리스트 불러오는 중…"
                  : wlReady
                    ? `서버 HKEX 화이트리스트 ${wlSymbols.length}종목으로 사전 검증`
                    : "화이트리스트 조회 불가 — 서버 게이트가 최종 검증"}
              </span>
            )}
          </label>
          <label className="block text-xs">
            <span className="text-muted">side</span>
            <select
              value={side}
              onChange={(e) => {
                setSide(e.target.value as typeof side);
                setQuote(null);
              }}
              className="mt-1 w-full rounded border border-border bg-surface px-2 py-1 text-foreground"
            >
              <option value="buy">매수 BUY</option>
              <option value="sell">매도 SELL</option>
            </select>
          </label>
          <label className="block text-xs">
            <span className="text-muted">수량</span>
            <input
              type="number"
              value={qty}
              onChange={(e) => {
                setQty(Number(e.target.value));
                setQuote(null);
              }}
              className="num mt-1 w-full rounded border border-border bg-surface px-2 py-1 text-foreground"
            />
          </label>
          <label className="col-span-2 block text-xs">
            <span className="text-muted">가격 ({ccy ?? ""})</span>
            <input
              type="number"
              value={price}
              onChange={(e) => {
                setPrice(Number(e.target.value));
                setQuote(null);
              }}
              className="num mt-1 w-full rounded border border-border bg-surface px-2 py-1 text-foreground"
            />
          </label>
          <div className="col-span-2 text-right text-xs text-muted">
            명목(개략) {fmtMoney(estNotional, ccy)}
          </div>
          {formError && (
            <div className="col-span-2 text-right text-[10px] text-down">⚠ {formError}</div>
          )}
        </div>
      ) : (
        <Summary ticket={ticket} ccy={ccy} estNotional={estNotional} />
      )}

      {editable && !result && (
        <button
          type="button"
          disabled={quoteM.isPending || !formValid}
          onClick={runQuote}
          className="w-full rounded border border-accent/60 bg-accent/10 px-3 py-1 text-xs font-semibold text-accent disabled:opacity-40"
        >
          {quoteM.isPending ? "견적 중…" : "견적(quote) 요청"}
        </button>
      )}

      {/* risk-gate preview */}
      {quoteM.isPending && <p className="text-muted">리스크 게이트 견적 중…</p>}
      {liveDisabled && (
        <div className="rounded border border-paper/40 bg-paper/5 p-2 text-xs text-paper">
          ⏳ LIVE(국내·해외주식) 주문 경로는 <b>M4 예정</b> — 현재 비활성(서버 403 LIVE_DISABLED).
          모의(해외선물)만 주문 가능합니다.
        </div>
      )}
      {quote && !liveDisabled && <DecisionPanel quote={quote} />}
      {quoteErr && !liveDisabled && (
        <p className="text-xs text-down">
          견적 실패 [{quoteErr.code}] {quoteErr.message}
        </p>
      )}

      {/* LIVE 2-step re-confirm (moot while LIVE is disabled, kept for M4) */}
      {isLive && !liveDisabled && (
        <label className="flex items-center gap-2 text-xs text-muted">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
          금액·수량·side 를 재확인했고 실거래(LIVE) 체결에 동의합니다
        </label>
      )}

      {/* commit result */}
      {result && <ResultPanel result={result} />}
      {resultErr && <CommitErrorPanel err={resultErr} />}

      <footer className="flex justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onClose}
          className="rounded border border-border px-3 py-1 text-xs text-muted hover:text-foreground"
        >
          {result ? "닫기" : "취소"}
        </button>
        {!result && (
          <button
            type="button"
            disabled={!canCommit || commitM.isPending}
            onClick={onCommit}
            className="rounded border border-paper/60 bg-paper/10 px-3 py-1 text-xs font-semibold text-paper disabled:opacity-40"
          >
            {commitM.isPending ? "전송 중…" : "확정(commit)"}
          </button>
        )}
      </footer>
    </div>
  );
}

// ── amend ────────────────────────────────────────────────────────────────────
function AmendBodyForm({
  ticket,
  ccy,
  onClose,
}: {
  ticket: OrderTicket;
  ccy: string | null;
  onClose: () => void;
}) {
  const amendM = useAmendOrder();
  const [qty, setQty] = useState(ticket.qty);
  const [price, setPrice] = useState(ticket.price ?? 0);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);

  function onSubmit() {
    amendM.mutate(
      { orderId: ticket.orderId!, body: { qty, price } },
      { onSuccess: () => setDone(true), onError: (e) => setErr(e as ApiError) },
    );
  }

  return (
    <div className="space-y-3 text-sm">
      <Summary ticket={ticket} ccy={ccy} />
      <div className="grid grid-cols-2 gap-2">
        <NumberField label="수량 qty" value={qty} onChange={setQty} />
        <NumberField label={`가격 price (${ccy ?? ""})`} value={price} onChange={setPrice} />
      </div>
      <p className="text-xs text-muted">
        ⓘ 정정은 서버에서 리스크 게이트로 재검증됩니다(per_order_cap·INV-7).
      </p>
      {done && <p className="text-xs text-up">✓ 정정 요청 전송됨</p>}
      {err && (
        <p className="text-xs text-down">
          정정 실패 [{err.code}] {err.message}
        </p>
      )}
      <footer className="flex justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onClose}
          className="rounded border border-border px-3 py-1 text-xs text-muted hover:text-foreground"
        >
          {done ? "닫기" : "취소"}
        </button>
        {!done && (
          <button
            type="button"
            disabled={amendM.isPending}
            onClick={onSubmit}
            className="rounded border border-accent/60 bg-accent/10 px-3 py-1 text-xs font-semibold text-accent disabled:opacity-40"
          >
            {amendM.isPending ? "전송 중…" : "정정 확정"}
          </button>
        )}
      </footer>
    </div>
  );
}

// ── cancel ───────────────────────────────────────────────────────────────────
function CancelBody({
  ticket,
  ccy,
  onClose,
}: {
  ticket: OrderTicket;
  ccy: string | null;
  onClose: () => void;
}) {
  const cancelM = useCancelOrder();
  const [done, setDone] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);

  return (
    <div className="space-y-3 text-sm">
      <Summary ticket={ticket} ccy={ccy} />
      <p className="text-xs text-muted">이 주문을 취소합니다. 취소는 멱등이며 노출을 줄입니다.</p>
      {done && <p className="text-xs text-up">✓ 취소 요청 전송됨</p>}
      {err && (
        <p className="text-xs text-down">
          취소 실패 [{err.code}] {err.message}
        </p>
      )}
      <footer className="flex justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onClose}
          className="rounded border border-border px-3 py-1 text-xs text-muted hover:text-foreground"
        >
          {done ? "닫기" : "닫기(취소 안 함)"}
        </button>
        {!done && (
          <button
            type="button"
            disabled={cancelM.isPending}
            onClick={() =>
              cancelM.mutate(ticket.orderId!, {
                onSuccess: () => setDone(true),
                onError: (e) => setErr(e as ApiError),
              })
            }
            className="rounded border border-down/60 bg-down/10 px-3 py-1 text-xs font-semibold text-down disabled:opacity-40"
          >
            {cancelM.isPending ? "전송 중…" : "주문 취소"}
          </button>
        )}
      </footer>
    </div>
  );
}

// ── shared bits ──────────────────────────────────────────────────────────────
function Summary({
  ticket,
  ccy,
  estNotional,
}: {
  ticket: OrderTicket;
  ccy: string | null;
  estNotional?: number;
}) {
  return (
    <dl className="grid grid-cols-2 gap-x-3 gap-y-1 rounded border border-border bg-surface-2 p-2 text-xs">
      <Field k="시장" v={ticket.market} />
      <Field k="심볼" v={ticket.symbol} />
      <Field
        k="side"
        v={
          <span className={ticket.side === "buy" ? "text-up" : "text-down"}>
            {ticket.side === "buy" ? "매수 BUY" : "매도 SELL"}
          </span>
        }
      />
      <Field k="수량" v={ticket.qty} />
      <Field k="가격" v={ticket.price != null ? fmtMoney(ticket.price, ccy) : "시장가"} />
      {estNotional != null && (
        <Field k="명목(개략)" v={fmtMoney(estNotional, ccy)} />
      )}
    </dl>
  );
}

function DecisionPanel({ quote }: { quote: QuoteResponse }) {
  const r = quote.decision.result;
  const color = r === "reject" ? "text-down" : r === "warn" ? "text-paper" : "text-up";
  return (
    <div className="rounded border border-border bg-surface-2 p-2 text-xs">
      <div className="flex items-center justify-between">
        <span className="text-muted">리스크 게이트</span>
        <span className={`font-semibold ${color}`}>{r.toUpperCase()}</span>
      </div>
      {quote.decision.reasons.length > 0 && (
        <ul className="mt-1 list-inside list-disc text-muted">
          {quote.decision.reasons.map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      )}
      {quote.confirm_token && (
        <div className="mt-1 text-[10px] text-muted">
          confirm_token 발급됨(서버측 2단계 확인) · …{quote.confirm_token.slice(-8)}
        </div>
      )}
    </div>
  );
}

function ResultPanel({ result }: { result: CommitResponse }) {
  return (
    <div className="rounded border border-up/40 bg-up/5 p-2 text-xs">
      <div className="font-semibold text-up">✓ 주문 접수</div>
      <div className="mt-1 text-muted">
        상태 {result.order?.status ?? "—"} · OrdNo {result.ack?.broker_ord_no ?? "—"} · rsp_cd{" "}
        {result.ack?.rsp_cd ?? "—"}
        {result.idempotent ? " · (멱등 재요청)" : ""}
        {result.in_doubt ? " · ⚠ in_doubt" : ""}
      </div>
    </div>
  );
}

function CommitErrorPanel({ err }: { err: ApiError }) {
  const detail = err.detail as { decision?: { reasons?: string[] } } | undefined;
  return (
    <div className="rounded border border-down/40 bg-down/5 p-2 text-xs text-down">
      <div className="font-semibold">주문 거부 [{err.code}]</div>
      <div className="mt-1 text-muted">{err.message}</div>
      {detail?.decision?.reasons && (
        <div className="mt-1 text-muted">사유: {detail.decision.reasons.join(", ")}</div>
      )}
    </div>
  );
}

function Field({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-muted">{k}</dt>
      <dd className="num text-foreground">{v}</dd>
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
}) {
  return (
    <label className="block text-xs">
      <span className="text-muted">{label}</span>
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="num mt-1 w-full rounded border border-border bg-surface px-2 py-1 text-foreground"
      />
    </label>
  );
}
