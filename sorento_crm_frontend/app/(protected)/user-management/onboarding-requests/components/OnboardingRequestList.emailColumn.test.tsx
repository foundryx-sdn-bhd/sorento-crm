/**
 * Onboarding Requests, Email column (owner 14 Oct 2026: one value per cell, one line per
 * row). The requester's email used to sit as a second line under their name in "From";
 * it is now its own column beside From, shown by default, and hidden or shown from the
 * Columns menu - the choice remembered per user through the shared column-config
 * preference (`useListingColumnPreferences`, keyed by the pathname).
 *
 * The real preferences hook runs here; only its service is stubbed.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { OnboardingRequestSummary } from '@/components/common/onboarding/types';
import { OnboardingRequestList } from './OnboardingRequestList';

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
  usePathname: () => '/user-management/onboarding-requests',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), custom: vi.fn() },
}));

const listOnboardingRequests = vi.fn();
vi.mock('../services/onboardingService', () => ({
  listOnboardingRequests: (...a: unknown[]) => listOnboardingRequests(...a),
  createOnboardingRequest: vi.fn(),
  getOnboardingRequest: vi.fn(),
  updateOnboardingPerson: vi.fn(),
  approveOnboardingPerson: vi.fn(),
  rejectOnboardingPerson: vi.fn(),
  startOnboardingReview: vi.fn(),
  approveOnboardingRequest: vi.fn(),
  sendOnboardingRequest: vi.fn(),
  revokeOnboardingRequest: vi.fn(),
  regenerateOnboardingToken: vi.fn(),
  deleteOnboardingRequest: vi.fn(),
}));

vi.mock('@/app/(protected)/system-management/companies/services/companyService', () => ({
  getCompaniesSelect: vi.fn(async () => []),
}));

const LISTING_KEY = '/user-management/onboarding-requests';

function summary(overrides: Partial<OnboardingRequestSummary> = {}): OnboardingRequestSummary {
  return {
    id: 'req-1',
    title: 'MOCHA staff onboarding',
    company_name: 'MOCHA Sdn Bhd',
    requester_name: 'Esther Lim',
    requester_email: 'person13@example.com',
    status: 'submitted',
    people_count: 3,
    approved_count: 0,
    rejected_count: 0,
    submitted_at: '2026-08-14T09:12:00',
    created_at: '2026-08-12T10:00:00',
    expires_at: '2026-08-26T10:00:00',
    revoked_at: null,
    ...overrides,
  };
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <OnboardingRequestList />
    </QueryClientProvider>,
  );
}

function columnHeaderTexts(): string[] {
  return screen
    .getAllByRole('columnheader')
    .map((th) => (th.textContent ?? '').trim());
}

async function rowFor(text: string): Promise<HTMLElement> {
  const node = await screen.findByText(text);
  const row = node.closest('tr');
  if (!row) throw new Error(`no table row holds ${text}`);
  return row as HTMLElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  listOnboardingRequests.mockResolvedValue({
    data: [summary()],
    pagination: { page: 1, limit: 50, total: 1 },
  });
  prefs.get.mockResolvedValue({ listing_key: LISTING_KEY, config: null });
  prefs.upsert.mockImplementation(async (listingKey: string, payload: unknown) => ({
    listing_key: listingKey,
    config: payload,
  }));
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => cleanup());

describe('OnboardingRequestList - Email is its own column (owner 14 Oct 2026)', () => {
  it('shows an Email column right after From, holding the email in its own cell', async () => {
    renderList();
    const row = await rowFor('Esther Lim');

    const headers = columnHeaderTexts();
    const fromAt = headers.indexOf('From');
    expect(fromAt).toBeGreaterThanOrEqual(0);
    expect(headers[fromAt + 1]).toBe('Email');

    const cells = within(row).getAllByRole('cell');
    const emailCell = cells.find(
      (c) => (c.textContent ?? '').trim() === 'person13@example.com',
    );
    expect(emailCell).toBeDefined();
  });

  it('the From cell holds the name only, no email line under it', async () => {
    renderList();
    const row = await rowFor('Esther Lim');

    const fromCell = within(row)
      .getAllByRole('cell')
      .find((c) => within(c).queryByText('Esther Lim'));
    expect(fromCell).toBeDefined();
    expect(fromCell?.textContent ?? '').not.toContain('person13@example.com');
  });

  it('Email is listed in the Columns menu, ticked by default', async () => {
    renderList();
    await rowFor('Esther Lim');

    await userEvent.click(screen.getByRole('button', { name: 'Columns' }));
    const item = await screen.findByRole('menuitemcheckbox', { name: 'Email' });
    expect(item).toHaveAttribute('aria-checked', 'true');
  });

  it('unticking Email hides the column and saves the choice to the user column config', async () => {
    renderList();
    await rowFor('Esther Lim');

    await userEvent.click(screen.getByRole('button', { name: 'Columns' }));
    await userEvent.click(await screen.findByRole('menuitemcheckbox', { name: 'Email' }));
    await userEvent.keyboard('{Escape}');

    await waitFor(() => expect(columnHeaderTexts()).not.toContain('Email'));
    expect(screen.queryByText('person13@example.com')).not.toBeInTheDocument();

    await waitFor(
      () =>
        expect(prefs.upsert).toHaveBeenCalledWith(
          LISTING_KEY,
          expect.objectContaining({
            columnVisibility: expect.objectContaining({ requester_email: false }),
          }),
        ),
      { timeout: 3000 },
    );
  });

  it('a user whose saved config hides Email opens the list with Email hidden', async () => {
    prefs.get.mockResolvedValue({
      listing_key: LISTING_KEY,
      config: { columnVisibility: { requester_email: false } },
    });
    renderList();
    await rowFor('Esther Lim');

    await waitFor(() => expect(columnHeaderTexts()).not.toContain('Email'));
    expect(screen.queryByText('person13@example.com')).not.toBeInTheDocument();
    expect(prefs.get).toHaveBeenCalledWith(LISTING_KEY);
  });
});
