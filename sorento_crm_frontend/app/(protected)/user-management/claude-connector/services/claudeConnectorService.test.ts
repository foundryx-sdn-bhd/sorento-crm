/**
 * CLAUDE-CONNECTOR S3 red test. Contract the coder builds to:
 *   file  app/(protected)/user-management/claude-connector/services/claudeConnectorService.ts
 *   exports listGrants(params), getGrant(id)
 * Layering: UI -> hooks -> THIS service -> lib/api (apiFetch) -> backend. Query strings go through
 * buildDataGridParams (page, limit, sort, dir, query) plus the `status` extra.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

import { getGrant, listGrants } from './claudeConnectorService';

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
const fail = (status: number, body: unknown) =>
  ({ ok: false, status, headers: new Headers({ 'content-type': 'application/json' }), json: async () => body, text: async () => JSON.stringify(body) }) as Response;

beforeEach(() => apiFetch.mockReset());

describe('claudeConnectorService', () => {
  it('lists grants with the DataGrid params and the status filter', async () => {
    const page = { data: [{ id: 'g1' }], pagination: { total: 1, page: 1, limit: 25 } };
    apiFetch.mockResolvedValue(ok(page));
    const out = await listGrants(
      { pageIndex: 0, pageSize: 25, sorting: [{ id: 'created_at', desc: true }], searchQuery: 'ann' },
      { status: 'Active' },
    );
    expect(apiFetch.mock.calls[0][0]).toBe(
      '/api/v1/analytics-connector/grants?page=1&limit=25&sort=created_at&dir=desc&query=ann&status=Active',
    );
    expect(out).toEqual(page);
  });

  it('omits status when it is not set', async () => {
    apiFetch.mockResolvedValue(ok({ data: [], pagination: { total: 0, page: 1, limit: 25 } }));
    await listGrants({ pageIndex: 1, pageSize: 10, sorting: [], searchQuery: '' });
    expect(apiFetch.mock.calls[0][0]).toBe('/api/v1/analytics-connector/grants?page=2&limit=10');
  });

  it('throws the extracted API error when the list fails', async () => {
    apiFetch.mockResolvedValue(fail(403, { detail: 'Permission required: analytics.connector.manage' }));
    await expect(
      listGrants({ pageIndex: 0, pageSize: 25, sorting: [], searchQuery: '' }),
    ).rejects.toThrow(/permission required/i);
  });

  it('gets one grant by id', async () => {
    const grant = { id: 'g 1', recent_calls: [] };
    apiFetch.mockResolvedValue(ok(grant));
    expect(await getGrant('g 1')).toEqual(grant);
    expect(apiFetch.mock.calls[0][0]).toBe('/api/v1/analytics-connector/grants/g%201');
  });
});
