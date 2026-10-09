/**
 * Claude connector consent - feature service.
 *
 * Layering: ConsentCard -> THIS service -> lib/api.
 *   GET  /api/v1/analytics-connector/authorize/{request_id}
 *   POST /api/v1/analytics-connector/authorize/{request_id}/approve | /deny -> { redirect_to }
 * A failure throws an Error carrying `code` (the backend `detail`, e.g. "expired") and `status`.
 */
import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';

const BASE = '/api/v1/analytics-connector/authorize';

export interface ConsentDetails {
  request_id: string;
  client_name: string;
  redirect_host: string;
  user_name: string;
  scope?: string;
  /** Naive UTC. */
  expires_at?: string;
  allowed: boolean;
}

export class ConsentError extends Error {
  constructor(
    public code: string,
    public status: number,
  ) {
    super(code);
  }
}

async function failure(response: Response, fallback: string): Promise<never> {
  const code = await extractApiError(response, fallback);
  throw new ConsentError(code, response.status);
}

export async function getConsent(requestId: string): Promise<ConsentDetails> {
  const response = await apiFetch(`${BASE}/${encodeURIComponent(requestId)}`);
  if (!response.ok) return failure(response, 'request_unavailable');
  return response.json();
}

async function answer(requestId: string, verb: 'approve' | 'deny'): Promise<{ redirect_to: string }> {
  const response = await apiFetch(`${BASE}/${encodeURIComponent(requestId)}/${verb}`, { method: 'POST' });
  if (!response.ok) return failure(response, 'request_unavailable');
  return response.json();
}

export const approveConsent = (requestId: string) => answer(requestId, 'approve');
export const denyConsent = (requestId: string) => answer(requestId, 'deny');
