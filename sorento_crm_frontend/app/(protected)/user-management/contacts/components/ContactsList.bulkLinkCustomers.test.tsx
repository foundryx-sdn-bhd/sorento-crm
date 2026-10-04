/**
 * Lane CUSTOMER-BULK-OPS, UAC U4 + U5 (Phase 2 red tests, written before the implementation).
 * Contacts list: bulk "Link customers (n)", the Customers column, the "No customers linked" filter.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const apiFetch = vi.fn();
vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

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

const permissionState = vi.hoisted(() => ({
  granted: new Set<string>(['user_management.contacts.edit']),
}));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => permissionState.granted.has(slug),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

const nav = vi.hoisted(() => ({
  search: '',
  replace: vi.fn(),
  push: vi.fn(),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: nav.replace, push: nav.push }),
  usePathname: () => '/user-management/contacts',
  useSearchParams: () => new URLSearchParams(nav.search),
}));

const services = vi.hoisted(() => ({
  getContactCustomers: vi.fn(),
  linkContactCustomers: vi.fn(),
}));
vi.mock('../[id]/services/contactCustomersService', () => services);

const customerSvc = vi.hoisted(() => ({
  searchCustomersSelect: vi.fn(),
  CUSTOMER_SELECT_PAGE_SIZE: 50,
}));
vi.mock('@/app/(protected)/order-management/customers/services/customerService', () => customerSvc);

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}));
vi.mock('@/lib/toast', () => ({ toast: toastMock }));

// Stand-in for the Radix popover multi-select: loads options on mount, lists them as checkboxes.
vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: (props: {
    value: string[];
    onChange: (v: string[]) => void;
    fetchOptions?: (q: string) => Promise<{ value: string; label: string }[]>;
  }) => {
    const [opts, setOpts] = React.useState<{ value: string; label: string }[]>([]);
    React.useEffect(() => {
      void props.fetchOptions?.('').then(setOpts);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
      <div>
        {opts.map((o) => (
          <label key={o.value}>
            <input
              type="checkbox"
              aria-label={o.label}
              checked={props.value.includes(o.value)}
              onChange={(e) =>
                props.onChange(
                  e.target.checked
                    ? [...props.value, o.value]
                    : props.value.filter((v) => v !== o.value),
                )
              }
            />
          </label>
        ))}
      </div>
    );
  },
}));

import ContactsList from './ContactsList';
import { contactsListFilters } from '../lib/listQuery';

const base = {
  outbound_enabled: true,
  access_type_codes: [],
  access_types: [],
  created_at: '2026-01-05T02:00:00',
  updated_at: '2026-01-05T02:00:00',
};
const CONTACTS = [
  {
    ...base,
    id: 'contact-aisyah',
    phone_number: '+60100000004',
    name: 'Aisyah Rahman',
    first_name: 'Aisyah',
    last_name: 'Rahman',
    respond_io_id: '10025901',
    customer_codes: ['C-100', 'C-200'],
  },
  {
    ...base,
    id: 'contact-farah',
    phone_number: '+60100000003',
    name: 'Farah Idris',
    first_name: 'Farah',
    last_name: 'Idris',
    respond_io_id: '10025902',
    customer_codes: [],
  },
];
const OPTIONS = [
  { value: 'cust-1', label: 'Option C-100', description: 'x' },
  { value: 'cust-2', label: 'Option C-300', description: 'x' },
];

function mockContacts(rows: unknown[] = CONTACTS) {
  apiFetch.mockResolvedValue({
    ok: true,
    json: async () => ({
      data: rows,
      pagination: { total: rows.length, page: 1, limit: 50 },
      empty: rows.length === 0,
    }),
  });
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContactsList />
    </QueryClientProvider>,
  );
}

async function selectBothRows() {
  await screen.findByText('+60100000004');
  const boxes = screen.getAllByRole('checkbox', { name: 'Select row' });
  boxes.forEach((b) => fireEvent.click(b));
}

beforeEach(() => {
  apiFetch.mockReset();
  nav.search = '';
  nav.replace.mockReset();
  nav.push.mockReset();
  Object.values(services).forEach((fn) => fn.mockReset());
  Object.values(toastMock).forEach((fn) => fn.mockReset());
  customerSvc.searchCustomersSelect.mockReset();
  customerSvc.searchCustomersSelect.mockResolvedValue(
    OPTIONS.map((o) => ({ ...o, disabled: false })),
  );
  permissionState.granted = new Set(['user_management.contacts.edit']);
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

async function openDialogAndPick(ids: string[]) {
  await selectBothRows();
  fireEvent.click(await screen.findByRole('button', { name: 'Link customers (2)' }));
  const dialog = await screen.findByRole('dialog');
  for (const label of ids) {
    fireEvent.click(await within(dialog).findByLabelText(label));
  }
  return dialog;
}

describe('ContactsList - bulk Link customers (U4)', () => {
  it('U4.1 shows "Link customers (2)" for 2 selected contacts with contacts.edit', async () => {
    mockContacts();
    renderList();
    await selectBothRows();
    expect(await screen.findByRole('button', { name: 'Link customers (2)' })).toBeInTheDocument();
  });

  it('U4.1 hides the button without contacts.edit', async () => {
    permissionState.granted = new Set();
    mockContacts();
    renderList();
    await selectBothRows();
    expect(screen.queryByRole('button', { name: /Link customers/ })).not.toBeInTheDocument();
  });

  it('U4.2 Apply is disabled until a customer is picked', async () => {
    mockContacts();
    renderList();
    await selectBothRows();
    fireEvent.click(await screen.findByRole('button', { name: 'Link customers (2)' }));
    const dialog = await screen.findByRole('dialog');
    const apply = within(dialog).getByRole('button', { name: 'Apply' });
    expect(apply).toBeDisabled();
    fireEvent.click(await within(dialog).findByLabelText('Option C-100'));
    expect(apply).toBeEnabled();
  });

  it('U4.3/U4.4 links once per selected contact with the picked ids, toasts, clears selection', async () => {
    mockContacts();
    services.linkContactCustomers.mockResolvedValue([]);
    renderList();
    const dialog = await openDialogAndPick(['Option C-100', 'Option C-300']);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(services.linkContactCustomers).toHaveBeenCalledTimes(2));
    const calls = services.linkContactCustomers.mock.calls;
    expect(calls.map((c) => c[0]).sort()).toEqual(['contact-aisyah', 'contact-farah']);
    calls.forEach((c) => expect([...(c[1] as string[])].sort()).toEqual(['cust-1', 'cust-2']));
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled());
    expect(toastMock.error).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /Link customers/ })).not.toBeInTheDocument(),
    );
  });

  it('U4.5 one failure: error toast names that contact, it stays selected, the ok one does not', async () => {
    mockContacts();
    services.linkContactCustomers.mockImplementation(async (id: string) => {
      if (id === 'contact-farah') throw new Error('Customer is inactive');
      return [];
    });
    renderList();
    const dialog = await openDialogAndPick(['Option C-100']);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    const msg = String(toastMock.error.mock.calls[0][0]);
    expect(/Farah Idris|\+60100000003/.test(msg)).toBe(true);
    expect(/Aisyah Rahman|\+60100000004/.test(msg)).toBe(false);
    expect(msg).toContain('Customer is inactive');
    expect(await screen.findByRole('button', { name: 'Link customers (1)' })).toBeInTheDocument();
    const boxes = screen.getAllByRole('checkbox', { name: 'Select row' }) as HTMLInputElement[];
    expect(boxes.filter((b) => b.checked || b.getAttribute('aria-checked') === 'true')).toHaveLength(1);
  });
});

describe('ContactsList - Customers column (U5.1)', () => {
  it('renders linked codes joined by ", " and empty when none', async () => {
    mockContacts();
    renderList();
    expect(await screen.findByText('Customers')).toBeInTheDocument();
    expect(await screen.findByText('C-100, C-200')).toBeInTheDocument();
  });
});

describe('ContactsList - "No customers linked" filter (U5.2/U5.3)', () => {
  it('the toolbar control sends customers=none (request or URL write)', async () => {
    mockContacts();
    renderList();
    await screen.findByText('+60100000004');
    fireEvent.click(await screen.findByLabelText('No customers linked'));
    await waitFor(() => {
      const fetched = apiFetch.mock.calls.some((c) => String(c[0]).includes('customers=none'));
      const pushed = [...nav.replace.mock.calls, ...nav.push.mock.calls].some((c) =>
        String(c[0]).includes('customers=none'),
      );
      expect(fetched || pushed).toBe(true);
    });
  });

  it('a page URL carrying customers=none sends it on the list GET', async () => {
    nav.search = 'customers=none';
    mockContacts();
    renderList();
    await waitFor(() =>
      expect(
        apiFetch.mock.calls.some(
          (c) => String(c[0]).includes('/contacts?') && String(c[0]).includes('customers=none'),
        ),
      ).toBe(true),
    );
  });

  it('contactsListFilters({ customersNone: true }) is { customers: "none" }', () => {
    expect(contactsListFilters({ chatbotMemoryLevel: null, customersNone: true })).toEqual({
      customers: 'none',
    });
    expect(contactsListFilters({ chatbotMemoryLevel: null, customersNone: false })).toEqual({});
  });
});

describe('ContactsList - selection resets on page change (review R2)', () => {
  const PAGE2 = [
    {
      ...base,
      id: 'contact-gita',
      phone_number: '+60100000073',
      name: 'Gita Lim',
      first_name: 'Gita',
      last_name: 'Lim',
      respond_io_id: '10025903',
      customer_codes: [],
    },
  ];

  function mockTwoPages() {
    apiFetch.mockImplementation(async (url: string) => {
      const page2 = /[?&]page=2(&|$)/.test(String(url));
      return {
        ok: true,
        json: async () => ({
          data: page2 ? PAGE2 : CONTACTS,
          pagination: { total: 100, page: page2 ? 2 : 1, limit: 50 },
          empty: false,
        }),
      };
    });
  }

  it('a page change drops the ticked rows and the bulk strip; page 2 ticks link exactly that contact', async () => {
    mockTwoPages();
    services.linkContactCustomers.mockResolvedValue([]);
    renderList();
    await screen.findByText('+60100000004');
    fireEvent.click(screen.getAllByRole('checkbox', { name: 'Select row' })[0]);
    expect(await screen.findByRole('button', { name: 'Link customers (1)' })).toBeInTheDocument();

    fireEvent.click(await screen.findByRole('button', { name: /Go to next page/ }));
    await screen.findByText('+60100000073');
    expect(screen.queryByRole('button', { name: /Link customers/ })).not.toBeInTheDocument();
    const boxes = screen.getAllByRole('checkbox', { name: 'Select row' }) as HTMLInputElement[];
    expect(boxes.some((b) => b.checked || b.getAttribute('aria-checked') === 'true')).toBe(false);

    fireEvent.click(boxes[0]);
    fireEvent.click(await screen.findByRole('button', { name: 'Link customers (1)' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(await within(dialog).findByLabelText('Option C-100'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(services.linkContactCustomers).toHaveBeenCalledTimes(1));
    expect(services.linkContactCustomers.mock.calls[0][0]).toBe('contact-gita');
  });
});
