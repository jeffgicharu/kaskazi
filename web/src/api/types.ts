// The shapes the API sends. They mirror kaskazi/api/customer.py and kaskazi/assistant/turn.py.

export type Persona = { key: "teacher" | "trader" | "student"; label: string; summary: string; first_name: string };

export type Session =
  | { signed_in: false }
  | {
      signed_in: true;
      persona: Persona["key"];
      label: string;
      first_name: string;
      conversation_id: string;
      questions_left_today: number | null;
      ai_available: boolean;
    };

export type Segment = { text: string; cites: number[] };

export type Source = {
  n: number;
  chunk_id: string;
  document_id: string;
  title: string;
  section: string;
  quote: string;
};

export type ConfirmCard = {
  type: "confirm";
  action_id: string;
  token?: string;
  tool: string;
  title: string;
  rows: [string, string][];
  note: string | null;
  needs_code: boolean;
  expires_at: string;
  status?: "pending" | "confirmed" | "done" | "failed" | "cancelled" | "expired";
};

export type Transaction = { at: string; description: string; amount: number; balance_after: number };

export type Card =
  | ConfirmCard
  | { type: "accounts"; accounts: { product: string; number: string; balance: number; currency: string }[] }
  | { type: "statement"; product: string; number: string; transactions: Transaction[] }
  | {
      type: "quote";
      amount: number;
      term_months: number;
      interest_rate: number;
      monthly_instalment: number;
      arrangement_fee: number;
      credit_life_insurance: number;
      amount_received: number;
      total_repaid: number;
      total_cost_of_credit: number;
    }
  | {
      type: "eligibility";
      eligible: boolean;
      reasons: string[];
      affordability_limit: number;
      room_for_new_instalment: number;
      largest_loan_by_term: Record<string, number>;
    };

export type AssistantDisplay = {
  text: string;
  segments: Segment[];
  sources: Source[];
  cards: Card[];
  notice: string | null;
  offer_handover: boolean;
  handover: boolean;
};

export type ThreadMessage =
  | { id: number; author: "customer"; display: { text: string }; at: string; rating: null }
  | { id: number; author: "assistant"; display: AssistantDisplay; at: string; rating: -1 | 1 | null }
  | { id: number; author: "agent"; display: { text: string; from: string }; at: string; rating: null }
  | {
      id: number;
      author: "bank";
      display: { type: "bank_notice" | "quick_action"; text: string; ok?: boolean; cards?: Card[] };
      at: string;
      rating: null;
    }
  | { id: number; author: "sms"; display: { text: string; from: string }; at: string; rating: null };

export type Conversation = { id: string; status: "open" | "with_agent" | "closed"; messages: ThreadMessage[] };

export type TurnResult = {
  outcome: string;
  turn_id?: string;
  customer_message_id: number | null;
  message_id: number | null;
  reply: AssistantDisplay | null;
  latency_ms?: number;
};

export type Overview = {
  accounts: { product: string; number: string; balance: number; currency: string }[];
  account_refs: string[];
  cards: { ref: string; kind: string; last4: string; status: string; expires: string }[];
};

export type SourceDocument = {
  id: string;
  title: string;
  owner: string;
  version: string;
  effective: string;
  body: string;
  highlight: string | null;
};
