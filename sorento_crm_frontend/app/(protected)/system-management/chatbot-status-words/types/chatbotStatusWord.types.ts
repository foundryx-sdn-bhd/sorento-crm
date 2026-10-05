/**
 * Chatbot Status Words (PLAN-prompt-dynamic-30sep D6). Per domain, a status value the
 * parser may emit and the customer words that set it. The parser prompt renders its
 * status bullets and value lists from these rows on the next turn, no publish needed.
 */
export interface ChatbotStatusWord {
  id: string;
  /** `chatbot_domains.name`, e.g. "order" or "sales". */
  domain: string;
  /** The value the parser emits, snake_case, e.g. "sales_report". */
  value: string;
  label: string;
  trigger_words: string[];
  sort_order: number;
  /** The parser prompt lists the row is in (owner answer 4, 2 Oct 2026). */
  prompt_lists: string[];
  updated_at: string;
}

export type ChatbotStatusWordInput = Omit<ChatbotStatusWord, 'id' | 'updated_at'>;
