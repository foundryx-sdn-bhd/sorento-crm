/**
 * L6 (NEVER-STUCK-UI S3 "Permissions query failure is not no access", audit row 34).
 *
 * When GET /me/permissions fails, the permission set is empty, and today every
 * guarded page (67 of them) tells a user who HAS access "You don't have access to
 * this page". A failed check must say it failed and offer Retry; only a check that
 * succeeded and lacks the slug is "no access".
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import RequireAccess from './RequireAccess';
import PermissionsLoadBanner from './PermissionsLoadBanner';

const fetchMyPermissions = vi.fn();
const failPermissions = async (): Promise<string[]> => {
  throw new Error('Failed to fetch permissions');
};
vi.mock('@/lib/permissions-service', () => ({
  fetchMyPermissions: () => fetchMyPermissions(),
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { id: 'u1' } }, status: 'authenticated' }),
}));

// Container reads the app settings provider, which this test does not stand up.
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/components/common/screen-loader', () => ({
  ScreenLoader: () => <div>loading-access</div>,
}));

function wrap(ui: React.ReactNode) {
  // retry: false - the failure under test must settle at once, not after the app's retry.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  fetchMyPermissions.mockReset();
});
afterEach(() => cleanup());

describe('RequireAccess when the permission check fails', () => {
  it('shows "Could not check your access" with Retry, never AccessDenied', async () => {
    fetchMyPermissions.mockImplementation(failPermissions);

    wrap(
      <RequireAccess permission="orders.view">
        <div>page body</div>
      </RequireAccess>,
    );

    expect(await screen.findByText('Could not check your access')).toBeTruthy();
    expect(screen.queryByText("You don't have access to this page")).toBeNull();
    expect(screen.queryByText('page body')).toBeNull();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('Retry refetches and renders the page once the check succeeds', async () => {
    fetchMyPermissions.mockImplementationOnce(failPermissions);
    fetchMyPermissions.mockResolvedValue(['orders.view']);

    wrap(
      <RequireAccess permission="orders.view">
        <div>page body</div>
      </RequireAccess>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('page body')).toBeTruthy();
    expect(fetchMyPermissions).toHaveBeenCalledTimes(2);
  });

  it('still shows AccessDenied when the check succeeded without the slug', async () => {
    fetchMyPermissions.mockResolvedValue(['something.else']);

    wrap(
      <RequireAccess permission="orders.view">
        <div>page body</div>
      </RequireAccess>,
    );

    expect(await screen.findByText("You don't have access to this page")).toBeTruthy();
    expect(screen.queryByText('Could not check your access')).toBeNull();
  });
});

describe('PermissionsLoadBanner', () => {
  it('tells the user, with Retry, when the permission check failed', async () => {
    fetchMyPermissions.mockImplementationOnce(failPermissions);
    fetchMyPermissions.mockResolvedValue(['orders.view']);

    wrap(<PermissionsLoadBanner />);

    expect(await screen.findByText(/Could not check your access/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText(/Could not check your access/)).toBeNull());
  });

  it('renders nothing when permissions loaded', async () => {
    fetchMyPermissions.mockResolvedValue(['orders.view']);
    const { container } = wrap(<PermissionsLoadBanner />);
    await waitFor(() => expect(fetchMyPermissions).toHaveBeenCalled());
    expect(container.textContent).toBe('');
  });
});
