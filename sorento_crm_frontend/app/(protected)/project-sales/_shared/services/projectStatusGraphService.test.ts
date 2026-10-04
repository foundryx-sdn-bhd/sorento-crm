/**
 * NS-SHARED-LOOKUPS (never-stuck L10): the pipeline board, the status-move buttons and the
 * task status dropdown read the salesperson-readable project-sales route, never the admin
 * `/system/statuses/graph` one (which needs `system.statuses.view`).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

import { apiFetch } from '@/lib/api';
import { getProjectSalesStatusGraph } from './projectStatusGraphService';

const mockedFetch = vi.mocked(apiFetch);

describe('getProjectSalesStatusGraph', () => {
  beforeEach(() => {
    mockedFetch.mockReset();
    mockedFetch.mockResolvedValue({ ok: true, json: async () => ({ statuses: [], transitions: [] }) } as Response);
  });

  it.each(['project', 'project_task', 'project_lead'] as const)('reads the %s graph from project-sales', async (entity) => {
    await getProjectSalesStatusGraph(entity);
    expect(mockedFetch).toHaveBeenCalledWith(`/api/v1/project-sales/status-graph/${entity}`);
  });

  it('passes a template scope', async () => {
    await getProjectSalesStatusGraph('project_task', 'tpl 1');
    expect(mockedFetch).toHaveBeenCalledWith('/api/v1/project-sales/status-graph/project_task?scope_id=tpl%201');
  });

  it('throws the refusal so the screen can say why', async () => {
    mockedFetch.mockResolvedValue({
      ok: false,
      headers: new Headers({ 'content-type': 'application/json' }),
      status: 403,
      json: async () => ({ detail: 'Permission required: projects.projects.view' }),
    } as Response);
    await expect(getProjectSalesStatusGraph('project')).rejects.toThrow('Permission required: projects.projects.view');
  });
});
