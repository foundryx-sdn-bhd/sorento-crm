/**
 * Sales Opportunities - feature service (plan 3.4, 3.5, 3.7; section 16, slice S2).
 *
 * Layering: components -> hooks (useSalesOpportunities) -> THIS service -> lib/api.
 *
 * Backend contract (module `sales`, mounted at /api/v1/sales behind its module guard):
 *   GET    /sales/opportunities                      -> ListResponse<SalesOpportunity>  .view
 *   GET    /sales/opportunities/meta                  -> {stages, lost_reasons}          .view
 *   GET    /sales/opportunities/customer-options?q=    -> {items, prospect, blocked}      .view
 *   GET    /sales/opportunities/agent-options          -> {data: [...]}                   .view
 *   GET    /sales/opportunities/{id}                   -> SalesOpportunity                .view
 *   GET    /sales/opportunities/{id}/sales-order-options?q= -> {data: [...]}              .view
 *   POST   /sales/opportunities                        body -> SalesOpportunity           .add
 *   PATCH  /sales/opportunities/{id}                   body -> SalesOpportunity           .edit
 *   DELETE /sales/opportunities/{id}                                                      .delete
 * Product options reuse the existing catalog picker (`/master-data/products/select`,
 * LESSONS-LEARNT: never a capped static list over ~22,000 products).
 */
import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import type {
  SalesOpportunityCustomerOptionsResponse,
  SalesOpportunityDetail,
  SalesOpportunityListParams,
  SalesOpportunityListResponse,
  SalesOpportunityMeta,
  SalesOpportunitySalesOrderOption,
  SalesOpportunitySavePayload,
} from '../types/salesOpportunity.types';
import { codeWithName } from '@/lib/product-display';

const BASE = '/api/v1/sales/opportunities';

async function read<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) throw new Error(await extractApiError(response, fallback));
  return response.json();
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

export async function getSalesOpportunities(
  params: SalesOpportunityListParams,
): Promise<SalesOpportunityListResponse> {
  const usp = buildDataGridParams(params, {
    status_id: params.statusId,
    sales_agent_id: params.salesAgentId,
    customer_id: params.customerId,
    close_from: params.closeFrom,
    close_to: params.closeTo,
    outcome: params.outcome,
  });
  return read(await apiFetch(`${BASE}?${usp.toString()}`), 'Failed to load opportunities');
}

export async function getSalesOpportunity(id: string): Promise<SalesOpportunityDetail> {
  return read(await apiFetch(`${BASE}/${id}`), 'Failed to load opportunity');
}

export async function createSalesOpportunity(
  payload: SalesOpportunitySavePayload,
): Promise<SalesOpportunityDetail> {
  return read(await apiFetch(BASE, jsonInit('POST', payload)), 'Failed to log opportunity');
}

export async function updateSalesOpportunity(
  id: string,
  payload: SalesOpportunitySavePayload,
): Promise<SalesOpportunityDetail> {
  return read(await apiFetch(`${BASE}/${id}`, jsonInit('PATCH', payload)), 'Failed to save opportunity');
}

export async function deleteSalesOpportunity(id: string): Promise<void> {
  const res = await apiFetch(`${BASE}/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to delete opportunity'));
}

export async function getSalesOpportunityCustomerOptions(
  q: string,
): Promise<SalesOpportunityCustomerOptionsResponse> {
  const usp = new URLSearchParams();
  if (q) usp.set('q', q);
  return read(
    await apiFetch(`${BASE}/customer-options?${usp.toString()}`),
    'Failed to search customers',
  );
}

/** A product option carrying its list price (F7) - the price the dealer flyer prints,
 *  and what a line's Unit price prefills to once this product is picked. Omitted when
 *  the catalog has no usable price ("0.00" reads as none, same as the flyer itself). */
export interface SalesOpportunityProductOption extends SearchableSelectOption {
  listPrice?: string;
}

/** The catalog's own async picker (LESSONS-LEARNT: never a capped static list). */
export async function getSalesOpportunityProductOptions(
  query: string,
  pageIndex = 0,
  pageSize = 50,
): Promise<SalesOpportunityProductOption[]> {
  const usp = new URLSearchParams({
    limit: String(pageSize),
    offset: String(pageIndex * pageSize),
  });
  if (query) usp.set('query', query);
  const body = await read<{
    data: { id: string; product_code: string; product_name: string; list_price: string | null }[];
  }>(
    await apiFetch(`/api/v1/master-data/products/select?${usp.toString()}`),
    'Failed to search products',
  );
  return body.data.map((p) => {
    const price = p.list_price != null ? Number(p.list_price) : 0;
    return {
      value: p.id,
      label: codeWithName(p.product_code, p.product_name),
      ...(Number.isFinite(price) && price > 0 ? { listPrice: p.list_price as string } : {}),
    };
  });
}

export async function getSalesOpportunityMeta(): Promise<SalesOpportunityMeta> {
  return read(await apiFetch(`${BASE}/meta`), 'Failed to load opportunity stages');
}

export async function getSalesOpportunitySalesOrderOptions(
  opportunityId: string,
  q?: string,
): Promise<SalesOpportunitySalesOrderOption[]> {
  const usp = new URLSearchParams();
  if (q) usp.set('q', q);
  const body = await read<{ data: SalesOpportunitySalesOrderOption[] }>(
    await apiFetch(`${BASE}/${opportunityId}/sales-order-options?${usp.toString()}`),
    'Failed to load sales orders',
  );
  return body.data;
}

export interface SalesOpportunityAgentOption {
  id: string;
  code: string;
  label: string;
}

export async function getSalesOpportunityAgentOptions(): Promise<SalesOpportunityAgentOption[]> {
  const body = await read<{ data: SalesOpportunityAgentOption[] }>(
    await apiFetch(`${BASE}/agent-options`),
    'Failed to load sales agents',
  );
  return body.data;
}
