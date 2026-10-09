/**
 * Claude connector consent - feature service (CLAUDE-CONNECTOR r3, contract section 2).
 *
 * Layering: ConsentCard -> hooks/useConsent -> THIS service -> lib/api.
 *   POST /api/v1/connector/consent/inspect | approve | deny   body { req }
 * `req` is the shared service's signed consent request; it travels in the body, never in a URL.
 * A failure throws an Error carrying `code` (the backend's error.code, e.g. "request_expired")
 * and `status`.
 */
import { apiFetch } from '@/lib/api';
import { codedError } from '@/lib/api-client';

const BASE = '/api/v1/connector/consent';

export interface ConsentDetails {
  request_id: string;
  client_name: string;
  redirect_host: string;
  user_name: string;
  /** ISO 8601 UTC. */
  expires_at?: string;
  allowed: boolean;
}

export interface ConsentApproval {
  request_id: string;
  return_url: string;
  assertion: string;
}

export interface ConsentDenial {
  request_id: string;
  return_url: string;
}

export type ConsentFailure = Error & { code: string | null; status: number };

async function post<T>(verb: 'inspect' | 'approve' | 'deny', req: string): Promise<T> {
  const response = await apiFetch(`${BASE}/${verb}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ req }),
  });
  if (!response.ok) {
    const error = (await codedError(response, 'request_unavailable')) as ConsentFailure;
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export const inspectConsent = (req: string) => post<ConsentDetails>('inspect', req);
export const approveConsent = (req: string) => post<ConsentApproval>('approve', req);
export const denyConsent = (req: string) => post<ConsentDenial>('deny', req);
