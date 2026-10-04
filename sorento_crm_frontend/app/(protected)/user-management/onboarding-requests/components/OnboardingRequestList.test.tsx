/**
 * The review queue and the dialog that starts one.
 *
 * What is pinned here is the queue's standard-list behaviour, because that is
 * exactly what the screen was missing: a pager that reports the whole waiting
 * set and moves through it, and a create dialog that asks for the company the
 * approved people will be granted against.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { OnboardingRequestSummary } from '@/components/common/onboarding/types';
import { NewOnboardingRequestDialog } from './NewOnboardingRequestDialog';
import { OnboardingRequestList } from './OnboardingRequestList';

// DataGrid fetches the user's hidden/resized columns and renders skeletons until
// that answers; under jsdom nothing does, so rows never mount without this.
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

const push = vi.fn();
vi.mock('next/navigation', () => ({
  usePathname: () => '/user-management/onboarding-requests',
  useRouter: () => ({ push, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), custom: vi.fn() },
}));

const listOnboardingRequests = vi.fn();
const createOnboardingRequest = vi.fn();

vi.mock('../services/onboardingService', () => ({
  listOnboardingRequests: (...a: unknown[]) => listOnboardingRequests(...a),
  createOnboardingRequest: (...a: unknown[]) => createOnboardingRequest(...a),
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

const getCompaniesSelect = vi.fn();
vi.mock('@/app/(protected)/system-management/companies/services/companyService', () => ({
  getCompaniesSelect: () => getCompaniesSelect(),
}));

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

function renderWith(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  getCompaniesSelect.mockResolvedValue([
    { id: 'co-1', name: 'Sorento Sdn Bhd', code: 'SOR' },
    { id: 'co-2', name: 'MOCHA Sdn Bhd', code: 'MCH' },
  ]);
});

describe('OnboardingRequestList', () => {
  it('says the queue is empty rather than showing an empty table', async () => {
    listOnboardingRequests.mockResolvedValue({
      data: [],
      pagination: { page: 1, limit: 50, total: 0 },
    });
    renderWith(<OnboardingRequestList />);
    expect(
      await screen.findByText('No onboarding requests yet. Create one to get started.'),
    ).toBeInTheDocument();
  });

  it('renders a pagination footer reporting the whole waiting set', async () => {
    listOnboardingRequests.mockResolvedValue({
      data: [summary()],
      pagination: { page: 1, limit: 50, total: 120 },
    });
    const { container } = renderWith(<OnboardingRequestList />);
    await waitFor(() =>
      expect(container.querySelector('[data-slot="data-grid-pagination"]')).toBeInTheDocument(),
    );
    // 120, not the one row that came back: the pager reports how much work is
    // waiting, which is the whole reason the paging moved server-side.
    expect(await screen.findByText(/of 120/)).toBeInTheDocument();
  });

  it('asks the server for the next page rather than slicing a full fetch', async () => {
    listOnboardingRequests.mockResolvedValue({
      data: [summary()],
      pagination: { page: 1, limit: 50, total: 120 },
    });
    renderWith(<OnboardingRequestList />);
    await screen.findByText(/of 120/);

    fireEvent.click(screen.getByRole('button', { name: 'Go to next page' }));
    await waitFor(() =>
      expect(listOnboardingRequests).toHaveBeenCalledWith(
        expect.objectContaining({ pageIndex: 1, pageSize: 50 }),
      ),
    );
  });

  it('returns to page one when a search narrows the set', async () => {
    // Searching from page 3 otherwise narrows the set to two rows and then asks
    // for the third page of it, and an empty grid reads as "nothing matched".
    listOnboardingRequests.mockResolvedValue({
      data: [summary()],
      pagination: { page: 1, limit: 50, total: 120 },
    });
    renderWith(<OnboardingRequestList />);
    await screen.findByText(/of 120/);

    fireEvent.click(screen.getByRole('button', { name: 'Go to next page' }));
    await waitFor(() =>
      expect(listOnboardingRequests).toHaveBeenCalledWith(
        expect.objectContaining({ pageIndex: 1 }),
      ),
    );

    fireEvent.change(screen.getByPlaceholderText('Search requests...'), {
      target: { value: 'mocha' },
    });
    await waitFor(() =>
      expect(listOnboardingRequests).toHaveBeenCalledWith(
        expect.objectContaining({ pageIndex: 0, searchQuery: 'mocha' }),
      ),
    );
  });

  it('does not tell the reviewer to create one when a filter hid them all', async () => {
    listOnboardingRequests.mockResolvedValue({
      data: [],
      pagination: { page: 1, limit: 50, total: 0 },
    });
    renderWith(<OnboardingRequestList />);
    await screen.findByText('No onboarding requests yet. Create one to get started.');

    fireEvent.change(screen.getByPlaceholderText('Search requests...'), {
      target: { value: 'nothing matches this' },
    });
    // "Create one to get started" is wrong advice when requests exist and the
    // filter is simply hiding them.
    expect(await screen.findByText('No requests match your filters.')).toBeInTheDocument();
  });

  it('sends the search and the default sort to the server', async () => {
    listOnboardingRequests.mockResolvedValue({
      data: [],
      pagination: { page: 1, limit: 50, total: 0 },
    });
    renderWith(<OnboardingRequestList />);
    await waitFor(() => expect(listOnboardingRequests).toHaveBeenCalled());
    expect(listOnboardingRequests).toHaveBeenCalledWith(
      expect.objectContaining({ sorting: [{ id: 'created_at', desc: true }] }),
    );

    fireEvent.change(screen.getByPlaceholderText('Search requests...'), {
      target: { value: 'mocha' },
    });
    await waitFor(() =>
      expect(listOnboardingRequests).toHaveBeenCalledWith(
        expect.objectContaining({ searchQuery: 'mocha' }),
      ),
    );
  });
});

describe('NewOnboardingRequestDialog', () => {
  function openDialog() {
    renderWith(<NewOnboardingRequestDialog />);
    fireEvent.click(screen.getByRole('button', { name: /New request/ }));
    return screen.findByRole('dialog');
  }

  it('asks for the company the approved people will be granted against', async () => {
    const dialog = await openDialog();
    // Required and first: the company decides what approval actually grants,
    // and there is no default worth guessing.
    const company = within(dialog).getByLabelText('Company');
    expect(company).toHaveAttribute('role', 'combobox');

    fireEvent.click(company);
    fireEvent.click(await screen.findByRole('option', { name: /MOCHA Sdn Bhd/ }));
    await waitFor(() => expect(within(dialog).getByText('MOCHA Sdn Bhd')).toBeInTheDocument());
  });

  it('will not create a request without a company', async () => {
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText('Title'), {
      target: { value: 'Staff onboarding' },
    });
    fireEvent.change(within(dialog).getByLabelText('Requester name'), {
      target: { value: 'Esther Lim' },
    });
    fireEvent.change(within(dialog).getByLabelText('Requester email'), {
      target: { value: 'person13@example.com' },
    });
    expect(within(dialog).getByRole('button', { name: 'Create' })).toBeDisabled();
  });

  it('creates the request with the company attached', async () => {
    createOnboardingRequest.mockResolvedValue({ id: 'req-9' });
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByLabelText('Company'));
    fireEvent.click(await screen.findByRole('option', { name: /MOCHA Sdn Bhd/ }));
    fireEvent.change(within(dialog).getByLabelText('Title'), {
      target: { value: 'Staff onboarding' },
    });
    fireEvent.change(within(dialog).getByLabelText('Requester name'), {
      target: { value: 'Esther Lim' },
    });
    fireEvent.change(within(dialog).getByLabelText('Requester email'), {
      target: { value: 'person13@example.com' },
    });

    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() =>
      expect(createOnboardingRequest).toHaveBeenCalledWith({
        company_id: 'co-2',
        title: 'Staff onboarding',
        requester_name: 'Esther Lim',
        requester_email: 'person13@example.com',
        requester_phone: null,
      }),
    );
    // Straight to the detail page, because the link only exists there.
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith('/user-management/onboarding-requests/req-9'),
    );
  });
});
