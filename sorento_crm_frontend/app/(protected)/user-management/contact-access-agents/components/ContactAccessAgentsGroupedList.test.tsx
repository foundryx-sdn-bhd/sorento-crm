import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ContactAccessAgentsGroupedList from './ContactAccessAgentsGroupedList';
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

vi.mock('../../access-agents/hooks/useAccessAgents', () => ({
  useAccessAgents: () => ({ data: { data: [] } }),
}));

vi.mock('../../access-agents/components/ContactAgentAccessDialog', () => ({
  default: () => null,
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/user-management/contact-access-agents',
  useSearchParams: () => ({ get: () => null }),
}));

const AISYAH = 'contact-aisyah';
const FARAH = 'contact-farah';

/** Aisyah is one contact holding two grants: the grouped view must show her once. */
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
    data: { data: rows, pagination: { total: rows.length, page: 1, limit: 10000 } },
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

describe('ContactAccessAgentsGroupedList - the outbound column', () => {
  it('shows one outbound state per contact', () => {
    mockState();
    renderWithClient(<ContactAccessAgentsGroupedList />);

    // Two groups: Aisyah (silenced, 2 grants) and Farah (reachable).
    expect(screen.getAllByText('Silenced')).toHaveLength(1);
    expect(screen.getAllByText('Can be messaged')).toHaveLength(1);
  });

  it('counts the contacts above the grid', () => {
    mockState();
    renderWithClient(<ContactAccessAgentsGroupedList />);

    expect(screen.getByText('Reachable on this page').nextElementSibling).toHaveTextContent('1');
    expect(screen.getByText('Silenced on this page').nextElementSibling).toHaveTextContent('1');
  });

  it('flips the contact from the group row', () => {
    mockState();
    renderWithClient(<ContactAccessAgentsGroupedList />);

    fireEvent.click(screen.getByLabelText(/Enable outbound for Aisyah Rahman/i));

    expect(setOneMutate).toHaveBeenCalledWith({ contactId: AISYAH, enabled: true });
  });

  it('marks a group with no linked contact as not linked', () => {
    mockState({}, [
      { ...ROWS[2], respond_contact_id: null, outbound_enabled: null },
    ]);
    renderWithClient(<ContactAccessAgentsGroupedList />);

    expect(screen.getByText('Not linked')).toBeInTheDocument();
  });

  it('keeps the loading and empty states', () => {
    mockState({ isLoading: true, data: undefined });
    const { container, rerender } = renderWithClient(<ContactAccessAgentsGroupedList />);
    // M5-02: the bare "Loading..." text became a SectionSkeleton.
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);

    cleanup();
    mockState({ data: { data: [], pagination: { total: 0, page: 1, limit: 10000 } } });
    renderWithClient(<ContactAccessAgentsGroupedList />);
    expect(screen.getByText('No contact access agents found')).toBeInTheDocument();
    void rerender;
  });
});
