/**
 * Product alternatives - feature service.
 * Layering: UI -> hooks (useProductAlternatives) -> THIS service -> lib/api-client.
 *
 *  GET  /api/v1/master-data/products/{id}/alternatives
 *    -> { data: { alternatives_manual, alternatives: ProductAlternativeRow[] } }
 *  POST /api/v1/master-data/products/{id}/alternatives  { alternative_product_id } -> 201 { data: Row }
 *
 * Unlink is a deferred action (`product_alternative.unlink`), not called from here.
 */
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type {
  ProductAlternativeRow,
  ProductAlternativesResult,
} from '../types/productAlternative.types';

const base = (productId: string) =>
  `/api/v1/master-data/products/${encodeURIComponent(productId)}/alternatives`;

async function readResult(res: Response): Promise<ProductAlternativesResult> {
  const body = (await res.json()) as { data?: Partial<ProductAlternativesResult> };
  return {
    alternatives_manual: body.data?.alternatives_manual ?? false,
    alternatives: body.data?.alternatives ?? [],
  };
}

export async function getProductAlternatives(
  productId: string,
): Promise<ProductAlternativesResult> {
  const res = await apiFetch(base(productId));
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to load alternatives'));
  return readResult(res);
}

export async function addProductAlternative(
  productId: string,
  alternativeProductId: string,
): Promise<ProductAlternativeRow> {
  const res = await apiFetch(base(productId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ alternative_product_id: alternativeProductId }),
  });
  if (!res.ok) throw new Error(await extractApiError(res, 'Failed to add the alternative'));
  const body = (await res.json()) as { data: ProductAlternativeRow };
  return body.data;
}
