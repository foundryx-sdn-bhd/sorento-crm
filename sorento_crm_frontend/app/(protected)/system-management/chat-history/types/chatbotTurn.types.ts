/**
 * The turn trace shown under each incoming message in the Chat History drawer.
 *
 * Mirrors `chatbot.turns` (backend `app/models/chatbot_turn.py`). Field names follow the
 * wire, not frontend taste: a rename here is a contract break with the endpoint and with
 * the 1,535 captured fixtures the engine is graded against.
 */

/** The eight stages a turn can record, in the order the timeline renders them. */
export const TURN_STAGES = [
  'received',
  'understood',
  'access',
  'routed',
  'looked_up',
  'replied',
  'remembered',
  'sent',
] as const;
export type TurnStage = (typeof TURN_STAGES)[number];

/**
 * Where a turn stopped. A superset of `TurnStage`: four failure points sit outside the
 * timeline - `intake` is before the first trace record exists, `queued` is the per-contact
 * wait, `casual_llm` is the small-talk clarifier, and `delegated` is a turn an n8n lane
 * took over and never finished (the server's sweep fails it after the TTL).
 */
export type TurnFailureStage = TurnStage | 'intake' | 'queued' | 'casual_llm' | 'delegated';

export type TurnStatus = 'queued' | 'processing' | 'delegated' | 'done' | 'failed';

export type TraceStatus = 'ok' | 'failed' | 'skipped';

/** The 13 lanes the router decides between, plus `media_denied` (chatbot media-into-turn
 *  S2): a photo or voice note the intake step refused (quota, burst, disabled number,
 *  clip too long) before a parser ever ran. */
export type BranchKind =
  | 'access_denied'
  | 'escalate_offer'
  | 'out_of_scope'
  | 'ideate'
  | 'offer_hold'
  | 'escalation_declined'
  | 'check_promotion'
  | 'low_signal'
  | 'clarify_menu'
  | 'not_supported'
  | 'stock_denied'
  | 'demand_qty'
  | 'business_query'
  | 'media_denied';

/**
 * One stage of one turn.
 *
 * `summary` and `why` are SENTENCES the engine composes from structured state - never from
 * the customer's text and never by an LLM (D11). The screen renders them as written; it
 * does not build prose of its own out of `facts`.
 */
/**
 * Everything in `trace` that is NOT a stage the turn ran.
 *
 * `note` is something that happened TO the turn - today, an operator asking for a retry;
 * it carries the stage the turn stopped at so the endpoint can file it with the failure.
 * The other six are sub-events a stage produced (`TurnTrace.add`, backend
 * `app/services/chatbot/trace.py`): one MCP tool call, one cross-domain rung probe, the
 * field reveals, and so on. They ride the SAME array as the stage records and carry no
 * `stage` at all, which is why the split below is on `kind` being absent and not on
 * `kind !== 'note'` - that read crashed the panel the first time a `tool` event landed.
 */
export type TurnTraceKind =
  | 'note'
  | 'tool'
  | 'crossdomain'
  | 'reveals'
  | 'decay'
  | 'focus'
  | 'open_question';

/**
 * One entry of `chatbot.turns.trace` - a stage record, a note, or a sub-event.
 *
 * Every field past `kind` is optional because a sub-event has none of them. Narrow to a
 * stage record with `stageRecords()` / `isStageRecord()` before reading `stage`.
 */
export interface TurnTraceRecord {
  /** Absent on a stage record. Present, and one of seven values, on everything else. */
  kind?: TurnTraceKind;
  stage?: TurnStage;
  status?: TraceStatus;
  started_at?: string;
  ms?: number;
  summary?: string;
  why?: string;
  /** Small flat dict rendered as key/value rows under the sentences. */
  facts?: Record<string, unknown>;
  error?: string | null;
  /** Technical payload for the "Technical details" viewer. Byte-capped by the engine. */
  raw?: unknown;
  /** A sub-event spreads its own payload flat beside `kind`. */
  [extra: string]: unknown;
}

/** A record `TurnTrace.record` wrote: carries `stage`, never `kind`. The timeline rows. */
export interface TurnStageRecord extends TurnTraceRecord {
  kind?: undefined;
  stage: TurnStage;
  status: TraceStatus;
  started_at: string;
  ms: number;
  summary: string;
  why: string;
  facts: Record<string, unknown>;
  error: string | null;
}

/** The answer the turn returned. Null while the turn is still running, or when it failed. */
export interface TurnResponseBody {
  ctx?: Record<string, unknown> | null;
  item?: Record<string, unknown> | null;
  reply?: { text?: string | null; quick_replies?: unknown[] } | null;
  actions?: Record<string, unknown>[] | null;
}

// `TurnAttachment` moved to `@/components/chatbot/turnAttachments` (round 2) - the
// chatbot console shares the identical `actions[].kind === 'send_attachments'` shape
// off its own `ConsoleTurnResponse`, so the type lives with the shared extractor
// rather than duplicated per screen.

/**
 * What the media-intake step (chatbot media-into-turn, S2/S4) read out of an incoming
 * photo or voice note, joined onto the turn it produced. Null on a text turn - the
 * transcript and the turn panel both treat its absence as "not a media turn", never as
 * a loading or error state.
 */
export interface ChatbotTurnMedia {
  modality: 'image' | 'voice';
  mime_type: string | null;
  /** The stored `attachments` row (S4). Never rendered - the FE reads `url`. */
  attachment_id: string | null;
  /** Signed, short-lived CDN url (S4). Null while the bytes were never stored (denied). */
  url: string | null;
  /** The text handed to the parser: the entity raws joined (image) or the transcript
   *  verbatim (voice). Null on a denied/failed job. */
  transcript_or_rendered_text: string | null;
  entities: Array<{ raw: string; hint?: string | null; confident?: boolean | null }>;
  attributes: Array<{ kind: string; raw: string; entity_raw?: string | null }>;
  notes: string | null;
  truncated: boolean;
  /** e.g. `accepted`, `denied_gate`, `denied_quota`, `denied_burst`, `failed`. */
  decision: string;
}

export interface ChatbotTurn {
  id: string;
  contact_respond_id: string;
  /** The respond.io message id this turn answers. Null for a console-driven turn. */
  message_id: string | null;
  status: TurnStatus;
  /** Where it stopped. Null on a turn that is still running. */
  stage: TurnFailureStage | null;
  branch_kind: BranchKind | null;
  /** Manual retries from this screen. 1 on a turn nobody has retried. */
  attempt: number;
  is_test: boolean;
  // Optional, all four: the API always sends them, but nothing in the UI needs them to
  // be there. Requiring them would only force every caller that legitimately does not
  // have one - a test factory, a narrower projection, a response from before the column
  // existed - to invent a value, which is how a type stops describing reality.
  ingress?: 'webhook' | 'poller' | 'retry' | 'console';
  error?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at: string | null;
  /** Set while a requested retry is on its way; the row itself stays `failed`. */
  retry_requested_at?: string | null;
  trace: TurnTraceRecord[];
  response: TurnResponseBody | null;
  /** The photo or voice note this turn read (chatbot media-into-turn, S2/S4). Null on a
   *  text turn. */
  media?: ChatbotTurnMedia | null;
}

export interface ChatbotTurnListResponse {
  items: ChatbotTurn[];
  /** Opaque; absent when there is no further page. */
  next_cursor?: string | null;
  /**
   * Whether Retry is wired in this environment at all (it deliberately is not, locally).
   * Rides the list because the screen needs it at the same moment it needs the turns, and
   * because a button that always 409s is what teaches an operator to distrust a screen.
   */
  retry_available?: boolean;
  retry_unavailable_reason?: string | null;
}

export interface ChatbotTurnFilters {
  contact_respond_id?: string;
  from?: string;
  to?: string;
  status?: TurnStatus;
  /** Page size. The endpoint defaults to 50 and caps at 200. */
  limit?: number;
  /** The previous page's opaque `next_cursor`. */
  cursor?: string | null;
}

export interface FailedContactFilters {
  from?: string;
  to?: string;
}

/** One contact with at least one failed turn in the range (AC-255). */
export interface FailedContactRow {
  contact_respond_id: string;
  last_failed_stage: TurnFailureStage | null;
  last_failed_at: string | null;
  count: number;
}

export interface FailedContactListResponse {
  items: FailedContactRow[];
}

export interface RetryTurnResponse {
  turn_id: string;
  /** What the RE-INJECTED turn will carry. The retried row keeps its own attempt. */
  attempt: number;
}

/**
 * `trace_detail` (chatbot growth r1, Slice D). Composed by the backend from
 * `trace` records, including `kind`-tagged entries the `data` / `dialogue`
 * lanes write as their slices land (`tool`, `crossdomain`, `reveals`, `decay`,
 * `focus`) plus an `open_question` entry. A kind this build has not shipped
 * yet renders as an empty section - `null` for a singleton, `[]` for a list -
 * never an error.
 */
export interface TurnDetailStage {
  name: string;
  started_at: string | null;
  ms: number | null;
  status: TraceStatus;
  summary: string | null;
  error: string | null;
  /** Browser pass, chatbot media-into-turn: the SAME flattened facts `TurnPanel`'s
   * own inline StageRow already prints (modality/decision/entities/attributes/
   * notes/... for a media_intake stage) - absent or `{}` on a stage that carries
   * none, same optionality as `TurnTraceRecord.facts` above. */
  facts?: Record<string, unknown>;
}

export interface TurnDetailParse {
  raw: Record<string, unknown> | null;
  post_processed: Record<string, unknown> | null;
  prompt_version: number | string | null;
  model: string | null;
}

export interface TurnDetailDecay {
  slot: string | null;
  value: unknown;
  set_at_turn: number | null;
  age_turns: number | null;
  age_minutes: number | null;
  reason: string | null;
}

export interface TurnDetailOpenQuestion {
  before: unknown;
  answer: unknown;
  after: unknown;
  handler: string | null;
  outcome: string | null;
}

export interface TurnDetailFocus {
  slot: string | null;
  before: unknown;
  after: unknown;
  rule: string | null;
  source: string | null;
}

export interface TurnDetailTool {
  name: string | null;
  args: Record<string, unknown> | null;
  /** May carry `{truncated: true, bytes, head}` in place of the real envelope past 8 KB. */
  envelope: Record<string, unknown> | null;
  ms: number | null;
}

export interface TurnDetailCrossdomain {
  rung: number | null;
  tool: string | null;
  args: Record<string, unknown> | null;
  rows: number | null;
  rendered: boolean | null;
}

export interface TurnDetailReveals {
  restricted_fields_seen: string[];
  granted: string[];
  dropped: string[];
}

export interface TurnDetailSessionDiffEntry {
  key: string;
  change: 'gained' | 'lost';
}

export interface TurnDetailSession {
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  diff: TurnDetailSessionDiffEntry[];
}

/**
 * APPLY and Memory (chatbot turn re-architecture S1/S3, AC-1514, AC-1549).
 *
 * Both are OPTIONAL on `TurnDetail`: a turn recorded before S3 ships never carries
 * them, and the drawer's own `Section` renders that as the same "nothing recorded
 * yet" empty state every other kind here already uses - never an error.
 */
export interface TurnDetailApplyDiffEntry {
  slot: string;
  before: unknown;
  after: unknown;
  /** Which rule moved it, e.g. "exclusive narrows the named axis". */
  reason?: string | null;
}

/**
 * What the backend actually sends: `turn_runtime.focus_diff` returns a MAP keyed by the
 * focus slot (`{ customers: { before, after } }`), and only a slot that moved is in it.
 * The array form is the older shape some recorded turns still carry, so the drawer
 * accepts either and renders one list.
 */
export type TurnDetailApplyDiff =
  | TurnDetailApplyDiffEntry[]
  | Record<string, { before?: unknown; after?: unknown; reason?: string | null }>;

/**
 * What APPLY read the message as, before any rule acted on it: one of `answer`,
 * `refine`, `new_ask` or `carry`, with the single rule that decided it.
 */
export interface TurnDetailApplyDecision {
  kind: string;
  why: string;
}

export interface TurnDetailApply {
  /** The parser verdict APPLY read, as sent (contract 102 to 105's shape). */
  verdict: Record<string, unknown> | null;
  decision?: TurnDetailApplyDecision | null;
  state_diff: TurnDetailApplyDiff | null;
  /** One line per (domain, entity kind) the narrower touched this turn. */
  narrowing: string[];
  /** `reconciled: <from> -> <to>`, or null when nothing was rewritten. */
  reconciliation?: string | null;
  /**
   * The turn's plan. The backend sends the `apply` record's own object (domains,
   * fetch, denied, ask, lane); an older turn carries the one-line string.
   */
  plan: string | Record<string, unknown> | null;
  /** The rendered user block plus hint blocks sent to the parser, capped at 64 KB. */
  prompt_text: string | null;
}

/**
 * Memory, repaired to the backend's own `memory` trace event (chatbot memory lane A,
 * contract section 6) - the `recall_hit`/`reason`/`last_frame_summary`/`frame_count`
 * keys and the array-of-slots shape for `focus` are gone; the S3 recall re-parse they
 * described was deleted in the same lane.
 */
export interface TurnDetailMemoryLevel {
  own: string | null;
  effective: string;
}

export interface TurnDetailMemoryFocus {
  before: unknown;
  /** The current subject, rendered as "Current subject" in the drawer. */
  after: unknown;
  writer: string | null;
}

export interface TurnDetailMemoryProfile {
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  writer: string | null;
}

export interface TurnDetailMemoryEpisodeWritten {
  id: string;
  turn_count: number;
  close_reason: string;
  summary: string;
  /** The closed conversation's topic (its first planned domain); absent on older turns. */
  domain?: string | null;
  /** `topic_reset` (the parser's) or `domain_switch` (a new domain); absent on older turns. */
  trigger?: string | null;
}

export interface TurnDetailMemoryEpisodes {
  /** Frame ids fed to the parser this turn. */
  read: string[];
  written: TurnDetailMemoryEpisodeWritten | null;
  writer: string;
}

export interface TurnDetailFactSaved {
  key: string;
  source: string;
}

export interface TurnDetailMemory {
  /** `null` on a turn recorded before this key existed on the `memory` trace event -
   *  the raw entry never had it, so the backend's `_memory()` reads `None`. */
  level: TurnDetailMemoryLevel | null;
  focus: TurnDetailMemoryFocus;
  profile: TurnDetailMemoryProfile;
  /** `null` for the same pre-lane reason as `level` above. */
  episodes: TurnDetailMemoryEpisodes | null;
  facts_saved: TurnDetailFactSaved[] | null;
  open_question?: unknown;
  written?: boolean;
  dry_run?: boolean;
}

/**
 * "Context sent to the AI" (chatbot memory lane A, contract section 6's `context`
 * trace event).
 */
export interface TurnDetailContextLayer {
  /** Machine key from the backend - "L3" / "L4" / "L5" / "current_subject" / etc. */
  layer: string;
  est_tokens: number;
  cap: number;
  /** Absent/false = nothing dropped from this layer; a list names what was cut. */
  dropped?: boolean | string[] | null;
}

export interface TurnDetailContext {
  level: string;
  layers: TurnDetailContextLayer[];
  total_est_tokens: number;
  cap: number;
}

/**
 * The per-contact ordering ticket (chatbot memory lane A, contract section 6's `order`
 * trace event) - read-only, nothing to set.
 */
export interface TurnDetailOrderNeighbor {
  turn_id: string;
  created_at: string;
  message: string;
  /** This neighbour's own ticket (AC-MEM015) - null when it never queued. */
  ticket: number | null;
}

export interface TurnDetailOrder {
  ticket: number;
  /** `null` on a queue TIMEOUT - the wait never finished, so nothing was timed
   *  (`app/services/chatbot/trace_detail.py::_order`). */
  wait_ms: number | null;
  previous: TurnDetailOrderNeighbor | null;
  next: TurnDetailOrderNeighbor | null;
}

/**
 * One customer-scope decision on the turn (CHATBOT-SELFREF-SCOPE R4, backend
 * `app/services/chatbot/trace_detail.py::_customer_scope`): the engine's gate, screen
 * and scoped-to-links pass, the fetch clamp and refusal, the tier-probe refusal, and the
 * scoped re-route of a sales analysis. A refusal carries `refused`; every other decision
 * carries `decision`. `ids` are the linked customer ids the turn ran on, `dropped` the
 * ids outside the links it dropped, `typed` the words typed this message.
 */
export interface TurnDetailCustomerScope {
  at: string | null;
  decision?: string;
  refused?: string;
  reason?: string;
  ids?: string[];
  dropped?: string[];
  kept?: string[];
  typed?: string[];
  self_reference?: boolean;
  tool?: string;
  offer_passed?: boolean;
}

export interface TurnDetail {
  stages: TurnDetailStage[];
  parse: TurnDetailParse | null;
  apply?: TurnDetailApply | null;
  memory?: TurnDetailMemory | null;
  context?: TurnDetailContext | null;
  order?: TurnDetailOrder | null;
  decay: TurnDetailDecay[];
  open_question: TurnDetailOpenQuestion | null;
  focus: TurnDetailFocus[];
  tool: TurnDetailTool | null;
  crossdomain: TurnDetailCrossdomain[];
  reveals: TurnDetailReveals;
  /** Absent on a trace composed before the lane shipped; renders as an empty section. */
  customer_scope?: TurnDetailCustomerScope[];
  session: TurnDetailSession;
}

export interface ChatbotTurnDetail extends ChatbotTurn {
  trace_detail: TurnDetail;
}
