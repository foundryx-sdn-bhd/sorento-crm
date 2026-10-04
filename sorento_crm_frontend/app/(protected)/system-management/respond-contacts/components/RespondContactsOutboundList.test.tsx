import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import RespondContactsOutboundList from './RespondContactsOutboundList';
import type { RespondContactOutboundRow } from '../types/respondContactOutbound.types';

// --- data + mutation hook mocks ---------------------------------------------
const useRespondContactsOutbound = vi.fn();
const setOneMutate = vi.fn();
const setBulkMutate = vi.fn();
vi.mock('../hooks/useRespondContactsOutbound', () => ({
  RESPOND_CONTACTS_OUTBOUND_KEY: 'respond-contacts-outbound',
  useRespondContactsOutbound: (...a: unknown[]) => useRespondContactsOutbound(...a),
  useRespondContactOutboundMutations: () => ({
    setOne: { mutate: setOneMutate, isPending: false },
    setBulk: { mutate: setBulkMutate, isPending: false },
  }),
}));

// The DataGrid keeps the table in a skeleton until the column-config query
// resolves; stub it "loaded" so real rows render synchronously in jsdom.
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/system-management/respond-contacts',
  useSearchParams: () => ({ get: () => null }),
}));

const ROWS: RespondContactOutboundRow[] = [
  {
    id: 'contact-enabled',
    name: 'Aisyah Rahman',
    phone_number: '+60100000004',
    respond_io_id: '10025901',
    outbound_enabled: true,
  },
  {
    id: 'contact-disabled',
    name: 'Farah Idris',
    phone_number: '+60100000003',
    respond_io_id: '10025902',
    outbound_enabled: false,
  },
];

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function mockState(state: Record<string, unknown> = {}) {
  useRespondContactsOutbound.mockReturnValue({
    data: {
      data: ROWS,
      pagination: { total: 2, page: 1, limit: 50 },
      empty: false,
      counts: { enabled: 1, disabled: 1, total: 2 },
    },
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...state,
  });
}

beforeEach(() => {
  useRespondContactsOutbound.mockReset();
  setOneMutate.mockReset();
  setBulkMutate.mockReset();
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

/** The params the component last handed the query hook. */
function lastQueryArgs(): Record<string, unknown> {
  const calls = useRespondContactsOutbound.mock.calls;
  return calls[calls.length - 1][0] as Record<string, unknown>;
}

describe('RespondContactsOutboundList - states', () => {
  it('renders the contacts with their outbound state', () => {
    mockState();
    renderWithClient(<RespondContactsOutboundList />);

    expect(screen.getByText('Aisyah Rahman')).toBeInTheDocument();
    expect(screen.getByText('+60100000003')).toBeInTheDocument();
    expect(screen.getByText('10025901')).toBeInTheDocument();
    expect(screen.getByText('Enabled')).toBeInTheDocument();
    expect(screen.getByText('Disabled')).toBeInTheDocument();
  });

  it('never renders the internal contact id', () => {
    mockState();
    const { container } = renderWithClient(<RespondContactsOutboundList />);
    expect(container.textContent).not.toContain('contact-enabled');
  });

  it('shows the enabled / disabled audit counts above the grid', () => {
    mockState({
      data: {
        data: ROWS,
        pagination: { total: 2, page: 1, limit: 50 },
        empty: false,
        counts: { enabled: 41, disabled: 7, total: 48 },
      },
    });
    renderWithClient(<RespondContactsOutboundList />);

    expect(screen.getByText('Can be messaged').nextElementSibling).toHaveTextContent('41');
    expect(screen.getByText('Silenced').nextElementSibling).toHaveTextContent('7');
    expect(screen.getByText('Total contacts').nextElementSibling).toHaveTextContent('48');
    expect(screen.getByText('7 contact(s) will receive nothing')).toBeInTheDocument();
  });

  it('calls out the total blackout when every contact is silenced', () => {
    mockState({
      data: {
        data: [],
        pagination: { total: 0, page: 1, limit: 50 },
        empty: true,
        counts: { enabled: 0, disabled: 12, total: 12 },
      },
    });
    renderWithClient(<RespondContactsOutboundList />);
    expect(screen.getByText('All outbound messaging is off')).toBeInTheDocument();
  });

  it('renders a loading state instead of rows', () => {
    mockState({ isLoading: true, data: undefined });
    renderWithClient(<RespondContactsOutboundList />);
    expect(screen.queryByText('Aisyah Rahman')).not.toBeInTheDocument();
  });

  it('renders an empty state with a next step', () => {
    mockState({
      data: {
        data: [],
        pagination: { total: 0, page: 1, limit: 50 },
        empty: true,
        counts: { enabled: 0, disabled: 0, total: 0 },
      },
    });
    renderWithClient(<RespondContactsOutboundList />);
    expect(screen.getByText(/No Respond.io contacts yet/i)).toBeInTheDocument();
  });

  it('renders the error message and a retry', () => {
    const refetch = vi.fn();
    mockState({
      isError: true,
      error: new Error('Failed to load Respond contacts'),
      data: undefined,
      refetch,
    });
    renderWithClient(<RespondContactsOutboundList />);

    expect(screen.getByText('Failed to load Respond contacts')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(refetch).toHaveBeenCalled();
  });
});

describe('RespondContactsOutboundList - search and filter', () => {
  it('passes the search term to the query and resets to the first page', async () => {
    mockState();
    renderWithClient(<RespondContactsOutboundList />);

    fireEvent.change(screen.getByPlaceholderText(/Search name, phone or Respond.io ID/i), {
      target: { value: '10025902' },
    });

    await waitFor(() => expect(lastQueryArgs().searchQuery).toBe('10025902'));
    expect(lastQueryArgs().pageIndex).toBe(0);
  });
});

describe('RespondContactsOutboundList - per-row toggle', () => {
  it('disables one contact immediately, without a dialog', () => {
    mockState();
    renderWithClient(<RespondContactsOutboundList />);

    fireEvent.click(screen.getByLabelText(/Disable outbound for Aisyah Rahman/i));

    expect(setOneMutate).toHaveBeenCalledWith({
      contactId: 'contact-enabled',
      enabled: false,
    });
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('re-enables one contact immediately', () => {
    mockState();
    renderWithClient(<RespondContactsOutboundList />);

    fireEvent.click(screen.getByLabelText(/Enable outbound for Farah Idris/i));

    expect(setOneMutate).toHaveBeenCalledWith({
      contactId: 'contact-disabled',
      enabled: true,
    });
  });
});

describe('RespondContactsOutboundList - bulk actions', () => {
  function selectAllRows() {
    fireEvent.click(screen.getByRole('checkbox', { name: /select all/i }));
  }

  it('enables a selection without a confirmation', () => {
    mockState();
    renderWithClient(<RespondContactsOutboundList />);
    selectAllRows();

    fireEvent.click(screen.getByRole('button', { name: /Enable \(2\)/ }));

    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(setBulkMutate).toHaveBeenCalledWith(
      { enabled: true, contactIds: ['contact-enabled', 'contact-disabled'] },
      expect.anything(),
    );
  });

  it('confirms before disabling a selection, and names the count', () => {
    mockState();
    renderWithClient(<RespondContactsOutboundList />);
    selectAllRows();

    fireEvent.click(screen.getByRole('button', { name: /Disable \(2\)/ }));

    const dialog = screen.getByRole('alertdialog');
    expect(within(dialog).getByText(/2 selected contact\(s\) will receive no WhatsApp messages/i))
      .toBeInTheDocument();
    expect(setBulkMutate).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole('button', { name: /^Disable$/ }));
    expect(setBulkMutate).toHaveBeenCalledWith(
      { enabled: false, contactIds: ['contact-enabled', 'contact-disabled'] },
      expect.anything(),
    );
  });

  it('cancelling the bulk-disable dialog changes nothing', () => {
    mockState();
    renderWithClient(<RespondContactsOutboundList />);
    selectAllRows();

    fireEvent.click(screen.getByRole('button', { name: /Disable \(2\)/ }));
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: /cancel/i }));

    expect(setBulkMutate).not.toHaveBeenCalled();
  });
});

describe('RespondContactsOutboundList - the kill switch', () => {
  it('spells out the blackout, with the count, before disabling everyone', () => {
    mockState({
      data: {
        data: ROWS,
        pagination: { total: 2, page: 1, limit: 50 },
        empty: false,
        counts: { enabled: 41, disabled: 7, total: 48 },
      },
    });
    renderWithClient(<RespondContactsOutboundList />);

    fireEvent.click(screen.getByRole('button', { name: /Disable all/i }));

    const dialog = screen.getByRole('alertdialog');
    expect(
      within(dialog).getByText(
        /No customer will receive any WhatsApp message until outbound is switched back on\. This silences all 41 contact\(s\)/i,
      ),
    ).toBeInTheDocument();
    expect(setBulkMutate).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole('button', { name: /Disable all contacts/i }));
    expect(setBulkMutate).toHaveBeenCalledWith(
      { enabled: false, all: true },
      expect.anything(),
    );
  });

  it('confirms before switching everyone back on', () => {
    mockState({
      data: {
        data: ROWS,
        pagination: { total: 2, page: 1, limit: 50 },
        empty: false,
        counts: { enabled: 41, disabled: 7, total: 48 },
      },
    });
    renderWithClient(<RespondContactsOutboundList />);

    fireEvent.click(screen.getByRole('button', { name: /Enable all/i }));

    const dialog = screen.getByRole('alertdialog');
    expect(
      within(dialog).getByText(/7 silenced contact\(s\) will be able to receive WhatsApp messages again/i),
    ).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: /^Enable all$/ }));
    expect(setBulkMutate).toHaveBeenCalledWith({ enabled: true, all: true }, expect.anything());
  });

  it('offers no kill switch when every contact is already silenced', () => {
    mockState({
      data: {
        data: [],
        pagination: { total: 0, page: 1, limit: 50 },
        empty: true,
        counts: { enabled: 0, disabled: 12, total: 12 },
      },
    });
    renderWithClient(<RespondContactsOutboundList />);

    // Two, on an empty listing: the toolbar's and the empty state's own copy of
    // it (S5-06). Both are the same disabled button.
    for (const button of screen.getAllByRole('button', { name: /Disable all/i })) {
      expect(button).toBeDisabled();
    }
  });
});
