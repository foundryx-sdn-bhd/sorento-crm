/**
 * Administrative Users, Email column (owner 14 Oct 2026: one value per cell, one line
 * per row). The email used to sit as a second line under the user's name; it is now
 * its own column beside User, shown by default, and hidden or shown from the Columns
 * menu like every other column - the choice remembered per user through the shared
 * column-config preference (`useListingColumnPreferences`, keyed by the pathname).
 *
 * The real preferences hook runs here; only its service is stubbed, so the test proves
 * the toggle goes through the same save and restore path as every other listing.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  render,
  screen,
  cleanup,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const fetchUsersListPageMock = vi.fn();
vi.mock('../lib/listQuery', async () => {
  const actual =
    await vi.importActual<typeof import('../lib/listQuery')>(
      '../lib/listQuery',
    );
  return {
    ...actual,
    fetchUsersListPage: (...args: unknown[]) => fetchUsersListPageMock(...args),
  };
});

vi.mock('../../roles/hooks/use-role-select-query', () => ({
  useRoleSelectQuery: () => ({ data: [] }),
}));

vi.mock('./user-add-dialog', () => ({
  default: () => null,
}));

const prefs = vi.hoisted(() => ({
  get: vi.fn(),
  upsert: vi.fn(),
}));
vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: (...a: unknown[]) => prefs.get(...a),
  upsertUserListColumnConfig: (...a: unknown[]) => prefs.upsert(...a),
  resetUserListColumnConfig: vi.fn(async () => undefined),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/user-management/users',
  useSearchParams: () => new URLSearchParams(),
}));

// A real row renders its actions menu, which reads the signed-in user.
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { id: 'admin', email: 'admin@zzt.test' } },
  }),
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

import UserList from './user-list';

const SIN_YEN = {
  id: 'user-sy',
  name: 'Sin Yen',
  email: 'pa@zzt.test',
  avatar: null,
  status: 'ACTIVE',
  roles: [{ id: 'role-pa', name: 'PA' }],
  createdAt: '2026-01-05T02:00:00',
  updatedAt: '2026-01-05T02:00:00',
};

function renderWithClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <UserList />
    </QueryClientProvider>,
  );
}

function columnHeaderTexts(): string[] {
  return screen
    .getAllByRole('columnheader')
    .map((th) => (th.textContent ?? '').trim());
}

async function rowFor(name: string): Promise<HTMLElement> {
  const nameNode = await screen.findByText(name);
  const row = nameNode.closest('tr');
  if (!row) throw new Error(`no table row holds ${name}`);
  return row as HTMLElement;
}

beforeEach(() => {
  fetchUsersListPageMock.mockReset();
  prefs.get.mockReset();
  prefs.upsert.mockReset();
  fetchUsersListPageMock.mockResolvedValue({
    data: [SIN_YEN],
    pagination: { total: 1, page: 1 },
  });
  prefs.get.mockResolvedValue({ listing_key: '/user-management/users', config: null });
  prefs.upsert.mockImplementation(async (listingKey: string, payload: unknown) => ({
    listing_key: listingKey,
    config: payload,
  }));
  if (!window.matchMedia) {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
  }
  if (!('ResizeObserver' in window)) {
    (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => cleanup());

describe('UserList - Email is its own column (owner 14 Oct 2026)', () => {
  it('shows an Email column right after User, holding the email in its own cell', async () => {
    renderWithClient();
    const row = await rowFor('Sin Yen');

    const headers = columnHeaderTexts();
    const userAt = headers.indexOf('User');
    expect(userAt).toBeGreaterThanOrEqual(0);
    expect(headers[userAt + 1]).toBe('Email');

    const cells = within(row).getAllByRole('cell');
    const emailCell = cells.find((c) => (c.textContent ?? '').trim() === 'pa@zzt.test');
    expect(emailCell).toBeDefined();
  });

  it('the User cell holds the avatar and the name only, no email line under it', async () => {
    renderWithClient();
    const row = await rowFor('Sin Yen');

    const nameCell = within(row)
      .getAllByRole('cell')
      .find((c) => within(c).queryByText('Sin Yen'));
    expect(nameCell).toBeDefined();
    expect(within(nameCell as HTMLElement).queryByText('pa@zzt.test')).not.toBeInTheDocument();
    expect(nameCell?.textContent ?? '').not.toContain('pa@zzt.test');
  });

  it('Email is listed in the Columns menu, ticked by default', async () => {
    renderWithClient();
    await rowFor('Sin Yen');

    await userEvent.click(screen.getByRole('button', { name: 'Columns' }));
    const item = await screen.findByRole('menuitemcheckbox', { name: 'Email' });
    expect(item).toHaveAttribute('aria-checked', 'true');
  });

  it('unticking Email hides the column and saves the choice to the user column config', async () => {
    renderWithClient();
    await rowFor('Sin Yen');

    await userEvent.click(screen.getByRole('button', { name: 'Columns' }));
    await userEvent.click(await screen.findByRole('menuitemcheckbox', { name: 'Email' }));
    await userEvent.keyboard('{Escape}');

    await waitFor(() => expect(columnHeaderTexts()).not.toContain('Email'));
    expect(screen.queryByText('pa@zzt.test')).not.toBeInTheDocument();

    await waitFor(
      () =>
        expect(prefs.upsert).toHaveBeenCalledWith(
          '/user-management/users',
          expect.objectContaining({
            columnVisibility: expect.objectContaining({ email: false }),
          }),
        ),
      { timeout: 3000 },
    );
  });

  it('a user whose saved config hides Email opens the list with Email hidden', async () => {
    prefs.get.mockResolvedValue({
      listing_key: '/user-management/users',
      config: { columnVisibility: { email: false } },
    });
    renderWithClient();
    await rowFor('Sin Yen');

    await waitFor(() => expect(columnHeaderTexts()).not.toContain('Email'));
    expect(screen.queryByText('pa@zzt.test')).not.toBeInTheDocument();
    expect(prefs.get).toHaveBeenCalledWith('/user-management/users');
  });
});
