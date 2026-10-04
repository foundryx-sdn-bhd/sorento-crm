/**
 * Contacts list, "Brands" column (CONTACT-BRAND-SCOPE, AC-3). Phase 2 RED.
 *
 * A scoped contact's row shows its brand names; an unscoped one (empty `brands`) reads "All".
 * The column is hideable, so it must be reachable through the column visibility menu.
 * Same harness as `ContactsList.accessTypesOverflow.test.tsx` (mocked at the contact service).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
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
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetchMock(...a) }));
vi.mock('@/hooks/useRespondContactOutbound', () => ({
  RESPOND_CONTACTS_OUTBOUND_KEY: 'respond-contacts-outbound',
  useRespondContactOutboundMutations: () => ({
    setOne: { mutate: vi.fn(), isPending: false },
    setBulk: { mutate: vi.fn(), isPending: false },
  }),
}));
vi.mock('@/components/contacts/PortalLinkButton', () => ({ default: () => null }));
vi.mock('@/services/contactImpersonationService', () => ({ startContactImpersonation: vi.fn() }));
vi.mock('next-auth/react', () => ({ useSession: () => ({ data: { user: { email: 'admin@zzt.test' } } }) }));
vi.mock('@/lib/is-superadmin', () => ({ isSuperadminUser: () => false }));
vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/user-management/contacts',
  useSearchParams: () => ({ get: () => null }),
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

const BASE_ROW = {
  phone_number: '+60123456701',
  respond_io_id: '10025901',
  outbound_enabled: true,
  access_type_codes: ['end_user'],
  access_types: [{ code: 'end_user', name: 'End User' }],
  created_at: '2026-01-05T02:00:00',
  updated_at: '2026-01-05T02:00:00',
  linked_user_id: null,
  linked_user_name: null,
};
const SCOPED = {
  ...BASE_ROW,
  id: 'contact-scoped',
  name: 'Scoped Sam',
  first_name: 'Scoped',
  last_name: 'Sam',
  brands: [
    { id: '11111111-1111-4111-8111-111111111111', brand_name: 'Mocha' },
    { id: '22222222-2222-4222-8222-222222222222', brand_name: 'Cabana' },
  ],
};
const OPEN = {
  ...BASE_ROW,
  id: 'contact-open',
  phone_number: '+60123456702',
  name: 'Open Olive',
  first_name: 'Open',
  last_name: 'Olive',
  brands: [],
};

const getContactsMock = vi.fn();
vi.mock('../[id]/services/contactService', () => ({
  getContacts: (...a: unknown[]) => getContactsMock(...a),
  getContact: vi.fn(),
  getContactCompanies: vi.fn(),
}));

import ContactsList from './ContactsList';

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContactsList />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getContactsMock.mockResolvedValue({ data: [SCOPED, OPEN], pagination: { total: 2 } } as never);
  apiFetchMock.mockResolvedValue({ ok: true, json: async () => [] } as unknown as Response);
});
afterEach(() => cleanup());

describe('ContactsList - Brands column (AC-3)', () => {
  it('has a "Brands" column header', async () => {
    renderList();
    await screen.findByText('+60123456701');
    expect(screen.getByText('Brands')).toBeInTheDocument();
  });

  it('shows the brand names of a scoped contact, never an id', async () => {
    renderList();
    const row = (await screen.findByText('+60123456701')).closest('tr') as HTMLElement;
    expect(within(row).getByText(/Mocha/)).toBeInTheDocument();
    expect(within(row).getByText(/Cabana/)).toBeInTheDocument();
    expect(row.textContent).not.toContain('11111111-1111');
  });

  it('shows "All" for a contact with no brand scope', async () => {
    renderList();
    const row = (await screen.findByText('+60123456702')).closest('tr') as HTMLElement;
    expect(within(row).getByText(/^All$/)).toBeInTheDocument();
    expect(within(row).queryByText(/Mocha/)).not.toBeInTheDocument();
  });
});
