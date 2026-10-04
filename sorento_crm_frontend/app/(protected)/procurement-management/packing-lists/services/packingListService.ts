import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type {
  PackingList,
  PackingListDetail,
  PackingListFormData,
} from '../types/packingList.types';
import type {
  DataGridApiFetchParams,
  DataGridApiResponse,
} from '@/components/ui/data-grid';

export type PackingListsListParams = DataGridApiFetchParams & {
  supplier_id?: string;
  shipment_status?: string;
  /** 'west' | 'east': packing lists serving that region. */
  region?: string;
};


export async function getPackingLists(
  params: PackingListsListParams,
): Promise<DataGridApiResponse<PackingList>> {
  const {
    pageIndex,
    pageSize,
    sorting,
    searchQuery,
    supplier_id,
    shipment_status,
    region,
  } = params;
  const sortField = sorting?.[0]?.id || '';
  const sortDirection = sorting?.[0]?.desc ? 'desc' : 'asc';
  const queryParams = new URLSearchParams({
    page: String(pageIndex + 1),
    limit: String(pageSize),
    ...(sortField ? { sort: sortField, dir: sortDirection } : {}),
    ...(searchQuery ? { query: searchQuery } : {}),
    ...(supplier_id ? { supplier_id } : {}),
    ...(shipment_status ? { shipment_status } : {}),
    ...(region ? { region } : {}),
  });
  const response = await apiFetch(
    `/api/v1/procurement/packing-lists?${queryParams.toString()}`,
  );
  if (!response.ok) throw new Error('Failed to fetch packing lists');
  return response.json();
}

export async function getPackingList(id: string): Promise<PackingListDetail> {
  const response = await apiFetch(`/api/v1/procurement/packing-lists/${id}`);
  if (!response.ok) throw new Error('Failed to fetch packing list');
  return response.json();
}

export async function createPackingList(
  data: PackingListFormData,
): Promise<PackingList> {
  const response = await apiFetch('/api/v1/procurement/packing-lists', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to create packing list'));
  }
  return response.json();
}

export async function updatePackingList(
  id: string,
  data: Partial<PackingListFormData>,
): Promise<PackingList> {
  const response = await apiFetch(`/api/v1/procurement/packing-lists/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to update packing list'));
  }
  return response.json();
}

export async function deletePackingList(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/procurement/packing-lists/${id}`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to delete packing list'));
  }
}

export async function bulkDeletePackingLists(
  ids: string[],
): Promise<{ message: string; deleted_count: number }> {
  const response = await apiFetch('/api/v1/procurement/packing-lists/bulk', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to bulk delete packing lists'));
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Container status import
//
// The workbook is parsed SERVER-SIDE. It holds 9 header blocks across 5 tabs,
// so the client-side first-sheet parse the shared upload dialog does by default
// would see one block and miss most of the 407 rows. Both calls therefore post
// the raw file and ignore the dialog's parsed rows.
//
//   POST /api/v1/procurement/packing-lists/container-status-import
//     multipart { file }
//     202 { message, job_id, queued: true }
//   POST  ...?validate_only=true
//     200 { valid, errors[], warnings[],
//           summary: { total_rows, would_update, would_create, error_count } }
//
// Those four summary keys are not free choice: TemplateUploadDialog renders
// exactly them and silently drops anything else.
// ---------------------------------------------------------------------------

const CONTAINER_STATUS_IMPORT_PATH =
  '/api/v1/procurement/packing-lists/container-status-import';

export interface ContainerStatusValidateResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
  summary: {
    total_rows: number;
    would_update: number;
    would_create: number;
    error_count: number;
  };
}

export interface ContainerStatusImportQueued {
  message: string;
  job_id: string;
  queued: boolean;
}

export async function validateContainerStatusImport(
  file: File,
): Promise<ContainerStatusValidateResult> {
  const formData = new FormData();
  formData.append('file', file);
  const response = await apiFetch(`${CONTAINER_STATUS_IMPORT_PATH}?validate_only=true`, {
    method: 'POST',
    body: formData,
  });
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Could not validate the Container Status workbook'),
    );
  }
  return response.json();
}

export async function importContainerStatus(
  file: File,
): Promise<ContainerStatusImportQueued> {
  const formData = new FormData();
  formData.append('file', file);
  const response = await apiFetch(CONTAINER_STATUS_IMPORT_PATH, {
    method: 'POST',
    body: formData,
  });
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Could not queue the Container Status import'),
    );
  }
  return response.json();
}

/**
 * Clearance checkpoint definitions for the timeline.
 *
 * Configuration, not data: which checkpoints exist, their labels, captions,
 * grouping, order and colour all live in `statuses` under entity_type
 * `inbound_shipment` and are admin-editable in
 * System Management -> Status Graphs. `field` is the date column on the packing
 * list payload the checkpoint reads, and is frozen server-side so renaming a
 * checkpoint can never break the link to its column.
 *
 *   GET /api/v1/procurement/packing-lists/clearance-checkpoints
 *   200 { checkpoints: [{ field, label, caption, color, sort_order }] }
 *
 * One flat ordered list - no grouping. Group names would have to be a hardcoded
 * map here, which leaves an admin-added checkpoint with nowhere to belong (D35).
 */
export interface ClearanceCheckpoint {
  field: string;
  label: string;
  caption: string | null;
  color: string | null;
  sort_order: number;
}

export async function getClearanceCheckpoints(): Promise<ClearanceCheckpoint[]> {
  const response = await apiFetch(
    '/api/v1/procurement/packing-lists/clearance-checkpoints',
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(response, 'Could not load the clearance checkpoints'),
    );
  }
  const body = await response.json();
  return body.checkpoints ?? [];
}

/**
 * A signed link to the most recently imported Container Status workbook.
 *
 * The import retains the original upload against the job row, which is owner-only
 * and buried in Import Job Details - so a colleague who did not run the import
 * could not get the sheet. This serves the latest one to anyone who can see
 * packing lists.
 */
export interface LatestContainerStatusDocument {
  attachment_id: string;
  url: string;
  filename: string;
  size: number | null;
  uploaded_at: string | null;
  /** The company that owns this workbook. Each company keeps its own current
   * sheet, so the endpoint answers for the caller's active company. Nothing
   * renders these today (a staff session is always one company, so the value
   * would be a constant), they are here so the type matches the response. */
  company_id: string | null;
  company_name: string | null;
}

export async function getLatestContainerStatusDocument(): Promise<LatestContainerStatusDocument> {
  const response = await apiFetch(
    '/api/v1/procurement/packing-lists/container-status/latest',
  );
  if (!response.ok) {
    throw new Error(
      await extractApiError(
        response,
        'No Container Status workbook has been imported yet',
      ),
    );
  }
  return response.json();
}

/**
 * ============================================================================
 * Where this container's lines came from - the proforma invoices behind it (F10)
 * ============================================================================
 * Layering: the packing-list tabs -> usePackingListSourceInvoices -> THIS -> api-client.
 *
 * ── BACKEND CONTRACT (app/api/v1/scm/proforma_invoices.py) ────────────────
 *  GET /api/v1/scm/inbound-shipments/{id}/source-proforma-invoices
 *      -> 200 PackingListSourceInvoices. Auth: `scm.dashboard.view`, the same permission
 *      the SPO planner tab on this very page already reads behind.
 *      200 with empty lists on a container that was never converted from a PI (the real
 *      packing-list upload path), so the card renders its empty state rather than erroring.
 *
 * ONE endpoint rather than four, because the Details card, the Lines column, the Timeline
 * entry and the Documents list are four readings of the same link rows (AC-F9).
 */
export interface PackingListSourceInvoice {
  id: string;
  pi_number: string;
  supplier_id: string | null;
  supplier_name: string | null;
  invoice_date: string | null;
  revision_no: number;
  /** How many revisions the chain holds - "Revision 2 of 2" (AC-F9). 1 on an original. */
  revision_count: number;
  status: 'current' | 'superseded';
  /** The file it was read from, which is what the Documents tab lists (AC-F9). */
  source_ref: string | null;
  currency: string | null;
  /** How many of ITS lines landed on this container, and how many it holds in total. */
  lines: number;
  total_lines: number;
  /** What came here, out of the invoice's whole quantity. */
  qty: number;
  total_qty: number;
  /** Value of what came here, at the invoice's own prices. Null when it states none. */
  amount: number | null;
}

export interface PackingListSourceInvoices {
  invoices: PackingListSourceInvoice[];
  /** Who created this container, BY NAME - `inbound_shipments.created_by` is a user id and
   *  this screen prints it. "System" when nobody is recorded, or the actor's user row has
   *  gone; never the id. */
  created_by: string;
  /** Per shipment line id, which invoice(s) that line's goods were charged on. */
  by_shipment_line: Record<
    string,
    Array<{ proforma_invoice_id: string; pi_number: string; qty: number }>
  >;
}

export async function getPackingListSourceInvoices(
  shipmentId: string,
): Promise<PackingListSourceInvoices> {
  const res = await apiFetch(
    `/api/v1/scm/inbound-shipments/${shipmentId}/source-proforma-invoices`,
  );
  if (!res.ok) {
    throw new Error(await extractApiError(res, 'Failed to load the source proforma invoices'));
  }
  return (await res.json()) as PackingListSourceInvoices;
}
