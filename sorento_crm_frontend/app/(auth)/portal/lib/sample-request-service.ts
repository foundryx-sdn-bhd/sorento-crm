/**
 * Portal-side sample request service (SR-CORE).
 *
 * Calls `/api/v1/public/portal/submissions/sample_request` through `portalFetch`.
 * The shapes live in `types/sampleRequest.ts`, shared with the staff feature.
 */
import { extractApiError } from '@/lib/api-client';
import { saveBlobAs } from '@/lib/save-blob';
import {
  SampleRequestError,
  type SampleRequestDetail,
  type SampleRequestProductHit,
  type SampleRequestRow,
  type SampleRequestWrite,
} from '@/types/sampleRequest';
import { portalFetch, type PortalSubmissionSummary } from './portal-client';

const BASE = '/api/v1/public/portal/submissions/sample_request';

/**
 * A refusal that names a field arrives as `{message, code, details: {field}}`. The clone
 * lets `extractApiError` own the message while this still reads the field beside it.
 */
async function unwrapNamingField<T>(res: Response, fallback: string): Promise<T> {
  if (res.ok) return (await res.json()) as T;
  const spare = res.clone();
  const message = await extractApiError(res, fallback);
  type Body = { code?: unknown; details?: { field?: unknown } };
  let body: Body | null = null;
  try {
    body = (await spare.json()) as Body;
  } catch {
    body = null;
  }
  throw new SampleRequestError(
    message,
    typeof body?.code === 'string' ? body.code : null,
    typeof body?.details?.field === 'string' ? body.details.field : null,
  );
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

/** A debtor the contact may pick: exactly the set submit accepts (their agent's book). */
export interface SampleRequestCustomerHit {
  customer_id: string;
  customer_code: string;
  debtor_name: string;
}

export async function lookupSampleRequestCustomers(q: string): Promise<SampleRequestCustomerHit[]> {
  const res = await portalFetch(
    `/api/v1/public/portal/sample-request/customers?q=${encodeURIComponent(q)}`,
  );
  return unwrapNamingField<SampleRequestCustomerHit[]>(res, 'Failed to load customers');
}

export async function searchSampleRequestProducts(
  q: string,
): Promise<SampleRequestProductHit[]> {
  const res = await portalFetch(
    `/api/v1/public/portal/sample-request/products?q=${encodeURIComponent(q)}`,
  );
  return unwrapNamingField<SampleRequestProductHit[]>(res, 'Failed to search products');
}

export async function listSampleRequests(q?: string): Promise<SampleRequestRow[]> {
  const usp = new URLSearchParams();
  if (q && q.trim()) usp.set('q', q.trim());
  const qs = usp.toString();
  const res = await portalFetch(qs ? `${BASE}?${qs}` : BASE);
  const page = await unwrapNamingField<{ data: SampleRequestRow[] }>(
    res,
    'Failed to load sample requests',
  );
  return page.data ?? [];
}

/** The landing card shape: number first, customer on the second line. */
export async function listSampleRequestsAsSummaries(
  q?: string,
): Promise<PortalSubmissionSummary[]> {
  const rows = await listSampleRequests(q);
  return rows.map((r) => {
    const isDraft = r.status === 'draft';
    return {
      id: r.id,
      kind: 'sample_request' as const,
      title: r.customer_name || r.request_no,
      document_number: r.request_no,
      reference: null,
      status: r.status,
      status_label: r.status_label,
      is_editable: isDraft,
      is_draft: isDraft,
      created_at: r.request_date,
      customer_name: r.customer_name,
      project_title: r.project_name,
    };
  });
}

export async function getSampleRequest(id: string): Promise<SampleRequestDetail> {
  const res = await portalFetch(`${BASE}/${encodeURIComponent(id)}`);
  return unwrapNamingField<SampleRequestDetail>(res, 'Failed to load the sample request');
}

export async function createSampleRequest(
  data: SampleRequestWrite,
): Promise<SampleRequestDetail> {
  const res = await portalFetch(BASE, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(data),
  });
  return unwrapNamingField<SampleRequestDetail>(res, 'Failed to save the sample request');
}

export async function updateSampleRequest(
  id: string,
  data: SampleRequestWrite,
): Promise<SampleRequestDetail> {
  const res = await portalFetch(`${BASE}/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify(data),
  });
  return unwrapNamingField<SampleRequestDetail>(res, 'Failed to save the sample request');
}

export async function submitSampleRequest(id: string): Promise<SampleRequestDetail> {
  const res = await portalFetch(`${BASE}/${encodeURIComponent(id)}/submit`, {
    method: 'POST',
  });
  return unwrapNamingField<SampleRequestDetail>(res, 'Failed to submit the sample request');
}

export async function cancelSampleRequest(id: string): Promise<SampleRequestDetail> {
  const res = await portalFetch(`${BASE}/${encodeURIComponent(id)}/cancel`, {
    method: 'POST',
  });
  return unwrapNamingField<SampleRequestDetail>(res, 'Failed to cancel the sample request');
}

/** The PDF needs the token header, so it is fetched and saved as a blob. */
export async function downloadSampleRequestPdf(id: string, requestNo: string): Promise<void> {
  const res = await portalFetch(`${BASE}/${encodeURIComponent(id)}/pdf`);
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to download the PDF'));
  const blob = await res.blob();
  saveBlobAs(blob, `${requestNo}.pdf`);
}
