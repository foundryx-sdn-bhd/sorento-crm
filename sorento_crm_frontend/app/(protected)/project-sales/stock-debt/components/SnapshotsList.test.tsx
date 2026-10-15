/**
 * STOCK-DEBT-SNAPSHOT AC-F2: the snapshots list page body.
 *
 * DataGrid (fixed layout, resizable) with Name, Filters (truncate + title), Products, Decided,
 * Created by, Created at; a row opens the snapshot (`rowHref`); Delete only where `can_delete`,
 * as the deferred-action countdown (no confirm dialog); empty state "No snapshots yet" with a
 * hint and no button. AC-F8: no UUID on screen.
 *
 * The component takes no props. The service is mocked at its module boundary.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => '/project-sales/stock-debt/snapshots',
  useSearchParams: () => new URLSearchParams(''),
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));
vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn().mockResolvedValue({ listing_key: 'x', config: null }),
  upsertUserListColumnConfig: vi.fn(),
  resetUserListColumnConfig: vi.fn(),
}));
vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    dismiss: vi.fn(),
    custom: vi.fn(),
  }),
}));

const createPendingAction = vi.fn();
vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: (...args: unknown[]) => createPendingAction(...args),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn().mockResolvedValue({ pending: null, last_outcome: null }),
}));

const listSnapshots = vi.fn();
vi.mock('../services/stockDebtSnapshotService', () => ({
  listSnapshots: (...args: unknown[]) => listSnapshots(...args),
}));

import { SnapshotsList } from './SnapshotsList';

const MINE = 'c0a80101-7d3e-4b8a-9f21-5e6d7c8b9a01';
const THEIRS = 'c0a80101-7d3e-4b8a-9f21-5e6d7c8b9a02';
const FILTERS_LABEL =
  'Book Project · All locations · Delivery 01/01/2023 to 31/05/2027 · Only in debt · Stock and PO columns';

function snap(over: Record<string, unknown> = {}) {
  return {
    id: MINE,
    name: 'Stock Debt 15/10/2026',
    filters_label: FILTERS_LABEL,
    row_count: 550,
    decided_count: 5,
    created_by_name: 'Jayson Foundryx',
    created_at: '2026-10-15T03:53:00Z',
    can_delete: true,
    ...over,
  };
}

function envelope(rows: Array<Record<string, unknown>>) {
  return { data: rows, pagination: { total: rows.length, page: 1, limit: 25 } };
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <SnapshotsList />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listSnapshots.mockResolvedValue(
    envelope([snap(), snap({ id: THEIRS, name: 'Month end', can_delete: false, created_by_name: 'Eling' })]),
  );
  createPendingAction.mockResolvedValue({
    id: 'pa-1',
    commit_at: new Date(Date.now() + 10_000).toISOString(),
    window_seconds: 10,
  });
});

describe('AC-F2 columns and rows', () => {
  it('shows the six columns', async () => {
    renderList();
    await screen.findByText('Stock Debt 15/10/2026');
    for (const name of ['Name', 'Filters', 'Products', 'Decided', 'Created by', 'Created at']) {
      expect(screen.getByRole('columnheader', { name })).toBeInTheDocument();
    }
  });

  it('prints name, products, decided, creator and created-at in Malaysia time', async () => {
    renderList();
    const row = (await screen.findByText('Stock Debt 15/10/2026')).closest('tr') as HTMLElement;
    expect(within(row).getByText('550')).toBeInTheDocument();
    expect(within(row).getByText('5')).toBeInTheDocument();
    expect(within(row).getByText('Jayson Foundryx')).toBeInTheDocument();
    expect(row).toHaveTextContent('15/10/2026');
    expect(row).toHaveTextContent('11:53');
  });

  it('the Filters cell truncates and carries the full text as its title', async () => {
    renderList();
    const row = (await screen.findByText('Stock Debt 15/10/2026')).closest('tr') as HTMLElement;
    const cell = within(row).getByTitle(FILTERS_LABEL);
    expect(cell.className).toMatch(/truncate/);
  });

  it('is a fixed, resizable grid', async () => {
    renderList();
    const table = await screen.findByRole('table');
    expect(table.className).toMatch(/table-fixed/);
    expect(table.querySelector('th[style*="width"]')).toBeInTheDocument();
  });

  it('every column has an explicit size and all of them, Actions included, sum to at most 1000 px', async () => {
    renderList();
    await screen.findByText('Stock Debt 15/10/2026');
    const heads = Array.from(document.querySelectorAll('thead th')) as HTMLElement[];
    expect(heads.length).toBeGreaterThanOrEqual(7);
    const widths = heads.map((th) => parseFloat(th.style.width));
    for (const w of widths) expect(Number.isFinite(w)).toBe(true);
    const sum = widths.reduce((a, b) => a + b, 0);
    expect(sum).toBeLessThanOrEqual(1000);
  });

  it('pressing a row opens that snapshot', async () => {
    renderList();
    fireEvent.click(await screen.findByText('Month end'));
    expect(push).toHaveBeenCalled();
    expect(String(push.mock.calls[0][0])).toContain(`/project-sales/stock-debt/snapshots/${THEIRS}`);
  });

  it('prints no UUID anywhere (AC-F8)', async () => {
    renderList();
    await screen.findByText('Stock Debt 15/10/2026');
    expect(document.body.textContent).not.toContain(MINE);
    expect(document.body.textContent).not.toContain(THEIRS);
    expect(document.body.textContent).not.toMatch(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i,
    );
  });
});

describe('AC-F2 Delete', () => {
  it('is offered only on the rows the caller can delete', async () => {
    renderList();
    const mine = (await screen.findByText('Stock Debt 15/10/2026')).closest('tr') as HTMLElement;
    const theirs = (await screen.findByText('Month end')).closest('tr') as HTMLElement;
    expect(within(mine).getByRole('button', { name: /delete/i })).toBeInTheDocument();
    expect(within(theirs).queryByRole('button', { name: /delete/i })).not.toBeInTheDocument();
  });

  it('starts the stock_debt_snapshot.delete deferred action with no confirm dialog and does not open the row', async () => {
    renderList();
    const mine = (await screen.findByText('Stock Debt 15/10/2026')).closest('tr') as HTMLElement;

    fireEvent.click(within(mine).getByRole('button', { name: /delete/i }));

    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(createPendingAction).toHaveBeenCalledWith(
        expect.objectContaining({ actionKey: 'stock_debt_snapshot.delete', entityId: MINE }),
      ),
    );
    expect(push).not.toHaveBeenCalledWith(expect.stringContaining(`/snapshots/${MINE}`));
  });
});

describe('AC-F2 states', () => {
  it('shows skeleton rows while loading', () => {
    listSnapshots.mockReturnValue(new Promise(() => {}));
    const { container } = renderList();
    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
    expect(screen.queryByText('No snapshots yet')).not.toBeInTheDocument();
  });

  it('empty state is the heading "No snapshots yet" and a hint, with no button', async () => {
    listSnapshots.mockResolvedValue(envelope([]));
    renderList();
    const heading = await screen.findByText('No snapshots yet');
    const box = heading.parentElement as HTMLElement;
    expect(box.textContent!.length).toBeGreaterThan('No snapshots yet'.length);
    expect(within(box).queryByRole('button')).not.toBeInTheDocument();
    expect(within(box).queryByRole('link')).not.toBeInTheDocument();
  });

  it('an error states the server message, not the empty state', async () => {
    listSnapshots.mockRejectedValue(new Error('Snapshots are unavailable'));
    renderList();
    expect(await screen.findByText('Snapshots are unavailable')).toBeInTheDocument();
    expect(screen.queryByText('No snapshots yet')).not.toBeInTheDocument();
  });

  it('searching sends the name query', async () => {
    renderList();
    await screen.findByText('Stock Debt 15/10/2026');
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'Month' } });
    await waitFor(() =>
      expect(listSnapshots).toHaveBeenLastCalledWith(expect.objectContaining({ query: 'Month' })),
    );
  });
});
