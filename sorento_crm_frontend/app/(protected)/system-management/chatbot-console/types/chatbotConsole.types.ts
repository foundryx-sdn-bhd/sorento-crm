/** The in-app chatbot console (Slice D final, chatbot growth r1): a WhatsApp-style
 * dry-run page under System Management. Every turn is `is_test=True`, `ingress=console`
 * (D14) - nothing here reaches a real customer.
 */
import type { TurnAttachment } from '@/components/chatbot/TurnAttachments';

export interface ConsoleMediaInput {
  kind: 'image' | 'audio';
  filename: string;
  mime: string;
  content_base64: string;
}

export interface ConsoleTurnRequest {
  contact_respond_id: string;
  text: string;
  /** Membership matters: `null` = use the contact's stored session, `{}` = a Reset
   * ("this contact remembers nothing"). */
  session_vars: Record<string, unknown> | null;
  prompt_version_id: string | null;
  run_id: string;
  media?: ConsoleMediaInput | null;
  /** The bot bubble this message quotes (WhatsApp reply-to). Omitted when nothing is quoted. */
  reply_to?: { id: string; text: string; source: 'n8n' | 'contact' };
}

export interface ConsoleTraceSummary {
  tool: string | null;
  args_short: Record<string, unknown> | null;
  crossdomain_rungs: string[];
  reveals_dropped: string[];
  /** An escalation turn's draw in one line (team, brand, source, assignee); null on
   * every other turn. Optional: an older backend does not send it. */
  routing_line?: string | null;
}

export interface ConsoleTurnResponse {
  turn_id: string | null;
  branch_kind: string | null;
  reply_text: string;
  quick_replies: string[];
  send_messages: string[];
  session_vars: Record<string, unknown> | null;
  trace_summary: ConsoleTraceSummary;
  /** Media (commit 2): all null on a plain text turn. */
  media_status: 'pending' | 'done' | 'failed' | null;
  media_id: string | null;
  /** What was read/heard, for the muted "Read from image: ..." / "Heard: ..." line. */
  media_text: string | null;
  media_error: string | null;
  /** Item 6: the parser prompt version this turn actually ran (the trace's own
   * `understood` fact); null when the turn never reached the parser. */
  prompt_version: number | null;
  /** The respond message id of the customer message this turn ran on. */
  message_id?: string | null;
  /** The turn's own raw `TurnResult.actions`, loosely typed like `send_messages`'
   * source (it is the engine's shape, not this endpoint's own) - carried through so
   * `extractTurnAttachments` can read a `send_attachments` action's own file list,
   * the same source Chat History's `ChatbotTurn.response.actions` already gives. */
  actions?: Record<string, unknown>[] | null;
}

export type ConsolePromptBase = 'full' | 'compact' | 'other';

export interface ConsolePromptVersion {
  id: string;
  version: number;
  label: string | null;
  chars: number;
  /** Item 6: the lineage of the body. The console defaults to the newest `full`. */
  base: ConsolePromptBase;
}

export interface ConsoleMediaStatusResponse {
  status: 'pending' | 'done' | 'failed';
  text: string | null;
  error: string | null;
}

/** One bubble in the console thread. A single turn can produce several bot bubbles
 * (`reply_text` plus each `send_messages` entry), so the thread is a flat list of
 * bubbles, not one entry per turn. */
export interface ChatbotConsoleMessage {
  id: string;
  role: 'user' | 'bot';
  text: string;
  /** User bubble only: the respond message id the backend ran this message as, so a later
   * message can quote it as the customer's own. */
  messageId?: string | null;
  /** User bubble only: the text of the bot bubble it quoted. */
  quotedText?: string;
  /** The turn this bubble came from - null for the two opening greeting lines and for
   * the customer's own typed messages. */
  turnId?: string | null;
  branchKind?: string | null;
  /** Item 6: the parser prompt version the turn ran - the small "v18" pill on a bot
   * bubble. Absent/null = no pill. */
  promptVersion?: number | null;
  /** Only the FIRST bot bubble of an escalation turn carries this: where it went. */
  routingLine?: string | null;
  /** Only the LAST bot bubble of a turn carries these - chips send that text on click. */
  quickReplies?: string[];
  /** Media status/preview, wired in commit 2. */
  mediaKind?: 'image' | 'audio' | null;
  mediaStatus?: 'pending' | 'done' | 'failed' | null;
  mediaCaption?: string | null;
  /** Local object URL for the user bubble's own thumbnail/player preview. */
  mediaUrl?: string | null;
  mediaError?: string | null;
  mediaId?: string | null;
  /** Carried on a FAILED status bubble so its Retry chip can resend the exact same
   * attachment without asking the user to re-attach it. */
  mediaRetry?: ConsoleMediaInput | null;
  /** Only the LAST bot bubble of a turn carries these - the files a `send_attachments`
   * action on this turn would send, via `extractTurnAttachments`. */
  attachments?: TurnAttachment[];
}

export const CONSOLE_GREETING_MESSAGES: readonly string[] = [
  'Sorento chat console (dry run, nothing reaches WhatsApp).',
  'Type a message to test the bot.',
];
