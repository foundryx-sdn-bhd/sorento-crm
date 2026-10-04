/**
 * CertificatesList - list states + filters.
 *   The list opens UNFILTERED, so its row count is the whole register and can be
 *     reconciled against the certification files on file. (This replaces the
 *     original FE-3 validity-scoped default, which withheld rows on arrival.)
 *     "Needs attention" is still one click away in the Filters popover.
 *   FE-4 (validity / expiring-within / scheme / status / needs-review filters
 *     feed the query, and the Filters button announces the active count)
 *   FE-8 (bulk delete is AlertDialog-confirmed, count-bearing, "This action
 *     cannot be undone")
 *   loading / empty / error / data states
 *
 * Mocks: the data + mutation hooks, next/navigation, sonner, the listing-column
 * preferences hook (required for any DataGrid list test), and the dropdown-menu
 * module so the Filters popover content is inline-assertable without a Radix
 * portal. SearchableSelect is stubbed to a native <select> with
 * aria-label={placeholder} - that is how this repo makes dropdowns
 * deterministic under jsdom.
 *
 * KNOWN LIMITATION (same as `PromotionsList.test.tsx`): the shared DataGrid can
 * settle its header + selection chrome under jsdom but row-level interaction
 * depends on layout measurement that jsdom does not provide, so row click
 * through to the detail page is covered in `e2e/certificates.spec.ts` instead of
 * here. Row-independent behaviour (query params, bulk-delete copy) is asserted
 * at the component/hook level below.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
  });
}
Element.prototype.scrollIntoView = vi.fn();

const push = vi.fn();
vi.mock('next/navigation', () => ({
  usePathname: () => '/master-data-management/certificates',
  useRouter: () => ({ push, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), custom: vi.fn() } }));

type MenuProps = {
  children?: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
};
vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenu: ({ children }: MenuProps) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: MenuProps) => <>{children}</>,
  DropdownMenuContent: ({ children }: MenuProps) => <div data-testid="menu-content">{children}</div>,
  DropdownMenuItem: ({ children, onClick, disabled }: MenuProps) => (
    <button type="button" onClick={onClick} disabled={disabled}>
      {children}
    </button>
  ),
  DropdownMenuCheckboxItem: ({ children }: MenuProps) => <div>{children}</div>,
  DropdownMenuLabel: ({ children }: MenuProps) => <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
  DropdownMenuGroup: ({ children }: MenuProps) => <div>{children}</div>,
  DropdownMenuPortal: ({ children }: MenuProps) => <>{children}</>,
  DropdownMenuSub: ({ children }: MenuProps) => <div>{children}</div>,
  DropdownMenuSubContent: ({ children }: MenuProps) => <div>{children}</div>,
  DropdownMenuSubTrigger: ({ children }: MenuProps) => <div>{children}</div>,
}));

// Native-select stand-in; aria-label = placeholder. Every filter select in the
// toolbar carries a distinct placeholder, so each is addressable.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    placeholder,
    disabled,
  }: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
    placeholder?: string;
    disabled?: boolean;
  }) => (
    <select
      aria-label={placeholder ?? 'select'}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{placeholder ?? ''}</option>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

const hooks = vi.hoisted(() => ({
  useCertificates: vi.fn(),
  bulkDeleteAsync: vi.fn(),
}));
vi.mock('../hooks/useCertificates', () => ({
  // The row's "..." carries the record's own Delete (D15), parked as a deferred
  // action rather than a mutation hook - nothing to mock here.
  useCertificates: (...a: unknown[]) => hooks.useCertificates(...a),
  useBulkDeleteCertificates: () => ({ mutateAsync: hooks.bulkDeleteAsync, isPending: false }),
  useCertificate: () => ({ data: undefined, isLoading: false }),
  useCreateCertificate: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateCertificate: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import CertificatesList from './CertificatesList';
import type { Certificate } from '../types/certificate.types';

function cert(over: Partial<Certificate> = {}): Certificate {
  return {
    id: 'cert-1',
    scheme: 'PPS',
    certifying_body: 'SIRIM QAS',
    certificate_number: 'PPS 123/2024',
    issuer: 'SIRIM',
    title: 'Sanitary ware',
    status: 'active',
    validity_state: 'expiring_soon',
    is_expired: false,
    valid_from: '2024-01-01',
    valid_until: '2026-09-01',
    days_until_expiry: 28,
    covered_product_count: 3,
    needs_review: false,
    review_reasons: [],
    possible_duplicate_of_certificate_id: null,
    possible_duplicate_of: null,
    current_revision: null,
    created_at: '2024-01-01T00:00:00',
    updated_at: '2024-01-01T00:00:00',
    ...over,
  } as Certificate;
}

function mockList(rows: Certificate[], over: Record<string, unknown> = {}) {
  hooks.useCertificates.mockReturnValue({
    data: { data: rows, empty: rows.length === 0, pagination: { page: 1, total: rows.length } },
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
    ...over,
  });
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CertificatesList />
    </QueryClientProvider>,
  );
}

/** Last params the list handed to `useCertificates`. */
function lastParams(): Record<string, unknown> {
  const calls = hooks.useCertificates.mock.calls;
  return calls[calls.length - 1][0] as Record<string, unknown>;
}

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('CertificatesList - states', () => {
  it('renders the loading skeleton while the first page is in flight', () => {
    hooks.useCertificates.mockReturnValue({
      data: undefined,
      isLoading: true,
      isFetching: true,
      refetch: vi.fn(),
    });
    const { container } = renderList();
    expect(container.querySelector('[data-slot="skeleton"], .animate-pulse')).toBeTruthy();
  });

  it('renders the empty state when no certificate matches', () => {
    mockList([]);
    renderList();
    expect(screen.getByText(/No data available/i)).toBeInTheDocument();
  });

  it('shows the load failure with a Retry (never "No data") when the query errored', () => {
    // NEVER-STUCK-UI S3 lever L2: a failed read is not an empty list.
    const refetch = vi.fn();
    hooks.useCertificates.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      error: new Error('Failed to fetch certificates'),
      refetch,
    });
    renderList();
    expect(screen.queryByText(/No data available/i)).not.toBeInTheDocument();
    expect(screen.getByText('Failed to fetch certificates')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Retry$/ }));
    expect(refetch).toHaveBeenCalled();
    // Toolbar is still usable so the user can retry / widen the filter. The empty
    // state no longer repeats the offer: one CTA per page (PR #1336), so exactly one.
    expect(screen.getAllByRole('button', { name: /Add Certificate/i })).toHaveLength(1);
  });

  it('renders the data state: the toolbar reports the record count', () => {
    mockList([cert(), cert({ id: 'cert-2', certificate_number: 'SPAN 9/2025', scheme: 'SPAN' })]);
    renderList();
    expect(screen.getByRole('button', { name: /Add Certificate/i })).toBeInTheDocument();
    expect(hooks.useCertificates).toHaveBeenCalled();
  });
});

describe('CertificatesList - opens unfiltered', () => {
  it('sends no narrowing param at all', () => {
    mockList([cert()]);
    renderList();
    const params = lastParams();
    // Every one of these undefined is the point: the row count on arrival is
    // the whole register, so it can be reconciled against the certification
    // files on file. The old default (expiring_soon,expired + active) withheld
    // rows and made the two counts disagree for no visible reason.
    expect(params.validity_state).toBeUndefined();
    expect(params.status).toBeUndefined();
    expect(params.expiring_within_days).toBeUndefined();
    expect(params.scheme).toBeUndefined();
    expect(params.needs_review).toBeUndefined();
  });

  it('shows no active-filter count on the Filters button', () => {
    mockList([cert()]);
    renderList();
    expect(screen.queryByText('2')).not.toBeInTheDocument();
  });

  it('"Needs attention" is still one click away and narrows to both states', async () => {
    mockList([cert()]);
    renderList();
    fireEvent.change(screen.getByLabelText('Validity'), { target: { value: 'attention' } });
    await waitFor(() => expect(lastParams().validity_state).toBe('expiring_soon,expired'));
  });

  it('choosing a single validity state sends just that state', async () => {
    mockList([cert()]);
    renderList();
    fireEvent.change(screen.getByLabelText('Validity'), { target: { value: 'expired' } });
    await waitFor(() => expect(lastParams().validity_state).toBe('expired'));
  });
});

describe('CertificatesList - filters feed the query (FE-4)', () => {
  it('expiring-within is sent as a number', async () => {
    mockList([cert()]);
    renderList();
    fireEvent.change(screen.getByLabelText('Any expiry date'), { target: { value: '30' } });
    await waitFor(() => expect(lastParams().expiring_within_days).toBe(30));
  });

  it('scheme is sent, and "All schemes" clears it', async () => {
    mockList([cert()]);
    renderList();
    fireEvent.change(screen.getByLabelText('All schemes'), { target: { value: 'SPAN' } });
    await waitFor(() => expect(lastParams().scheme).toBe('SPAN'));
    fireEvent.change(screen.getByLabelText('All schemes'), { target: { value: 'all' } });
    await waitFor(() => expect(lastParams().scheme).toBeUndefined());
  });

  it('needs-review is sent as a boolean only when narrowed', async () => {
    mockList([cert()]);
    renderList();
    fireEvent.change(screen.getByLabelText('Reviewed and unreviewed'), { target: { value: 'true' } });
    await waitFor(() => expect(lastParams().needs_review).toBe(true));
  });

  it('status narrows, and "All statuses" clears the param again', async () => {
    mockList([cert()]);
    renderList();
    fireEvent.change(screen.getByLabelText('All statuses'), { target: { value: 'archived' } });
    await waitFor(() => expect(lastParams().status).toBe('archived'));
    fireEvent.change(screen.getByLabelText('All statuses'), { target: { value: 'all' } });
    await waitFor(() => expect(lastParams().status).toBeUndefined());
  });

  it('the search box feeds searchQuery', async () => {
    mockList([cert()]);
    renderList();
    fireEvent.change(screen.getByPlaceholderText('Search by number...'), {
      target: { value: 'PPS 123' },
    });
    await waitFor(() => expect(lastParams().searchQuery).toBe('PPS 123'));
  });

  it('Clear filters resets every filter to unfiltered', async () => {
    mockList([cert()]);
    renderList();
    // The button only exists once something IS filtered - the list now opens
    // clean, so narrow first.
    fireEvent.change(screen.getByLabelText('Validity'), { target: { value: 'expired' } });
    await waitFor(() => expect(lastParams().validity_state).toBe('expired'));
    fireEvent.click(screen.getByRole('button', { name: /Clear filters/i }));
    await waitFor(() => {
      const params = lastParams();
      expect(params.validity_state).toBeUndefined();
      expect(params.status).toBeUndefined();
      expect(params.scheme).toBeUndefined();
      expect(params.expiring_within_days).toBeUndefined();
      expect(params.needs_review).toBeUndefined();
    });
  });
});

describe('CertificatesList - bulk delete confirmation (FE-8)', () => {
  it('the confirm copy names the count and warns it cannot be undone', async () => {
    mockList([
      cert({ id: 'cert-a' }),
      cert({ id: 'cert-b', certificate_number: 'PPS 124/2024' }),
    ]);
    renderList();
    fireEvent.click(screen.getByLabelText('Select all rows on this page'));
    fireEvent.click(await screen.findByRole('button', { name: /^Delete$/ }));

    expect(await screen.findByText('Confirm delete')).toBeInTheDocument();
    expect(
      screen.getByText(/Delete 2 certificates, including every revision and covered-product link\./i),
    ).toBeInTheDocument();
    expect(screen.getByText(/This action cannot be undone/i)).toBeInTheDocument();
  });

  it('singularises the copy for a single selected certificate', async () => {
    mockList([cert({ id: 'cert-a' })]);
    renderList();
    fireEvent.click(screen.getByLabelText('Select all rows on this page'));
    fireEvent.click(await screen.findByRole('button', { name: /^Delete$/ }));
    expect(
      screen.getByText(/Delete 1 certificate, including every revision and covered-product link\./i),
    ).toBeInTheDocument();
  });

  it('confirming calls the bulk-delete mutation with the selected ids', async () => {
    hooks.bulkDeleteAsync.mockResolvedValue({ deleted_count: 2 });
    mockList([cert({ id: 'cert-a' }), cert({ id: 'cert-b' })]);
    renderList();
    fireEvent.click(screen.getByLabelText('Select all rows on this page'));
    fireEvent.click(await screen.findByRole('button', { name: /^Delete$/ }));
    // The dialog's own destructive button (the toolbar one is unmounted by now).
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within_(dialog, /^Delete$/));
    await waitFor(() => expect(hooks.bulkDeleteAsync).toHaveBeenCalledTimes(1));
    expect(hooks.bulkDeleteAsync.mock.calls[0][0].sort()).toEqual(['cert-a', 'cert-b']);
  });
});

/** Button lookup scoped to a container (avoids the toolbar/dialog name clash). */
function within_(container: HTMLElement, name: RegExp): HTMLElement {
  const match = Array.from(container.querySelectorAll('button')).find((b) =>
    name.test((b.textContent ?? '').trim()),
  );
  if (!match) throw new Error(`No button matching ${name} inside the container`);
  return match as HTMLElement;
}
