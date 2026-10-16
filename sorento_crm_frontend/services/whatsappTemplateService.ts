/**
 * WhatsApp Template service - Respond.io template sync, per-use-case defaults,
 * 24h-window state and template sending.
 *
 * =========================================================================
 * EXPECTED API CONTRACT (Phase 1 - locked for Phase 2 backend work)
 * See docs/plans/PLAN-whatsapp-template-fallback.md
 * =========================================================================
 *
 * GET /api/v1/integrations/respond/templates
 *   Query: page, limit, sort, dir, query (buildDataGridParams), status?
 *   200: { data: WhatsAppTemplate[], pagination: { total, page, limit } }
 *
 * POST /api/v1/integrations/respond/templates/sync
 *   200: { synced: number, deleted: number, channels: number }
 *   Errors: 502 when Respond.io API unreachable.
 *
 * GET /api/v1/integrations/respond/template-defaults
 *   200: TemplateDefault[]  (always 4 rows - one per use_case, template_id
 *        null when unset; is_valid=false when the referenced template was
 *        deleted on sync or is no longer `approved`)
 *
 * PUT /api/v1/integrations/respond/template-defaults/{use_case}
 *   Body: { template_id: string, param_mapping: Record<string, ParamVariable> }
 * - param_mapping keys are the template's positional params ("1".."n");
 *     every param of the template MUST be mapped (422 otherwise).
 * - template must have status=approved (422 otherwise).
 *   200: TemplateDefault
 *
 * DELETE /api/v1/integrations/respond/template-defaults/{use_case}
 *   200: TemplateDefault (template_id null)
 *
 * Per-entity chat routes (keyed by use case in ENTITY_CHAT_BASE):
 *   GET  /{entity_base}/{id}/conversation/window-state
 *   - Backend scans Respond.io list_messages for the latest incoming
 *       message; window treated as 23h (margin). Degrades to chat_history
 *       when the Respond API errors; no data at all => closed.
 *     200: WindowState
 *   POST /{entity_base}/{id}/conversation/template-message
 *     Body: { template_id: string, params: Record<string, string> }
 *   - params keys "1".."n" must cover the template's param_count (422).
 *       Contact is resolved server-side from the entity (no contact_id needed).
 *     200: { ok: true, template_name, rendered_body }
 *     Errors: 422 template not approved / params missing; 502 send failed.
 *   entity_base: complaint=/complaints-management/complaints,
 *     stock_inquiry|purchase_request|sponsorship_form=/procurement/*
 *
 * Status enums:
 *   TemplateStatus: approved | pending | rejected
 *   UseCase: complaint | stock_inquiry | purchase_request | sponsorship_form | ...
 *     (full list: `USE_CASES` below, kept in sync with the backend's
 *     `TEMPLATE_DEFAULT_USE_CASES` tuple - app/models/respond_template.py)
 *   ParamVariable: contact_name | entity_number | status | reason | portal_url | message
 * =========================================================================
 */

export type TemplateStatus = 'approved' | 'pending' | 'rejected';

export type UseCase =
  | 'complaint'
  | 'stock_inquiry'
  | 'purchase_request'
  | 'sponsorship_form'
  | 'complaint_chat'
  | 'stock_inquiry_chat'
  | 'purchase_request_chat'
  | 'sponsorship_form_chat'
  | 'conversation_chat'
  | 'portal_otp'
  | 'sla_daily_summary'
  | 'sla_assignment'
  | 'sla_escalation'
  | 'sla_deadline_extended'
  | 'sla_takeover_pending'
  | 'sla_task_moved'
  | 'sla_takeover_cancelled'
  | 'sla_handling_claimed'
  | 'sla_handling_taken_over'
  | 'sla_handling_released'
  | 'form_action_voided'
  | 'form_action_reopened'
  | 'product_discontinued'
  | 'ticket_resolved'
  | 'price_tag_update'
  | 'quotation_approval'
  | 'ideation_draft_reminder'
  | 'ideation_status_update'
  | 'supplier_request_chat'
  | 'stock_ask_salesman';

export type ParamVariable =
  | 'contact_name'
  | 'sender_name'
  | 'entity_number'
  | 'status'
  | 'reason'
  | 'portal_url'
  | 'message'
  | 'otp_code'
  | 'outstanding'
  | 'escalated_last_24h'
  | 'resolved_last_24h'
  | 'discontinued_count'
  | 'discontinued_link'
  | 'today_date'
  | 'system_url'
  | 'respond_due_at'
  | 'resolve_due_at'
  | 'form_url'
  | 'customer'
  | 'delivery_order'
  | 'update'
  | 'view_url'
  | 'project'
  | 'product_code'
  | 'initiator'
  | 'handler_name'
  | 'idea_number'
  | 'status_label'
  | 'track_url'
  | 'outcome'
  | 'customer_name'
  | 'product'
  | 'quantity'
  | 'asked_at';

export interface WhatsAppTemplate {
  id: string;
  respond_template_id: string;
  name: string;
  language: string;
  category: string; // MARKETING | UTILITY | AUTHENTICATION
  status: TemplateStatus;
  body_text: string; // body component text containing {{1}}..{{n}}
  param_count: number;
  /** true when the template carries a dynamic URL button (url has {{n}}) */
  has_url_button: boolean;
  /** static prefix before the {{n}} in the button URL, e.g. https://fe-sorento.foundryx.my/ */
  button_url_base: string | null;
  button_text: string | null;
  /** true for a WhatsApp Authentication COPY_CODE button - no link variable to map */
  button_is_copy_code?: boolean;
  channel_name: string;
  synced_at: string; // ISO UTC
}

export interface TemplateDefault {
  use_case: UseCase;
  template_id: string | null;
  template_name: string | null;
  template_status: TemplateStatus | null;
  param_mapping: Record<string, ParamVariable>;
  /** false when the referenced template vanished on sync or lost approval */
  is_valid: boolean;
  /** dynamic URL button metadata (present when the template has one) */
  has_url_button?: boolean;
  button_url_base?: string | null;
  button_url_var?: ParamVariable | null;
  /** true for a WhatsApp Authentication COPY_CODE button - no link variable to map */
  button_is_copy_code?: boolean;
}

/** Reserved (non-numeric) param_mapping key for a template's dynamic URL button. */
export const BUTTON_URL_KEY = 'button_url';

/** Link-type variables offered for a dynamic URL button mapping. */
export const BUTTON_LINK_VARIABLES: ParamVariable[] = [
  'portal_url',
  'view_url',
  'form_url',
  'discontinued_link',
  'system_url',
  'track_url',
];

export interface WindowState {
  open: boolean;
  last_incoming_at: string | null; // ISO UTC
  checked_at: string; // ISO UTC
}

export interface TemplateListResult {
  data: WhatsAppTemplate[];
  pagination: { total: number; page: number; limit: number };
}

export interface SyncResult {
  synced: number;
  deleted: number;
  channels: number;
}

export const USE_CASES: {
  key: UseCase;
  label: string;
  description: string;
  /** 'update' = status-update templates (existing); 'chat' = free-text chat-reply templates. */
  group?: 'update' | 'chat';
}[] = [
  {
    key: 'complaint',
    label: 'Complaint',
    description: 'Decision updates (approved / rejected) sent to the complainant.',
  },
  {
    key: 'stock_inquiry',
    label: 'Stock Inquiry',
    description: 'Reply and rejection updates sent to the inquiring contact.',
  },
  {
    key: 'purchase_request',
    label: 'Purchase Request',
    description: 'Status updates (form number, approval) sent to the requester.',
  },
  {
    key: 'sponsorship_form',
    label: 'Sponsorship Form',
    description: 'Approval updates sent to the sponsorship applicant.',
  },
  {
    key: 'complaint_chat',
    label: 'Complaint - Chat Reply',
    description:
      'Free-text chat reply to the complainant when their 24h window is closed. Map a parameter to "Full update message" (the typed text) and, ideally, "Sender name".',
    group: 'chat',
  },
  {
    key: 'stock_inquiry_chat',
    label: 'Stock Inquiry - Chat Reply',
    description:
      'Free-text chat reply to the inquiring contact when their 24h window is closed. Map a parameter to "Full update message" and, ideally, "Sender name".',
    group: 'chat',
  },
  {
    key: 'purchase_request_chat',
    label: 'Purchase Request - Chat Reply',
    description:
      'Free-text chat reply to the requester when their 24h window is closed. Map a parameter to "Full update message" and, ideally, "Sender name".',
    group: 'chat',
  },
  {
    key: 'sponsorship_form_chat',
    label: 'Sponsorship Form - Chat Reply',
    description:
      'Free-text chat reply to the sponsorship applicant when their 24h window is closed. Map a parameter to "Full update message" and, ideally, "Sender name".',
    group: 'chat',
  },
  {
    key: 'conversation_chat',
    label: 'Conversation SLA - Chat Reply',
    description:
      'Free-text chat reply to a Respond contact from the Conversation SLA panel when their 24h window is closed. Map a parameter to "Full update message" and, ideally, "Sender name". No view link.',
    group: 'chat',
  },
  {
    key: 'supplier_request_chat',
    label: 'Supplier Request - Chat Reply',
    description:
      'Free-text chat reply to the supplier\'s WeChat contact when their 24h window is closed. Map a parameter to "Full update message" (the bilingual ask + public link). Nothing is seeded - the WeChat channel and its template need approving first.',
    group: 'chat',
  },
  {
    key: 'portal_otp',
    label: 'Portal OTP',
    description:
      'Login verification code sent when a contact opens the portal on a new device and the 24h window is closed. Map the code param to "OTP code".',
  },
  {
    key: 'sla_daily_summary',
    label: 'SLA Daily Summary',
    description:
      'Daily SLA digest sent to staff on WhatsApp when their 24h window is closed. Bounded template - map params to the outstanding / escalated (24h) / resolved (24h) counts and the dashboard deep link (Portal URL).',
  },
  {
    key: 'sla_assignment',
    label: 'SLA Assignment',
    description:
      'Sent to a staff member when an SLA task is assigned to them. Map params to "Contact name" (the staff name) and "Full update message" at minimum; add "Entity number" / "Status" when the template carries them.',
  },
  {
    key: 'sla_escalation',
    label: 'SLA Escalation',
    description:
      'Sent to a staff member when an SLA task escalates to them. Map params to "Contact name" and "Full update message" at minimum; add "Entity number" / "Status" when the template carries them.',
  },
  {
    key: 'sla_deadline_extended',
    label: 'SLA Deadline Extended',
    description:
      'Sent to the next escalation tier when an assignee extends a task’s resolution deadline. Map params to "Contact name" and "Reason" at minimum; add "Entity number" / "Resolve by" when the template carries them.',
  },
  {
    key: 'sla_takeover_pending',
    label: 'SLA Takeover - Pending',
    description:
      'Sent to a task’s current assignee when a teammate starts a takeover (cooldown window). Map params to "Contact name" (the assignee) and "Full update message" at minimum; add "Entity number" when the template carries it.',
  },
  {
    key: 'form_action_voided',
    label: 'Form Action Undo - Task Voided',
    description:
      'Sent to the staff member whose task was voided because a form action was undone. Map a parameter to "Full update message" at minimum (it carries who undid what and the reason); add "Sender name" / the link when the template carries them.',
  },
  {
    key: 'form_action_reopened',
    label: 'Form Action Undo - Task Returned',
    description:
      'Sent to the staff member a form returned to after an undo (their SLA clock restarts). Map a parameter to "Full update message" at minimum; add "Sender name" / the link when the template carries them.',
  },
  {
    key: 'sla_task_moved',
    label: 'SLA Task Moved',
    description:
      'Sent to the previous assignee when their SLA task is reassigned/taken over. Map params to "Contact name" and "Full update message" at minimum; add "Entity number" when present.',
  },
  {
    key: 'sla_takeover_cancelled',
    label: 'SLA Takeover - Cancelled',
    description:
      'Sent to the initiator when their takeover is rejected by the owner or voided (task resolved / reassigned / escalated). Map params to "Contact name" and "Full update message" at minimum.',
  },
  {
    key: 'sla_handling_claimed',
    label: 'SLA Handling - Claimed',
    description:
      'Sent to the assignee and other eligible team members when someone claims handling of an escalated form ("I\'m handling this"). Map params to "Contact name" (the recipient) and "Handler name" (who claimed) at minimum; add "Entity number" / "Full update message" when the template carries them.',
  },
  {
    key: 'sla_handling_taken_over',
    label: 'SLA Handling - Taken Over',
    description:
      'Sent to the displaced holder when a teammate takes over handling of the form. Map params to "Contact name" (the displaced holder) and "Handler name" (who took over) at minimum; add "Entity number" / "Full update message".',
  },
  {
    key: 'sla_handling_released',
    label: 'SLA Handling - Unclaimed',
    description:
      'Sent to eligible team members when the current holder unclaims a form (open to handle again). Map params to "Contact name" (the recipient) and "Handler name" (who unclaimed) at minimum; add "Entity number" / "Full update message".',
  },
  {
    key: 'product_discontinued',
    label: 'Product Discontinued',
    description:
      'Batch alert sent to subscribed staff when products are newly discontinued. Map params to "Discontinued count" and "Discontinued link" (the deep link to the filtered product list).',
  },
  {
    key: 'ticket_resolved',
    label: 'Ticket Resolved (closing message)',
    description:
      'Sent to the CONTACT each time one of their enquiries is resolved. Map params to "Contact name" and "Full update message" (the enquiry excerpt) at minimum.',
  },
  {
    key: 'price_tag_update',
    label: 'Price Tag Request - Update',
    description:
      'Sent to the salesperson when their price tag request moves (received, design ready, changes requested, approved, PDF ready, ready for collection, collected, rejected) and their 24h window is closed. Map params to "Full update message" at minimum; add "Entity number" and "Portal URL" when the template carries them.',
  },
  {
    key: 'quotation_approval',
    label: 'Quotation - Approval',
    description:
      'Sent to the approvers when a below-floor quotation is sent for approval or withdrawn, and their 24h window is closed. Map params to "Contact name" and "Full update message" at minimum; add "Entity number" and "Portal URL" when the template carries them.',
  },
  {
    key: 'ideation_draft_reminder',
    label: 'Ideation - Draft Reminder',
    description:
      'Sent once, 24h after the last turn on an open idea draft, when the free-text window has closed. Map a parameter to "Full update message" (the reminder text naming the idea).',
  },
  {
    key: 'ideation_status_update',
    label: 'Ideation - Status Update',
    description:
      'Sent to the requester each time their idea moves on the ideas board (new stage, merged into another idea, or separated again). Always sent as this template. Use a Utility template; map params to "Idea number", "New status label" and "Track link", or one param to "Full update message".',
  },
  {
    key: 'stock_ask_salesman',
    label: 'Stock Ask - Salesman Notification',
    description:
      'Sent to the customer\'s sales agent when the chatbot answers a dealer\'s stock ask (too big, in stock, or no stock and no incoming) and the dealer contact has "Notify salesman" on. Map params to "Customer", "Contact name" (the dealer who asked), "Product", "Quantity", "Outcome" and "Asked at".',
  },
];

export const PARAM_VARIABLES: { key: ParamVariable; label: string; description: string }[] = [
  { key: 'contact_name', label: 'Contact name', description: 'WhatsApp contact display name' },
  { key: 'sender_name', label: 'Sender name', description: 'Staff member who sent the reply (logged-in user; "Customer Service" for system sends)' },
  { key: 'entity_number', label: 'Entity number', description: 'e.g. CMP-2606-0012 / RMA-PS2605-0017' },
  { key: 'status', label: 'Status', description: 'New status of the record (approved, rejected…)' },
  { key: 'reason', label: 'Reason', description: 'Decision reason when present' },
  { key: 'portal_url', label: 'Portal URL', description: 'Interactive portal link - contact can act / resubmit (complaint: /portal/c/…)' },
  { key: 'view_url', label: 'View link', description: 'Read-only public view of the record (token link, /view/…)' },
  { key: 'message', label: 'Full update message', description: 'The composed update text, flattened to one line' },
  { key: 'otp_code', label: 'OTP code', description: 'The 6-digit portal login verification code' },
  { key: 'outstanding', label: 'Outstanding count', description: 'Outstanding conversations assigned to the staff member' },
  { key: 'escalated_last_24h', label: 'Escalated (24h)', description: 'Conversations escalated to the staff member in the last 24h' },
  { key: 'resolved_last_24h', label: 'Resolved (24h)', description: 'Conversations the staff member resolved in the last 24h' },
  { key: 'discontinued_count', label: 'Discontinued count', description: 'Number of products newly discontinued in this batch' },
  { key: 'discontinued_link', label: 'Discontinued link', description: 'Deep link to the product list filtered to that batch' },
  { key: 'today_date', label: "Today's date", description: 'Date the message is sent (DD/MM/YYYY, Malaysia time)' },
  { key: 'system_url', label: 'System URL', description: 'CRM base URL (e.g. https://fe-sorento.foundryx.my)' },
  { key: 'respond_due_at', label: 'Respond by', description: 'SLA response deadline (KL wall time)' },
  { key: 'resolve_due_at', label: 'Resolve by', description: 'SLA resolution deadline (KL wall time)' },
  { key: 'handler_name', label: 'Handler name', description: 'Staff member who claimed / took over / unclaimed the form handling lock' },
  { key: 'form_url', label: 'Form link', description: 'Opens the form record, or the ticket in the CRM (/?ticket=...) for a conversation ticket - same as clicking the task' },
  { key: 'customer', label: 'Customer name', description: 'Customer name on the record (complaint / purchase request)' },
  { key: 'project', label: 'Project name', description: 'Project name/title on the record (complaint / purchase request)' },
  { key: 'delivery_order', label: 'DO number', description: 'Delivery order number on the complaint' },
  { key: 'product_code', label: 'Product code', description: 'Product code on the stock inquiry' },
  { key: 'initiator', label: 'Initiator', description: 'SLA takeover: teammate who requested the takeover ("Requested by")' },
  { key: 'idea_number', label: 'Idea number', description: 'Ideation: the idea number (e.g. IDEA-0031), or its title when it has no number' },
  { key: 'status_label', label: 'New status label', description: 'Ideation: the new stage, or "combined with IDEA-0012" / "handled separately again from IDEA-0012, now <stage>"' },
  { key: 'track_url', label: 'Track link', description: 'Ideation: public link to follow the idea, no login needed' },
  { key: 'update', label: 'Update', description: 'Lean action core - technical reply / "Approved" / "Rejected, reason: X" / "Processed by CS" / "Root cause is X" / "Resolution is X". No preamble or link.' },
  { key: 'outcome', label: 'Outcome', description: 'Stock ask: in stock / too big / no stock no incoming / no cap set for <category>' },
  { key: 'customer_name', label: 'Customer', description: 'Stock ask: the customer the dealer contact buys for' },
  { key: 'product', label: 'Product', description: 'Stock ask: product code - name' },
  { key: 'quantity', label: 'Quantity', description: 'Stock ask: the quantity the dealer asked for' },
  { key: 'asked_at', label: 'Asked at', description: 'Stock ask: when the dealer asked (DD/MM/YYYY HH:MM, Malaysia time)' },
];


// ===========================================================================
// Real API calls (Phase 2). Window-state + template send are per-entity routes
// keyed by use case; the rest hit /integrations/respond/*.
// ===========================================================================

import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

async function jsonOrThrow<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) throw new Error(await extractApiError(res, fallback));
  return (await res.json()) as T;
}

const RESPOND_BASE = '/api/v1/integrations/respond';

/** Conversation base path per chat entity (window-state + template-message live under it). */
const ENTITY_CHAT_BASE: Record<string, string> = {
  complaint: '/api/v1/complaints-management/complaints',
  stock_inquiry: '/api/v1/procurement/stock-inquiries',
  purchase_request: '/api/v1/procurement/purchase-requests',
  sponsorship_form: '/api/v1/procurement/purchase-requests',
  conversation_sla: '/api/v1/sla-management/conversation-sla-tracking',
};

function chatBase(entityType: string): string {
  const base = ENTITY_CHAT_BASE[entityType];
  if (!base) throw new Error(`No chat route configured for entity type: ${entityType}`);
  return base;
}

export async function listTemplates(params: {
  page?: number;
  limit?: number;
  query?: string;
  status?: TemplateStatus | 'all';
}): Promise<TemplateListResult> {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('limit', String(params.limit ?? 50));
  if (params.query) sp.set('query', params.query);
  if (params.status && params.status !== 'all') sp.set('status', params.status);
  const res = await apiFetch(`${RESPOND_BASE}/templates?${sp.toString()}`);
  return jsonOrThrow<TemplateListResult>(res, 'Failed to load templates');
}

export async function syncTemplates(): Promise<SyncResult> {
  const res = await apiFetch(`${RESPOND_BASE}/templates/sync`, { method: 'POST' });
  return jsonOrThrow<SyncResult>(res, 'Template sync failed');
}

export async function listTemplateDefaults(): Promise<TemplateDefault[]> {
  const res = await apiFetch(`${RESPOND_BASE}/template-defaults`);
  return jsonOrThrow<TemplateDefault[]>(res, 'Failed to load template defaults');
}

export async function setTemplateDefault(
  useCase: UseCase,
  body: { template_id: string; param_mapping: Record<string, ParamVariable> },
): Promise<TemplateDefault> {
  const res = await apiFetch(`${RESPOND_BASE}/template-defaults/${useCase}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return jsonOrThrow<TemplateDefault>(res, 'Failed to save default');
}

export async function clearTemplateDefault(useCase: UseCase): Promise<TemplateDefault> {
  const res = await apiFetch(`${RESPOND_BASE}/template-defaults/${useCase}`, {
    method: 'DELETE',
  });
  return jsonOrThrow<TemplateDefault>(res, 'Failed to clear default');
}

export async function getWindowState(
  entityType: string,
  entityId: string,
): Promise<WindowState> {
  const res = await apiFetch(
    `${chatBase(entityType)}/${encodeURIComponent(entityId)}/conversation/window-state`,
  );
  return jsonOrThrow<WindowState>(res, 'Failed to check window state');
}

/** Approved templates for the send dialog - reuses the templates list endpoint. */
export async function listApprovedTemplates(): Promise<WhatsAppTemplate[]> {
  const res = await listTemplates({ page: 1, limit: 200, status: 'approved' });
  return res.data;
}

export async function sendTemplateMessage(
  entityType: string,
  entityId: string,
  body: {
    contact_id: string;
    template_id: string;
    params: Record<string, string>;
    /**
     * Intervention ticket this template answers. The backend stamps that
     * ticket's response clock once the send succeeds, so an out-of-window
     * template reply counts as a reply. Omitted on every other chat surface.
     */
    tracking_id?: string | null;
  },
): Promise<{ ok: true }> {
  const res = await apiFetch(
    `${chatBase(entityType)}/${encodeURIComponent(entityId)}/conversation/template-message`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        template_id: body.template_id,
        params: body.params,
        ...(body.tracking_id ? { tracking_id: body.tracking_id } : {}),
      }),
    },
  );
  await jsonOrThrow(res, 'Failed to send template');
  return { ok: true };
}

export interface ChatTemplateSlot {
  variable: string | null;
  /** Resolved value for non-message slots; null for the editable message slot. */
  value: string | null;
  editable: boolean;
}

export interface ChatTemplatePreview {
  configured: boolean;
  reason?: string;
  settings_url?: string;
  template_name?: string;
  body_text?: string;
  slots?: Record<string, ChatTemplateSlot>;
}

/** Describe the form's *_chat template so the composer can render it inline with a
 * fill-in field for the message (out-of-window). DB-only on the backend - no send. */
export async function getChatTemplatePreview(
  entityType: string,
  entityId: string,
): Promise<ChatTemplatePreview> {
  const res = await apiFetch(
    `${chatBase(entityType)}/${encodeURIComponent(entityId)}/conversation/chat-template`,
  );
  return jsonOrThrow<ChatTemplatePreview>(res, 'Failed to load chat template');
}

export interface SendMessageResult {
  /** 'text' = plain typed text delivered (in-window); 'template' = *_chat template delivered (out-of-window). */
  sent_as: 'text' | 'template';
  /** What the contact actually received (typed text, or the rendered template body with params filled). */
  rendered_text: string;
  /** true when the typed text was flattened (newlines/tabs/space-runs collapsed, or truncated) to fit the template param. */
  flattened: boolean;
  window_state: WindowState;
}

/** Thrown when an out-of-window chat send has no configured `*_chat` template for the form. */
export class NoChatTemplateError extends Error {
  readonly code = 'no_chat_template';
  readonly settingsUrl: string;
  constructor(message: string, settingsUrl: string) {
    super(message);
    this.name = 'NoChatTemplateError';
    this.settingsUrl = settingsUrl;
  }
}

/**
 * Pure chat-window send. Backend decides plain-vs-template by the 24h window:
 * in-window -> raw typed text; out-of-window -> the form's `*_chat` template with
 * {{sender_name}} (logged-in user) + the typed text (flattened into `message`).
 * Never mutates the entity. Synchronous; returns what was delivered.
 */
export async function sendConversationMessage(
  entityType: string,
  entityId: string,
  text: string,
): Promise<SendMessageResult> {
  const res = await apiFetch(
    `${chatBase(entityType)}/${encodeURIComponent(entityId)}/conversation/send-message`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    },
  );
  if (!res.ok) {
    // Surface the "no chat template configured" case as a typed error the composer can act on.
    let body: unknown = null;
    try {
      body = await res.clone().json();
    } catch {
      /* fall through to extractApiError */
    }
    const code = extractErrorCode(body);
    if (code === 'no_chat_template') {
      const settingsUrl = extractSettingsUrl(body) ?? '/integration-management/whatsapp-templates';
      throw new NoChatTemplateError(
        extractApiErrorMessage(body) ?? 'No chat reply template configured for this form.',
        settingsUrl,
      );
    }
    throw new Error(await extractApiError(res, 'Failed to send message'));
  }
  return (await res.json()) as SendMessageResult;
}

/** Pull an error_code out of the various shapes the backend error handler may emit. */
function extractErrorCode(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null;
  const b = body as Record<string, unknown>;
  const detail = b.detail as Record<string, unknown> | undefined;
  return (
    (typeof b.code === 'string' && b.code) ||
    (typeof b.error_code === 'string' && b.error_code) ||
    (detail && typeof detail.code === 'string' && detail.code) ||
    null
  );
}

function extractSettingsUrl(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null;
  const b = body as Record<string, unknown>;
  // Backend AppException carries the settings URL in `detail` (a plain path string).
  if (typeof b.settings_url === 'string') return b.settings_url;
  if (typeof b.detail === 'string' && b.detail.startsWith('/')) return b.detail;
  const detail = b.detail as Record<string, unknown> | undefined;
  if (detail && typeof detail === 'object') {
    if (typeof detail.settings_url === 'string') return detail.settings_url;
  }
  return null;
}

function extractApiErrorMessage(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null;
  const b = body as Record<string, unknown>;
  const detail = b.detail as Record<string, unknown> | undefined;
  return (
    (typeof b.message === 'string' && b.message) ||
    (detail && typeof detail.message === 'string' && detail.message) ||
    (typeof b.detail === 'string' && (b.detail as string)) ||
    null
  );
}

/** Render a template body with params filled; unfilled params stay as {{n}}. */
export function renderTemplateBody(bodyText: string, params: Record<string, string>): string {
  return bodyText.replace(/\{\{(\d+)\}\}/g, (m, n: string) => {
    const v = (params[n] ?? '').trim();
    return v || m;
  });
}
