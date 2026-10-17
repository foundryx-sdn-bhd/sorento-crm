import { apiFetch } from '@/lib/api';
import type { DeferredFormAction } from '@/app/(protected)/sla-management/_shared/formAction';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type {
  Complaint,
  ComplaintFormData,
  ComplaintDetail,
} from '../types/complaint.types';
import type {
  DataGridApiFetchParams,
  DataGridApiResponse,
} from '@/components/ui/data-grid';

export type ComplaintsListParams = DataGridApiFetchParams & {
  assigned_to?: string;
  status?: string;
  /** Match complaints whose root cause is ANY of these ids (list filter + the
   *  linked-complaints grid on a root cause detail page). */
  root_cause_ids?: string[];
  /** Same, for resolutions. */
  resolution_ids?: string[];
  /** Only this project's complaints (the project page's Forms tab). */
  project_id?: string;
};


/** Serialize the list filters the same way for the list GET and the neighbours GET,
 *  so the backend can never see a different filter set for the two. */
export function complaintListExtraParams(params: ComplaintsListParams) {
  return {
    assigned_to: params.assigned_to,
    status: params.status,
    root_cause_ids: params.root_cause_ids?.length
      ? params.root_cause_ids.join(',')
      : undefined,
    resolution_ids: params.resolution_ids?.length
      ? params.resolution_ids.join(',')
      : undefined,
    project_id: params.project_id,
  };
}

export async function getComplaints(
  params: ComplaintsListParams,
): Promise<DataGridApiResponse<Complaint>> {
  const queryParams = buildDataGridParams(params, complaintListExtraParams(params));
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints?${queryParams.toString()}`,
  );
  if (!response.ok) throw new Error('Failed to fetch complaints');
  return response.json();
}

export async function getComplaint(id: string): Promise<ComplaintDetail> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${id}`,
  );
  if (!response.ok) throw new Error('Failed to fetch complaint');
  return response.json();
}

export async function createComplaint(
  data: ComplaintFormData,
): Promise<Complaint> {
  const response = await apiFetch('/api/v1/complaints-management/complaints', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to create complaint' }));
    throw new Error(error.message);
  }
  return response.json();
}

export async function updateComplaint(
  id: string,
  data: Partial<ComplaintFormData>,
): Promise<Complaint> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${id}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to update complaint' }));
    throw new Error(error.message);
  }
  return response.json();
}

export async function updateComplaintAndReply(
  id: string,
  data: Partial<ComplaintFormData>,
): Promise<Complaint> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${id}/update-and-reply`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to update and reply' }));
    throw new Error(error.detail || error.message || 'Failed to update and reply');
  }
  return response.json();
}

export async function approveComplaint(id: string): Promise<Complaint | DeferredFormAction> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${id}/approve`,
    { method: 'POST' },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to approve complaint' }));
    throw new Error(error.detail || error.message || 'Failed to approve complaint');
  }
  return response.json();
}

export async function rejectComplaint(
  id: string,
  rejection_reason: string,
): Promise<Complaint | DeferredFormAction> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${id}/reject`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rejection_reason }),
    },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to reject complaint' }));
    throw new Error(error.detail || error.message || 'Failed to reject complaint');
  }
  return response.json();
}

export async function processComplaintByCs(
  id: string,
  note?: string,
): Promise<Complaint | DeferredFormAction> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${id}/process`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note: note?.trim() || null }),
    },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to mark complaint processed by CS' }));
    throw new Error(
      error.detail || error.message || 'Failed to mark complaint processed by CS',
    );
  }
  return response.json();
}

export async function closeComplaint(
  id: string,
  note?: string,
): Promise<Complaint | DeferredFormAction> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${id}/close`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note: note?.trim() || null }),
    },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to close complaint' }));
    throw new Error(error.detail || error.message || 'Failed to close complaint');
  }
  return response.json();
}

export interface ComplaintExportDownload {
  id: string;
  kind: string;
  status: string;
  filename?: string | null;
}

export async function exportComplaintPdf(
  id: string,
): Promise<ComplaintExportDownload> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${id}/export/pdf`,
    { method: 'POST' },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to start PDF export' }));
    throw new Error(error.detail || error.message || 'Failed to start PDF export');
  }
  return response.json();
}

export async function notifyComplaintRootCause(id: string): Promise<Complaint> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${id}/notify-root-cause`,
    { method: 'POST' },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to notify salesperson on root cause' }));
    throw new Error(
      error.detail || error.message || 'Failed to notify salesperson on root cause',
    );
  }
  return response.json();
}

export async function notifyComplaintResolution(id: string): Promise<Complaint> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${id}/notify-resolution`,
    { method: 'POST' },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to notify salesperson on resolution' }));
    throw new Error(
      error.detail || error.message || 'Failed to notify salesperson on resolution',
    );
  }
  return response.json();
}

export async function deleteComplaint(id: string): Promise<void> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${id}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to delete complaint' }));
    throw new Error(error.message);
  }
}

export async function bulkDeleteComplaints(
  ids: string[],
): Promise<{ message: string; deleted_count: number }> {
  const response = await apiFetch(
    '/api/v1/complaints-management/complaints/bulk',
    {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to bulk delete complaints' }));
    const msg =
      typeof error.detail === 'object' && error.detail?.message
        ? error.detail.message
        : error.message || 'Failed to bulk delete complaints';
    throw new Error(msg);
  }
  return response.json();
}

export async function linkComplaintAttachment(
  complaintId: string,
  attachmentId: string,
): Promise<{ message: string; link_id: string }> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${complaintId}/attachments`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ attachment_id: attachmentId }),
    },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to link attachment' }));
    throw new Error(error.message);
  }
  return response.json();
}

export async function deleteComplaintAttachment(linkId: string): Promise<void> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/attachments/${linkId}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: 'Failed to unlink attachment' }));
    throw new Error(error.message);
  }
}

export interface ResponseAttachmentUploadResult {
  link_id: string;
  attachment_id: string;
  filename: string;
  size: number;
  url: string;
  content_type: string;
}

/**
 * Upload ONE file as a staff "technical team response" attachment (its own
 * `response_attachment` type/quota, separate from the contact's own uploads).
 * Called once per file - loop for multiple.
 */
export async function uploadComplaintResponseAttachment(
  complaintId: string,
  file: File,
): Promise<ResponseAttachmentUploadResult> {
  const formData = new FormData();
  formData.append('file', file);
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${complaintId}/response-attachments`,
    { method: 'POST', body: formData },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to upload attachment'));
  }
  return response.json();
}

export async function deleteComplaintResponseAttachment(linkId: string): Promise<void> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/response-attachments/${linkId}`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to unlink attachment'));
  }
}

export interface SyncAssigneeResult {
  updated: boolean;
  message: string;
  assigned_to?: string;
  assigned_to_id?: string;
}

export async function syncComplaintAssigneeFromRespond(
  complaintId: string,
): Promise<SyncAssigneeResult> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${complaintId}/sync-assignee`,
    { method: 'POST' },
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Sync assignee failed' }));
    const msg =
      typeof err.detail === 'object' && err.detail?.message
        ? err.detail.message
        : err.detail || 'Sync assignee failed';
    throw new Error(msg);
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

export async function getComplaintConversation(
  complaintId: string,
  params?: { limit?: number; cursor?: string },
): Promise<RespondConversationResponse> {
  const sp = new URLSearchParams();
  if (params?.limit != null) sp.set('limit', String(params.limit));
  if (params?.cursor) sp.set('cursor', params.cursor);
  const qs = sp.toString();
  const url = `/api/v1/complaints-management/complaints/${complaintId}/conversation${qs ? `?${qs}` : ''}`;
  const response = await apiFetch(url);
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || err.message || 'Failed to load conversation');
  }
  return response.json();
}

export interface ViewLinkResponse {
  view_token: string;
  view_url: string;
}

export async function getOrCreateComplaintViewLink(
  complaintId: string,
  baseUrl?: string,
): Promise<ViewLinkResponse> {
  const response = await apiFetch(
    `/api/v1/complaints-management/complaints/${complaintId}/view-link`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(baseUrl != null ? { base_url: baseUrl } : {}),
    },
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Failed to get view link' }));
    const msg = typeof err.detail === 'string' ? err.detail : err.message || 'Failed to get view link';
    throw new Error(msg);
  }
  return response.json();
}

/** Strip legacy composed customer message so the CRM shows only technician wording (matches backend). */
export function displayComplaintTechnicalResponse(text: string | null | undefined): string {
  const s = (text ?? '').trim();
  if (!s.startsWith('There has been an update regarding your complaint')) {
    return s;
  }
  const idx = s.lastIndexOf(': ');
  if (idx === -1) return s;
  return s.slice(idx + 2).trim();
}

export { searchProjectsForLink } from '@/services/projectLinkService';
