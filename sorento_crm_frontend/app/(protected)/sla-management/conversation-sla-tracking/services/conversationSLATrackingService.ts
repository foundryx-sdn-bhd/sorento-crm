import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type {
  ConversationSearchMatch,
  ConversationThreadPage,
} from '@/components/common/conversation/useConversationThread';
import type { ConversationSLATracking, ConversationSLATrackingDetail, ConversationSLAEventLog, SLATrackingDashboardMetrics } from '../types/conversationSLATracking.types';
import type { DataGridApiFetchParams, DataGridApiResponse } from '@/components/ui/data-grid';

export interface ConversationSLAEventLogsParams {
  tracking_id: string;
  page?: number;
  limit?: number;
  sort?: string;
  dir?: 'asc' | 'desc';
  event_type?: string;
  date_from?: string; // YYYY-MM-DD
  date_to?: string;   // YYYY-MM-DD
  assigned_to_id?: string;
}

export interface ConversationSLAEventLogsResponse {
  data: ConversationSLAEventLog[];
  pagination: { total: number; page: number; limit: number };
}

export async function getConversationSLAEventLogs(params: ConversationSLAEventLogsParams): Promise<ConversationSLAEventLogsResponse> {
  const { tracking_id, page = 1, limit = 50, sort = 'event_at', dir = 'desc', event_type, date_from, date_to, assigned_to_id } = params;
  const queryParams = new URLSearchParams({
    tracking_id,
    page: String(page),
    limit: String(limit),
    sort,
    dir,
    ...(event_type ? { event_type } : {}),
    ...(date_from ? { date_from: date_from } : {}),
    ...(date_to ? { date_to: date_to } : {}),
    ...(assigned_to_id ? { assigned_to_id } : {}),
  });
  const response = await apiFetch(`/api/v1/sla-management/conversation-sla-tracking/event-logs?${queryParams.toString()}`);
  if (!response.ok) throw new Error('Failed to fetch event logs');
  return response.json();
}

export type ConversationSLATrackingListParams = DataGridApiFetchParams & {
  policy_id?: string;
  status?: string;
  assigned_to?: string;
  /** AC-M2 history links. Respond.io contact id / phone - never the CRM UUID. */
  contact?: string;
  is_resolved?: boolean;
  /** users.id, respond_user_id, email, or the literal 'me' (expanded server-side). */
  resolved_by?: string;
};


export async function getConversationSLATracking(params: ConversationSLATrackingListParams): Promise<DataGridApiResponse<ConversationSLATracking>> {
  const {
    pageIndex,
    pageSize,
    sorting,
    searchQuery,
    policy_id,
    status,
    assigned_to,
    contact,
    is_resolved,
    resolved_by,
  } = params;
  const sortField = sorting?.[0]?.id || '';
  const sortDirection = sorting?.[0]?.desc ? 'desc' : 'asc';
  const queryParams = new URLSearchParams({
    page: String(pageIndex + 1),
    limit: String(pageSize),
    ...(sortField ? { sort: sortField, dir: sortDirection } : {}),
    ...(searchQuery ? { query: searchQuery } : {}),
    ...(policy_id ? { policy_id } : {}),
    ...(status ? { status } : {}),
    ...(assigned_to ? { assigned_to } : {}),
    ...(contact ? { contact } : {}),
    ...(is_resolved !== undefined ? { is_resolved: String(is_resolved) } : {}),
    ...(resolved_by ? { resolved_by } : {}),
  });
  const response = await apiFetch(`/api/v1/sla-management/conversation-sla-tracking?${queryParams.toString()}`);
  if (!response.ok) throw new Error('Failed to fetch conversation SLA tracking');
  return response.json();
}

export async function getConversationSLATrackingDetail(id: string): Promise<ConversationSLATrackingDetail> {
  const response = await apiFetch(`/api/v1/sla-management/conversation-sla-tracking/${id}`);
  if (!response.ok) throw new Error('Failed to fetch conversation SLA tracking detail');
  return response.json();
}

export async function getSLATrackingDashboardMetrics(): Promise<SLATrackingDashboardMetrics> {
  const response = await apiFetch('/api/v1/sla-management/conversation-sla-tracking/dashboard');
  if (!response.ok) throw new Error('Failed to fetch dashboard metrics');
  return response.json();
}

/** A pending (or just-resolved) takeover attached to an SLA row. Drives the countdown
 *  bar, the Cancel/Reject affordances, and the contested-task banner. */
export interface TakeoverInfo {
  request_id: string;
  tracking_id: string;
  initiator_id: string;
  initiator_name: string;
  contested_assignee_id: string | null;
  contested_assignee_name: string | null;
  team_id: string | null;
  status: 'pending' | 'committed' | 'cancelled' | 'rejected' | 'voided';
  /** Absolute ISO timestamp the takeover commits - drives the countdown locally. */
  commit_at: string | null;
  /** Total cooldown window in seconds - fixed bar denominator (survives remounts). */
  window_seconds?: number | null;
  resolution_reason: string | null;
  /** Viewer-relative (server-computed): initiator/admin can cancel; owner/admin can reject. */
  can_cancel?: boolean;
  can_reject?: boolean;
}

export interface MyPendingSLAItem {
  id: string;
  source_entity_type: string | null;
  source_entity_id: string | null;
  /** Authoritative form-vs-conversation flag from the backend (FORM_SLA_TYPES).
   *  Conversation rows = false. The widget must use this, not re-derive from routes. */
  is_form_sla: boolean;
  /** Human-readable reference: complaint/inquiry/request/ticket number, or contact name. */
  reference: string | null;
  /** Respond.io contact id for conversation rows; used to build the inbox deep link. */
  respond_io_id: string | null;
  /** SLA-config-driven next action for form rows (e.g. "Send for approval"); null for conversation rows. */
  next_action: string | null;
  due_at: string | null;
  /** Resolution deadline (the clock Extend targets). Emitted by the `/my-pending`
   *  serializer so the Extend gate hides when there is no resolution due and the
   *  dialog can show "Current due". Null when the row has no resolution deadline.
   *  See PLAN-sla-extend-deadline.md. */
  due_at_resolution?: string | null;
  /** The deadline this row is actually racing for its next action: resolution due for
   *  resolution-phase rows (what Extend moves), response due otherwise. Use this for the
   *  row's deadline badge so an extended resolution deadline shows instead of a stale
   *  response clock. Falls back to due_at when absent. */
  active_due_at?: string | null;
  /** Which clock active_due_at represents - drives the primary badge label. */
  due_kind?: 'respond' | 'resolve';
  /** When the response clock was satisfied (for the muted "Responded ✓" context line). */
  responded_at?: string | null;
  is_responded: boolean;
  current_tier: number;
  /** How many times the resolution deadline has been extended (0 = never).
   *  Drives the row's "Extended" marker: a deadline somebody pushed out is one
   *  somebody is waiting on, and it must not read like an ordinary due date. */
  extension_count?: number;
  policy_name: string | null;
  /** Pending takeover on this row, if any (inline "being taken over" indicator). */
  takeover?: TakeoverInfo | null;
}

export async function getMyPendingSLA(limit = 1000): Promise<MyPendingSLAItem[]> {
  // Fetch the user's FULL unresolved set so the widget's count is honest and its
  // search/pagination operate over everything (browse and search see the same rows).
  // The old 50-cap both under-counted the badge and hid search matches past the page.
  const response = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/my-pending?limit=${limit}`,
  );
  if (!response.ok) throw new Error('Failed to fetch my pending SLAs');
  const body = await response.json();
  return Array.isArray(body?.data) ? (body.data as MyPendingSLAItem[]) : [];
}

/**
 * Resolve a conversation SLA tracking row (assignee action). Flips is_resolved;
 * the backend also best-effort closes the matching Respond.io conversation.
 */
export async function resolveConversationSLATracking(id: string): Promise<void> {
  // Dedicated, permission + actor-scope gated route (sla…resolve). The legacy
  // PUT /{id} stays for the n8n integration path only.
  const response = await apiFetch(`/api/v1/sla-management/conversation-sla-tracking/${id}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to resolve conversation SLA'));
  }
}

/**
 * Manually escalate a conversation SLA tracking row to the next tier with a reason.
 * Stored as "manual: <reason>"; reassigns + notifies per policy. Capped at tier 3.
 */
export async function escalateConversationSLATracking(id: string, reason: string): Promise<void> {
  const response = await apiFetch(`/api/v1/sla-management/conversation-sla-tracking/${id}/escalate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to escalate conversation SLA'));
  }
}

// ---------------------------------------------------------------------------
// Extend resolution deadline (assignee action) - see PLAN-sla-extend-deadline.md
// ---------------------------------------------------------------------------

/** Exactly one of `days` / `target_date` is supplied (mutually exclusive views
 *  of one target datetime). `target_date` is a calendar day "YYYY-MM-DD". */
export interface ExtendSLAInput {
  days?: number;
  target_date?: string;
}

/** Preview response (no mutation). `warnings` are non-blocking soft-limit breaches
 *  surfaced from the policy config; the extension still applies on confirm. */
export interface ExtendSLAPreview {
  current_due_at: string | null;
  new_due_at: string | null;
  working_days: number;
  warnings: string[];
}

/**
 * Preview a resolution-deadline extension. The backend owns the holiday calendar
 * and work-calendar config, so the working-day math + soft-limit warnings come
 * from here - never recomputed on the client. No mutation.
 */
export async function getExtendPreview(
  id: string,
  input: ExtendSLAInput,
): Promise<ExtendSLAPreview> {
  const response = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/${id}/extend/preview`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to preview extension'));
  }
  return response.json();
}

/**
 * Extend the resolution deadline (current assignee only). The backend recomputes
 * the new due authoritatively (ignores the client-previewed value), bumps the
 * extension counters, resets the reminder cycle, writes an `extend` event log,
 * and best-effort notifies the next escalation tier.
 */
export async function extendSLATracking(
  id: string,
  input: ExtendSLAInput & { reason: string },
): Promise<ConversationSLATracking> {
  const response = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/${id}/extend`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to extend deadline'));
  }
  return response.json();
}

export async function deleteConversationSLATracking(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/sla-management/conversation-sla-tracking/${id}`, {
    method: 'DELETE',
  });
  if (!response.ok) throw new Error('Failed to delete conversation SLA tracking');
}

export async function deleteConversationSLAEventLog(logId: string): Promise<void> {
  const response = await apiFetch(`/api/v1/sla-management/conversation-sla-tracking/event-logs/${logId}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to delete event log'));
  }
}

export interface SyncAssigneeResult {
  updated: boolean;
  message: string;
  assigned_to_id?: string;
  assigned_to?: string;
}

export async function syncAssigneeFromRespond(trackingId: string): Promise<SyncAssigneeResult> {
  const response = await apiFetch(`/api/v1/sla-management/conversation-sla-tracking/${trackingId}/sync-assignee`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Sync assignee failed'));
  }
  return response.json();
}

export interface ConversationSLATestOverridesBody {
  assigned_to_id?: string | null;
  current_tier_started_at?: string;
  initiated_at?: string;
  is_responded?: boolean;
  is_resolved?: boolean;
  agent_code?: string | null;
  team_set_code?: string | null;
}

export async function postConversationSLATestOverrides(
  trackingId: string,
  body: ConversationSLATestOverridesBody,
): Promise<{ message: string }> {
  const response = await apiFetch(`/api/v1/sla-management/conversation-sla-tracking/${trackingId}/test-overrides`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Update failed'));
  }
  return response.json();
}

export async function getSlaTrackingConversation(
  trackingId: string,
  params?: { limit?: number; cursor?: string },
): Promise<ConversationThreadPage> {
  const sp = new URLSearchParams();
  if (params?.limit != null) sp.set('limit', String(params.limit));
  if (params?.cursor) sp.set('cursor', params.cursor);
  const qs = sp.toString();
  const url = `/api/v1/sla-management/conversation-sla-tracking/${trackingId}/conversation${qs ? `?${qs}` : ''}`;
  const response = await apiFetch(url);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load conversation'));
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Thread scroll-back + in-thread search (UAC AC-L7 / AC-L8)
//
// Backend contract (GET .../{tracking_id}/conversation/page):
//   query: before | after | around (at most ONE, each a Respond message id) + limit (1..200)
//   body : { items, has_more_older, has_more_newer, oldest_message_id,
//            newest_message_id, anchor_message_id, source, backfilled, error }
//   items are ALWAYS oldest-to-newest, whichever direction was requested.
//
// GET .../{tracking_id}/conversation/search?q=&limit=
//   body : { items: [{ message_id, sent_at, direction, snippet }], total,
//            truncated, query } newest-first.
// ---------------------------------------------------------------------------

export async function getSlaTrackingConversationPage(
  trackingId: string,
  params: { before?: string; after?: string; around?: string; limit?: number },
): Promise<ConversationThreadPage> {
  const sp = new URLSearchParams();
  if (params.before) sp.set('before', params.before);
  if (params.after) sp.set('after', params.after);
  if (params.around) sp.set('around', params.around);
  if (params.limit != null) sp.set('limit', String(params.limit));
  const qs = sp.toString();
  const response = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/${trackingId}/conversation/page${qs ? `?${qs}` : ''}`,
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load earlier messages'));
  }
  return response.json();
}

export async function searchSlaTrackingConversation(
  trackingId: string,
  query: string,
  limit = 100,
): Promise<ConversationSearchMatch[]> {
  const sp = new URLSearchParams({ q: query, limit: String(limit) });
  const response = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/${trackingId}/conversation/search?${sp.toString()}`,
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Search failed'));
  }
  const body = (await response.json()) as { items?: ConversationSearchMatch[] };
  return body.items ?? [];
}

/**
 * Ticket-scoped byte proxy for a chat attachment (UAC AC-N4).
 *
 * GET .../{tracking_id}/media?url=<absolute url>
 *   -> the upstream bytes, upstream content-type, `Content-Disposition: inline`.
 *   Host allowlisted server-side (our storage + Respond media); 400 off it,
 *   413 over 50 MB, 404 for a viewer who cannot see the ticket.
 *
 * Returns the raw `Response` on purpose: the only consumer is
 * `AttachmentPreviewModal`'s `fetchBytes`, which wants to read `.blob()` /
 * `.arrayBuffer()` itself rather than be handed a copy of the payload.
 */
export async function fetchSlaTrackingMedia(
  trackingId: string,
  url: string,
): Promise<Response> {
  const response = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/${encodeURIComponent(trackingId)}/media?url=${encodeURIComponent(url)}`,
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Could not load this attachment'));
  }
  return response;
}

// ---------------------------------------------------------------------------
// My Team Tasks / Takeover / Reassign (team coverage & reassignment)
// ---------------------------------------------------------------------------

/**
 * One visible-team pending SLA task. Names are resolved server-side (no UUIDs
 * in UI). Extends `MyPendingSLAItem` rather than duplicating its field list -
 * the backend row builder (`ConversationSLATrackingService._pending_row`) is
 * now shared by `/my-pending` and `/team-pending`, so a team row carries the
 * exact same base contract (including `is_intervention_ticket`,
 * `enquiry_snippet`, `respond_io_id`, ...) plus the team-only context below.
 */
export interface TeamPendingItem extends MyPendingSLAItem {
  assignee_id: string;
  assignee_name: string;
  team_id: string;
  team_label: string;
}

export interface TeamPendingResponse {
  data: TeamPendingItem[];
  total: number;
  page: number;
  limit: number;
  empty: boolean;
}

export interface TeamPendingFilters {
  page?: number;
  limit?: number;
  assignee?: string;
  team?: string;
  query?: string;
}

/** Visible-team pending tasks (peers + descendant teams), excluding my own. */
export async function getTeamPendingSLA(filters: TeamPendingFilters = {}): Promise<TeamPendingResponse> {
  const { page = 1, limit = 50, assignee, team, query } = filters;
  const params = buildDataGridParams(
    { pageIndex: page - 1, pageSize: limit },
    { assignee, team, ...(query ? { query } : {}) },
  );
  const response = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/team-pending?${params.toString()}`,
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to fetch team tasks'));
  }
  return response.json();
}

/** Scope-B user picker source (members of teams I can see, excluding me). */
export interface VisibleUser {
  id: string;
  name: string;
  email: string;
  /**
   * AC-N7: whether a reply sent by this person can carry a real Respond sender
   * identity. Resolved server-side by the SAME helper the send path uses, so
   * the badge can never promise a linkage the send would find unusable. Comes
   * from this endpoint, NOT from the `user_management.users.view`-gated
   * user-select one - which is what makes it work for every holder of the
   * picker rather than only for user-admins.
   */
  respond_linked: boolean;
}

export async function getVisibleUsers(): Promise<VisibleUser[]> {
  const response = await apiFetch(
    '/api/v1/sla-management/conversation-sla-tracking/visible-users',
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to fetch visible users'));
  }
  const body = await response.json();
  return Array.isArray(body?.data) ? (body.data as VisibleUser[]) : [];
}

/** Result of initiating a takeover. With a cooldown configured the takeover is
 *  PENDING (a veto window); cooldown 0 or an unassigned task commits instantly. */
export interface TakeoverInitiateResult {
  committed: boolean;
  request?: TakeoverInfo;
  /** True when a takeover was already pending - `request` is the existing one (bar resumes). */
  already_pending?: boolean;
}

/** Initiate a takeover. 409 (already pending) is NOT an error - the existing request
 *  is returned so the UI shows the running countdown. */
export async function takeoverSLATracking(
  id: string,
  teamId: string,
): Promise<TakeoverInitiateResult> {
  const response = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/${id}/takeover`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ team_id: teamId }),
    },
  );
  if (response.status === 409) {
    const body = await response.json().catch(() => ({}));
    return { committed: false, already_pending: true, request: body?.request };
  }
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to take over task'));
  }
  return response.json();
}

/** Initiator (or admin) withdraws a pending takeover. */
export async function cancelTakeover(requestId: string): Promise<TakeoverInfo> {
  const response = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/takeover-requests/${requestId}/cancel`,
    { method: 'POST' },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to cancel takeover'));
  }
  return response.json();
}

/** Contested assignee (or admin) vetoes a pending takeover. */
export async function rejectTakeover(requestId: string): Promise<TakeoverInfo> {
  const response = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/takeover-requests/${requestId}/reject`,
    { method: 'POST' },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to reject takeover'));
  }
  return response.json();
}

/** Pin-fetch a single contested task + its latest takeover, for the My Pending banner. */
export interface TakeoverStateRow {
  id: string;
  source_entity_type: string | null;
  source_entity_id: string | null;
  is_form_sla: boolean;
  reference: string | null;
  due_at: string | null;
  current_tier: number | null;
  is_resolved: boolean;
  assigned_to_id: string | null;
  takeover: TakeoverInfo | null;
}

export async function getTakeoverState(trackingId: string): Promise<TakeoverStateRow> {
  const response = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/${trackingId}/takeover-state`,
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load takeover'));
  }
  return response.json();
}

/** Hand a task to a chosen user, keeping the original team/tier/clock. */
export async function reassignSLATracking(id: string, userId: string): Promise<void> {
  const response = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/${id}/reassign`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId }),
    },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to reassign task'));
  }
}

export async function postSlaTrackingConversationReply(
  trackingId: string,
  message: string,
): Promise<void> {
  const response = await apiFetch(
    `/api/v1/sla-management/conversation-sla-tracking/${trackingId}/conversation/reply`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to send message'));
  }
}
