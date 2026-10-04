/**
 * Internal Users list - the "User" column and the "Create user" row action
 * (s3-contract.md 2.3, AC-59).
 *
 * Mocked at the CONTACT SERVICE boundary (`getContacts` from
 * `../[id]/services/contactService`, which `../lib/listQuery.ts` calls for
 * this list) rather than `apiFetch` - that service file is being swapped from
 * its Phase 1 mock to a real call while this file is written.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
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

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { email: 'admin@zzt.test' } } }),
}));
vi.mock('@/lib/is-superadmin', () => ({ isSuperadminUser: () => false }));

const permsRef = vi.hoisted(() => ({ view: true, add: true }));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => {
    if (slug === 'user_management.users.view') return permsRef.view;
    if (slug === 'user_management.users.add') return permsRef.add;
    return false;
  },
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/user-management/contacts',
  useSearchParams: () => ({ get: () => null }),
}));

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

type Row = {
  id: string;
  phone_number: string;
  name: string;
  first_name: string;
  last_name: string;
  respond_io_id: string;
  outbound_enabled: boolean;
  access_type_codes: string[];
  access_types: never[];
  created_at: string;
  updated_at: string;
  linked_user_id: string | null;
  linked_user_name: string | null;
};

const UNLINKED_ROW: Row = {
  id: 'contact-aisyah',
  phone_number: '+60100000004',
  name: 'Aisyah Rahman',
  first_name: 'Aisyah',
  last_name: 'Rahman',
  respond_io_id: '10025901',
  outbound_enabled: true,
  access_type_codes: [],
  access_types: [],
  created_at: '2026-01-05T02:00:00',
  updated_at: '2026-01-05T02:00:00',
  linked_user_id: null,
  linked_user_name: null,
};

const LINKED_USER_ID = 'a8888888-8888-4888-8888-888888888888';
const LINKED_ROW: Row = {
  id: 'contact-priya',
  phone_number: '+60100000003',
  name: 'Priya Nair',
  first_name: 'Priya',
  last_name: 'Nair',
  respond_io_id: '10025902',
  outbound_enabled: true,
  access_type_codes: [],
  access_types: [],
  created_at: '2026-01-06T02:00:00',
  updated_at: '2026-01-06T02:00:00',
  linked_user_id: LINKED_USER_ID,
  linked_user_name: 'Priya Nair',
};

const getContactsMock = vi.fn();
const getContactMock = vi.fn(async (id: string) =>
  [UNLINKED_ROW, LINKED_ROW].find((r) => r.id === id),
);
const getContactCompaniesMock = vi.fn();
vi.mock('../[id]/services/contactService', () => ({
  getContacts: (...a: unknown[]) => getContactsMock(...a),
  getContact: (id: string) => getContactMock(id),
  getContactCompanies: (...a: unknown[]) => getContactCompaniesMock(...a),
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

/**
 * Radix opens the row menu on pointerdown, which jsdom does not synthesize
 * from a click - the established pattern (`ComplaintsList.rowActions.test.tsx`).
 *
 * Both rows share the trigger's accessible name ("contact actions"), so this
 * always opens a SPECIFIC row's menu (scoped to the `<tr>` the caller hands
 * in) rather than `findByRole` over the whole document, which throws
 * "found multiple elements" the moment a second row is on screen.
 */
async function openRowMenu(row: HTMLElement) {
  const trigger = within(row).getByRole('button', { name: /contact actions/i });
  trigger.focus();
  fireEvent.keyDown(trigger, { key: 'ArrowDown', code: 'ArrowDown' });
  return screen.findByRole('menu');
}

beforeEach(() => {
  vi.clearAllMocks();
  permsRef.view = true;
  permsRef.add = true;
  getContactsMock.mockResolvedValue({
    data: [UNLINKED_ROW, LINKED_ROW],
    pagination: { total: 2 },
  } as never);
  apiFetchMock.mockResolvedValue({ ok: true, json: async () => [] } as unknown as Response);
});

afterEach(() => cleanup());

// The grid's contact-name columns are split (First name / Last name); the
// combined "Aisyah Rahman" never appears as one text node, but the outbound
// switch's own accessible name always names the whole contact - and only
// exists once a row has actually rendered, so it is also a safe "data has
// loaded" wait target (`ContactsList.test.tsx` uses the same anchor).
const waitForAisyahRow = () => screen.findByLabelText(/outbound for Aisyah Rahman/i);

describe('ContactsList - the User column (AC-59)', () => {
  it('shows the User column with users.view, linking the linked row and leaving the other with no link', async () => {
    renderList();
    const aisyahSwitch = await waitForAisyahRow();

    expect(screen.getByRole('columnheader', { name: 'User' })).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Priya Nair' });
    expect(link).toHaveAttribute('href', `/user-management/users/${LINKED_USER_ID}`);

    // Aisyah's row (unlinked) carries no such link.
    const aisyahRow = aisyahSwitch.closest('tr')!;
    expect(within(aisyahRow).queryByRole('link')).not.toBeInTheDocument();
  });

  it('hides the column entirely without users.view', async () => {
    permsRef.view = false;
    renderList();
    await waitForAisyahRow();

    expect(screen.queryByRole('columnheader', { name: 'User' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Priya Nair' })).not.toBeInTheDocument();
  });
});

describe('ContactsList - the "Create user" row action (AC-59)', () => {
  it('appears first, only on the row with no linked user', async () => {
    renderList();
    const aisyahSwitch = await waitForAisyahRow();

    const unlinkedMenu = await openRowMenu(aisyahSwitch.closest('tr')!);
    expect(within(unlinkedMenu).getAllByRole('menuitem')[0]).toHaveTextContent('Create user');
  });

  it('is absent on the already-linked row', async () => {
    renderList();
    await waitForAisyahRow();

    const priyaRow = screen.getByText('Priya Nair').closest('tr')!;
    const menu = await openRowMenu(priyaRow);

    expect(within(menu).queryByRole('menuitem', { name: 'Create user' })).not.toBeInTheDocument();
  });

  it('is absent everywhere without users.add, even on an unlinked row', async () => {
    permsRef.add = false;
    renderList();
    const aisyahSwitch = await waitForAisyahRow();

    const menu = await openRowMenu(aisyahSwitch.closest('tr')!);
    expect(within(menu).queryByRole('menuitem', { name: 'Create user' })).not.toBeInTheDocument();
  });

  // Fix round 2, N3: without users.view the list sends no linked_user_id, so
  // every row looks unlinked and "Create user" would answer 409 on a linked one.
  it('is absent without users.view, even with users.add', async () => {
    permsRef.view = false;
    renderList();
    await waitForAisyahRow();

    for (const row of screen.getAllByRole('row').slice(1)) {
      const trigger = within(row).queryByRole('button', { name: /contact actions/i });
      if (!trigger) continue;
      const menu = await openRowMenu(row);
      expect(within(menu).queryByRole('menuitem', { name: 'Create user' })).not.toBeInTheDocument();
      fireEvent.keyDown(menu, { key: 'Escape', code: 'Escape' });
    }
  });

  it('opens the Add user modal with that contact locked', async () => {
    renderList();
    const aisyahSwitch = await waitForAisyahRow();

    const menu = await openRowMenu(aisyahSwitch.closest('tr')!);
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Create user' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Add User')).toBeInTheDocument();
  });
});
