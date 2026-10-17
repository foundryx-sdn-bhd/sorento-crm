/**
 * Portal-side new product application service (NEW-PRODUCT-APP).
 *
 * Calls `/api/v1/public/portal/submissions/new_product_application` through `portalFetch`.
 * The shapes live in `types/newProductApplication.ts`, shared with the staff feature.
 */
import { extractApiError } from '@/lib/api-client';
import {
  NewProductApplicationError,
  type NewProductApplicationDetail,
  type NewProductApplicationPrefill,
  type NewProductApplicationSummary,
  type NewProductApplicationWrite,
} from '@/types/newProductApplication';
import { portalFetch, type PortalSubmissionSummary } from './portal-client';

const BASE = '/api/v1/public/portal/submissions/new_product_application';
const OWN = '/api/v1/public/portal/new-product-application';

/** A refusal that names a field arrives as `{message, code, details: {field}}`. */
async function unwrap<T>(res: Response, fallback: string): Promise<T> {
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
  throw new NewProductApplicationError(
    message,
    typeof body?.code === 'string' ? body.code : null,
    typeof body?.details?.field === 'string' ? body.details.field : null,
  );
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };
const at = (id: string) => encodeURIComponent(id);

/** One of the contact's own responded stock inquiries (the "start from" picker). */
export interface PortalInquiryHit {
  id: string;
  inquiry_number: string | null;
  product_code: string | null;
  item_description: string | null;
  project_name: string | null;
  purchasing_response: string | null;
  last_responded_at: string | null;
}

export interface PortalLookupHit {
  id: string;
  code: string;
  name: string;
}

export type PortalLookupKind = 'item-groups' | 'uoms' | 'brands' | 'item-types';

export interface PortalCustomerHit {
  customer_id: string;
  customer_code: string;
  debtor_name: string;
}

export async function listMyRespondedInquiries(): Promise<PortalInquiryHit[]> {
  const res = await portalFetch(`${OWN}/inquiries`);
  return unwrap<PortalInquiryHit[]>(res, 'Failed to load your stock inquiries');
}

export async function getNewProductApplicationPrefill(
  stockInquiryId: string,
): Promise<NewProductApplicationPrefill> {
  const res = await portalFetch(
    `${OWN}/prefill?stock_inquiry_id=${at(stockInquiryId)}`,
  );
  return unwrap<NewProductApplicationPrefill>(
    res,
    'Failed to load the stock inquiry',
  );
}

export async function lookupNewProductApplication(
  kind: PortalLookupKind,
  q: string,
): Promise<PortalLookupHit[]> {
  const res = await portalFetch(
    `${OWN}/lookups/${kind}?q=${encodeURIComponent(q)}`,
  );
  return unwrap<PortalLookupHit[]>(res, 'Failed to load the list');
}

export async function lookupNewProductApplicationCustomers(
  q: string,
): Promise<PortalCustomerHit[]> {
  const res = await portalFetch(`${OWN}/customers?q=${encodeURIComponent(q)}`);
  return unwrap<PortalCustomerHit[]>(res, 'Failed to load customers');
}

export async function listNewProductApplications(
  q?: string,
): Promise<NewProductApplicationSummary[]> {
  const usp = new URLSearchParams();
  if (q && q.trim()) usp.set('q', q.trim());
  const qs = usp.toString();
  const res = await portalFetch(qs ? `${BASE}?${qs}` : BASE);
  const page = await unwrap<{ data: NewProductApplicationSummary[] }>(
    res,
    'Failed to load applications',
  );
  return page.data ?? [];
}

/** The landing card shape: number first, customer on the second line. */
export async function listNewProductApplicationsAsSummaries(
  q?: string,
): Promise<PortalSubmissionSummary[]> {
  const rows = await listNewProductApplications(q);
  return rows.map((r) => {
    const editable = r.status === 'draft' || r.status === 'rejected';
    return {
      id: r.id,
      kind: 'new_product_application' as const,
      title: r.first_line || r.customer_name || r.application_no,
      document_number: r.application_no,
      reference: null,
      status: r.status,
      status_label: r.status_label,
      is_editable: editable,
      is_draft: r.status === 'draft',
      created_at: r.created_at,
      customer_name: r.customer_name,
      project_title: r.project_name,
    };
  });
}

export async function getNewProductApplication(
  id: string,
): Promise<NewProductApplicationDetail> {
  const res = await portalFetch(`${BASE}/${at(id)}`);
  return unwrap<NewProductApplicationDetail>(
    res,
    'Failed to load the application',
  );
}

export async function createNewProductApplication(
  data: NewProductApplicationWrite,
): Promise<NewProductApplicationDetail> {
  const res = await portalFetch(BASE, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(data),
  });
  return unwrap<NewProductApplicationDetail>(
    res,
    'Failed to save the application',
  );
}

export async function updateNewProductApplication(
  id: string,
  data: NewProductApplicationWrite,
): Promise<NewProductApplicationDetail> {
  const res = await portalFetch(`${BASE}/${at(id)}`, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify(data),
  });
  return unwrap<NewProductApplicationDetail>(
    res,
    'Failed to save the application',
  );
}

const move = async (
  id: string,
  verb: string,
  fallback: string,
  body?: unknown,
) =>
  unwrap<NewProductApplicationDetail>(
    await portalFetch(`${BASE}/${at(id)}/${verb}`, {
      method: 'POST',
      ...(body === undefined
        ? {}
        : { headers: JSON_HEADERS, body: JSON.stringify(body) }),
    }),
    fallback,
  );

export const submitNewProductApplication = (id: string) =>
  move(id, 'submit', 'Failed to submit the application');
export const reviseNewProductApplication = (id: string, reason: string) =>
  move(id, 'revise', 'Failed to revise the application', { reason });
export const cancelNewProductApplication = (id: string, reason?: string) =>
  move(
    id,
    'cancel',
    'Failed to cancel the application',
    reason ? { reason } : {},
  );
