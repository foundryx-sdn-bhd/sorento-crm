/**
 * CustomerGroupDetail (CUSTOMER-GROUP S3, AC-18). Group record page: header card, edit in
 * place, Delete as a parked server action (no confirm dialog), and the Ledgers tab.
 *
 * Expected service exports: getCustomerGroup, updateCustomerGroup, getCustomerGroupCustomers,
 * addCustomerGroupCustomers. Expected component: default export, prop `groupId`.
 * The ledger picker is `useCustomerMultiPicker` over `searchCustomersSelect` (as the sales
 * agent Customers tab).
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  });
}
Element.prototype.scrollIntoView = vi.fn();

const services = vi.hoisted(() => ({
  getCustomerGroup: vi.fn(),
  updateCustomerGroup: vi.fn(),
  getCustomerGroupCustomers: vi.fn(),
  addCustomerGroupCustomers: vi.fn(),
}));
vi.mock('../../services/customerGroupService', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ...services,
}));

// The picker runs the REAL `searchCustomersSelect` mapping; only the HTTP call under it is
// stubbed, so what the option shows is decided by the code under test, not by this file.
const api = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiFetch: api.apiFetch }));

const deferred = vi.hoisted(() => ({
  startRecord: vi.fn(),
  runRow: vi.fn(),
  recordInputs: [] as Record<string, unknown>[],
  rowInputs: [] as Record<string, unknown>[],
}));
vi.mock('@/hooks/useDeferredAction', () => ({
  useDeferredAction: (input: Record<string, unknown>) => {
    deferred.recordInputs.push(input);
    return {
      pending: null,
      isPending: false,
      isBlocked: false,
      start: deferred.startRecord,
      cancel: vi.fn(),
      countdown: null,
    };
  },
}));
vi.mock('@/hooks/useDeferredRowAction', () => ({
  useDeferredRowAction: (input: Record<string, unknown>) => {
    deferred.rowInputs.push(input);
    return { run: deferred.runRow, targetId: null, countdown: null, isPending: false };
  },
  useRowPending: () => () => false,
}));

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: nav.push, replace: vi.fn() }),
  usePathname: () => '/order-management/customer-groups/grp-1',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));
vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn(async () => ({ listing_key: 'k', config: null })),
  upsertUserListColumnConfig: vi.fn(async (listingKey: string, payload: unknown) => ({
    listing_key: listingKey,
    config: payload,
  })),
  resetUserListColumnConfig: vi.fn(async () => undefined),
}));

vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: (props: {
    value: string[];
    onChange: (v: string[]) => void;
    fetchOptions?: (
      q: string,
    ) => Promise<{ value: string; label: string; description?: string; disabled?: boolean }[]>;
  }) => {
    const [opts, setOpts] = React.useState<
      { value: string; label: string; description?: string; disabled?: boolean }[]
    >([]);
    return (
      <div>
        <button type="button" onClick={() => void props.fetchOptions?.('').then(setOpts)}>
          open picker
        </button>
        {opts.map((o) => (
          <label key={o.value}>
            <input
              type="checkbox"
              aria-label={o.label}
              checked={props.value.includes(o.value)}
              disabled={o.disabled}
              onChange={(e) =>
                props.onChange(
                  e.target.checked
                    ? [...props.value, o.value]
                    : props.value.filter((v) => v !== o.value),
                )
              }
            />
            <span>{o.description}</span>
          </label>
        ))}
      </div>
    );
  },
}));

import CustomerGroupDetail from './CustomerGroupDetail';

const GROUP = {
  id: 'grp-1',
  name: 'HANLIM TRADING SDN BHD',
  ledger_count: 6,
  account_levels: [1, 2, 3, 4],
  created_at: '2026-10-01T08:00:00',
  updated_at: '2026-10-02T08:00:00',
};

const LEDGERS = [
  { id: 'c-1', customer_code: '300-H030', customer_name: 'HANLIM TRADING SDN BHD [A/C I]', account_level: 1, is_active: true },
  { id: 'c-2', customer_code: '300-H070', customer_name: 'HANLIM TRADING SDN BHD [A/C II]', account_level: 2, is_active: true },
  { id: 'c-3', customer_code: '300-H118', customer_name: 'HANLIM TRADING SDN BHD (CERAMIC & ELLECI)', account_level: null, is_active: true },
];

// Rows as GET /customers/select returns them.
const SELECT_ROWS = [
  { id: 'free-1', customer_code: '300-H119', customer_name: 'HANLIM TRADING SDN BHD [A/C IV]', customer_group_id: 'grp-9', customer_group_name: 'JUBIN KEMUNING SDN BHD' },
  { id: 'free-2', customer_code: '300-H030', customer_name: 'HANLIM TRADING SDN BHD', customer_group_id: null, customer_group_name: null },
  { id: 'own-1', customer_code: '300-H070', customer_name: 'HANLIM TRADING SDN BHD [A/C II]', customer_group_id: 'grp-1', customer_group_name: 'HANLIM TRADING SDN BHD' },
];
const OPTIONS = [
  { label: '300-H119 - HANLIM TRADING SDN BHD [A/C IV]' },
  { label: '300-H030 - HANLIM TRADING SDN BHD' },
  { label: '300-H070 - HANLIM TRADING SDN BHD [A/C II]' },
];

function page(rows: typeof LEDGERS) {
  return { data: rows, pagination: { total: rows.length, page: 1, limit: 50 } };
}

function renderDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CustomerGroupDetail groupId="grp-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  Object.values(services).forEach((fn) => fn.mockReset());
  deferred.startRecord.mockReset();
  deferred.runRow.mockReset();
  deferred.recordInputs.length = 0;
  deferred.rowInputs.length = 0;
  nav.push.mockReset();
  services.getCustomerGroup.mockResolvedValue(GROUP);
  services.getCustomerGroupCustomers.mockResolvedValue(page(LEDGERS));
  services.updateCustomerGroup.mockResolvedValue({ ...GROUP, name: 'HANLIM RENAMED' });
  services.addCustomerGroupCustomers.mockResolvedValue({ data: [] });
  api.apiFetch.mockReset();
  api.apiFetch.mockResolvedValue({
    ok: true,
    json: async () => ({ data: SELECT_ROWS }),
  });
});
afterEach(() => cleanup());

describe('CustomerGroupDetail', () => {
  it('AC-18: the header shows the name, "6 ledgers" and the accounts', async () => {
    renderDetail();

    expect(await screen.findByText('HANLIM TRADING SDN BHD')).toBeInTheDocument();
    expect(screen.getByText('6 ledgers')).toBeInTheDocument();
    expect(screen.getByText('A/C 1 to 4')).toBeInTheDocument();
  });

  it('AC-18: Edit swaps the name for an input and Save sends PATCH with the new name', async () => {
    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: /^edit$/i }));
    const input = (await screen.findByDisplayValue('HANLIM TRADING SDN BHD')) as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'HANLIM RENAMED' } });
    fireEvent.click(screen.getByRole('button', { name: /^save/i }));

    await waitFor(() => expect(services.updateCustomerGroup).toHaveBeenCalled());
    expect(services.updateCustomerGroup).toHaveBeenCalledWith('grp-1', { name: 'HANLIM RENAMED' });
  });

  it('AC-18: Delete parks customer_group.delete with no confirm dialog', async () => {
    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: /^delete/i }));

    await waitFor(() => expect(deferred.startRecord).toHaveBeenCalled());
    expect(deferred.recordInputs.at(-1)).toMatchObject({
      actionKey: 'customer_group.delete',
      entityType: 'customer_group',
      entityId: 'grp-1',
    });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('AC-18: Ledgers tab rows show code, name and "Account N" or "-"', async () => {
    renderDetail();

    expect(await screen.findByText('300-H030')).toBeInTheDocument();
    expect(screen.getByText('HANLIM TRADING SDN BHD [A/C I]')).toBeInTheDocument();
    expect(screen.getByText('Account 1')).toBeInTheDocument();
    expect(screen.getByText('Account 2')).toBeInTheDocument();
    const noLevel = screen.getByText('300-H118').closest('tr') as HTMLElement;
    expect(noLevel.textContent).toContain('-');
    expect(noLevel.textContent).not.toMatch(/Account \d/);
  });

  it('AC-18: Remove parks customer.remove_from_group with the customer_group_id payload', async () => {
    renderDetail();

    await screen.findByText('300-H030');
    fireEvent.click(screen.getAllByRole('button', { name: /remove/i })[0]);

    expect(deferred.rowInputs.at(-1)).toMatchObject({
      actionKey: 'customer.remove_from_group',
      entityType: 'customer',
    });
    expect(deferred.runRow).toHaveBeenCalledTimes(1);
    const target = deferred.runRow.mock.calls[0][0] as {
      id: string;
      payload?: Record<string, unknown>;
    };
    expect(target.id).toBe('c-1');
    expect(target.payload).toEqual({ customer_group_id: 'grp-1' });
  });

  it('AC-18: Add ledgers posts the ticked ids in one call', async () => {
    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: 'open picker' }));
    fireEvent.click(await screen.findByLabelText(OPTIONS[0].label));
    fireEvent.click(screen.getByLabelText(OPTIONS[1].label));
    fireEvent.click(screen.getByRole('button', { name: /add .*ledger/i }));

    await waitFor(() => expect(services.addCustomerGroupCustomers).toHaveBeenCalledTimes(1));
    expect(services.addCustomerGroupCustomers).toHaveBeenCalledWith('grp-1', [
      'free-1',
      'free-2',
    ]);
  });

  it('mock 5: the picker shows each customer\'s current group, "no group" when none', async () => {
    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: 'open picker' }));
    await screen.findByLabelText(OPTIONS[0].label);
    expect(screen.getByText('in JUBIN KEMUNING SDN BHD')).toBeInTheDocument();
    expect(screen.getByText('no group')).toBeInTheDocument();
  });

  it('mock 5: a customer already in THIS group is shown but cannot be ticked', async () => {
    renderDetail();

    fireEvent.click(await screen.findByRole('button', { name: 'open picker' }));
    expect(await screen.findByLabelText(OPTIONS[2].label)).toBeDisabled();
    expect(screen.getByLabelText(OPTIONS[0].label)).toBeEnabled();
    expect(screen.getByLabelText(OPTIONS[1].label)).toBeEnabled();
  });

  it('AC-18: an empty group shows "No ledgers in this group"', async () => {
    services.getCustomerGroupCustomers.mockResolvedValue(page([]));
    services.getCustomerGroup.mockResolvedValue({ ...GROUP, ledger_count: 0, account_levels: [] });
    renderDetail();

    expect(await screen.findByText('No ledgers in this group')).toBeInTheDocument();
  });
});

describe('CustomerGroupDetail agent (CUSTOMER-SALES-AGENT, AC-8)', () => {
  it('header shows "Agent: <label>" and the Ledgers tab shows the code and name', async () => {
    services.getCustomerGroup.mockResolvedValue({
      ...GROUP, name: 'GRP-1 TRADING',
      sales_agent_label: 'AGENT-A',
      sales_agent_mixed: false,
    });
    services.getCustomerGroupCustomers.mockResolvedValue(
      page([
        { ...LEDGERS[0], sales_agent_code: 'AGENT-A I', sales_agent_name: 'Person A' },
        { ...LEDGERS[1], sales_agent_code: 'AGENT-A III', sales_agent_name: null },
      ] as unknown as typeof LEDGERS),
    );
    renderDetail();

    expect(await screen.findByText('Agent: AGENT-A')).toBeInTheDocument();
    expect(await screen.findByText('AGENT-A I - Person A')).toBeInTheDocument();
    expect(screen.getByText('AGENT-A III')).toBeInTheDocument();
  });

  it('header shows "Agent: Mixed" when the ledgers disagree and nothing when none is set', async () => {
    services.getCustomerGroup.mockResolvedValue({ ...GROUP, name: 'GRP-1 TRADING', sales_agent_mixed: true });
    const mixed = renderDetail();
    expect(await screen.findByText('Agent: Mixed')).toBeInTheDocument();
    mixed.unmount();

    services.getCustomerGroup.mockResolvedValue({ ...GROUP, name: 'GRP-1 TRADING', sales_agent_label: null });
    renderDetail();
    await screen.findByText('GRP-1 TRADING');
    expect(screen.queryByText(/^Agent:/)).toBeNull();
  });
});
