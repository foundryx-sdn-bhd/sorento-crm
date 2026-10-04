import { apiFetch } from '@/lib/api';
import { buildDataGridParams } from '@/lib/api-client';
import type { Attachment } from '../types/attachment.types';
import type { DataGridApiFetchParams, DataGridApiResponse } from '@/components/ui/data-grid';

/**
 * Filterable list query for attachments - the DataGrid params plus the same
 * resource filters the list GET accepts. Reused by the list fetch AND by the
 * neighbours nav so the two cannot drift.
 */
export type AttachmentsListParams = DataGridApiFetchParams & {
  entity_type?: string;
  file_type?: string;
  attachment_type_id?: string;
  upload_date_from?: string;
  upload_date_to?: string;
  uploaded_at_from?: string;
  uploaded_at_to?: string;
  uploaded_by?: string;
  is_deleted?: boolean;
  virus_status?: string;
  directory_id?: string | null;
  link_status?: 'linked' | 'unlinked';
  storage_status?: 'accessible' | 'missing' | 'unchecked';
  resolve_signed_urls?: boolean;
  access_levels?: string[];
  access_levels_match?: 'any' | 'all' | 'exact';
  /** Exact mime type, e.g. `application/pdf`. */
  mime_type?: string;
  /** Several mime types. Unions with `mime_type`, the same way `attachment_type_ids` does. */
  mime_types?: string[];
  /** A company id, `shared`, or omitted for today's `IS NULL OR IN (scope)` result. */
  company?: string;
};


export async function getAttachments(params: AttachmentsListParams): Promise<DataGridApiResponse<Attachment>> {
  const { pageIndex, pageSize, sorting, searchQuery, entity_type, file_type, attachment_type_id, upload_date_from, upload_date_to, uploaded_at_from, uploaded_at_to, uploaded_by, is_deleted, virus_status, directory_id, link_status, storage_status, resolve_signed_urls, access_levels, access_levels_match, mime_type, mime_types, company } = params;
  const queryParams = buildDataGridParams(
    { pageIndex, pageSize, sorting, searchQuery },
    {
      entity_type,
      attachment_type_id: attachment_type_id || file_type,
      uploaded_by,
      uploaded_at_from: uploaded_at_from || upload_date_from,
      uploaded_at_to: uploaded_at_to || upload_date_to,
      is_deleted: is_deleted !== undefined ? String(is_deleted) : undefined,
      virus_status,
      directory_id: directory_id != null && directory_id !== '' ? directory_id : undefined,
      link_status,
      storage_status,
      resolve_signed_urls: resolve_signed_urls !== undefined ? String(resolve_signed_urls) : undefined,
      mime_type,
      company,
    },
  );
  // Repeated params, like access_levels: the backend unions `mime_types` with
  // `mime_type`. Absent means every type, which is what every caller that does
  // not ask for a filter must keep getting.
  if (mime_types && mime_types.length > 0) {
    for (const mime of mime_types) {
      if (mime) queryParams.append('mime_types', mime);
    }
  }
  if (access_levels && access_levels.length > 0) {
    for (const lvl of access_levels) {
      if (lvl) queryParams.append('access_levels', lvl);
    }
    if (access_levels_match) {
      queryParams.set('access_levels_match', access_levels_match);
    }
  }
  const response = await apiFetch(`/api/v1/resource-management/attachments?${queryParams.toString()}`);
  if (!response.ok) throw new Error('Failed to fetch attachments');
  return response.json();
}

export interface AttachmentCollisionCheck {
  collides: boolean;
  existing_attachment_id?: string;
  existing_file_name?: string;
}

/** Pre-upload check: is there a live attachment with this name in this folder? */
export async function checkAttachmentCollision(
  filename: string,
  directoryId?: string | null,
): Promise<AttachmentCollisionCheck> {
  const params = new URLSearchParams({ filename });
  if (directoryId) params.set('directory_id', directoryId);
  const response = await apiFetch(`/api/v1/resource-management/attachments/collision-check?${params.toString()}`);
  if (!response.ok) return { collides: false }; // fail-open: never block upload on a check error
  return response.json();
}

export type AttachmentConflictResolution = 'replace' | 'copy';

export interface AttachmentFilenameCollision {
  existing_attachment_id: string;
  existing_file_name: string;
  existing_target_entity_type?: string | null;
  existing_target_field_keys?: string[] | null;
}

export class AttachmentFilenameCollisionError extends Error {
  detail: AttachmentFilenameCollision;
  constructor(detail: AttachmentFilenameCollision) {
    super(`Attachment with name '${detail.existing_file_name}' already exists in this folder`);
    this.name = 'AttachmentFilenameCollisionError';
    this.detail = detail;
  }
}

export async function uploadAttachment(
  file: File,
  options: {
    attachmentTypeId?: string | null;
    entityType?: string;
    entityId?: string;
    accessLevels?: string[];
    /** Packing List uploads only: 'west' / 'east'. */
    regions?: string[];
    directoryId?: string | null;
    /** Field-linkage template: target table this doc describes. */
    targetEntityType?: string | null;
    /** Field-linkage template: list of field keys this doc answers. */
    targetFieldKeys?: string[] | null;
    /** Google-Drive style collision resolution. Omit to receive 409 + AttachmentFilenameCollisionError. */
    onConflict?: AttachmentConflictResolution;
    /** Shared per-submit UUID so the BE notification layer coalesces n8n callbacks into one email. */
    uploadBatchId?: string;
  }
): Promise<Attachment> {
  const {
    attachmentTypeId,
    entityType,
    entityId,
    accessLevels,
    regions,
    directoryId,
    targetEntityType,
    targetFieldKeys,
    onConflict,
    uploadBatchId,
  } = options;
  const formData = new FormData();
  formData.append('file', file);
  if (attachmentTypeId) formData.append('attachment_type_id', attachmentTypeId);
  if (entityType) formData.append('entity_type', entityType);
  if (entityId) formData.append('entity_id', entityId);
  if (directoryId) formData.append('directory_id', directoryId);
  if (accessLevels && accessLevels.length > 0) {
    formData.append('access_levels', JSON.stringify(accessLevels));
  }
  if (regions && regions.length > 0) {
    formData.append('regions', JSON.stringify(regions));
  }
  if (targetEntityType) {
    formData.append('target_entity_type', targetEntityType);
  }
  if (targetFieldKeys && targetFieldKeys.length > 0) {
    formData.append('target_field_keys', JSON.stringify(targetFieldKeys));
  }
  if (onConflict) {
    formData.append('on_conflict', onConflict);
  }
  if (uploadBatchId) {
    formData.append('upload_batch_id', uploadBatchId);
  }

  const response = await apiFetch('/api/v1/resource-management/attachments', {
    method: 'POST',
    body: formData,
  });

  if (response.status === 409) {
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const body = await response.json().catch(() => null);
      const detail = (body && typeof body === 'object' && 'detail' in body ? body.detail : body) as
        | AttachmentFilenameCollision
        | undefined;
      if (detail && typeof detail === 'object' && 'existing_attachment_id' in detail) {
        throw new AttachmentFilenameCollisionError(detail);
      }
    }
  }

  if (!response.ok) {
    const contentType = response.headers.get('content-type') || '';
    let message = 'Failed to upload attachment';
    try {
      if (contentType.includes('application/json')) {
        const error = await response.json();
        // FastAPI uses "detail" (string or array of validation errors)
        const detail = error.detail;
        if (typeof detail === 'string') {
          message = detail;
        } else if (Array.isArray(detail) && detail.length > 0) {
          const first = detail[0];
          message = typeof first === 'string' ? first : (first?.msg || first?.message || JSON.stringify(first));
        } else if (error.message) {
          message = error.message;
        }
      } else {
        const text = await response.text();
        if (text) message = text.slice(0, 200);
      }
    } catch {
      // ignore parse errors, use default message
    }
    if (response.status === 401) {
      message = message || 'Not signed in or session expired. Please sign in again.';
    } else if (response.status === 413) {
      message =
        'File too large. The server limits upload size (often 1MB by default). Try a smaller file or ask your admin to increase nginx client_max_body_size.';
    } else if (response.status >= 500) {
      message = message || 'Server error. Try again or contact support.';
    }
    throw new Error(message);
  }
  return response.json();
}

/**
 * Get the current Stock_List attachment (the one non-archived with type Stock_List). Returns null if none.
 */
export async function getCurrentStockListAttachment(): Promise<Attachment | null> {
  const response = await apiFetch('/api/v1/resource-management/attachments/current-stock-list');
  if (response.status === 404) return null;
  if (!response.ok) throw new Error('Failed to fetch Stock List attachment');
  return response.json();
}

/**
 * Replace the Stock_List attachment. Archives any existing one with type Stock_List, then uploads the new file (for n8n AI agent).
 */
export async function replaceLatestStockList(file: File): Promise<Attachment> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await apiFetch('/api/v1/resource-management/attachments/replace-latest-stock-list', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const contentType = response.headers.get('content-type') || '';
    let message = 'Failed to save Stock List attachment';
    try {
      if (contentType.includes('application/json')) {
        const error = await response.json();
        const detail = error.detail;
        message = typeof detail === 'string' ? detail : message;
      }
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  return response.json();
}

export interface BulkImportJobStarted {
  message: string;
  job_id: string;
  id: string;
}

export interface JobStatusResponse {
  job_id: string;
  status: string;
  progress?: {
    total: number;
    processed: number;
    successful: number;
    failed: number;
    skipped: number;
    percentage: number;
  };
  result?: {
    message?: string;
    directories_created?: number;
    attachments_created?: number;
    attachments?: Array<{ id: string; path: string }>;
    errors?: string[];
  };
  error?: string;
}

export async function bulkImportAttachments(
  zipFile: File,
  attachmentTypeId: string,
  accessLevels?: string[],
  parentDirectoryId?: string | null
): Promise<BulkImportJobStarted> {
  const formData = new FormData();
  formData.append('file', zipFile);
  formData.append('attachment_type_id', attachmentTypeId);
  if (accessLevels && accessLevels.length > 0) {
    formData.append('access_levels', JSON.stringify(accessLevels));
  }
  if (parentDirectoryId) {
    formData.append('parent_directory_id', parentDirectoryId);
  }

  const response = await apiFetch('/api/v1/resource-management/attachments/bulk-import', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const contentType = response.headers.get('content-type') || '';
    let message = 'Bulk import failed';
    try {
      if (contentType.includes('application/json')) {
        const error = await response.json();
        const detail = error.detail;
        message = typeof detail === 'string' ? detail : error.message ?? message;
      }
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  return response.json();
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const response = await apiFetch(`/api/v1/system/jobs/${jobId}/status`);
  if (!response.ok) throw new Error('Failed to fetch job status');
  return response.json();
}

export async function updateAttachment(
  attachmentId: string,
  data: {
    directory_id?: string | null;
    description?: string | null;
    access_levels?: string[] | null;
    /** User-facing display name (rename). The object key is uuid-segregated + immutable;
     *  only the UI label + Content-Disposition + n8n webhook filename change. */
    stored_filename?: string;
  }
): Promise<Attachment> {
  const response = await apiFetch(`/api/v1/resource-management/attachments/${attachmentId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to update attachment' }));
    const message = typeof error.detail === 'string' ? error.detail : error.message ?? 'Failed to update attachment';
    throw new Error(message);
  }
  return response.json();
}

export async function bulkMoveAttachments(
  attachmentIds: string[],
  directoryId: string | null,
): Promise<{ updated: number }> {
  const response = await apiFetch('/api/v1/resource-management/attachments/bulk-move', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ attachment_ids: attachmentIds, directory_id: directoryId }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to move attachments' }));
    const message = typeof error.detail === 'string' ? error.detail : error.message ?? 'Failed to move attachments';
    throw new Error(message);
  }
  return response.json();
}

export async function deleteAttachment(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/resource-management/attachments/${id}`, { method: 'DELETE' });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to delete attachment' }));
    throw new Error(error.detail ?? error.message ?? 'Failed to delete attachment');
  }
}

export async function archiveAttachment(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/resource-management/attachments/${id}/archive`, { method: 'POST' });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to archive attachment' }));
    throw new Error(error.detail ?? error.message ?? 'Failed to archive attachment');
  }
}

export async function bulkArchiveAttachments(ids: string[]): Promise<{ message: string; archived_count: number }> {
  const response = await apiFetch('/api/v1/resource-management/attachments/bulk-archive', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ attachment_ids: ids }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail ?? error.message ?? 'Failed to archive attachments');
  }
  return response.json();
}

export type AccessPropagationKind = 'product' | 'promotion' | 'form' | 'packing_list';

export interface AccessPropagationTarget {
  kind: AccessPropagationKind;
  entity_id: string;
  code: string;
  name?: string | null;
}

export async function previewBulkAccessLevels(body: {
  attachment_ids?: string[];
  directory_id?: string;
}): Promise<{ attachment_count: number; targets: AccessPropagationTarget[] }> {
  const response = await apiFetch('/api/v1/resource-management/attachments/bulk-access-levels/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Preview failed' }));
    const d = error.detail;
    const message =
      typeof d === 'string'
        ? d
        : typeof d === 'object' && d && 'message' in d
          ? String((d as { message?: string }).message)
          : error.message ?? 'Preview failed';
    throw new Error(message);
  }
  return response.json();
}

export async function applyBulkAccessLevels(body: {
  attachment_ids?: string[];
  directory_id?: string;
  access_levels: string[];
  propagate_to_linked: boolean;
}): Promise<{ updated_attachments: number; propagated?: Record<string, number> | null }> {
  const response = await apiFetch('/api/v1/resource-management/attachments/bulk-access-levels/apply', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Apply failed' }));
    const d = error.detail;
    const message =
      typeof d === 'string'
        ? d
        : typeof d === 'object' && d && 'message' in d
          ? String((d as { message?: string }).message)
          : error.message ?? 'Apply failed';
    throw new Error(message);
  }
  return response.json();
}

export async function reorderAttachments(
  attachmentIds: string[],
  directoryId?: string | null
): Promise<{ message: string; attachment_ids: string[] }> {
  const response = await apiFetch('/api/v1/resource-management/attachments/reorder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ attachment_ids: attachmentIds, directory_id: directoryId ?? null }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to reorder' }));
    const message = typeof error.detail === 'string' ? error.detail : error.message ?? 'Failed to reorder';
    throw new Error(message);
  }
  return response.json();
}

export async function bulkDeleteAttachments(ids: string[]): Promise<{ message: string; deleted_count: number }> {
  const response = await apiFetch('/api/v1/resource-management/attachments/bulk-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ attachment_ids: ids }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to delete attachments' }));
    throw new Error(error.message ?? error.detail ?? 'Failed to delete attachments');
  }
  return response.json();
}

export async function restoreAttachment(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/resource-management/attachments/${id}/restore`, { method: 'PUT' });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to restore attachment' }));
    throw new Error(error.message);
  }
}

export async function bulkRestoreAttachments(ids: string[]): Promise<{ restored_count: number }> {
  const results = await Promise.allSettled(ids.map((id) => restoreAttachment(id)));
  const restored = results.filter((r) => r.status === 'fulfilled').length;
  return { restored_count: restored };
}

export async function downloadAttachment(id: string): Promise<Blob> {
  const response = await apiFetch(`/api/v1/resource-management/attachments/${id}/download`);
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to download attachment' }));
    throw new Error(error.message);
  }
  return response.blob();
}

export async function getAttachmentMetadata(id: string): Promise<Attachment> {
  const response = await apiFetch(`/api/v1/resource-management/attachments/${id}/metadata`);
  if (!response.ok) throw new Error('Failed to fetch attachment metadata');
  return response.json();
}

export async function getAttachmentPreviewUrl(id: string): Promise<string> {
  const response = await apiFetch(`/api/v1/resource-management/attachments/${id}/preview-url`);
  if (!response.ok) throw new Error('Failed to fetch attachment preview URL');
  const data = await response.json();
  return data.preview_url;
}

export async function checkDuplicateByHash(hash: string): Promise<Attachment | null> {
  const response = await apiFetch(`/api/v1/resource-management/attachments/hash/${hash}`);
  if (!response.ok) return null;
  return response.json();
}

export async function resubmitAttachmentWebhook(id: string): Promise<{ message: string; integration_log_id: string }> {
  const response = await apiFetch(`/api/v1/resource-management/attachments/${id}/resubmit`, {
    method: 'POST',
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Failed to resubmit webhook' }));
    throw new Error(error.detail || error.message || 'Failed to resubmit webhook');
  }
  return response.json();
}

export async function linkAttachmentToPackingList(attachmentId: string, packingListId: string): Promise<{ message: string; link_id: string }> {
  const response = await apiFetch(`/api/v1/resource-management/attachments/${attachmentId}/link-packing-list`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ packing_list_id: packingListId }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to link' }));
    throw new Error(typeof error.detail === 'string' ? error.detail : error.message ?? 'Failed to link to packing list');
  }
  return response.json();
}

export async function deleteAttachmentLink(linkId: string, entityType: string): Promise<void> {
  const response = await apiFetch(
    `/api/v1/resource-management/attachments/links/${linkId}?entity_type=${encodeURIComponent(entityType)}`,
    { method: 'DELETE' }
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to unlink' }));
    throw new Error(typeof error.detail === 'string' ? error.detail : error.message ?? 'Failed to unlink');
  }
}

/** Unlink a packing list from an attachment when the link is only via the packing list's attachment_id (no link_id). */
export async function unlinkPackingListFromAttachment(attachmentId: string, packingListId: string): Promise<void> {
  const response = await apiFetch(
    `/api/v1/resource-management/attachments/${attachmentId}/unlink-packing-list`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ packing_list_id: packingListId }),
    }
  );
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to unlink' }));
    throw new Error(typeof error.detail === 'string' ? error.detail : error.message ?? 'Failed to unlink packing list');
  }
}
