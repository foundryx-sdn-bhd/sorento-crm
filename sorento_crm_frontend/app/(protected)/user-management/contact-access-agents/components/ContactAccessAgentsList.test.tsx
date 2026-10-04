import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ContactAccessAgentsList from './ContactAccessAgentsList';
import type { ContactAccessAgent } from '../types/contactAccessAgent.types';

const useContactAccessAgents = vi.fn();
vi.mock('../hooks/useContactAccessAgents', () => ({
  useContactAccessAgents: (...a: unknown[]) => useContactAccessAgents(...a),
}));

const setOneMutate = vi.fn();
const setBulkMutate = vi.fn();
vi.mock('@/hooks/useRespondContactOutbound', () => ({
  RESPOND_CONTACTS_OUTBOUND_KEY: 'respond-contacts-outbound',
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
  usePathname: () => '/user-management/contact-access-agents',
  useSearchParams: () => ({ get: () => null }),
}));

/** Two agents hold a grant on the SAME silenced contact; a third row is another contact. */
const AISYAH = 'contact-aisyah';
const FARAH = 'contact-farah';

const ROWS: ContactAccessAgent[] = [
  {
    id: 'grant-1',
    respond_contact_id: AISYAH,
    respond_contact_phone: '+60100000004',
    respond_contact_name: 'Aisyah Rahman',
    agent_id: 'agent-1',
    agent_code: 'CS01',
    agent_name: 'Customer Service',
    is_allowed: true,
    outbound_enabled: false,
    created_at: new Date('2026-01-05T02:00:00'),
    synced_to_excel: false,
  },
  {
    id: 'grant-2',
    respond_contact_id: AISYAH,
    respond_contact_phone: '+60100000004',
    respond_contact_name: 'Aisyah Rahman',
    agent_id: 'agent-2',
    agent_code: 'SL02',
    agent_name: 'Sales',
    is_allowed: true,
    outbound_enabled: false,
    created_at: new Date('2026-01-06T02:00:00'),
    synced_to_excel: false,
  },
  {
    id: 'grant-3',
    respond_contact_id: FARAH,
    respond_contact_phone: '+60100000003',
    respond_contact_name: 'Farah Idris',
    agent_id: 'agent-1',
    agent_code: 'CS01',
    agent_name: 'Customer Service',
    is_allowed: true,
    outbound_enabled: true,
    created_at: new Date('2026-01-07T02:00:00'),
    synced_to_excel: false,
  },
];

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function mockState(state: Record<string, unknown> = {}, rows: ContactAccessAgent[] = ROWS) {
  useContactAccessAgents.mockReturnValue({
    data: { data: rows, pagination: { total: rows.length, page: 1, limit: 50 } },
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
    ...state,
  });
}

beforeEach(() => {
  useContactAccessAgents.mockReset();
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

describe('ContactAccessAgentsList - the outbound column', () => {
  it('shows each contact state, once per grant row', () => {
    mockState();
    renderWithClient(<ContactAccessAgentsList />);

    // Aisyah is silenced and holds two grants, so the badge appears twice.
    expect(screen.getAllByText('Silenced')).toHaveLength(2);
    expect(screen.getAllByText('Can be messaged')).toHaveLength(1);
  });

  it('counts CONTACTS, not rows, in the summary', () => {
    mockState();
    renderWithClient(<ContactAccessAgentsList />);

    expect(screen.getByText('Reachable on this page').nextElementSibling).toHaveTextContent('1');
    expect(screen.getByText('Silenced on this page').nextElementSibling).toHaveTextContent('1');
  });

  it('marks a grant with no linked contact as not linked, with no switch', () => {
    mockState({}, [
      {
        ...ROWS[2],
        id: 'grant-orphan',
        respond_contact_id: null,
        respond_contact_name: 'Legacy Row',
        outbound_enabled: null,
      },
    ]);
    renderWithClient(<ContactAccessAgentsList />);

    expect(screen.getByText('Not linked')).toBeInTheDocument();
    expect(screen.queryByLabelText(/outbound for Legacy Row/i)).not.toBeInTheDocument();
  });

  it('renders no rows while loading', () => {
    mockState({ isLoading: true, data: undefined });
    renderWithClient(<ContactAccessAgentsList />);
    expect(screen.queryByText('Aisyah Rahman')).not.toBeInTheDocument();
  });

  it('renders an empty state', () => {
    mockState({ data: { data: [], pagination: { total: 0, page: 1, limit: 50 } } });
    renderWithClient(<ContactAccessAgentsList />);
    expect(screen.queryByText('Aisyah Rahman')).not.toBeInTheDocument();
    expect(screen.getByText('Reachable on this page').nextElementSibling).toHaveTextContent('0');
  });
});

describe('ContactAccessAgentsList - per-row toggle', () => {
  it('flips the CONTACT, not the grant row', () => {
    mockState();
    renderWithClient(<ContactAccessAgentsList />);

    fireEvent.click(screen.getByLabelText(/Disable outbound for Farah Idris/i));

    expect(setOneMutate).toHaveBeenCalledWith({ contactId: FARAH, enabled: false });
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('re-enables from either of the contact rows, with the same contact id', () => {
    mockState();
    renderWithClient(<ContactAccessAgentsList />);

    const switches = screen.getAllByLabelText(/Enable outbound for Aisyah Rahman/i);
    expect(switches).toHaveLength(2);
    fireEvent.click(switches[1]);

    expect(setOneMutate).toHaveBeenCalledWith({ contactId: AISYAH, enabled: true });
  });
});

describe('ContactAccessAgentsList - bulk actions de-duplicate by contact', () => {
  function selectAllRows() {
    fireEvent.click(screen.getByRole('checkbox', { name: /select all/i }));
  }

  it('labels the bulk actions with the contact count, not the row count', () => {
    mockState();
    renderWithClient(<ContactAccessAgentsList />);
    selectAllRows();

    // 3 selected rows, 2 distinct contacts.
    expect(screen.getByRole('button', { name: /Enable messaging \(2 contacts\)/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Disable messaging \(2 contacts\)/ })).toBeInTheDocument();
  });

  it('sends one id per contact when enabling', () => {
    mockState();
    renderWithClient(<ContactAccessAgentsList />);
    selectAllRows();

    fireEvent.click(screen.getByRole('button', { name: /Enable messaging \(2 contacts\)/ }));

    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(setBulkMutate).toHaveBeenCalledWith(
      { enabled: true, contactIds: [AISYAH, FARAH] },
      expect.anything(),
    );
  });

  it('confirms a disable, naming the CONTACT count, before sending', () => {
    mockState();
    renderWithClient(<ContactAccessAgentsList />);
    selectAllRows();

    fireEvent.click(screen.getByRole('button', { name: /Disable messaging \(2 contacts\)/ }));

    const dialog = screen.getByRole('alertdialog');
    expect(
      within(dialog).getByText(
        /2 contact\(s\) will receive no WhatsApp messages until outbound is switched back on/i,
      ),
    ).toBeInTheDocument();
    expect(setBulkMutate).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole('button', { name: /^Disable$/ }));
    expect(setBulkMutate).toHaveBeenCalledWith(
      { enabled: false, contactIds: [AISYAH, FARAH] },
      expect.anything(),
    );
  });

  it('cancelling the confirmation changes nothing', () => {
    mockState();
    renderWithClient(<ContactAccessAgentsList />);
    selectAllRows();

    fireEvent.click(screen.getByRole('button', { name: /Disable messaging \(2 contacts\)/ }));
    fireEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: /cancel/i }),
    );

    expect(setBulkMutate).not.toHaveBeenCalled();
  });

  it('leaves unlinked rows out of the selection count and the payload', () => {
    mockState({}, [
      ROWS[0],
      { ...ROWS[0], id: 'grant-orphan', respond_contact_id: null, outbound_enabled: null },
    ]);
    renderWithClient(<ContactAccessAgentsList />);
    selectAllRows();

    fireEvent.click(screen.getByRole('button', { name: /Enable messaging \(1 contact\)/ }));

    expect(setBulkMutate).toHaveBeenCalledWith(
      { enabled: true, contactIds: [AISYAH] },
      expect.anything(),
    );
  });
});
