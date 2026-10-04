/**
 * CUSTOMER-BULK-OPS U6: bulk "Set customer group" and "Remove from group" on the customers list.
 *
 * Contract the implementation must match:
 * - bulk strip buttons `Set customer group (N)` and `Remove from group (N)`, only with
 *   `order_management.customers.edit`
 * - Set opens a dialog (role=dialog) with a SearchableSelect (existing groups via `options` or
 *   `fetchOptions`, value = group id) carrying `createOption`; `Apply` is disabled until a group is
 *   chosen or a new name was created through `createOption.onCreate(name)`
 * - Apply, existing group: addCustomerGroupCustomers(groupId, customerIds) once
 * - Apply, new name: createCustomerGroup({ name }) first, then addCustomerGroupCustomers(newId, ids)
 * - success: toast.success, selection clears; failure: toast.error(message), selection kept
 * - Remove: real useDeferredBulkAction, key `customer.remove_from_group`, entity `customer`, one
 *   target per selected customer that HAS a group (payload { customer_group_id }), no dialog
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Customer } from '../types/customer.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => '/order-management/customers',
  useSearchParams: () => new URLSearchParams(''),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}));
vi.mock('@/lib/toast', () => ({ toast: toastMock }));

vi.mock('@/components/upload-activity', () => ({
  useImportJobDrawer: () => ({ notifyImportQueued: vi.fn() }),
}));

const perms = vi.hoisted(() => ({ granted: new Set<string>() }));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => perms.granted.has(slug),
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

vi.mock('../services/customerImportService', () => ({
  importCustomers: vi.fn(),
  validateCustomerImport: vi.fn(),
}));

vi.mock('@/components/common/deferredToast', () => ({
  deferredToast: (input: { id?: string }) => input.id ?? 'toast',
  dismissDeferredToast: vi.fn(),
}));

const pending = vi.hoisted(() => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn(),
}));
vi.mock('@/services/pendingActionService', () => pending);

const svc = vi.hoisted(() => ({
  addCustomerGroupCustomers: vi.fn(),
  createCustomerGroup: vi.fn(),
  searchCustomerGroupsSelect: vi.fn(),
}));
vi.mock(
  '@/app/(protected)/order-management/customer-groups/services/customerGroupService',
  async (importOriginal) => ({
    ...(await importOriginal<
      typeof import('@/app/(protected)/order-management/customer-groups/services/customerGroupService')
    >()),
    ...svc,
  }),
);

// Native stand-in: a <select> over the options (given or fetched), plus a button that triggers createOption.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    value: string;
    onChange: (v: string) => void;
    options?: { value: string; label: string }[];
    fetchOptions?: (q: string) => Promise<{ value: string; label: string }[]>;
    placeholder?: string;
    createOption?: { label: (q: string) => React.ReactNode; onCreate: (q: string) => void };
  }) => {
    const [fetched, setFetched] = React.useState<{ value: string; label: string }[]>([]);
    React.useEffect(() => {
      void props.fetchOptions?.('').then(setFetched);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    const opts = props.options ?? fetched;
    return (
      <div>
        <select
          aria-label={props.placeholder ?? 'select'}
          value={props.value ?? ''}
          onChange={(e) => props.onChange(e.target.value)}
        >
          <option value="" />
          {opts.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        {props.createOption ? (
          <button type="button" onClick={() => props.createOption?.onCreate('New Dealer Group')}>
            stub create group
          </button>
        ) : null}
      </div>
    );
  },
}));

const ROWS: Customer[] = [
  ['cust-a', 'C-001', 'Alpha Trading'],
  ['cust-b', 'C-002', 'Beta Supplies'],
  ['cust-c', 'C-003', 'Gamma Foods'],
].map(([id, code, name], i) => ({
  id,
  customer_code: code,
  customer_name: name,
  email: null,
  phone_number: null,
  is_active: true,
  created_at: new Date(`2026-01-0${i + 1}T00:00:00Z`),
  sales_agent_id: null,
  sales_agent_code: null,
  sales_agent_name: null,
  customer_group_id: id === 'cust-a' ? 'g-1' : null,
  customer_group_name: id === 'cust-a' ? 'Group One' : null,
})) as Customer[];

vi.mock('../hooks/useCustomers', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../hooks/useCustomers')>()),
  useCustomers: () => ({
    data: { data: ROWS, pagination: { page: 1, limit: 50, total: ROWS.length } },
    isLoading: false,
    isPlaceholderData: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
}));

import CustomersList from './CustomersList';

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <CustomersList />
    </QueryClientProvider>,
  );
}

function tickRow(code: string) {
  const row = screen.getByText(code).closest('tr') as HTMLElement;
  fireEvent.click(within(row).getByRole('checkbox'));
}

async function openSetDialog() {
  renderList();
  tickRow('C-001');
  tickRow('C-002');
  fireEvent.click(await screen.findByRole('button', { name: 'Set customer group (2)' }));
  return await screen.findByRole('dialog');
}

beforeEach(() => {
  vi.clearAllMocks();
  perms.granted = new Set(['order_management.customers.edit']);
  svc.addCustomerGroupCustomers.mockResolvedValue([]);
  svc.createCustomerGroup.mockResolvedValue({ id: 'g-new', name: 'New Dealer Group' });
  svc.searchCustomerGroupsSelect.mockResolvedValue([
    { id: 'g-1', name: 'Group One', ledger_count: 3 },
    { id: 'g-2', name: 'Group Two', ledger_count: 1 },
  ]);
  pending.createPendingAction.mockImplementation(async (input: { entityId: string }) => ({
    id: `pa-${input.entityId}`,
    entity_id: input.entityId,
    entity_type: 'customer',
    action_key: 'customer.remove_from_group',
    commit_at: new Date(Date.now() + 10000).toISOString().replace(/\.\d+Z$/, ''),
    status: 'pending',
  }));
});

describe('CustomersList - bulk Set customer group (U6.1 to U6.5)', () => {
  it('U6.1 shows both buttons with edit permission, neither without it', async () => {
    renderList();
    tickRow('C-001');
    tickRow('C-002');
    expect(await screen.findByRole('button', { name: 'Set customer group (2)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove from group (1)' })).toBeInTheDocument();
    cleanup();

    // Same list, sales agent perm on but customers.edit off: the strip exists, the group actions do not.
    perms.granted = new Set(['master_data.sales_agents.edit']);
    renderList();
    tickRow('C-001');
    tickRow('C-002');
    expect(await screen.findByRole('button', { name: 'Set sales agent (2)' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Set customer group/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Remove from group/ })).toBeNull();
  });

  it('U6.2 Apply is disabled until a group is chosen', async () => {
    const dialog = await openSetDialog();
    const apply = within(dialog).getByRole('button', { name: 'Apply' });
    expect(apply).toBeDisabled();
    await within(dialog).findByRole('option', { name: 'Group One' });
    fireEvent.change(within(dialog).getByRole('combobox'), { target: { value: 'g-1' } });
    expect(apply).toBeEnabled();
  });

  it('U6.2 Apply is enabled once a new name is created through createOption', async () => {
    const dialog = await openSetDialog();
    const apply = within(dialog).getByRole('button', { name: 'Apply' });
    expect(apply).toBeDisabled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'stub create group' }));
    await waitFor(() => expect(apply).toBeEnabled());
  });

  it('U6.3/U6.4 existing group: one addCustomerGroupCustomers, no create, toast, selection cleared', async () => {
    const dialog = await openSetDialog();
    await within(dialog).findByRole('option', { name: 'Group One' });
    fireEvent.change(within(dialog).getByRole('combobox'), { target: { value: 'g-1' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(svc.addCustomerGroupCustomers).toHaveBeenCalledTimes(1));
    const [groupId, ids] = svc.addCustomerGroupCustomers.mock.calls[0];
    expect(groupId).toBe('g-1');
    expect([...ids].sort()).toEqual(['cust-a', 'cust-b']);
    expect(svc.createCustomerGroup).not.toHaveBeenCalled();
    await waitFor(() => expect(toastMock.success).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /Set customer group/ })).toBeNull(),
    );
  });

  it('U6.3 new name: createCustomerGroup({ name }) first, then assign with the new id', async () => {
    const order: string[] = [];
    svc.createCustomerGroup.mockImplementation(async () => {
      order.push('create');
      return { id: 'g-new', name: 'New Dealer Group' };
    });
    svc.addCustomerGroupCustomers.mockImplementation(async () => {
      order.push('assign');
      return [];
    });
    const dialog = await openSetDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: 'stub create group' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(svc.addCustomerGroupCustomers).toHaveBeenCalledTimes(1));
    expect(svc.createCustomerGroup).toHaveBeenCalledTimes(1);
    expect(svc.createCustomerGroup).toHaveBeenCalledWith({ name: 'New Dealer Group' });
    const [groupId, ids] = svc.addCustomerGroupCustomers.mock.calls[0];
    expect(groupId).toBe('g-new');
    expect([...ids].sort()).toEqual(['cust-a', 'cust-b']);
    expect(order).toEqual(['create', 'assign']);
  });

  it('U6.5 a failed assign shows the error toast and keeps the selection', async () => {
    svc.addCustomerGroupCustomers.mockRejectedValue(new Error('Customer not in your company'));
    const dialog = await openSetDialog();
    await within(dialog).findByRole('option', { name: 'Group One' });
    fireEvent.change(within(dialog).getByRole('combobox'), { target: { value: 'g-1' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));

    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        expect.stringContaining('Customer not in your company'),
      ),
    );
    expect(toastMock.success).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /Set customer group \(2\)/ })).toBeInTheDocument();
  });
});

describe('CustomersList - bulk Remove from group (U6.6)', () => {
  it('parks one customer.remove_from_group per selected customer that has a group, no dialog', async () => {
    renderList();
    tickRow('C-001'); // cust-a, in g-1
    tickRow('C-002'); // cust-b, no group
    fireEvent.click(await screen.findByRole('button', { name: 'Remove from group (1)' }));

    await waitFor(() => expect(pending.createPendingAction).toHaveBeenCalledTimes(1));
    const call = pending.createPendingAction.mock.calls[0][0];
    expect(call.actionKey).toBe('customer.remove_from_group');
    expect(call.entityType).toBe('customer');
    expect(call.entityId).toBe('cust-a');
    expect(call.payload).toMatchObject({ customer_group_id: 'g-1' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });
});

describe('CustomersList - Remove from group counts only grouped customers (review R4)', () => {
  it('two customers with no group: no Remove from group button, or it is disabled', async () => {
    renderList();
    tickRow('C-002');
    tickRow('C-003');
    await screen.findByRole('button', { name: /Set customer group \(2\)/ });
    const remove = screen.queryByRole('button', { name: /Remove from group/ });
    if (remove) expect(remove).toBeDisabled();
    else expect(remove).toBeNull();
  });

  it('one grouped and one not: label counts the grouped one only', async () => {
    renderList();
    tickRow('C-001');
    tickRow('C-002');
    expect(await screen.findByRole('button', { name: 'Remove from group (1)' })).toBeInTheDocument();
  });
});

describe('CustomersList - new group created then assign fails (review R3)', () => {
  it('the error toast says the group was created and carries the server message', async () => {
    svc.addCustomerGroupCustomers.mockRejectedValue(new Error('Customer not in your company'));
    const dialog = await openSetDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: 'stub create group' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    const msg = String(toastMock.error.mock.calls[0][0]);
    expect(msg).toContain('Group New Dealer Group created');
    expect(msg).toContain('Customer not in your company');
  });
});
