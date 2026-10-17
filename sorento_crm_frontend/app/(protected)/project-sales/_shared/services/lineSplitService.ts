import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type {
  LineSplitsPayload,
  LineSplitsSaveBody,
} from '../types/lineSplit.types';

const BASE = '/api/v1/project-sales';

/** OI-LINE-SPLIT: every open line of one sales order with its base and its splits. */
export async function getLineSplits(psoId: string): Promise<LineSplitsPayload> {
  const response = await apiFetch(`${BASE}/sales-orders/${psoId}/line-splits`);
  if (!response.ok)
    throw new Error(
      await extractApiError(response, 'Failed to load the splits'),
    );
  return response.json();
}

/** Replaces the splits of every line in the body in one call; answers the GET payload. */
export async function saveLineSplits(
  psoId: string,
  body: LineSplitsSaveBody,
): Promise<LineSplitsPayload> {
  const response = await apiFetch(`${BASE}/sales-orders/${psoId}/line-splits`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok)
    throw new Error(
      await extractApiError(response, 'Failed to save the splits'),
    );
  return response.json();
}
