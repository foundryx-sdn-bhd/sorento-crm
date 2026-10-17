/**
 * Portal-side sales opportunity service (plan 3.5; section 16, slice S2).
 *
 * Calls `/api/v1/public/portal/sales-opportunities` and reuses the portal's own product
 * lookup (`/lookups/products`, the same one `ComplaintLinesTable` calls) via `portalFetch`
 * - no CRM `lib/api-client`, no bearer auth, the portal token header only.
 */
import { portalFetch, unwrap } from './portal-client';

const BASE = '/api/v1/public/portal/sales-opportunities';

export interface PortalSalesOpportunityLine {
  id?: string;
  product_id: string;
  product_code?: string;
  product_name?: string;
  qty: number | string;
  unit_price: string | null;
  line_amount: string | null;
}

export interface PortalSalesOpportunity {
  id: string;
  opportunity_no: string;
  title: string;
  customer_id: string | null;
  customer_name: string | null;
  prospect_name: string | null;
  /** The registered project, when one is linked. */
  project_id?: string | null;
  project_code?: string | null;
  project_label?: string | null;
  stage_key: string;
  stage_label: string;
  outcome: string;
  expected_amount: string;
  expected_close_date: string;
  lost_reason: string | null;
  lost_reason_label: string | null;
  source: string;
  lines: PortalSalesOpportunityLine[];
  available_transitions: { to_status_id: string; key: string; label: string }[];
}

export interface PortalSalesOpportunitySavePayload {
  title?: string;
  expected_amount?: string | number;
  expected_close_date?: string;
  customer_id?: string | null;
  prospect_name?: string | null;
  /** null clears the link; omitted leaves it unchanged. */
  project_id?: string | null;
  status_id?: string;
  lost_reason?: string | null;
  lines?: { product_id: string; qty: number; unit_price?: number | string | null }[];
}

export interface PortalCustomerOptionItem {
  customer_id: string;
  customer_code: string;
  customer_name: string;
}

export interface PortalCustomerOptionsResponse {
  items: PortalCustomerOptionItem[];
  prospect: { name: string } | null;
  blocked: { name: string; message: string } | null;
}

export async function listPortalSalesOpportunities(): Promise<PortalSalesOpportunity[]> {
  const res = await portalFetch(BASE);
  const body = await unwrap<{ items: PortalSalesOpportunity[] }>(res, 'Failed to load opportunities.');
  return body.items;
}

export async function getPortalSalesOpportunity(id: string): Promise<PortalSalesOpportunity> {
  const res = await portalFetch(`${BASE}/${encodeURIComponent(id)}`);
  return unwrap(res, 'Failed to load opportunity.');
}

export async function createPortalSalesOpportunity(
  payload: PortalSalesOpportunitySavePayload,
): Promise<PortalSalesOpportunity> {
  const res = await portalFetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return unwrap(res, 'Failed to log the opportunity.');
}

export async function updatePortalSalesOpportunity(
  id: string,
  payload: PortalSalesOpportunitySavePayload,
): Promise<PortalSalesOpportunity> {
  const res = await portalFetch(`${BASE}/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return unwrap(res, 'Failed to save the opportunity.');
}

export async function getPortalCustomerOptions(q: string): Promise<PortalCustomerOptionsResponse> {
  const res = await portalFetch(`${BASE}/customer-options?q=${encodeURIComponent(q)}`);
  return unwrap(res, 'Failed to search customers.');
}

export interface PortalProductOption {
  id: string;
  code: string;
  name: string | null;
  /** Null when the product has no real price (owner: "my product no price one" - the
   *  line's Unit price then starts blank instead of guessing at a number). */
  listPrice: string | null;
}

/** The catalog's own portal lookup (LESSONS-LEARNT: never a capped static list). */
export async function getPortalProductOptions(q: string): Promise<PortalProductOption[]> {
  const url = `/api/v1/public/portal/lookups/products?q=${encodeURIComponent(q)}`;
  const res = await portalFetch(url);
  const items = await unwrap<
    {
      product_id?: string | null;
      product_code: string;
      product_name: string | null;
      list_price?: string | null;
    }[]
  >(res, 'Failed to search products.');
  return items
    .filter((item) => !!item.product_id)
    .map((item) => ({
      id: item.product_id as string,
      code: item.product_code,
      name: item.product_name,
      listPrice: item.list_price ?? null,
    }));
}

export interface PortalSalesOpportunityMeta {
  stages: { id: string; key: string; label: string; win_probability: string | number | null; is_active: boolean }[];
  lost_reasons: { value: string; label: string }[];
}

export async function getPortalOpportunityMeta(): Promise<PortalSalesOpportunityMeta> {
  const res = await portalFetch(`${BASE}/meta`);
  return unwrap(res, 'Failed to load stages.');
}
