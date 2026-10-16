/**
 * CONTACT-TAGS S4a (mock views "A", "A Filters open", "A Differs only"): the contacts list toolbar keeps
 * Search, Tag and Tag settings as primary controls; every other filter lives in the toolbar "Filters"
 * popover (data-grid-list-toolbar `filters` kind custom) with a count badge and a chip per active filter.
 * Harness mirrors ContactsList.regions.test.tsx.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
const lastFilters = () => {
  const calls = getContactsMock.mock.calls;
  return calls[calls.length - 1][1] as Record<string, string>;
};
const filtersButton = () => screen.getByRole('button', { name: /^filters/i });

beforeEach(() => {
  vi.clearAllMocks();
  getContactsMock.mockResolvedValue({ data: [ROW], pagination: { total: 1 } } as never);
  apiFetchMock.mockResolvedValue({ ok: true, json: async () => [] } as unknown as Response);
});
afterEach(() => cleanup());

describe('ContactsList filters popover', () => {
  it('shows Tag and Tag settings in the toolbar and keeps the other filters out of the row until Filters opens', async () => {
    const user = userEvent.setup();
    renderList();
    await screen.findByText('+60100000011');
    expect(screen.getByLabelText('Tag settings')).toBeInTheDocument();
    expect(filtersButton()).toBeInTheDocument();
    expect(screen.queryByLabelText(/region/i)).toBeNull();
    expect(screen.queryByLabelText(/no customers linked/i)).toBeNull();
    expect(screen.queryByLabelText(/^no tags$/i)).toBeNull();

    await user.click(filtersButton());
    expect(await screen.findByLabelText(/region/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/no customers linked/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^no tags$/i)).toBeInTheDocument();
  });

  it('a Region filter shows count 1 on the button and a removable chip', async () => {
    const user = userEvent.setup();
    renderList();
    await screen.findByText('+60100000011');
    await user.click(filtersButton());
    await user.selectOptions(await screen.findByLabelText(/region/i), 'west');
    await waitFor(() => expect(lastFilters().region).toBe('west'));
    await user.keyboard('{Escape}');

    await waitFor(() => expect(within(filtersButton()).getByText('1')).toBeInTheDocument());
    expect(screen.getByText('Region: West')).toBeInTheDocument();
    expect(window.location.search).toContain('region=west');
    await user.click(screen.getByRole('button', { name: 'Clear filter: Region: West' }));
    // The list query is cached (staleTime Infinity): assert what the user sees and the URL.
    await waitFor(() => expect(screen.queryByText('Region: West')).toBeNull());
    expect(filtersButton()).toHaveTextContent(/^Filters$/);
    expect(window.location.search).not.toContain('region');
  });

  it('Clear filters in the popover clears every popover filter and leaves Tag settings alone', async () => {
    const user = userEvent.setup();
    renderList();
    await screen.findByText('+60100000011');
    await user.selectOptions(screen.getByLabelText('Tag settings'), 'differs');
    await user.click(filtersButton());
    await user.selectOptions(await screen.findByLabelText(/region/i), 'west');
    await user.click(screen.getByLabelText(/no customers linked/i));
    await waitFor(() => {
      expect(lastFilters().region).toBe('west');
      expect(lastFilters().customers).toBe('none');
    });

    await user.click(screen.getByRole('button', { name: /clear filters/i }));
    await waitFor(() => expect(window.location.search).not.toContain('region'));
    expect(window.location.search).not.toContain('customers');
    expect(window.location.search).toContain('tag_settings=differs');
    expect(screen.queryByText('Region: West')).toBeNull();
    expect(screen.queryByText('No customers linked', { selector: 'span' })).toBeNull();
    expect(screen.getByLabelText('Tag settings')).toHaveValue('differs');
  });
});
