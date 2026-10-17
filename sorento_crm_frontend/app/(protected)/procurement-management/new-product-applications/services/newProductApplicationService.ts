import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type { DataGridApiFetchParams } from '@/components/ui/data-grid';
import { stockInquiryLabel } from '@/lib/new-product-application-form';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import {
  NewProductApplicationError,
  type NewProductApplicationDetail,
  type NewProductApplicationItemType,
  type NewProductApplicationPage,
  type NewProductApplicationPrefill,
  type NewProductApplicationProductHit,
  type NewProductApplicationSummary,
  type NewProductApplicationWrite,
} from '@/types/newProductApplication';

const BASE = '/api/v1/procurement/new-product-applications';

export type NewProductApplicationListParams = DataGridApiFetchParams & {
  /** Comma list of status keys; empty means every status. */
  status?: string;
  applicantId?: string;
  stockInquiryId?: string;
};

/** The list in the shape the DataGrid and the detail pager read. */
export interface NewProductApplicationListResult {
  data: NewProductApplicationSummary[];
  pagination: { total: number; page: number; limit: number };
}

/** A refusal that names a line or field arrives as `{message, code, details: {field}}`. */
async function fail(res: Response, fallback: string): Promise<never> {
  const spare = res.clone();
  const message = await extractApiError(res, fallback);
  type Body = { code?: unknown; details?: { field?: unknown } };
  let body: Body | null = null;
  try {
    body = (await spare.json()) as Body;
  } catch {
    body = null;
  }
  throw new NewProductApplicationError(
    message,
    typeof body?.code === 'string' ? body.code : null,
    typeof body?.details?.field === 'string' ? body.details.field : null,
  );
}

async function read<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) return fail(res, fallback);
  return (await res.json()) as T;
}

const post = (path: string, fallback: string, body?: unknown) =>
  apiFetch(`${BASE}/${path}`, {
    method: 'POST',
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  }).then((res) => read<NewProductApplicationDetail>(res, fallback));

export async function getNewProductApplications(
  params: NewProductApplicationListParams,
): Promise<NewProductApplicationListResult> {
  const sp = buildDataGridParams(
    { ...params, searchQuery: undefined },
    {
      q: params.searchQuery,
      status: params.status,
      applicant_id: params.applicantId,
      stock_inquiry_id: params.stockInquiryId,
    },
  );
  const res = await apiFetch(`${BASE}?${sp.toString()}`);
  const body = await read<NewProductApplicationPage>(
    res,
    'Failed to load applications',
  );
  return {
    data: body.items,
    pagination: { total: body.total, page: body.page, limit: body.limit },
  };
}

/** The applications raised from one stock inquiry (its detail page row). */
export async function getStockInquiryApplications(
  stockInquiryId: string,
): Promise<NewProductApplicationSummary[]> {
  const sp = buildDataGridParams(
    { pageIndex: 0, pageSize: 50 },
    { stock_inquiry_id: stockInquiryId },
  );
  const res = await apiFetch(`${BASE}?${sp.toString()}`);
  const body = await read<NewProductApplicationPage>(
    res,
    'Failed to load applications',
  );
  return body.items;
}

export async function getNewProductApplication(
  id: string,
): Promise<NewProductApplicationDetail> {
  const res = await apiFetch(`${BASE}/${encodeURIComponent(id)}`);
  return read<NewProductApplicationDetail>(
    res,
    'Failed to load the application',
  );
}

export async function getNewProductApplicationPrefill(
  stockInquiryId: string,
): Promise<NewProductApplicationPrefill> {
  const res = await apiFetch(
    `${BASE}/prefill?stock_inquiry_id=${encodeURIComponent(stockInquiryId)}`,
  );
  return read<NewProductApplicationPrefill>(
    res,
    'Failed to load the stock inquiry',
  );
}

export async function createNewProductApplication(
  data: NewProductApplicationWrite,
): Promise<NewProductApplicationDetail> {
  const res = await apiFetch(BASE, {
    method: 'POST',
    body: JSON.stringify(data),
  });
  return read<NewProductApplicationDetail>(
    res,
    'Failed to save the application',
  );
}

export async function updateNewProductApplication(
  id: string,
  data: NewProductApplicationWrite,
): Promise<NewProductApplicationDetail> {
  const res = await apiFetch(`${BASE}/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
  return read<NewProductApplicationDetail>(
    res,
    'Failed to save the application',
  );
}

const at = (id: string) => encodeURIComponent(id);

export const submitNewProductApplication = (id: string) =>
  post(`${at(id)}/submit`, 'Failed to submit the application');
export const approveNewProductApplication = (id: string) =>
  post(`${at(id)}/approve`, 'Failed to approve the application');
export const rejectNewProductApplication = (id: string, reason: string) =>
  post(`${at(id)}/reject`, 'Failed to reject the application', { reason });
export const reviseNewProductApplication = (id: string, reason: string) =>
  post(`${at(id)}/revise`, 'Failed to revise the application', { reason });
export const cancelNewProductApplication = (id: string, reason?: string) =>
  post(
    `${at(id)}/cancel`,
    'Failed to cancel the application',
    reason ? { reason } : {},
  );
export const linkNewProductApplicationLine = (
  id: string,
  lineId: string,
  productId: string,
) =>
  post(`${at(id)}/lines/${at(lineId)}/link`, 'Failed to link the product', {
    product_id: productId,
  });
export const closeNewProductApplicationLine = (
  id: string,
  lineId: string,
  reason: string,
) =>
  post(`${at(id)}/lines/${at(lineId)}/close`, 'Failed to close the line', {
    reason,
  });
export const undoNewProductApplicationLine = (id: string, lineId: string) =>
  post(`${at(id)}/lines/${at(lineId)}/undo`, 'Failed to undo the line');

/** Lists matches only (dashes, spaces and case ignored); a person picks one. */
export async function searchNewProductApplicationProducts(
  q: string,
): Promise<NewProductApplicationProductHit[]> {
  const res = await apiFetch(`${BASE}/products?q=${encodeURIComponent(q)}`);
  return read<NewProductApplicationProductHit[]>(
    res,
    'Failed to search products',
  );
}

/** The item type select (no master-data select endpoint exists for it). */
export async function searchNewProductApplicationItemTypes(
  q: string,
): Promise<SearchableSelectOption[]> {
  const res = await apiFetch(`${BASE}/item-types?q=${encodeURIComponent(q)}`);
  const rows = await read<NewProductApplicationItemType[]>(
    res,
    'Failed to load item types',
  );
  return rows.map((r) => ({
    value: r.id,
    label: r.item_type_name,
    code: r.item_type_code,
  }));
}

/** Stock inquiries for the header picker, labelled "SI26-0412 - <product code>". */
export async function searchStockInquiryOptions(
  q: string,
): Promise<SearchableSelectOption[]> {
  const sp = buildDataGridParams(
    { pageIndex: 0, pageSize: 25 },
    { query: q.trim() },
  );
  const res = await apiFetch(
    `/api/v1/procurement/stock-inquiries?${sp.toString()}`,
  );
  const body = await read<{
    data: {
      id: string;
      inquiry_number?: string | null;
      product_code?: string | null;
    }[];
  }>(res, 'Failed to load stock inquiries');
  return body.data.map((s) => ({
    value: s.id,
    label: stockInquiryLabel(s.inquiry_number, s.product_code),
  }));
}
