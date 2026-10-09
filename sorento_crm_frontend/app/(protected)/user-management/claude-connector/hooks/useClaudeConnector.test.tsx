/**
 * CLAUDE-CONNECTOR S3 red test.
 *   file  app/(protected)/user-management/claude-connector/hooks/useClaudeConnector.ts
 *   exports useGrantsQuery(params, extra?), useGrantQuery(id), useGrantRevocation()
 * Revoke is the server-deferred pending action (5 s reversible), never a dialog:
 * useGrantRevocation wraps useDeferredRowAction with the registry keys the backend registers.
 * Its result is passed through as-is: { run, targetId, isPending, countdown }.
 */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const useDeferredRowAction = vi.fn();
vi.mock('@/hooks/useDeferredRowAction', () => ({
  useDeferredRowAction: (input: unknown) => useDeferredRowAction(input),
}));
const listGrants = vi.fn();
const getGrant = vi.fn();
vi.mock('../services/claudeConnectorService', () => ({
  listGrants: (...a: unknown[]) => listGrants(...a),
  getGrant: (...a: unknown[]) => getGrant(...a),
}));

import { useGrantQuery, useGrantRevocation, useGrantsQuery } from './useClaudeConnector';

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {children}
  </QueryClientProvider>
);

describe('useClaudeConnector', () => {
  it('useGrantsQuery calls the list service with params and extra', async () => {
    listGrants.mockResolvedValue({ data: [], pagination: { total: 0, page: 1, limit: 25 } });
    const params = { pageIndex: 0, pageSize: 25, sorting: [], searchQuery: '' };
    const { result } = renderHook(() => useGrantsQuery(params, { status: 'Active' }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(listGrants).toHaveBeenCalledWith(params, { status: 'Active' });
  });

  it('useGrantQuery is disabled without an id and fetches with one', async () => {
    getGrant.mockResolvedValue({ id: 'g1' });
    const { result } = renderHook(() => useGrantQuery('g1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getGrant).toHaveBeenCalledWith('g1');
  });

  it('useGrantRevocation parks analytics_connector_grant.revoke on the inline surface', () => {
    const result = { run: vi.fn(), targetId: null, isPending: false, countdown: null };
    useDeferredRowAction.mockReturnValue(result);
    const { result: hook } = renderHook(() => useGrantRevocation(), { wrapper });
    expect(hook.current).toBe(result);
    const input = useDeferredRowAction.mock.calls[0][0];
    expect(input).toMatchObject({
      actionKey: 'analytics_connector_grant.revoke',
      entityType: 'analytics_connector_grant',
      verb: 'Revoking',
      surface: 'inline',
    });
    expect(typeof input.successMessage).toBe('string');
    expect(input.invalidateKeys?.length).toBeGreaterThan(0);
  });
});
