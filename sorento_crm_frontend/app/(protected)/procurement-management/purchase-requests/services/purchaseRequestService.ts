import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import {
  isDeferredFormAction,
  type DeferredFormAction,
} from '@/app/(protected)/sla-management/_shared/formAction';
import type {
  PurchaseRequest,
  PurchaseRequestFormData,
  PurchaseRequestDetail,
  SendApprovalLinkRequest,
  SendApprovalLinkResponse,
} from '../types/purchaseRequest.types';

export interface UserForSelect {
  id: string;
  name: string | null;
  email: string;
}

export async function getUsersForApproverSelect(): Promise<UserForSelect[]> {
  const response = await apiFetch('/api/user-management/users/select');
  if (!response.ok) throw new Error('Failed to fetch users');
  return response.json();
}
import type {
  DataGridApiFetchParams,
  DataGridApiResponse,
} from '@/components/ui/data-grid';
import type { FormRevisionEntry } from '@/components/common/RevisionTimeline';
import type { FormPdfExportOptions } from '@/lib/revision-export';

/**
 * List query shape for purchase requests / sponsorship forms. The snake_case
 * filter keys (`request_type`, `approval_status`, `assigned_to`) match the
 * backend list GET params exactly, so the neighbours hook can forward them.
 */
export type PurchaseRequestsListParams = DataGridApiFetchParams & {
  request_type?: string;
  approval_status?: string;
  assigned_to?: string;
};


export async function getPurchaseRequests(
  params: DataGridApiFetchParams & {
    requestType?: string;
    approvalStatus?: string;
    assignedTo?: string;
    /** Only this project's records (the project page's Forms tab). */
    projectId?: string;
  },
): Promise<DataGridApiResponse<PurchaseRequest>> {
  const {
    pageIndex,
    pageSize,
    sorting,
    searchQuery,
    requestType,
    approvalStatus,
    assignedTo,
    projectId,
  } = params;
  const sortField = sorting?.[0]?.id || 'request_date';
  const sortDirection = sorting?.[0]?.desc ? 'desc' : 'asc';
  const queryParams = new URLSearchParams({
    page: String(pageIndex + 1),
    limit: String(pageSize),
    sort: sortField,
    dir: sortDirection,
    ...(searchQuery ? { query: searchQuery } : {}),
    ...(requestType ? { request_type: requestType } : {}),
    ...(approvalStatus ? { approval_status: approvalStatus } : {}),
    ...(assignedTo ? { assigned_to: assignedTo } : {}),
    ...(projectId ? { project_id: projectId } : {}),
  });
  const response = await apiFetch(
    `/api/v1/procurement/purchase-requests?${queryParams.toString()}`,
  );
  if (!response.ok) throw new Error('Failed to load records');
  return response.json();
}

export async function getPurchaseRequest(id: string): Promise<PurchaseRequestDetail> {
  const response = await apiFetch(`/api/v1/procurement/purchase-requests/${id}`);
  if (!response.ok) throw new Error('Failed to load record');
  return response.json();
}

/**
 * Revision lineage for the office Revisions panel (UAC H2/H3).
 *
 * Contract:
 *   GET /api/v1/procurement/purchase-requests/{id}/revisions
 *   Serves BOTH purchase requests and sponsorship forms: the backend reads the
 *   header's own `request_type`, so the caller never has to say which it is.
 *   Auth: the existing purchase request view permission.
 *   200: { items: FormRevisionEntry[] } - oldest first, each entry carrying what
 *        changed since the version before it plus the voided-stage context.
 *   Read-only: the office never creates, edits or deletes a revision (UAC H5).
 */
export async function getPurchaseRequestRevisions(
  id: string,
): Promise<FormRevisionEntry[]> {
  const response = await apiFetch(
    `/api/v1/procurement/purchase-requests/${id}/revisions`,
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load revisions'));
  }
  const data = await response.json();
  return (data?.items ?? []) as FormRevisionEntry[];
}

export async function linkPurchaseRequestAttachment(
  requestId: string,
  attachmentId: string,
): Promise<{ id: string; purchase_request_id: string; attachment_id: string }> {
  const response = await apiFetch(
    `/api/v1/procurement/purchase-requests/${requestId}/attachments`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ attachment_id: attachmentId }),
    },
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Failed to link attachment' }));
    throw new Error(typeof err.detail === 'string' ? err.detail : err.message || 'Failed to link attachment');
  }
  return response.json();
}

export async function deletePurchaseRequestAttachment(linkId: string): Promise<void> {
  const response = await apiFetch(
    `/api/v1/procurement/purchase-requests/attachments/${linkId}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Failed to unlink attachment' }));
    throw new Error(typeof err.detail === 'string' ? err.detail : err.message || 'Failed to unlink attachment');
  }
}

function toRequestBody(data: PurchaseRequestFormData) {
  const products = (data.products ?? []).map((p) => ({
    item_code: p.item_code ?? null,
    quantity: p.quantity ?? null,
    remark: p.remark ?? null,
    unit_price: p.unit_price ?? null,
    total: p.total ?? null,
  }));
  return {
    request_type: data.request_type,
    request_number: data.request_number ?? null,
    request_date: data.request_date || null,
    customer_name: data.customer_name || null,
    customer_id: data.customer_id ?? null,
    pic: data.pic ?? null,
    project_title: data.project_title || null,
    project_id: data.project_id || null,
    purpose: data.purpose ?? null,
    delivery_address: data.delivery_address ?? null,
    total_project_value: data.total_project_value ?? null,
    total_project_value_text: data.total_project_value_text ?? null,
    sales_type: data.sales_type ?? null,
    sponsor_subject: data.sponsor_subject ?? null,
    sponsor_subject_other: data.sponsor_subject_other ?? null,
    expected_delivery_date: data.expected_delivery_date || null,
    expected_po_date: data.expected_po_date ?? data.expected_po_date_text ?? null,
    expected_po_date_text: data.expected_po_date_text || null,
    requested_by: data.requested_by || null,
    requested_by_contact_id: data.requested_by_contact_id ?? null,
    requested_at: data.requested_at || null,
    products,
  };
}

export async function createPurchaseRequest(
  data: PurchaseRequestFormData,
): Promise<PurchaseRequest> {
  const response = await apiFetch('/api/v1/procurement/purchase-requests', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(toRequestBody(data)),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to create request' }));
    throw new Error(error.detail || error.message);
  }
  return response.json();
}

export async function updatePurchaseRequest(
  id: string,
  data: Partial<PurchaseRequestFormData>,
): Promise<PurchaseRequest> {
  const body = toRequestBody(data as PurchaseRequestFormData);
  const response = await apiFetch(`/api/v1/procurement/purchase-requests/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to update request' }));
    throw new Error(error.detail || error.message);
  }
  return response.json();
}

export async function deletePurchaseRequest(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/procurement/purchase-requests/${id}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to delete request' }));
    throw new Error(error.detail || error.message);
  }
}

export interface BulkDeletePurchaseRequestsResponse {
  message: string;
  deleted_count: number;
}

export async function bulkDeletePurchaseRequests(
  ids: string[],
): Promise<BulkDeletePurchaseRequestsResponse> {
  const response = await apiFetch('/api/v1/procurement/purchase-requests/bulk', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to bulk delete' }));
    throw new Error(error.detail || error.message);
  }
  return response.json();
}

export async function sendApprovalLink(
  id: string,
  data: SendApprovalLinkRequest,
): Promise<SendApprovalLinkResponse> {
  const response = await apiFetch(
    `/api/v1/procurement/purchase-requests/${id}/send-approval-link`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        approver_email: data.approver_email ?? undefined,
        approver_user_id: data.approver_user_id ?? undefined,
        expires_hours: data.expires_hours ?? 24,
        send_email: data.send_email ?? false,
        base_url: data.base_url ?? undefined,
      }),
    },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to create approval link' }));
    throw new Error(error.detail || error.message);
  }
  return response.json();
}

/**
 * In-system approve/reject of a pending-approval PR / sponsorship form (the form's
 * Approve / Reject buttons). Behaves identically to the public approval link.
 * Reject requires a reason (`comments`).
 */
// The 202 contract has ONE definition - the shared module the complaint, stock-inquiry
// and ticket services already import. These aliases keep this service's existing import
// surface working without a second copy of the shape that can drift.
export type DeferredApprovalDecision = DeferredFormAction;
export const isDeferredDecision = isDeferredFormAction;

export async function submitApprovalDecision(
  id: string,
  action: 'approved' | 'rejected',
  comments?: string,
): Promise<PurchaseRequest | DeferredApprovalDecision> {
  const response = await apiFetch(
    `/api/v1/procurement/purchase-requests/${id}/approval-decision`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, comments: comments || undefined }),
    },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, `Failed to ${action === 'approved' ? 'approve' : 'reject'} request`));
  }
  return response.json();
}

export async function rejectSubmittedPurchaseRequest(
  id: string,
  rejectionReason: string,
): Promise<PurchaseRequest | DeferredApprovalDecision> {
  const response = await apiFetch(
    `/api/v1/procurement/purchase-requests/${id}/reject-submitted`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rejection_reason: rejectionReason }),
    },
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({ message: 'Failed to reject submission' }));
    const detail = err.detail;
    const msg =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join(' ')
          : typeof detail === 'object' && detail !== null && 'message' in detail
            ? String((detail as { message?: string }).message)
            : err.message || 'Failed to reject submission';
    throw new Error(msg);
  }
  return response.json();
}

export async function setPendingApproval(id: string): Promise<PurchaseRequest | DeferredApprovalDecision> {
  const response = await apiFetch(
    `/api/v1/procurement/purchase-requests/${id}/set-pending-approval`,
    { method: 'POST' },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to set pending approval' }));
    throw new Error(error.detail || error.message);
  }
  return response.json();
}

async function finalizeRequestByCs(
  id: string,
  action: 'process' | 'close',
  note?: string,
): Promise<PurchaseRequest | DeferredApprovalDecision> {
  const response = await apiFetch(
    `/api/v1/procurement/purchase-requests/${id}/${action}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note: note?.trim() || null }),
    },
  );
  if (!response.ok) {
    const err = await response
      .json()
      .catch(() => ({ message: 'Failed to update request' }));
    throw new Error(err.detail || err.message || 'Failed to update request');
  }
  return response.json();
}

export function processPurchaseRequestByCs(
  id: string,
  note?: string,
): Promise<PurchaseRequest | DeferredApprovalDecision> {
  return finalizeRequestByCs(id, 'process', note);
}

export function closePurchaseRequestByCs(
  id: string,
  note?: string,
): Promise<PurchaseRequest | DeferredApprovalDecision> {
  return finalizeRequestByCs(id, 'close', note);
}

export interface ViewLinkResponse {
  view_token: string;
  view_url: string;
}

export async function getOrCreateViewLink(
  id: string,
  baseUrl?: string,
): Promise<ViewLinkResponse> {
  const response = await apiFetch(
    `/api/v1/procurement/purchase-requests/${id}/view-link`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: baseUrl ?? undefined }),
    },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to get view link' }));
    throw new Error(error.detail || error.message);
  }
  return response.json();
}

export interface PurchaseRequestUpdateAndReplyData {
  request_number?: string | null;
  reply_message?: string | null;
  /** Full form payload for update-and-reply (from edit form). */
  formData?: Partial<PurchaseRequestFormData>;
}

export async function updatePurchaseRequestAndReply(
  id: string,
  data: PurchaseRequestUpdateAndReplyData,
): Promise<PurchaseRequest> {
  const body: Record<string, unknown> = { reply_message: data.reply_message ?? null };
  if (data.formData) {
    Object.assign(body, toRequestBody(data.formData as PurchaseRequestFormData));
  } else if (data.request_number !== undefined) {
    body.request_number = data.request_number ?? null;
  }
  const response = await apiFetch(
    `/api/v1/procurement/purchase-requests/${id}/update-and-reply`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to update and reply' }));
    throw new Error(error.detail || error.message);
  }
  return response.json();
}

export interface RespondMessageItem {
  messageId?: number;
  channelMessageId?: string;
  contactId?: number;
  channelId?: number;
  traffic?: string;
  message?: { type?: string; text?: string; messageTag?: string };
  status?: Array<{ value?: string; timestamp?: number; message?: string }>;
  sender?: { source?: string; userId?: number; teamId?: number };
}

export interface RespondConversationResponse {
  items: RespondMessageItem[];
  pagination?: { next?: string; previous?: string };
  error?: string;
  contact?: { name?: string | null; phone?: string | null } | null;
}

export async function getPurchaseRequestConversation(
  requestId: string,
  params?: { limit?: number; cursor?: string },
): Promise<RespondConversationResponse> {
  const sp = new URLSearchParams();
  if (params?.limit != null) sp.set('limit', String(params.limit));
  if (params?.cursor) sp.set('cursor', params.cursor);
  const qs = sp.toString();
  const url = `/api/v1/procurement/purchase-requests/${requestId}/conversation${qs ? `?${qs}` : ''}`;
  const response = await apiFetch(url);
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || err.message || 'Failed to load conversation');
  }
  return response.json();
}

/**
 * Project options for the PR / sponsorship-form project picker (AC-L3).
 *
 * Re-exported from the complaint feature's implementation rather than copied: both forms
 * link to the same registered projects with the same label rule (code + title, never a UUID),
 * and two copies would drift the moment one of them starts showing the developer.
 */
export { searchProjectsForLink } from '@/app/(protected)/complaint-management/complaints/services/complaintService';

/**
 * Queue a printable Purchase Request / Sponsorship Form PDF.
 *
 * Exists because the Excel export auto-sizes its columns, so a long delivery
 * address made the printed sheet unusable. The PDF is fixed-layout.
 *
 * Contract:
 *   POST /api/v1/procurement/purchase-requests/{id}/export/pdf
 *   Body: OPTIONAL. Omitted body == the current form, as it has always been.
 *     { revision_id?: string }    - print ONE stored version (round 6, 6.3)
 *     { include_revisions?: true } - current form + the whole lineage (6.4)
 *     The two are mutually exclusive; sending both 400s.
 *   200: { download_id, status: 'queued' }
 *   Rendered by the RQ worker; surfaces in My Downloads.
 */
export async function exportPurchaseRequestPdf(
  id: string,
  options?: FormPdfExportOptions | null,
): Promise<{ download_id: string; status: string }> {
  // No options, no body: the request stays byte-identical to the one this export
  // has always sent.
  const init: RequestInit = options
    ? {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(options),
      }
    : { method: 'POST' };
  const response = await apiFetch(
    `/api/v1/procurement/purchase-requests/${id}/export/pdf`,
    init,
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to start PDF export'));
  }
  return response.json();
}
