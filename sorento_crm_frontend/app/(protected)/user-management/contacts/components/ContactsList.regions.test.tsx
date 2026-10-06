/**
 * Lane CONTACT-REGION, AC-CR-19 / AC-CR-20 (contacts list Regions column + Region filter).
 * Harness mirrors `ContactsList.accessTypesOverflow.test.tsx` (mocked at the contact service
 * boundary). RED until the list renders the column and sends `region` to the list request.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
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
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { email: 'admin@zzt.test' } } }),
}));
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
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

// A native <select> stand-in so the Region filter is driven deterministically.
vi.mock('@/components/common/SearchableSelect', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/common/SearchableSelect')>();
  type Props = Parameters<typeof actual.SearchableSelect>[0];
  const Stub = ({ value, onChange, options, placeholder, disabled }: Props) => (
    <select
      aria-label={placeholder ?? 'select'}
      value={value ?? ''}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="" />
      {(options ?? []).map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
  return { ...actual, SearchableSelect: (props: Props) => <Stub {...props} /> };
});

const BASE_ROW = {
  phone_number: '+60100000011',
  first_name: 'ZZT',
  last_name: 'Region',
  respond_io_id: '10025911',
  outbound_enabled: true,
  access_type_codes: [],
  access_types: [],
  created_at: '2026-01-05T02:00:00',
  updated_at: '2026-01-05T02:00:00',
  linked_user_id: null,
  linked_user_name: null,
};
const BOTH_ROW = { ...BASE_ROW, id: 'contact-both', name: 'ZZT Both', regions: ['west', 'east'] };
const WEST_ROW = { ...BASE_ROW, id: 'contact-west', name: 'ZZT West', phone_number: '+60100000012', regions: ['west'] };

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
  getContactsMock.mockResolvedValue({ data: [BOTH_ROW, WEST_ROW], pagination: { total: 2 } } as never);
  apiFetchMock.mockResolvedValue({ ok: true, json: async () => [] } as unknown as Response);
});
afterEach(() => cleanup());

describe('ContactsList - Regions column and filter (AC-CR-19, AC-CR-20)', () => {
  it('AC-CR-19: a west+east row shows one badge per region with the long labels', async () => {
    renderList();
    const row = (await screen.findByText('+60100000011')).closest('tr') as HTMLElement;
    expect(within(row).getByText('West Malaysia')).toBeInTheDocument();
    expect(within(row).getByText('East Malaysia')).toBeInTheDocument();
  });

  it('AC-CR-19: a west-only row shows only West Malaysia', async () => {
    renderList();
    const row = (await screen.findByText('+60100000012')).closest('tr') as HTMLElement;
    expect(within(row).getByText('West Malaysia')).toBeInTheDocument();
    expect(within(row).queryByText('East Malaysia')).not.toBeInTheDocument();
  });

  it('AC-CR-20: choosing East in the Region filter sends region=east to the list request', async () => {
    renderList();
    await screen.findByText('+60100000011');
    fireEvent.change(screen.getByLabelText(/region/i), { target: { value: 'east' } });
    await waitFor(() => {
      const calls = getContactsMock.mock.calls;
      const filters = calls[calls.length - 1][1] as Record<string, string>;
      expect(filters.region).toBe('east');
    });
  });
});
