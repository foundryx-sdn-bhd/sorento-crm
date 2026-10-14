/**
 * Product set master - feature service.
 *
 * Layering: components -> hooks (useProductSets) -> THIS service -> lib/api-client.
 *
 * Backend contract (mounted under the `product` module guard):
 *   GET    /api/v1/master-data/product-sets?page&limit&sort&dir&query
 *            -> { data: ProductSet[], pagination: { total, page, limit }, empty }
 *            gated `master_data.product_sets.view`; `query` matches set_code and name.
 *   GET    /api/v1/master-data/product-sets/{id}
 *            -> ProductSetDetail, members ordered by sort_order, each carrying
 *            its own `available` and the set carrying `complete_sets` +
 *            `limiting_member_code`.        gated `master_data.product_sets.view`
 *   POST   /api/v1/master-data/product-sets          body ProductSetPayload -> ProductSetDetail
 *            409 when (company, set_code) already exists. Uniqueness is per
 *            company: Sorento and Mocha both legitimately carry the same codes.
 *            gated `master_data.product_sets.edit`
 *   PUT    /api/v1/master-data/product-sets/{id}     body ProductSetPayload -> ProductSetDetail
 *            `members` omitted leaves membership alone; `members: []` empties it.
 *            gated `master_data.product_sets.edit`
 *   DELETE /api/v1/master-data/product-sets/{id}     -> 204
 *            Hard delete. Members go with it; no product is touched.
 *            gated `master_data.product_sets.delete`
 *
 * A set is NOT orderable and is never a `products` row, so there is deliberately
 * no stock write, no costing and no order endpoint here.
 */
import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type { DataGridApiFetchParams, DataGridApiResponse } from '@/components/ui/data-grid';
import type {
  ProductSet,
  ProductSetDetail,
  ProductSetPayload,
} from '../types/productSet.types';
import { codeWithName } from '@/lib/product-display';

const BASE = '/api/v1/master-data/product-sets';

/**
 * Money and quantities arrive as STRINGS.
 *
 * The API serialises `Decimal` as a string, so `1180.00` reaches us as `"1180.00"`.
 * `String.prototype.toLocaleString` exists and returns the string untouched, so a
 * component formatting it gets `RM 1180.00` with no thousands separator and no
 * error anywhere - it looked right against the mock, which used real numbers.
 * Coerced once here, at the boundary, so no component has to remember.
 */
function num(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizePrice(price: ProductSetDetail['price']): ProductSetDetail['price'] {
  return {
    ...price,
    computed: num(price?.computed),
    override: num(price?.override),
    resolved: num(price?.resolved),
  };
}

function normalizeSet<T extends ProductSet>(set: T): T {
  const members = (set as unknown as Partial<ProductSetDetail>).members;
  return {
    ...set,
    price: normalizePrice(set.price),
    complete_sets: num(set.complete_sets),
    // A list row has no `members`; a detail read does. Leaving the key absent
    // rather than inventing [] keeps "not loaded" distinct from "none".
    ...(Array.isArray(members)
      ? {
          members: members.map((m) => ({
            ...m,
            list_price: num(m.list_price),
            quantity: num(m.quantity) ?? 1,
            available: num(m.available),
          })),
        }
      : {}),
  } as T;
}

export async function getProductSets(
  params: DataGridApiFetchParams,
): Promise<DataGridApiResponse<ProductSet>> {
  const search = buildDataGridParams(params);
  const response = await apiFetch(`${BASE}?${search.toString()}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load product sets'));
  }
  const page = await response.json();
  return { ...page, data: (page.data ?? []).map(normalizeSet) };
}

export async function getProductSet(id: string): Promise<ProductSetDetail> {
  const response = await apiFetch(`${BASE}/${id}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load product set'));
  }
  return normalizeSet(await response.json());
}

export async function createProductSet(data: ProductSetPayload): Promise<ProductSetDetail> {
  const response = await apiFetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to create product set'));
  }
  return normalizeSet(await response.json());
}

export async function updateProductSet(
  id: string,
  data: ProductSetPayload,
): Promise<ProductSetDetail> {
  const response = await apiFetch(`${BASE}/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save product set'));
  }
  return normalizeSet(await response.json());
}

export async function deleteProductSet(id: string): Promise<void> {
  const response = await apiFetch(`${BASE}/${id}`, { method: 'DELETE' });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to delete product set'));
  }
}

/** One product on the "Add member" picker. `value` is the product CODE, not
 * the id - the write path (`ProductSetMemberPayload.product_code`) is by
 * code, so no UUID ever has to travel from this picker to the save call.
 * `product_id` is carried only to build the member's link back to its own
 * product page; it is never rendered as text. */
export interface ProductSetMemberOption {
  value: string;
  label: string;
  product_id: string;
  product_name: string;
  list_price: number | null;
  is_discontinued: boolean;
}

/**
 * Uses the shared products select endpoint rather than a set-specific route:
 * a member is a link to an existing product, so there is nothing set-shaped
 * about the option list.
 */
export async function getProductSetMemberOptions(
  query: string,
): Promise<ProductSetMemberOption[]> {
  const search = new URLSearchParams();
  if (query) search.set('query', query);
  const response = await apiFetch(`/api/v1/master-data/products/select?${search.toString()}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load products'));
  }
  const body = (await response.json()) as {
    data: {
      id: string;
      product_code: string;
      product_name: string;
      list_price: unknown;
      is_discontinued: boolean;
    }[];
  };
  return (body.data ?? []).map((p) => ({
    value: p.product_code,
    label: codeWithName(p.product_code, p.product_name),
    product_id: p.id,
    product_name: p.product_name,
    list_price: num(p.list_price),
    is_discontinued: Boolean(p.is_discontinued),
  }));
}
