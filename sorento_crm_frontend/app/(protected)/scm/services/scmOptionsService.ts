/**
 * ============================================================================
 * SCM option lists - real bindings for the filter bar + sales-order form
 * ============================================================================
 * SCM has no dedicated option endpoints, so these reuse existing peer routes
 * (all reachable by an admin):
 *   warehouses - derived from GET /scm/dashboard/warehouses
 *   suppliers - derived from GET /scm/dashboard/suppliers
 *   categories - GET /master-data/product-categories/select (value = id)
 *   customers - GET /order-management/customers/select (value = customer_code)
 *   products  - GET /master-data/products/select (value = product_code)
 *
 * Both of those come in TWO shapes. `getXOptions()` pulls a static list for a select that
 * filters in the browser; `searchXOptions(query, pageIndex)` is the server-searched, paged
 * form a `SearchableSelect` in async mode calls. Anything over a few hundred rows uses the
 * second - see LESSONS-LEARNT.md, "A product dropdown must never be a capped static list".
 *   order types - GET /lookup/by-binding?table=sales_orders&column=order_type
 *                 (falls back to a sensible static list when no binding exists)
 * ============================================================================
 */
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import { EMPTY_SCM_FILTERS, getSuppliers, getWarehouseHealth } from './scmDashboardService';
import { codeWithName } from '@/lib/product-display';

export interface Option {
  value: string;
  label: string;
  description?: string;
  /** Free text the SearchableSelect's own filter reads, for options whose label alone
   *  does not carry everything a person types. */
  searchText?: string;
}

export async function getWarehouseOptions(): Promise<Option[]> {
  const { data } = await getWarehouseHealth(EMPTY_SCM_FILTERS);
  return data.map((w) => ({ value: w.warehouse_code, label: w.warehouse_name }));
}

export async function getSupplierOptions(): Promise<Option[]> {
  const { data } = await getSuppliers(EMPTY_SCM_FILTERS);
  return data.map((s) => ({ value: s.supplier_code, label: s.supplier_name }));
}

export async function getCategoryOptions(): Promise<Option[]> {
  const res = await apiFetch('/api/v1/master-data/product-categories/select');
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load categories'));
  const rows = (await res.json()) as { id: string; category_name: string }[];
  return rows.map((c) => ({ value: c.id, label: c.category_name }));
}

export async function getCustomerOptions(): Promise<Option[]> {
  const res = await apiFetch('/api/v1/order-management/customers/select');
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load customers'));
  const body = (await res.json()) as {
    data: { customer_code: string; customer_name: string; market_segment_code?: string | null }[];
  };
  // customer_code is not unique in this dataset - dedupe to one option per code.
  const seen = new Set<string>();
  const out: Option[] = [];
  for (const c of body.data) {
    if (seen.has(c.customer_code)) continue;
    seen.add(c.customer_code);
    out.push({
      value: c.customer_code,
      label: c.customer_name,
      description: c.market_segment_code ?? undefined,
    });
  }
  return out;
}

/** How many rows a server-searched select asks for per page. Matches the `pageSize` the
 *  SearchableSelect is given, so a full page is what tells it another page may exist. */
export const SELECT_PAGE_SIZE = 50;

/**
 * Customers, SEARCHED ON THE SERVER, one page at a time.
 *
 * `getCustomerOptions` above pulls the whole debtor master (6,397 rows on the client's
 * database) so a static select can filter it in the browser: seconds to open a dropdown.
 * Use this wherever the select is a `SearchableSelect` in async mode; the static one stays
 * for the filter bar, whose callers hold the array.
 *
 * `pageIndex` is what `SearchableSelect`'s `fetchOptions` hands back on "Load more".
 */
export async function searchCustomerOptions(
  query: string,
  pageIndex = 0,
): Promise<Option[]> {
  const search = new URLSearchParams({
    limit: String(SELECT_PAGE_SIZE),
    offset: String(pageIndex * SELECT_PAGE_SIZE),
  });
  if (query.trim()) search.set('query', query.trim());
  const res = await apiFetch(`/api/v1/order-management/customers/select?${search.toString()}`);
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load customers'));
  const body = (await res.json()) as {
    data: { customer_code: string; customer_name: string; market_segment_code?: string | null }[];
  };
  // customer_code is not unique in this dataset - dedupe to one option per code, the same
  // rule the static list follows, since the value the form sends is the CODE.
  const seen = new Set<string>();
  const out: Option[] = [];
  for (const c of body.data) {
    if (seen.has(c.customer_code)) continue;
    seen.add(c.customer_code);
    out.push({
      value: c.customer_code,
      label: c.customer_name,
      description: c.market_segment_code ?? undefined,
    });
  }
  return out;
}

/**
 * Suppliers, SEARCHED ON THE SERVER, one page at a time.
 *
 * `getSupplierOptions` above derives its list from the SCM dashboard's supplier AGGREGATE -
 * a per-supplier health computation - purely to read a code and a name off it, which is a
 * lot of work for a dropdown. This asks the purchase-order router for the two columns it
 * actually needs (`GET /scm/purchase-orders/suppliers`), gated on the SAME `scm.dashboard.view`
 * this screen already holds, so a purchasing role does not 403 on a supplier select the way
 * it would against the procurement master's own route.
 *
 * The label carries the CODE as well as the name, because the read view of the field it
 * feeds shows the code beside the name and a select that showed only one of the two would
 * relabel the value the moment it was opened.
 */
export async function searchSupplierOptions(
  query: string,
  pageIndex = 0,
): Promise<Option[]> {
  const search = new URLSearchParams({
    limit: String(SELECT_PAGE_SIZE),
    offset: String(pageIndex * SELECT_PAGE_SIZE),
  });
  if (query.trim()) search.set('query', query.trim());
  const res = await apiFetch(`/api/v1/scm/purchase-orders/suppliers?${search.toString()}`);
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load suppliers'));
  const rows = (await res.json()) as { supplier_code: string; supplier_name: string }[];
  return rows.map((s) => ({
    value: s.supplier_code,
    label: s.supplier_name ? `${s.supplier_code} · ${s.supplier_name}` : s.supplier_code,
    searchText: `${s.supplier_code} ${s.supplier_name ?? ''}`,
  }));
}

/**
 * Products, SEARCHED ON THE SERVER, one page at a time.
 *
 * `getProductOptions` below asks for no `query` and no `limit`, so the endpoint's own
 * default of 100 applies against ~22,000 active products - a picker that silently holds
 * 0.5% of the catalogue and answers "no products match" for the rest. See LESSONS-LEARNT.md
 * ("A product dropdown must never be a capped static list").
 */
export async function searchProductOptions(
  query: string,
  pageIndex = 0,
): Promise<Option[]> {
  const search = new URLSearchParams({
    limit: String(SELECT_PAGE_SIZE),
    offset: String(pageIndex * SELECT_PAGE_SIZE),
  });
  if (query.trim()) search.set('query', query.trim());
  const res = await apiFetch(`/api/v1/master-data/products/select?${search.toString()}`);
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load products'));
  const body = (await res.json()) as {
    data: { product_code: string; product_name: string }[];
  };
  return body.data.map((p) => ({
    value: p.product_code,
    label: codeWithName(p.product_code, p.product_name, ' · '),
    searchText: `${p.product_code} ${p.product_name}`,
  }));
}

export async function getProductOptions(): Promise<Option[]> {
  const res = await apiFetch('/api/v1/master-data/products/select');
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load products'));
  const body = (await res.json()) as {
    data: { product_code: string; product_name: string }[];
  };
  return body.data.map((p) => ({
    value: p.product_code,
    label: codeWithName(p.product_code, p.product_name, ' · '),
  }));
}

const STATIC_ORDER_TYPES: Option[] = [
  { value: 'dealer', label: 'Dealer' },
  { value: 'project', label: 'Project' },
];

export async function getOrderTypeOptions(): Promise<Option[]> {
  const res = await apiFetch(
    '/api/v1/lookup/by-binding?table=sales_orders&column=order_type',
  );
  if (!res.ok) return STATIC_ORDER_TYPES;
  const body = (await res.json()) as { options?: { value: string; label: string }[] };
  const opts = (body.options ?? []).map((o) => ({ value: o.value, label: o.label }));
  return opts.length ? opts : STATIC_ORDER_TYPES;
}
