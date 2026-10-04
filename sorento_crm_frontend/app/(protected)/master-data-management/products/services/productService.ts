/**
 * Product Service
 * 
 * API service layer for Product CRUD operations
 */

import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type {
  Product,
  ProductFormData,
  ProductFilters,
  ProductApiResponse,
  ProductDetail,
  ProductVariantRef,
  ProductLineRef,
  PriceHistory,
} from '../types/product.types';
import type { DataGridApiFetchParams } from '@/components/ui/data-grid';

/**
 * Chatbot stock limits, X / Y (PLAN-chatbot-stock-ask-v2-24sep.md S1):
 *   products.chatbot_max_qty          integer | null  (X: overrides the
 *     product's category value when set; unset falls back to the category)
 *   products.chatbot_eta_offset_days  integer | null  (Y: same override rule)
 * Both ride the GET/PUT /api/v1/master-data/products/{id} payload as plain
 * fields, gated the same way as the category columns (see categoryService.ts).
 */

export interface GetProductsParams extends DataGridApiFetchParams {
  category_id?: string;
  /**
   * One brand id, or a comma-separated list (`a,b`) which the backend filters
   * with IN. The list form is what a discontinued-notice deep link carries for a
   * recipient scoped to specific brands.
   */
  brand_id?: string;
  status?: 'active' | 'inactive' | 'all';
  price_min?: number;
  price_max?: number;
  item_type?: string;
  /** Filter by position in the variant graph. Default 'all' (no filter). */
  variant_filter?: 'base' | 'variant' | 'all';
  /** Deep link from a "products discontinued" notification - show only that batch. */
  discontinued_batch_id?: string;
  /** "Discontinued at" range (issue #1287), YYYY-MM-DD, Malaysia calendar day inclusive. */
  discontinued_from?: string;
  discontinued_to?: string;
}


/**
 * Get products with pagination, sorting, and filtering
 */
export async function getProducts(
  params: GetProductsParams,
): Promise<ProductApiResponse> {
  const {
    pageIndex,
    pageSize,
    sorting,
    searchQuery,
    category_id,
    brand_id,
    status,
    price_min,
    price_max,
    item_type,
    variant_filter,
    discontinued_batch_id,
    discontinued_from,
    discontinued_to,
  } = params;

  const sortField = sorting?.[0]?.id || '';
  const sortDirection = sorting?.[0]?.desc ? 'desc' : 'asc';

  const queryParams = new URLSearchParams({
    page: String(pageIndex + 1),
    limit: String(pageSize),
    ...(sortField ? { sort: sortField, dir: sortDirection } : {}),
    ...(searchQuery ? { query: searchQuery } : {}),
    ...(category_id ? { category_id } : {}),
    ...(brand_id ? { brand_id } : {}),
    ...(status && status !== 'all' ? { status } : {}),
    ...(price_min ? { price_min: String(price_min) } : {}),
    ...(price_max ? { price_max: String(price_max) } : {}),
    ...(item_type ? { item_type } : {}),
    ...(variant_filter && variant_filter !== 'all' ? { variant_filter } : {}),
    ...(discontinued_batch_id ? { discontinued_batch_id } : {}),
    ...(discontinued_from ? { discontinued_from } : {}),
    ...(discontinued_to ? { discontinued_to } : {}),
  });

  const response = await apiFetch(
    `/api/v1/master-data/products?${queryParams.toString()}`,
    {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    },
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({
      message: 'Failed to fetch products',
    }));
    throw new Error(error.message || 'Failed to fetch products');
  }

  return response.json();
}

/**
 * Get single product by ID
 */
export async function getProduct(id: string): Promise<ProductDetail> {
  const response = await apiFetch(`/api/v1/master-data/products/${id}`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
    credentials: 'include',
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({
      message: 'Failed to fetch product',
    }));
    throw new Error(error.message || 'Failed to fetch product');
  }

  return response.json();
}

/**
 * Create new product
 */
export async function createProduct(
  data: ProductFormData,
): Promise<Product> {
  const response = await apiFetch('/api/v1/master-data/products', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({
      message: 'Failed to create product',
    }));
    throw new Error(error.message || 'Failed to create product');
  }

  return response.json();
}

/**
 * Update existing product
 */
export async function updateProduct(
  id: string,
  data: Partial<ProductFormData>,
): Promise<Product> {
  const response = await apiFetch(`/api/v1/master-data/products/${id}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({
      message: 'Failed to update product',
    }));
    throw new Error(error.message || 'Failed to update product');
  }

  return response.json();
}

/**
 * Set / change a product's variant parent (manual curation).
 * Reused by "Add variant" (attach a child): call with the CHILD's id and the
 * parent's id. `parentId` accepts a UUID or product_code; the backend resolves
 * it and enforces self/cycle rules (surfaced as 400s).
 */
export async function setVariantParent(
  productId: string,
  parentId: string,
): Promise<ProductDetail> {
  const response = await apiFetch(
    `/api/v1/master-data/products/${productId}/variant-parent`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parent_id: parentId }),
    },
  );

  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to set variant parent'));
  }

  return response.json();
}

/**
 * Unlink a product from its variant parent (manual curation).
 * Reused by "Remove variant": call with the CHILD's id.
 */
export async function unlinkVariant(productId: string): Promise<ProductDetail> {
  const response = await apiFetch(
    `/api/v1/master-data/products/${productId}/variant-parent`,
    {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
    },
  );

  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to unlink variant'));
  }

  return response.json();
}

/**
 * Reset a manually-curated product back to automatic variant derivation.
 */
export async function resetVariantAuto(productId: string): Promise<ProductDetail> {
  const response = await apiFetch(
    `/api/v1/master-data/products/${productId}/variant-reset`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    },
  );

  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to reset variant link'));
  }

  return response.json();
}

/**
 * The same `/select` rows, keeping the fields a picked product decides for the line it lands on.
 *
 * `getProductsForVariantSelect` below narrows every row to a code and a name, which is all a
 * variant picker needs. A quotation line needs the record itself: choosing a product there has
 * to answer the description, the brand, the unit and the list price beside it, and a second
 * round trip per pick to fetch what the dropdown was already holding would be latency for
 * nothing. The endpoint returns the whole product row, so this mapper simply keeps more of it.
 */
export async function getProductsForLineSelect(
  query?: string,
  options?: { companyScope?: 'grants' },
): Promise<ProductLineRef[]> {
  const queryParams = new URLSearchParams(query ? { query } : {});
  if (options?.companyScope) queryParams.set('company_scope', options.companyScope);
  const response = await apiFetch(
    `/api/v1/master-data/products/select?${queryParams.toString()}`,
    {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
    },
  );

  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to fetch products'));
  }

  const body = await response.json();
  const rows: Array<{
    id: string;
    product_code: string;
    product_name: string;
    description?: string | null;
    brand_id?: string | null;
    base_uom_id?: string | null;
    list_price?: number | string | null;
    company_name?: string | null;
  }> = body?.data ?? [];
  return rows.map((p) => ({
    id: p.id,
    product_code: p.product_code,
    product_name: p.product_name,
    description: p.description ?? null,
    brand_id: p.brand_id ?? null,
    base_uom_id: p.base_uom_id ?? null,
    // Left as the STRING the API sent. A price that becomes a number here comes back out of
    // `String(...)` as `1250` or `392.85000000000002`, and the line endpoints take decimals.
    list_price: p.list_price === null || p.list_price === undefined ? null : String(p.list_price),
    company_name: p.company_name ?? undefined,
  }));
}

/**
 * Product options for the variant-parent / add-child combobox.
 * Maps the shared `/select` endpoint to human-readable refs (no UUID in the UI).
 */
export async function getProductsForVariantSelect(
  query?: string,
): Promise<ProductVariantRef[]> {
  const queryParams = new URLSearchParams(query ? { query } : {});
  const response = await apiFetch(
    `/api/v1/master-data/products/select?${queryParams.toString()}`,
    {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
    },
  );

  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to fetch products'));
  }

  const body = await response.json();
  const rows: Array<{ id: string; product_code: string; product_name: string }> =
    body?.data ?? [];
  return rows.map((p) => ({
    id: p.id,
    product_code: p.product_code,
    product_name: p.product_name,
  }));
}

/**
 * Permanently delete product from database
 */
export async function deleteProduct(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/master-data/products/${id}`, {
    method: 'DELETE',
    headers: {
      'Content-Type': 'application/json',
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({
      message: 'Failed to delete product',
    }));
    throw new Error(error.message || 'Failed to delete product');
  }
}

/**
 * Duplicate product
 */
export async function duplicateProduct(
  id: string,
  newProductCode: string,
): Promise<Product> {
  const response = await apiFetch(`/api/v1/master-data/products/${id}/duplicate`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ product_code: newProductCode }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({
      message: 'Failed to duplicate product',
    }));
    throw new Error(error.message || 'Failed to duplicate product');
  }

  return response.json();
}

/**
 * Get price history for a product
 */
export async function getPriceHistory(id: string): Promise<PriceHistory[]> {
  const response = await apiFetch(
    `/api/v1/master-data/products/${id}/price-history`,
    {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    },
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({
      message: 'Failed to fetch price history',
    }));
    throw new Error(error.message || 'Failed to fetch price history');
  }

  return response.json();
}

/** One purchase-order line that bought this product. */
export interface ProductPurchaseLine {
  purchase_order_id: string;
  po_number: string;
  /** Date-only ISO string, or null on an order with no issue date. */
  issue_date: string | null;
  status: string;
  supplier_code: string | null;
  supplier_name: string | null;
  qty_ordered: number | null;
  qty_received: number | null;
  unit_cost: number | null;
  currency: string | null;
}

/** What we last paid, and the evidence for it.
 *
 *  `status` separates three facts a bare dash cannot: a known price (`ok`), an item nobody
 *  has ever bought (`never_purchased`), and orders that exist but carry no unit cost
 *  (`no_price_recorded`). A recorded 0 is a price OF zero and comes back as `ok`. */
export interface ProductCostSummary {
  status: 'ok' | 'never_purchased' | 'no_price_recorded';
  unit_cost: number | null;
  currency: string | null;
  po_number: string | null;
  purchase_order_id: string | null;
  supplier_code: string | null;
  supplier_name: string | null;
  issue_date: string | null;
}

export interface ProductPurchaseHistory {
  product_id: string;
  lines: ProductPurchaseLine[];
  /** Every line that exists, whether or not it was returned - the cap is never silent. */
  total: number;
  shown: number;
  cost: ProductCostSummary;
}

/** GET /api/v1/master-data/products/{id}/purchase-history?limit=n */
export async function getProductPurchaseHistory(
  id: string,
  limit = 50,
): Promise<ProductPurchaseHistory> {
  const response = await apiFetch(
    `/api/v1/master-data/products/${id}/purchase-history?limit=${limit}`,
  );
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load purchase history'));
  }
  return response.json();
}

/** What `PUT /products/bulk` accepts: one field today, the chat-search flag. */
export type ProductBulkUpdates = Pick<ProductFormData, 'is_searchable'>;

/**
 * Bulk update products: the same edit applied to every selected row.
 */
export async function bulkUpdateProducts(
  ids: string[],
  updates: ProductBulkUpdates,
): Promise<{ updated_count: number }> {
  const response = await apiFetch('/api/v1/master-data/products/bulk', {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ ids, updates }),
  });

  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to update products'));
  }
  return response.json();
}

// No bulk-delete client: the list parks one `product.delete` per selected row through
// `/pending-actions` (D7), so this route has no frontend caller. The route itself still
// stands for the API and n8n.

export interface ValidateImportResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
  summary?: { total_rows?: number; would_create?: number; would_update?: number; error_count?: number };
}

/**
 * Validate product import data without importing (same validation as bulk import).
 */
export async function validateProductsImport(
  data: Record<string, unknown>[],
): Promise<ValidateImportResult> {
  const response = await apiFetch('/api/v1/master-data/products/bulk-import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ products: data, validate_only: true }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Validation failed' }));
    const message =
      typeof error.detail === 'string'
        ? error.detail
        : Array.isArray(error.detail)
          ? error.detail.map((e: { msg?: string }) => e.msg || String(e)).join('; ')
          : error.message || 'Validation failed';
    throw new Error(message);
  }
  return response.json();
}

/**
 * Bulk import products from Excel data (queued).
 * Expected columns: Item Code, Description, Desc 2, Item Group, Item Brand, Price, Is Active (T/F), UOM (optional),
 * Reorder Level (optional), Reorder Qty (optional).
 * Item Group / Item Brand / UOM match a category / brand / unit-of-measure code or name;
 * anything unmatched is created by the import (code = name = the value in the file),
 * so master data does not have to exist before the upload.
 * Reorder Level / Reorder Qty land on the product AND on the SCM planning table. A 0 is a
 * real level; a blank cell in a file that CARRIES the column clears the held one; a file
 * with no such column leaves every level untouched.
 * Returns job_id for tracking progress in Import Jobs.
 */
export async function bulkImportProducts(
  data: Record<string, unknown>[],
): Promise<{ job_id: string; status: string; message: string }> {
  const response = await apiFetch('/api/v1/master-data/products/bulk-import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ products: data }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to queue import job' }));
    const message =
      typeof error.detail === 'string'
        ? error.detail
        : Array.isArray(error.detail)
          ? error.detail.map((e: { msg?: string }) => e.msg || String(e)).join('; ')
          : error.message || 'Failed to queue import job';
    throw new Error(message);
  }
  return response.json();
}

/**
 * Export products to CSV
 */
export async function exportProducts(
  filters?: ProductFilters,
): Promise<Blob> {
  const queryParams = new URLSearchParams();
  if (filters) {
    if (filters.category_id) queryParams.set('category_id', filters.category_id);
    if (filters.brand_id) queryParams.set('brand_id', filters.brand_id);
    if (filters.is_active !== undefined)
      queryParams.set('is_active', String(filters.is_active));
    if (filters.search) queryParams.set('search', filters.search);
    if (filters.price_range?.min)
      queryParams.set('price_min', String(filters.price_range.min));
    if (filters.price_range?.max)
      queryParams.set('price_max', String(filters.price_range.max));
  }

  const response = await apiFetch(
    `/api/v1/master-data/products/export?${queryParams.toString()}`,
    {
      method: 'GET',
      headers: {
        Accept: 'text/csv',
      },
    },
  );

  if (!response.ok) {
    const error = await response.json().catch(() => ({
      message: 'Failed to export products',
    }));
    throw new Error(error.message || 'Failed to export products');
  }

  return response.blob();
}
