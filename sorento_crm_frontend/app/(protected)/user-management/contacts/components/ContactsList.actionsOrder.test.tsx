/**
 * (harness copied from the S4a popover test) CONTACT-TAGS S4a (mock views "A", "A Filters open", "A Differs only"): the contacts list toolbar keeps
 * Search, Tag and Tag settings as primary controls; every other filter lives in the toolbar "Filters"
 * popover (data-grid-list-toolbar `filters` kind custom) with a count badge and a chip per active filter.
 * Harness mirrors ContactsList.regions.test.tsx.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
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

// Native <select> stand-in, labelled by its placeholder, so each filter is driven deterministically.
vi.mock('@/components/common/SearchableSelect', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/common/SearchableSelect')>();
  type Props = Parameters<typeof actual.SearchableSelect>[0];
  const Stub = ({ value, onChange, options, placeholder, disabled }: Props) => (
    <select aria-label={placeholder ?? 'select'} value={value ?? ''} disabled={disabled}
      onChange={(e) => onChange(e.target.value)}>
      <option value="" />
      {(options ?? []).map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
  return { ...actual, SearchableSelect: (props: Props) => <Stub {...props} /> };
});

const ROW = {
  id: 'contact-1', name: 'ZZT One', phone_number: '+60100000011', first_name: 'ZZT', last_name: 'One',
  respond_io_id: '1', outbound_enabled: true, access_type_codes: [], access_types: [], regions: ['west'],
  created_at: '2026-01-05T02:00:00', updated_at: '2026-01-05T02:00:00', linked_user_id: null, linked_user_name: null,
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
  getContactsMock.mockResolvedValue({ data: [ROW], pagination: { total: 1 } } as never);
  apiFetchMock.mockResolvedValue({ ok: true, json: async () => [] } as unknown as Response);
});
afterEach(() => cleanup());

describe('ContactsList Actions menu order (S4b)', () => {
  it('has one Actions menu with the tag items between Copy access and the destructive ones', async () => {
    renderList();
    await screen.findByText('+60100000011');
    fireEvent.click(screen.getAllByRole('checkbox', { name: 'Select row' })[0]);
    const triggers = await screen.findAllByRole('button', { name: /^Actions/ });
    expect(triggers).toHaveLength(1);
    expect(screen.queryByRole('button', { name: /^(Add tag|Remove tag|Apply tag settings)/ })).toBeNull();

    fireEvent.pointerDown(triggers[0], { button: 0, ctrlKey: false });
    const items = (await screen.findAllByRole('menuitem')).map((i) => i.textContent ?? '');
    const at = (prefix: string) => items.findIndex((t) => t.startsWith(prefix));
    expect(at('Copy access')).toBeGreaterThan(-1);
    expect(at('Add tag')).toBeGreaterThan(at('Copy access'));
    expect(at('Remove tag')).toBeGreaterThan(at('Add tag'));
    expect(at('Apply tag settings')).toBeGreaterThan(at('Remove tag'));
    expect(at('Disable messaging')).toBeGreaterThan(at('Apply tag settings'));
    expect(at('Delete')).toBeGreaterThan(at('Disable messaging'));
  });
});
