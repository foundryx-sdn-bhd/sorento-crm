/**
 * L9 (NEVER-STUCK-UI S3, S5.3, S6.4; audit row 11). The user layout owns the
 * record read for every user tab. A non-404 failure used to leave `user`
 * undefined and every tab (hero, profile, danger zone) on its skeleton forever,
 * and a 404 redirected from inside `queryFn` (so once per retry, with `push`).
 */
import React, { Suspense } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import UserLayout from './layout';
import { pendingEntityStore } from '@/lib/pending-entity-store';

const h = vi.hoisted(() => ({ apiFetch: vi.fn(), push: vi.fn(), replace: vi.fn(), toast: vi.fn() }));

vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));
vi.mock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => h.apiFetch(...args) }));
vi.mock('next/navigation', () => ({
  usePathname: () => '/user-management/users/u1',
  useRouter: () => ({ push: h.push, replace: h.replace }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/toast', () => ({ toast: Object.assign(h.toast, { success: vi.fn(), error: vi.fn() }) }));
vi.mock('@/components/common/RecordNavigation', () => ({ default: () => null }));
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('./components/user-hero', () => ({ default: () => <div>hero</div> }));

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const PARAMS = Promise.resolve({ id: 'u1' });
const clients: QueryClient[] = [];

async function renderLayout() {
  // The layout sets its own retry rule; the client default must not mask it.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <Suspense fallback={null}>
          <UserLayout params={PARAMS}>
            <div>child tab</div>
          </UserLayout>
        </Suspense>
      </QueryClientProvider>,
    );
  });
}

beforeEach(async () => {
  await PARAMS;
  h.apiFetch.mockReset();
  h.push.mockReset();
  h.replace.mockReset();
  h.toast.mockReset();
  clients.length = 0;
  pendingEntityStore.reset();
});
afterEach(() => cleanup());

describe('user detail when the record read fails', () => {
  it('a 500 shows the error with Retry in place of the tabs, no endless skeleton', async () => {
    h.apiFetch.mockImplementation(async () => json(500, { detail: 'Database unavailable' }));
    await renderLayout();

    expect(await screen.findByText('Could not load this user', {}, { timeout: 4000 })).toBeTruthy();
    expect(screen.getByText('Database unavailable')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.queryByText('child tab')).toBeNull();
    expect(screen.queryByText('hero')).toBeNull();
  });

  it('Retry refetches and draws the tabs once the read succeeds', async () => {
    h.apiFetch.mockImplementationOnce(async () => json(500, { detail: 'Database unavailable' }));
    h.apiFetch.mockImplementationOnce(async () => json(500, { detail: 'Database unavailable' }));
    h.apiFetch.mockImplementation(async () => json(200, { id: 'u1', name: 'Ada', roles: [] }));
    await renderLayout();

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }, { timeout: 4000 }));
    expect(await screen.findByText('child tab')).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Profile' })).toBeTruthy();
  });

  it('a 403 shows AccessDenied, never the raw permission string', async () => {
    h.apiFetch.mockResolvedValue(
      json(403, { detail: 'Permission required: user_management.users.view' }),
    );
    await renderLayout();

    expect(await screen.findByText("You don't have access to this page")).toBeTruthy();
    expect(screen.queryByText(/Permission required/)).toBeNull();
    expect(screen.queryByText('child tab')).toBeNull();
    // A refusal is final: no retry round-trip.
    expect(h.apiFetch).toHaveBeenCalledTimes(1);
  });

  it('a 404 goes back to the list once, with replace (no Back-button trap), not from queryFn', async () => {
    h.apiFetch.mockImplementation(async () => json(404, { detail: 'User not found' }));
    await renderLayout();

    await waitFor(() => expect(h.replace).toHaveBeenCalledWith('/user-management/users'));
    expect(h.replace).toHaveBeenCalledTimes(1);
    expect(h.push).not.toHaveBeenCalled();
    expect(h.apiFetch).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('child tab')).toBeNull();
  });

  it('a user this tab just deleted leaves for the list once, through the deleted-record guard only', async () => {
    pendingEntityStore.noteCommittedDelete('u1');
    h.apiFetch.mockImplementation(async () => json(404, { detail: 'User not found' }));
    await renderLayout();

    await waitFor(() => expect(h.replace).toHaveBeenCalledWith('/user-management/users'));
    expect(h.replace).toHaveBeenCalledTimes(1);
    expect(h.toast).toHaveBeenCalledWith('Already deleted', expect.anything());
  });

  it('a failed background refetch keeps the user on screen', async () => {
    h.apiFetch.mockImplementation(async () => json(200, { id: 'u1', name: 'Ada', roles: [] }));
    await renderLayout();
    expect(await screen.findByText('child tab')).toBeTruthy();

    h.apiFetch.mockImplementation(async () => json(500, { detail: 'Database unavailable' }));
    await act(async () => {
      await clients[0].refetchQueries({ queryKey: ['user-user', 'u1'] });
    });
    await waitFor(() => expect(h.apiFetch.mock.calls.length).toBeGreaterThanOrEqual(2));
    // Give the error a chance to reach the layout before asserting it did not swap.
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText('child tab')).toBeTruthy();
    expect(screen.queryByText('Could not load this user')).toBeNull();
  });
});
