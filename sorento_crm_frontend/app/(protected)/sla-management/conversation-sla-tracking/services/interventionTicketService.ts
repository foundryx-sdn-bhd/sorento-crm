/**
 * Conversation intervention tickets - feature service.
 *
 * Contract: documentation/plans/sla/conversation-intervention-tickets-acceptance-criteria.md
 * Plan:     documentation/plans/sla/PLAN-conversation-intervention-tickets.md
 *
 * ============================================================================
 * PHASE 2 (this file today): every function below calls the real backend
 * (S2.7). The worklist itself is NOT fetched here - a ticket is just a row on
 * the existing `/my-pending` response (`getMyPendingSLA` in
 * `conversationSLATrackingService.ts`) flagged `is_intervention_ticket: true`;
 * this file only covers what's specific to a ticket once it's opened (detail,
 * thread, send, resolve). The Phase 1 in-memory fixtures under `./__mocks__/`
 * have been deleted along with the merge logic in `MyPendingSLAWidget`.
 * ============================================================================
 *
 * ---------------------------------------------------------------------------
 * API CONTRACT (as implemented)
 * ---------------------------------------------------------------------------
 * All paths are under `/api/v1/sla-management/conversation-sla-tracking`.
 * Datetimes are naive UTC strings (backend convention) rendered through
 * `formatDateTimeInMalaysia` / `parseDateTimeAsUTC`.
 *
 * A. CREATE (n8n -> CRM, existing route, extended) - UAC AC-A1/A2/A4
 *    POST /integration
 *    Request (additive to today's `ConversationSLATrackingCreate`):
 *      {
 *        contact_phone_number: string,      // required, existing
 *        agent_code: string,                // required, existing
 *        team_set_code: string,             // required, existing
 *        assigned_to_id?: string,           // existing (round-robin / explicit)
 *        message_id?: string,               // existing (kept for back-compat)
 *        source_message_id: string,         // NEW: the triggering message id.
 *                                           //      Identity + idempotency key.
 *        source_message_text?: string       // NEW: enquiry snippet source text
 *      }
 *    Response 200 (additive; every field n8n reads today is preserved):
 *      {
 *        status: 'success',
 *        message: string,
 *        tracking_id: string,
 *        is_update: boolean,
 *        already_active: boolean,           // true on a same source_message_id retry
 *        in_working_hours: boolean,         // NEW (R4): n8n picks in-hours vs
 *                                           //      out-of-hours auto-reply copy
 *        initiated_at: string,              // real request time
 *        due_at: string | null,             // response deadline (clock start is
 *                                           //      normalized to the next working window)
 *        due_at_resolution: string | null,
 *        assigned_to: string | null,
 *        assigned_to_id: string | null
 *      }
 *    NOTE for the Phase 2 implementer: today's `/integration` route returns only
 *    `status/message/tracking_id/is_update/already_active`. The four clock/assignee
 *    fields AC-A2 requires plus `in_working_hours` are a delta to add there.
 *    Multi-open is the rule: a new `source_message_id` for a contact that already
 *    has an open ticket creates a SECOND open ticket (never merges).
 *
 * B. WORKLIST (existing `/my-pending`, extended) - UAC AC-B1
 *    GET /my-pending?limit=<n>   ->  { data: MyPendingSLAItem[] }
 *    Conversation-scope rows gain (see `InterventionTicketListItem`):
 *      is_intervention_ticket: true   // explicit from the backend, never re-derived
 *                                     //   here (same rule as `is_form_sla`)
 *      contact_name, contact_phone, enquiry_snippet, source_message_id,
 *      team_label, initiated_at, escalated_at
 *    Rows are NOT de-duplicated by contact: two open tickets for one contact are
 *    two rows, each with its own `due_at` / `due_at_resolution` / tier.
 *
 * C. TICKET DETAIL (drawer header + composer state) - UAC AC-C1
 *    GET /{tracking_id}/ticket   ->  InterventionTicketDetail
 *    Assignee-or-manager scoped. `window` + `chat_template` come from the same
 *    backend window/template service the shared composer uses, returned inline so
 *    the drawer opens in one round trip.
 *
 * D. THREAD (shared contact conversation) - UAC AC-C2
 *    GET /{tracking_id}/conversation?limit=&cursor=   (EXISTING route)
 *      -> ConversationThreadPage (local-first: items oldest to newest, `source`,
 *         cursor = message id of the oldest row, pages older)
 *    Siblings for the same contact return the SAME thread; only the header and
 *    clocks differ.
 *    NOT implemented here: this is the same route the SLA detail page's
 *    conversation panel reads, so the drawer uses the SAME service + hook
 *    (`getSlaTrackingConversation` / `useSlaTrackingConversation` in
 *    `conversationSLATrackingService` / `useConversationSLATracking`) under the
 *    ONE query key `['sla-tracking-conversation', id, limit, cursor]`. A second
 *    copy under its own key meant a send from the drawer never refreshed the
 *    detail page's panel, and dropped the cursor pagination the shared service
 *    already supports.
 *
 * E. SEND (ticket-stamped) - UAC AC-D1/D2/D3, AC-E1
 *    POST /{tracking_id}/ticket/send
 *      JSON body when there are no files:
 *        { text: string, reply_to_message_id?: string, reply_to_excerpt?: string }
 *      multipart/form-data when files are attached:
 *        text, reply_to_message_id?, reply_to_excerpt?, files[] (repeated)
 *    Response 200: SendTicketMessageResult
 *    Semantics the backend owns:
 *    - in-window  -> raw text; out-of-window -> the existing `*_chat` template
 *        smart-send (same `send_text_or_template` path as the unified composer)
 *    - attachments are uploaded through CRM storage and delivered to Respond by
 *        URL (CMYK JPEG converted to RGB)
 *    - the send is stamped with THIS tracking id: it sets
 *        is_responded / responded_at / responded_by / response_time on this ticket
 *        only; sibling tickets for the same contact are untouched
 *    - an `integration_log` outbox row is written on success AND failure
 *    - a multi-file send is NEVER all-or-nothing and never 502s on one file:
 *        the caption ships first, attachments go sequentially and stop at the
 *        first failure, and the call returns 200 with
 *        `attachments: { delivered: string[], failed: {filename, error} | null }`.
 *        The composer clears ONLY the delivered files and keeps the rest staged,
 *        so a retry cannot resend what the contact already has.
 *    R1 (resolved 2026-08-12): Respond.io supports text / attachment
 *    (image, video, audio, file) / quick_reply / whatsapp_template. There is NO
 *    sticker type and NO reply-to/context parameter, so:
 *    - the composer has no sticker affordance at all
 *    - "reply to" is emulated (#1317, restoring what shipped 12 Aug and was
 *        dropped 16 Aug): the quoted excerpt is sent as a ">"-prefixed line
 *        above the body (`buildQuotedReplyText` in lib/respondIoChatRender),
 *        which WhatsApp renders as quoted text and our chat list re-parses via
 *        `splitMessageQuote`. `reply_to_message_id` / `reply_to_excerpt` go to
 *        the event log / audit trail only, never to Respond.
 *        Inbound quoted context (the contact quoting us) is separate - it
 *        comes from Respond's structured `replyTo`.
 *
 * E2. MANUAL TEMPLATE SEND (the composer's "Send template" dialog) - AC-E1
 *    POST /{tracking_id}/conversation/template-message
 *      { template_id, params, tracking_id? }
 *    The shared chat route (every chat surface mounts it), queued on the
 *    respond_io worker. `tracking_id` is the ticket the template answers: the
 *    worker stamps THAT ticket's response clock once the send succeeds, so an
 *    out-of-window template reply stops the clock exactly like a text reply.
 *    Omitted by every other surface. The worker only honours it when the
 *    ticket's contact is the one that received the template.
 *
 * F. RESOLVE - UAC AC-C3
 *    POST /{tracking_id}/resolve   (EXISTING route)
 *    For an intervention ticket it resolves THAT row only: sibling tickets stay
 *    open and NO Respond.io conversation-close call is made (Respond is a message
 *    pipe; conversation state is not ours to change).
 * ---------------------------------------------------------------------------
 */

import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { ChatTemplatePreview } from '@/services/whatsappTemplateService';
import type { MyPendingSLAItem } from './conversationSLATrackingService';
import { resolveConversationSLATracking } from './conversationSLATrackingService';

const BASE = '/api/v1/sla-management/conversation-sla-tracking';

/** Send types the composer may offer. Bounded by R1: no sticker, no native reply-to. */
export type TicketSendCapability = 'text' | 'attachment';

/** Attachment subtypes Respond.io accepts on an outgoing message. */
export type TicketAttachmentKind = 'image' | 'video' | 'audio' | 'file';

/** 24h messaging-window state for the ticket's contact. */
export interface TicketWindowState {
  open: boolean;
  /** When the window closes (or closed). Null when unknown. */
  expires_at: string | null;
}

/**
 * A worklist row. Extends the existing `/my-pending` item so the dashboard widget
 * renders tickets and legacy rows through one code path.
 */
export interface InterventionTicketListItem extends MyPendingSLAItem {
  /** Always true on ticket rows. Absent on pre-migration conversation rows. */
  is_intervention_ticket: true;
  contact_name: string | null;
  contact_phone: string | null;
  /** First ~140 chars of the message that triggered the intervention. */
  enquiry_snippet: string | null;
  /** Respond.io message id of the trigger message (identity + thread highlight). */
  source_message_id: string | null;
  team_label: string | null;
  /** Real request time (may sit outside working hours; the clock start does not). */
  initiated_at: string;
  /** Set ONLY by a real escalation. Tier alone never means escalated. */
  escalated_at: string | null;
}

export interface InterventionTicketDetail {
  id: string;
  contact_name: string | null;
  contact_phone: string | null;
  respond_io_id: string | null;
  /** The triggering message, quoted in the drawer header. */
  source_message_id: string | null;
  source_message_text: string | null;
  source_message_at: string | null;
  team_label: string | null;
  assignee_name: string | null;
  policy_name: string | null;
  initiated_at: string;
  current_tier: number;
  escalated_at: string | null;
  escalation_reason: string | null;
  due_at: string | null;
  due_at_resolution: string | null;
  is_responded: boolean;
  responded_at: string | null;
  is_resolved: boolean;
  resolved_at: string | null;
  /** Times the resolution deadline has been extended (0 = never). */
  extension_count?: number;
  /** Viewer-relative gates, server-computed (assignee / RBAC). */
  can_send: boolean;
  /** Assignee-or-admin only - My Team can open this drawer without being able
   *  to resolve it (reading is wider than acting). */
  can_resolve: boolean;
  /** True when the viewer IS the assignee. Drives the drawer's Takeover
   *  affordance: offered only when this is false (My Team opened it) and the
   *  viewer holds the takeover permission. */
  is_assignee: boolean;
  /** The assignee's queue team id, resolved from the VIEWER's own visible
   *  teams - the same id My Team's row already passes to Takeover
   *  (`TeamPendingItem.team_id`). Null when the assignee is outside the
   *  viewer's visible teams (e.g. an admin acting outside their hierarchy). */
  assignee_team_id: string | null;
  /** What the composer may offer. Anything absent here is absent from the UI. */
  send_capabilities: TicketSendCapability[];
  window: TicketWindowState;
  /** Out-of-window fill-in template; null when the window is open or none configured. */
  chat_template: ChatTemplatePreview | null;
}

export interface SendTicketMessageInput {
  text: string;
  attachments?: File[];
  /** #1317, audit only: Respond has no reply-to; the quote is in `text`. */
  reply_to_message_id?: string | null;
  reply_to_excerpt?: string | null;
}

/**
 * Per-file outcome of a multi-attachment send. The backend never fails a send
 * all-or-nothing: attachments go sequentially and stop at the FIRST failure, so
 * `delivered` is the ordered prefix that actually reached the contact and
 * `failed` names the one that did not (everything after it was not attempted).
 * `null` on the text-only path.
 */
export interface SendTicketAttachmentsOutcome {
  delivered: string[];
  failed: { filename: string; error: string } | null;
}

export interface SendTicketMessageResult {
  sent_as: 'text' | 'template' | 'attachment';
  /** Exactly what the contact received. */
  rendered_text: string;
  /** True when the text was flattened to fit a template parameter. */
  flattened: boolean;
  window: TicketWindowState;
  attachments?: SendTicketAttachmentsOutcome | null;
}

/** C. Drawer header + composer state for one ticket. */
export async function getInterventionTicket(id: string): Promise<InterventionTicketDetail> {
  const response = await apiFetch(`${BASE}/${encodeURIComponent(id)}/ticket`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load this ticket'));
  }
  return response.json();
}

// D. The shared contact thread this ticket was raised in: see the contract note
// above - `getSlaTrackingConversation` in conversationSLATrackingService owns it.

/** E. Ticket-stamped send: stops THIS ticket's first-response clock only. */
export async function sendInterventionTicketMessage(
  id: string,
  input: SendTicketMessageInput,
): Promise<SendTicketMessageResult> {
  const files = input.attachments ?? [];
  const url = `${BASE}/${encodeURIComponent(id)}/ticket/send`;
  const response = await (files.length > 0
    ? apiFetch(url, { method: 'POST', body: buildSendFormData(input, files) })
    : apiFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: input.text,
          ...(input.reply_to_message_id ? { reply_to_message_id: input.reply_to_message_id } : {}),
          ...(input.reply_to_excerpt ? { reply_to_excerpt: input.reply_to_excerpt } : {}),
        }),
      }));
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to send message'));
  }
  return response.json();
}

function buildSendFormData(input: SendTicketMessageInput, files: File[]): FormData {
  const formData = new FormData();
  formData.append('text', input.text);
  if (input.reply_to_message_id) formData.append('reply_to_message_id', input.reply_to_message_id);
  if (input.reply_to_excerpt) formData.append('reply_to_excerpt', input.reply_to_excerpt);
  for (const file of files) formData.append('files', file);
  return formData;
}

/**
 * G. AI assist (UAC AC-L5): draft a reply INTO the composer.
 *
 *    POST /{tracking_id}/ai-draft
 *      body: { instruction?: string, tail?: number }   (both optional)
 *      -> { draft, model, grounded_on, elapsed_ms }
 *
 *    The thread is read SERVER-SIDE from the same paginated conversation read
 *    the drawer uses, so the browser never posts the conversation back up. The
 *    draft is text for the assignee to edit; nothing here sends anything. A
 *    missing/failing/empty model is a 503 with a readable message rather than a
 *    silent no-op, because a button that does nothing is worse than one that
 *    says the assistant is not configured.
 */
export interface TicketAIDraftInput {
  /** What the agent wants the draft to do ("offer Tuesday delivery"). */
  instruction?: string;
  /** How many recent messages to ground on. Server default is 20. */
  tail?: number;
}

export interface TicketAIDraftResult {
  draft: string;
  model: string | null;
  /** How many thread messages the prompt actually carried. */
  grounded_on: number;
  elapsed_ms: number;
}

export async function draftInterventionTicketReply(
  id: string,
  input: TicketAIDraftInput = {},
): Promise<TicketAIDraftResult> {
  const response = await apiFetch(`${BASE}/${encodeURIComponent(id)}/ai-draft`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      instruction: input.instruction || undefined,
      tail: input.tail ?? undefined,
    }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to draft a reply'));
  }
  return response.json();
}

/**
 * F. Resolve THIS ticket. Siblings stay open; Respond conversation untouched.
 * Delegates to the existing dedicated resolve route - a ticket is resolved
 * exactly like any other conversation SLA row (UAC AC-C3).
 */
export async function resolveInterventionTicket(id: string): Promise<void> {
  return resolveConversationSLATracking(id);
}
