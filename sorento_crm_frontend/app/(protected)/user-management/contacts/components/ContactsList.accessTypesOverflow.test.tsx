/**
 * Internal Users, Access types column (owner rule 29 Sep 2026, AC-PO-3): a contact with
 * several access types reads as ONE row line - the types that fit, then "+N" - and "+N"
 * opens a popover listing every type, on top of the list, closed by Escape.
 *
 * Same harness as `ContactsList.user-column.test.tsx` (mocked at the contact service
 * boundary). jsdom lays the row out at width 0, so the shared `PillOverflow` shows only
 * the first pill and folds the rest behind "+N"; the fit math itself is covered by
 * `components/common/PillOverflow.test.tsx`. Every visible-pill query is scoped to the
 * cell's own test id: the hidden measuring row repeats the same labels off-screen.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  cleanup,
  waitFor,
  within,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

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
  })) as unknown as typeof window.matchMedia;
}
if (!('ResizeObserver' in window)) {
  (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? vi.fn();

const apiFetchMock = vi.fn();
vi.mock('@/lib/api', () => ({
  apiFetch: (...a: unknown[]) => apiFetchMock(...a),
}));

vi.mock('@/hooks/useRespondContactOutbound', () => ({
  RESPOND_CONTACTS_OUTBOUND_KEY: 'respond-contacts-outbound',
  useRespondContactOutboundMutations: () => ({
    setOne: { mutate: vi.fn(), isPending: false },
    setBulk: { mutate: vi.fn(), isPending: false },
  }),
}));

vi.mock('@/components/contacts/PortalLinkButton', () => ({
  default: () => null,
}));
vi.mock('@/services/contactImpersonationService', () => ({
  startContactImpersonation: vi.fn(),
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { email: 'admin@zzt.test' } } }),
}));
vi.mock('@/lib/is-superadmin', () => ({ isSuperadminUser: () => false }));

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({
    resetToDefaults: vi.fn(),
    isLoading: false,
  }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/user-management/contacts',
  useSearchParams: () => ({ get: () => null }),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

const ACCESS_TYPES = [
  { code: 'sorento_office', name: 'Sorento Office' },
  { code: 'sorento_dealer', name: 'Sorento Dealer' },
  { code: 'mocha_dealer', name: 'Mocha Dealer' },
  { code: 'mocha_office', name: 'Mocha Office' },
  { code: 'cabana_office', name: 'Cabana Office' },
  { code: 'cabana_dealer', name: 'Cabana Dealer' },
  { code: 'end_user', name: 'End User' },
];

const SEVEN_TYPES_ROW = {
  id: 'contact-seven',
  phone_number: '+60100000004',
  name: 'Seven Types',
  first_name: 'Seven',
  last_name: 'Types',
  respond_io_id: '10025901',
  outbound_enabled: true,
  access_type_codes: ACCESS_TYPES.map((t) => t.code),
  access_types: ACCESS_TYPES,
  created_at: '2026-01-05T02:00:00',
  updated_at: '2026-01-05T02:00:00',
  linked_user_id: null,
  linked_user_name: null,
};

const ONE_TYPE_ROW = {
  ...SEVEN_TYPES_ROW,
  id: 'contact-one',
  phone_number: '+60100000003',
  name: 'One Type',
  first_name: 'One',
  last_name: 'Type',
  respond_io_id: '10025902',
  access_type_codes: ['end_user'],
  access_types: [ACCESS_TYPES[6]],
};

const getContactsMock = vi.fn();
vi.mock('../[id]/services/contactService', () => ({
  getContacts: (...a: unknown[]) => getContactsMock(...a),
  getContact: vi.fn(),
  getContactCompanies: vi.fn(),
}));

import ContactsList from './ContactsList';

function renderList() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ContactsList />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getContactsMock.mockResolvedValue({
    data: [SEVEN_TYPES_ROW, ONE_TYPE_ROW],
    pagination: { total: 2 },
  } as never);
  apiFetchMock.mockResolvedValue({
    ok: true,
    json: async () => [],
  } as unknown as Response);
});

afterEach(() => cleanup());

describe('ContactsList - Access types column folds to one line with "+N" (AC-PO-3)', () => {
  it('shows the first access type and a "+N" chip counting the folded ones', async () => {
    renderList();
    const cell = within(
      await screen.findByTestId('contact-access-types-contact-seven'),
    );

    expect(cell.getByText('Sorento Office')).toBeInTheDocument();
    expect(cell.getByText('+6')).toBeInTheDocument();
    expect(cell.queryByText('End User')).not.toBeInTheDocument();
    expect(
      screen.getByRole('group', { name: 'Access types of Seven Types' }),
    ).toBeInTheDocument();
  });

  it('shows no "+N" for a contact with one access type (AC-PO-6)', async () => {
    renderList();
    const cell = within(
      await screen.findByTestId('contact-access-types-contact-one'),
    );

    expect(cell.getByText('End User')).toBeInTheDocument();
    expect(cell.queryByText(/^\+\d+$/)).not.toBeInTheDocument();
  });

  it('"+N" opens a popover listing every access type, and Escape closes it', async () => {
    renderList();
    const cell = within(
      await screen.findByTestId('contact-access-types-contact-seven'),
    );

    fireEvent.click(cell.getByText('+6'));

    const popoverId = 'contact-access-types-contact-seven-popover';
    const popover = within(await screen.findByTestId(popoverId));
    for (const type of ACCESS_TYPES) {
      expect(popover.getByText(type.name)).toBeInTheDocument();
    }

    fireEvent.keyDown(screen.getByTestId(popoverId), { key: 'Escape' });
    await waitFor(() =>
      expect(screen.queryByTestId(popoverId)).not.toBeInTheDocument(),
    );
  });

  it('"+N" is keyboard reachable: Space opens the same popover', async () => {
    renderList();
    const cell = within(
      await screen.findByTestId('contact-access-types-contact-seven'),
    );
    const more = cell.getByText('+6');
    expect(more).toHaveAttribute('tabindex', '0');

    more.focus();
    fireEvent.keyDown(more, { key: ' ' });

    expect(
      await screen.findByTestId('contact-access-types-contact-seven-popover'),
    ).toBeInTheDocument();
  });
});
