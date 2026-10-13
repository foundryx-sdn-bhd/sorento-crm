/**
 * Model price service (MODEL-PRICES).
 * Layering: UI -> hooks (useModelPrices) -> THIS service -> lib/api -> backend.
 *
 *   GET    /api/v1/system/ai-assistant/model-prices            list, `system.ai_assistant_settings.view`
 *   GET    /api/v1/system/ai-assistant/model-prices/unpriced   models in the usage log with no price
 *   POST   /api/v1/system/ai-assistant/model-prices            create, `system.ai_assistant_settings.edit`
 *   PUT    /api/v1/system/ai-assistant/model-prices/{id}       update (the model never changes)
 *
 * No delete here: `llm_model_price.delete` is a server-deferred pending action.
 */

import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

const BASE = '/api/v1/system/ai-assistant/model-prices';

export interface ModelPrice {
  id: string;
  model: string;
  input_usd_per_1m: number;
  cached_input_usd_per_1m: number | null;
  output_usd_per_1m: number;
  note: string | null;
  updated_at: string;
}

export interface ModelPriceInput {
  model: string;
  input_usd_per_1m: number;
  cached_input_usd_per_1m: number | null;
  output_usd_per_1m: number;
  note: string | null;
}

export interface UnpricedModel {
  model: string;
  calls: number;
  last_seen_at: string;
}

async function unwrap<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) throw new Error(await extractApiError(response, fallback));
  return response.json() as Promise<T>;
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export async function listModelPrices(): Promise<ModelPrice[]> {
  return unwrap(await apiFetch(BASE), 'Failed to load model prices.');
}

export async function listUnpricedModels(): Promise<UnpricedModel[]> {
  return unwrap(await apiFetch(`${BASE}/unpriced`), 'Failed to load unpriced models.');
}

export async function createModelPrice(input: ModelPriceInput): Promise<ModelPrice> {
  const response = await apiFetch(BASE, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(input),
  });
  return unwrap(response, 'Failed to save the price.');
}

export async function updateModelPrice(id: string, input: ModelPriceInput): Promise<ModelPrice> {
  const response = await apiFetch(`${BASE}/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify(input),
  });
  return unwrap(response, 'Failed to save the price.');
}
