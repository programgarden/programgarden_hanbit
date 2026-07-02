/**
 * Order-ticket store — a tiny global channel for opening the shared order
 * confirmation modal from anywhere (Positions "청산", Orders "정정/취소/새 주문").
 *
 * Screens describe the intended action; `OrderTicketModal` (mounted once in
 * Providers) renders the quote→commit / amend / cancel flow for it.
 */

import { create } from "zustand";
import type {
  IntentKind,
  Market,
  OrderTypeT,
  Side,
  TradeMode,
} from "@/lib/api/types";

export type TicketKind = "new" | "liquidate" | "amend" | "cancel";

export interface OrderTicket {
  kind: TicketKind;
  title: string;
  market: Market;
  mode: TradeMode;
  symbol: string;
  side: Side;
  orderType: OrderTypeT;
  qty: number;
  price: number | null;
  intent: IntentKind;
  /** Contract multiplier, for an estimated-notional display (FUT = 50). */
  multiplier?: number;
  /** For amend/cancel — the existing order id. */
  orderId?: number;
  exchange?: string;
}

interface TicketStore {
  ticket: OrderTicket | null;
  open: (t: OrderTicket) => void;
  close: () => void;
}

export const useOrderTicket = create<TicketStore>((set) => ({
  ticket: null,
  open: (ticket) => set({ ticket }),
  close: () => set({ ticket: null }),
}));
