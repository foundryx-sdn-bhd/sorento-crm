/**
 * Seat cover <-> WC compatibility - feature service.
 * Layering: UI -> hooks (useProductCompatibility) -> THIS service -> lib/api-client.
 *
 *  GET  /api/v1/master-data/products/{id}/compatibility -> { data: { covers, fits } }
 *  POST /api/v1/master-data/products/{id}/compatibility
 *       { other_product_id, other_role } -> 201 { data: Row }
 *
 * Unlink (`product_compatibility.unlink`) is a deferred action, not called from here.
 */
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type {
  CompatibilityRole,
  ProductCompatibilityResult,
  ProductCompatibilityRow,
} from '../types/productCompatibility.types';

const base = (productId: string) =>
  `/api/v1/master-data/products/${encodeURIComponent(productId)}/compatibility`;

export async function getProductCompatibility(
  productId: string,
): Promise<ProductCompatibilityResult> {
  const res = await apiFetch(base(productId));
  if (!res.ok)
    throw new Error(
      await extractApiError(res, 'Failed to load compatible products'),
    );
  const body = (await res.json()) as {
    data?: Partial<ProductCompatibilityResult>;
  };
  return { covers: body.data?.covers ?? [], fits: body.data?.fits ?? [] };
}

export async function addProductCompatibility(
  productId: string,
  otherProductId: string,
  otherRole: CompatibilityRole,
): Promise<ProductCompatibilityRow> {
  const res = await apiFetch(base(productId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      other_product_id: otherProductId,
      other_role: otherRole,
    }),
  });
  if (!res.ok)
    throw new Error(await extractApiError(res, 'Failed to add the link'));
  const body = (await res.json()) as { data: ProductCompatibilityRow };
  return body.data;
}
