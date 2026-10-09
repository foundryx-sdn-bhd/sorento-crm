/**
 * Claude connector access - feature service.
 *
 * Layering: components -> hooks (useClaudeConnector) -> THIS service -> lib/api-client.
 *
 * Backend (reads `analytics.connector.manage`):
 *   GET /api/v1/analytics-connector/grants?page&limit&sort&dir&query&status
 *   GET /api/v1/analytics-connector/grants/{id}
 * Revoke has NO route: it is the pending action `analytics_connector_grant.revoke`.
 */
import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type { DataGridApiFetchParams } from '@/components/ui/data-grid';
import type { GrantDetail, GrantsPage } from '../types/claudeConnector.types';

const BASE = '/api/v1/analytics-connector/grants';

export type GrantsExtraParams = { status?: string };

export async function listGrants(
  params: DataGridApiFetchParams,
  extra?: GrantsExtraParams,
): Promise<GrantsPage> {
  const response = await apiFetch(`${BASE}?${buildDataGridParams(params, extra).toString()}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load Claude connector grants'));
  }
  return response.json();
}

export async function getGrant(id: string): Promise<GrantDetail> {
  const response = await apiFetch(`${BASE}/${encodeURIComponent(id)}`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load the grant'));
  }
  return response.json();
}
