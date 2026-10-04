import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ContactsList from './ContactsList';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({
  apiFetch: (...a: unknown[]) => apiFetch(...a),
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

vi.mock('@/components/contacts/PortalLinkButton', () => ({ default: () => null }));
vi.mock('@/services/contactImpersonationService', () => ({
  startContactImpersonation: vi.fn(),
}));

// Identity S3: the row now renders the Add user modal (closed) for the "Create
// user" action, which reads the session and the caller's permissions even
// while closed - neither is under test here.
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { email: 'admin@zzt.test' } } }),
}));
vi.mock('@/lib/is-superadmin', () => ({ isSuperadminUser: () => false }));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => false,
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/user-management/contact-access-agents',
  useSearchParams: () => ({ get: () => null }),
}));

const CONTACTS = [
  {
    id: 'contact-aisyah',
    phone_number: '+60100000004',
    name: 'Aisyah Rahman',
    first_name: 'Aisyah',
    last_name: 'Rahman',
    respond_io_id: '10025901',
    outbound_enabled: false,
    access_type_codes: [],
    access_types: [],
    created_at: '2026-01-05T02:00:00',
    updated_at: '2026-01-05T02:00:00',
  },
  {
    id: 'contact-farah',
    phone_number: '+60100000003',
    name: 'Farah Idris',
    first_name: 'Farah',
    last_name: 'Idris',
    respond_io_id: '10025902',
    outbound_enabled: true,
    access_type_codes: [],
    access_types: [],
    created_at: '2026-01-06T02:00:00',
    updated_at: '2026-01-06T02:00:00',
  },
];

function mockContacts(rows = CONTACTS) {
  apiFetch.mockResolvedValue({
    ok: true,
    json: async () => ({
      data: rows,
      pagination: { total: rows.length, page: 1, limit: 50 },
      empty: rows.length === 0,
    }),
  });
}

function renderWithClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContactsList />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiFetch.mockReset();
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

describe('ContactsList - the outbound column', () => {
  it('shows who can be messaged and who is silenced', async () => {
    mockContacts();
    renderWithClient();

    expect(await screen.findByText('Silenced')).toBeInTheDocument();
    expect(screen.getByText('Can be messaged')).toBeInTheDocument();
  });

  it('counts the reachable and the silenced above the grid', async () => {
    mockContacts();
    renderWithClient();

    await screen.findByLabelText(/Enable outbound for Aisyah Rahman/i);
    expect(screen.getByText('Reachable on this page').nextElementSibling).toHaveTextContent('1');
    expect(screen.getByText('Silenced on this page').nextElementSibling).toHaveTextContent('1');
  });

  it('flips one contact immediately, with no dialog', async () => {
    mockContacts();
    renderWithClient();

    fireEvent.click(await screen.findByLabelText(/Disable outbound for Farah Idris/i));

    expect(setOneMutate).toHaveBeenCalledWith({
      contactId: 'contact-farah',
      enabled: false,
    });
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('re-enables a silenced contact', async () => {
    mockContacts();
    renderWithClient();

    fireEvent.click(await screen.findByLabelText(/Enable outbound for Aisyah Rahman/i));

    expect(setOneMutate).toHaveBeenCalledWith({
      contactId: 'contact-aisyah',
      enabled: true,
    });
  });

  it('renders an empty grid without counts it cannot know', async () => {
    mockContacts([]);
    renderWithClient();

    await waitFor(() =>
      expect(screen.getByText('Reachable on this page').nextElementSibling).toHaveTextContent('0'),
    );
    expect(screen.queryByText('Aisyah Rahman')).not.toBeInTheDocument();
  });
});

describe('ContactsList - bulk messaging actions', () => {
  async function selectAllRows() {
    // Wait for the rows themselves: the header checkbox renders during the
    // skeleton too, and toggling it then selects nothing.
    await screen.findByLabelText(/outbound for Aisyah Rahman/i);
    fireEvent.click(screen.getByRole('checkbox', { name: /select all/i }));
  }

  it('enables a selection without a confirmation', async () => {
    mockContacts();
    renderWithClient();
    await selectAllRows();

    fireEvent.click(screen.getByRole('button', { name: /Enable messaging \(2\)/ }));

    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(setBulkMutate).toHaveBeenCalledWith(
      { enabled: true, contactIds: ['contact-aisyah', 'contact-farah'] },
      expect.anything(),
    );
  });

  it('confirms a bulk disable, naming the contact count', async () => {
    mockContacts();
    renderWithClient();
    await selectAllRows();

    fireEvent.click(screen.getByRole('button', { name: /Disable messaging \(2\)/ }));

    const dialog = screen.getByRole('alertdialog');
    expect(
      within(dialog).getByText(
        /2 contact\(s\) will receive no WhatsApp messages until outbound is switched back on/i,
      ),
    ).toBeInTheDocument();
    expect(setBulkMutate).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole('button', { name: /^Disable$/ }));
    expect(setBulkMutate).toHaveBeenCalledWith(
      { enabled: false, contactIds: ['contact-aisyah', 'contact-farah'] },
      expect.anything(),
    );
  });
});
