import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { StatusGraph } from '@/app/(protected)/system-management/status-graphs/types/statusGraph.types';

const BASE = '/api/v1/project-sales';

/** The graphs project-sales screens ride. The backend 404s any other entity. */
export type ProjectSalesGraphEntity = 'project' | 'project_task' | 'project_lead';

/**
 * Mirrors `VIEW` in `app/api/v1/projects/status_graphs.py`. Any project viewer can read the
 * pipeline stages and the status moves; the admin `/system/statuses/graph/{entity}` route
 * needs `system.statuses.view`, which salespeople do not hold (never-stuck L10).
 */
export const PROJECT_STATUS_GRAPH_PERMS = { read: 'projects.projects.view' } as const;

/**
 *   GET /project-sales/status-graph/{project|project_task|project_lead}?scope_id= -> StatusGraph
 *
 * Same response shape as the admin route (without record counts), so the shared
 * `availableStatusMoves` helpers read it unchanged.
 */
export async function getProjectSalesStatusGraph(
  entityType: ProjectSalesGraphEntity,
  scopeId?: string | null,
): Promise<StatusGraph> {
  const query = scopeId ? `?scope_id=${encodeURIComponent(scopeId)}` : '';
  const response = await apiFetch(`${BASE}/status-graph/${entityType}${query}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load the status steps'));
  return response.json();
}
