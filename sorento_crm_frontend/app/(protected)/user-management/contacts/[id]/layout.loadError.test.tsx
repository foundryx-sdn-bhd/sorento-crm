/**
 * L9 (NEVER-STUCK-UI S3 + S5.3, audit row 15). The contact layout owns the record
 * read for every tab. A failed read used to fall through to `!contact` and read as
 * "Contact not found" (a refusal or a 500 is not "not found"), and the Profile
 * tab's `isLoading || !contact` skeleton could spin forever. The layout now
 * branches on the error before it draws a tab.
 */
import React, { Suspense } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ContactLayout from './layout';

const ID = '11111111-1111-4111-8111-111111111111';

const h = vi.hoisted(() => ({ apiFetch: vi.fn() }));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => `/user-management/contacts/11111111-1111-4111-8111-111111111111`,
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/contacts/PortalLinkButton', () => ({ default: () => null }));
vi.mock('../components/ContactDeleteDialog', () => ({ default: () => null }));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiFetch: (...args: unknown[]) => h.apiFetch(...args),
}));

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const PARAMS = Promise.resolve({ id: ID });

const clients: QueryClient[] = [];

async function renderLayout() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(client);
  await act(async () => {
    render(
      <QueryClientProvider client={client}>
        <Suspense fallback={null}>
          <ContactLayout params={PARAMS}>
            <div>Tab body</div>
          </ContactLayout>
        </Suspense>
      </QueryClientProvider>,
    );
  });
}

beforeEach(async () => {
  await PARAMS;
  h.apiFetch.mockReset();
  clients.length = 0;
});
afterEach(() => cleanup());

describe('contact detail when the record read fails', () => {
  it('a 500 shows the error with Retry, not "Contact not found" and not the tabs', async () => {
    h.apiFetch.mockImplementation(async () => json(500, { detail: 'Database unavailable' }));
    await renderLayout();

    expect(await screen.findByText('Could not load this contact', {}, { timeout: 4000 })).toBeTruthy();
    expect(screen.getByText('Database unavailable')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
    expect(screen.queryByText('Contact not found')).toBeNull();
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.queryByText('Tab body')).toBeNull();
  });

  it('Retry refetches and draws the tabs once the read succeeds', async () => {
    // Two failures: the read gets one automatic retry for a fault before it settles.
    h.apiFetch.mockImplementationOnce(async () => json(500, { detail: 'Database unavailable' }));
    h.apiFetch.mockImplementationOnce(async () => json(500, { detail: 'Database unavailable' }));
    h.apiFetch.mockImplementation(async (url: string) =>
      url.endsWith(ID)
        ? json(200, { id: ID, phone_number: '6012', name: 'Aisyah', access_types: [] })
        : json(200, { data: [], pagination: { total: 0 } }),
    );
    await renderLayout();

    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }, { timeout: 4000 }));
    expect(await screen.findByText('Tab body')).toBeTruthy();
    expect(screen.getAllByRole('tab').length).toBeGreaterThan(0);
  });

  it('a 403 shows AccessDenied, not "not found"', async () => {
    h.apiFetch.mockResolvedValue(
      json(403, { detail: 'Permission required: user_management.contacts.view' }),
    );
    await renderLayout();

    expect(await screen.findByText("You don't have access to this page")).toBeTruthy();
    expect(screen.queryByText('Contact not found')).toBeNull();
    expect(screen.queryByText(/Permission required/)).toBeNull();
    expect(screen.queryByText('Tab body')).toBeNull();
  });

  it('a 404 still says "Contact not found"', async () => {
    h.apiFetch.mockImplementation(async () => json(404, { detail: 'Contact not found' }));
    await renderLayout();

    expect(await screen.findByText('Contact not found')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull();
    expect(screen.queryByText('Tab body')).toBeNull();
  });

  it('a failed background refetch keeps the contact on screen', async () => {
    h.apiFetch.mockImplementation(async (url: string) =>
      url.endsWith(ID)
        ? json(200, { id: ID, phone_number: '6012', name: 'Aisyah', access_types: [] })
        : json(200, { data: [], pagination: { total: 0 } }),
    );
    await renderLayout();
    expect(await screen.findByText('Tab body')).toBeTruthy();

    h.apiFetch.mockImplementation(async () => json(500, { detail: 'Database unavailable' }));
    await act(async () => {
      await clients[0].refetchQueries({ queryKey: ['respond-contact', ID] });
    });
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByText('Tab body')).toBeTruthy();
    expect(screen.queryByText('Could not load this contact')).toBeNull();
  });
});
