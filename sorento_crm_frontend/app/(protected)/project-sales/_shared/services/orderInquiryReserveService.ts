import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

const BASE = '/api/v1/project-sales';

/**
 * Order inquiry: request CS to reserve stock (`PLAN-oi-request-cs-reserve.md` 3.2/3.3).
 *
 * A sibling of `orderInquiryService.ts`, its own file because this is a self-contained
 * sub-feature (request / cancel / reserve) rather than another verb on the row itself.
 */

export interface CreateReserveRequestRow {
  row_id: string;
  qty_requested: string | number;
  warehouse_id: string;
}

export interface CreateReserveRequestPayload {
  rows: CreateReserveRequestRow[];
  note?: string | null;
  /** Save the request without mailing CS; sent later from Order inquiries > Actions. */
  park?: boolean;
}

export interface OrderInquiryReserveRequestRow {
  id: string;
  row_id: string;
  item_code: string | null;
  qty_requested: string;
  warehouse_id: string | null;
  location: string | null;
  qty_reserved: string | null;
  reason: string | null;
}

export interface OrderInquiryReserveRequest {
  id: string;
  order_inquiry_id: string;
  ordinal: number;
  state: 'requested' | 'reserved' | 'cancelled';
  requested_by: string | null;
  requested_by_name: string | null;
  requested_at: string | null;
  note: string | null;
  reserved_by_name: string | null;
  reserved_at: string | null;
  cancelled_at: string | null;
  rows: OrderInquiryReserveRequestRow[];
  /** The first recipient the request mail actually named, for the dialog's own toast
   * (plan 3.7: "Request #2 sent to Eling"). Null when nobody is configured yet. */
  first_to_name: string | null;
}

/** Purchasing's own "Request CS to reserve" (AC-RS-1 to AC-RS-5). Quantities travel as
 * strings, as everywhere else in this domain - the caller may hand this a number (the
 * dialog's own number input), stringified here rather than trusted on the wire. */
export async function createOrderInquiryReserveRequest(
  inquiryId: string,
  payload: CreateReserveRequestPayload,
): Promise<OrderInquiryReserveRequest> {
  const requestBody = {
    ...payload,
    rows: payload.rows.map((row) => ({ ...row, qty_requested: String(row.qty_requested) })),
  };
  const response = await apiFetch(`${BASE}/order-inquiries/${inquiryId}/reserve-requests`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestBody),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to send that reserve request'));
  const body = await response.json();
  return { ...body, first_to_name: body.notified_name ?? null };
}

/** The requester's own undo while nothing has been reserved yet (AC-RS-19). Mostly
 * reached through the deferred action `order_inquiry_reserve_request.cancel`; kept as a
 * plain call too for a caller that wants it immediate. */
export async function cancelOrderInquiryReserveRequest(
  requestId: string,
): Promise<OrderInquiryReserveRequest> {
  const response = await apiFetch(`${BASE}/order-inquiries/reserve-requests/${requestId}/cancel`, {
    method: 'POST',
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to cancel that reserve request'));
  const body = await response.json();
  return { ...body, first_to_name: body.notified_name ?? null };
}

export interface OrderInquiryReserveHistoryEntry {
  kind: 'requested' | 'reserved' | 'unreserved' | 'cancelled' | string;
  qty: string | null;
  location: string | null;
  reason: string | null;
  actor_name: string | null;
  created_at: string | null;
}

export interface CommitReserveRow {
  row_id: string;
  /** Omitted when CS chose no location: the server falls back to the request row's
   * own default pool (6e.4, S8). */
  warehouse_id?: string;
  qty_reserved: string | number;
  reason?: string | null;
}

export interface CommitAmendRow {
  row_id: string;
  qty_reserved: string | number;
  reason?: string | null;
}

export interface CommitReservePayload {
  reserves: CommitReserveRow[];
  amendments: CommitAmendRow[];
  /** Write the answers without mailing purchasing; sent later from Order inquiries > Actions. */
  park_notice?: boolean;
}

/**
 * CS's own line-by-line commit (`PLAN-oi-request-cs-reserve.md` 6e.1, re-keyed by 6e.4).
 * One call per click of the Lines grid's own `Reserve (N)` CTA, keyed by the INQUIRY:
 * the server resolves each `reserves` row to its open request row and each
 * `amendments` row to its latest answered one, so the client never picks a request.
 * One transaction, one `order_inquiry_reserved` email per request touched. Returns the
 * touched requests.
 */
export async function commitOrderInquiryReserve(
  inquiryId: string,
  payload: CommitReservePayload,
): Promise<OrderInquiryReserveRequest[]> {
  const requestBody = {
    park_notice: payload.park_notice ?? false,
    reserves: payload.reserves.map((row) => ({ ...row, qty_reserved: String(row.qty_reserved) })),
    amendments: payload.amendments.map((row) => ({
      ...row,
      qty_reserved: String(row.qty_reserved),
    })),
  };
  const response = await apiFetch(`${BASE}/order-inquiries/${inquiryId}/reserve-commit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestBody),
  });
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to reserve those lines'));
  const body = await response.json();
  return (Array.isArray(body) ? body : []).map((item: OrderInquiryReserveRequest) => ({
    ...item,
    first_to_name: null,
  }));
}

/** F3: one row's own history, newest first - `ReserveLineHistoryDialog`'s own read. */
export async function getOrderInquiryRowHistory(
  requestId: string,
  rowId: string,
): Promise<OrderInquiryReserveHistoryEntry[]> {
  const response = await apiFetch(
    `${BASE}/order-inquiries/reserve-requests/${requestId}/rows/${rowId}/history`,
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load that history'));
  const body = await response.json();
  return Array.isArray(body) ? body : [];
}

export interface DecisionTrailEntry {
  kind: string;
  actor_name: string | null;
  at: string | null;
  detail: string | null;
}

/**
 * `PLAN-oi-decision-trail-ui.md` (round 2, AC-DT-10): the History icon's own read, keyed
 * by the CORE sales-order line - the id an OI row and a fulfilment-board line both point
 * at (`OrderInquiryWorklistRow.core_line_id`, `BoardContribution.line_id`), never by the
 * order inquiry or the board itself. Newest first.
 */
export async function getDecisionTrail(coreLineId: string): Promise<DecisionTrailEntry[]> {
  const response = await apiFetch(`${BASE}/sales-order-lines/${coreLineId}/decision-trail`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load that decision trail'));
  const body = await response.json();
  return Array.isArray(body?.entries) ? body.entries : [];
}

/** Every reserve request this header has ever raised, newest first - used to find
 * a row's own open request (or latest answered one) on the Lines grid. */
export async function getOrderInquiryReserveRequests(
  inquiryId: string,
): Promise<OrderInquiryReserveRequest[]> {
  const response = await apiFetch(`${BASE}/order-inquiries/${inquiryId}/reserve-requests`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load reserve requests'));
  const body = await response.json();
  return (Array.isArray(body) ? body : []).map((item: OrderInquiryReserveRequest) => ({
    ...item,
    first_to_name: null,
  }));
}

// ------------------------------------------------ park / send all parked (OI-STAGED-NOTIFY)

export type ReserveStatus = 'parked' | 'needs_reserve' | 'notice_parked' | 'reserved';

export interface ReserveStatusSummary {
  parked: number;
  needs_reserve: number;
  notice_parked: number;
  reserved: number;
  /** The caller's own parked items, counted in order inquiries. */
  my_parked_requests: number;
  my_parked_notices: number;
}

export interface ParkedPerson {
  name: string;
}

export interface ParkedEmailPreview {
  to: ParkedPerson;
  cc: ParkedPerson[];
  inquiry_count: number;
  line_count: number;
  subject: string;
  body_html: string;
}

export interface ParkedUnresolved {
  inquiry_count: number;
  line_count: number;
}

export interface ParkedPreview {
  emails: ParkedEmailPreview[];
  unresolved: ParkedUnresolved[];
}

export interface ParkedSendResult {
  emails_sent: number;
  items_sent: number;
  unresolved: ParkedUnresolved[];
}

export interface ReserveNoticeRecipients {
  to: ParkedPerson | null;
  cc: ParkedPerson[];
}

export async function getReserveStatusSummary(): Promise<ReserveStatusSummary> {
  const response = await apiFetch(`${BASE}/order-inquiries/reserve-status/summary`);
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load reserve status counts'));
  return response.json();
}

async function postParked<T>(path: string, fallback: string): Promise<T> {
  const response = await apiFetch(`${BASE}/order-inquiries/${path}`, { method: 'POST' });
  if (!response.ok) throw new Error(await extractApiError(response, fallback));
  return response.json();
}

export function previewParkedReserveRequests(): Promise<ParkedPreview> {
  return postParked('reserve-requests/parked/preview', 'Failed to load the parked requests');
}

export function sendParkedReserveRequests(): Promise<ParkedSendResult> {
  return postParked('reserve-requests/parked/send', 'Failed to send the parked requests');
}

export function previewParkedReserveNotices(): Promise<ParkedPreview> {
  return postParked('reserve-notices/parked/preview', 'Failed to load the parked reserves');
}

export function sendParkedReserveNotices(): Promise<ParkedSendResult> {
  return postParked('reserve-notices/parked/send', 'Failed to send the parked reserves');
}

/** Who the reserve confirmation's notice would go to (AC-SN-27). */
export async function getReserveNoticeRecipients(
  inquiryId: string,
  rowIds?: string[],
): Promise<ReserveNoticeRecipients> {
  const search = rowIds && rowIds.length > 0 ? `?${new URLSearchParams({ row_ids: rowIds.join(',') })}` : '';
  const response = await apiFetch(
    `${BASE}/order-inquiries/${inquiryId}/reserve-notices/recipients${search}`,
  );
  if (!response.ok)
    throw new Error(await extractApiError(response, 'Failed to load the notice recipients'));
  return response.json();
}
