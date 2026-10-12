/**
 * Warehouse EDIT view - pins the "View and Edit are the same layout (binding)" contract
 * from CLAUDE.md ("CRUD UX standard").
 *
 * The edit view must present the SAME two tabs, in the same order, holding the same fields
 * in the same order, as the read view (see the sibling `[id]/page.test.tsx`, which asserts
 * the identical arrays):
 *
 *   Basic Information : System Location, System Location Description, Warehouse, Active Status
 *   Planning          : Available for planning (switch), Fulfilment planning (switch),
 *                       Draws stock from (select)
 *
 * A warehouse that is its own pool (`pool_warehouse_id == id`, the seeded state for site
 * pools such as BRW and HQ) must survive an edit: it shows as "This warehouse (own pool)"
 * and a save that never touched the field must not send it as null.
 *
 * Plus two rules the current form breaks:
 * - "Draws stock from" is optional, so it must be clearable. The user-reported bug is that
 *     once a pool is chosen it can be changed but never unset.
 * - No multi-sentence explanatory prose in the UI.
 *
 * The data layer (hooks + service) is mocked at the module boundary; nothing hits the network.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import WarehouseForm from './WarehouseForm';
import type { Warehouse } from '../types/warehouse.types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CURRENT_ID = '11111111-1111-4111-8111-111111111111';
const POOL_ID = '22222222-2222-4222-8222-222222222222';
const OTHER_ID = '33333333-3333-4333-8333-333333333333';

const CURRENT: Warehouse = {
  id: CURRENT_ID,
  warehouse_code: 'WH-CUR',
  warehouse_name: 'Main Store',
  location: 'Shah Alam DC',
  manager_id: null,
  is_active: true,
  created_at: new Date('2026-01-05T02:00:00Z'),
  updated_at: new Date('2026-02-09T04:30:00Z'),
  counts_as_available: true,
  fulfilment_planning: false,
  pool_warehouse_id: POOL_ID,
  pool_warehouse_code: 'WH-POOL',
};

const POOL: Warehouse = {
  id: POOL_ID,
  warehouse_code: 'WH-POOL',
  warehouse_name: 'Shared Pool',
  location: 'Shah Alam DC',
  manager_id: null,
  is_active: true,
  created_at: new Date('2026-01-05T02:00:00Z'),
  updated_at: null,
  counts_as_available: true,
  fulfilment_planning: false,
  pool_warehouse_id: null,
  pool_warehouse_code: null,
};

const OTHER: Warehouse = {
  id: OTHER_ID,
  warehouse_code: 'WH-OTHER',
  warehouse_name: 'Overflow',
  location: 'Klang',
  manager_id: null,
  is_active: true,
  created_at: new Date('2026-01-05T02:00:00Z'),
  updated_at: null,
  counts_as_available: true,
  fulfilment_planning: false,
  pool_warehouse_id: null,
  pool_warehouse_code: null,
};

const ALL = [CURRENT, POOL, OTHER];

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const h = vi.hoisted(() => ({
  push: vi.fn(),
  back: vi.fn(),
  createMutate: vi.fn(),
  updateMutate: vi.fn(),
  loadingWarehouse: false,
  // The record the edit form loads. Swapped per test for a self-pooled one.
  current: null as Warehouse | null,
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: h.push, back: h.back, replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
  usePathname: () => `/inventory-management/warehouses/${CURRENT_ID}/edit`,
}));

// Partial mock: any hook the implementation reaches for that is not overridden here stays
// real (and is inert, because lib/api is stubbed below).
vi.mock('../hooks/useWarehouses', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../hooks/useWarehouses')>()),
  useWarehouse: (id: string | null) => ({
    data: id ? (h.current ?? CURRENT) : undefined,
    isLoading: h.loadingWarehouse,
    isError: false,
  }),
  useWarehouses: () => ({
    data: { data: ALL, pagination: { page: 1, limit: 100, total: ALL.length, total_pages: 1 } },
    isLoading: false,
    isError: false,
  }),
  useCreateWarehouse: () => ({ mutateAsync: h.createMutate, isPending: false }),
  useUpdateWarehouse: () => ({ mutateAsync: h.updateMutate, isPending: false }),
}));

vi.mock('../services/warehouseService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../services/warehouseService')>()),
  getWarehouses: vi.fn(async () => ({
    data: ALL,
    pagination: { page: 1, limit: 1000, total: ALL.length, total_pages: 1 },
  })),
  getWarehouse: vi.fn(async () => CURRENT),
}));

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiFetch: vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      data: ALL,
      pagination: { page: 1, limit: 1000, total: ALL.length, total_pages: 1 },
    }),
  })),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderForm(warehouseId: string | undefined = CURRENT_ID) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <WarehouseForm warehouseId={warehouseId} />
    </QueryClientProvider>,
  );
}

/** Create mode. `renderForm(undefined)` would fall back to the default id, so it needs its own. */
function renderCreateForm() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <WarehouseForm />
    </QueryClientProvider>,
  );
}

const tabNames = () =>
  screen.getAllByRole('tab').map((t) => (t.textContent ?? '').trim());

/**
 * Radix TabsTrigger activates on mousedown; click alone is not enough in jsdom.
 * Tolerant of a tab-less layout so the field-level tests below fail on their own
 * subject rather than cascading off the (separately asserted) tab structure.
 */
function selectTab(name: string) {
  const tab = screen.queryByRole('tab', { name });
  if (!tab) return;
  fireEvent.mouseDown(tab, { button: 0 });
  fireEvent.click(tab);
}

const activePanel = () => screen.getByRole('tabpanel');

/** Assert the given strings appear in this DOM order inside `scope`. */
function expectTextOrder(scope: HTMLElement, texts: string[]) {
  const content = (scope.textContent ?? '').replace(/\s+/g, ' ');
  let cursor = -1;
  for (const text of texts) {
    const at = content.indexOf(text, cursor + 1);
    expect(
      at,
      `expected "${text}" to appear after the preceding field, in: ${content}`,
    ).toBeGreaterThan(cursor);
    cursor = at;
  }
}

/**
 * The column span each field takes in the two-column grid, keyed by label.
 *
 * DOM order agreeing between the two views is not enough: at >= md a field given
 * `md:col-span-2` in one view and a single cell in the other lands in a different row
 * AND a different column, so "Active Status" appears at row2-right in one and row3-full
 * in the other. That is the drift the "nothing moves" clause exists to stop, and text
 * order is blind to it.
 *
 * This map is asserted VERBATIM in the sibling `[id]/page.test.tsx`. Change one side and
 * the other's copy goes red.
 */
const FIELD_SPANS: Record<string, 'full' | 'half'> = {
  'System Location': 'half',
  'System Location Description': 'half',
  Warehouse: 'full',
  'Active Status': 'full',
  'Available for planning': 'full',
  // Borrow ladder v7.1 S1 (AC-S1-3): the flag sits directly under `Available for planning`,
  // full width, in BOTH views - the read view mirrors it in the same position.
  'Fulfilment planning': 'full',
  'Draws stock from': 'half',
  'Sells to': 'half',
};

/**
 * Span per field label for the grid inside the active tab panel.
 *
 * The grid cell is a direct child of the grid container: a `FormItem` on the edit view,
 * a read-only `Field` wrapper on the read view. Its label is a `<label>` (edit) or the
 * leading `<p>` (read), so one helper serves both files.
 */
function gridSpans(): Record<string, 'full' | 'half'> {
  const panel = activePanel();
  const grid = (panel.querySelector('.grid') ?? panel) as HTMLElement;
  const spans: Record<string, 'full' | 'half'> = {};
  for (const cell of Array.from(grid.children) as HTMLElement[]) {
    const labelEl = cell.querySelector('label') ?? cell.querySelector('p');
    const label = (labelEl?.textContent ?? '').replace(/\*$/, '').trim();
    if (!label) continue;
    spans[label] = cell.className.includes('md:col-span-2') ? 'full' : 'half';
  }
  return spans;
}

/** The subset of FIELD_SPANS covering the given labels. */
const expectedSpans = (labels: string[]) =>
  Object.fromEntries(labels.map((label) => [label, FIELD_SPANS[label]]));

const poolTrigger = () =>
  document.querySelector('[data-slot="searchable-select-trigger"]') as HTMLElement | null;

const optionLabels = () =>
  [...document.querySelectorAll('[role="option"]')].map((o) => (o.textContent ?? '').trim());

/**
 * Multi-sentence teaching text that must not be in the UI. Short labels and one-line
 * "what happens if I set this" hints are fine; these are not.
 */
const BANNED_PROSE = [
  'Turn this off for held, reserved, defective or clearance locations',
  'The shared pool this location draws on',
  'A shortage here is covered',
  'Stock here is ignored when the plan decides what to buy',
];

beforeEach(() => {
  h.push.mockClear();
  h.back.mockClear();
  h.createMutate.mockReset().mockResolvedValue(CURRENT);
  h.updateMutate.mockReset().mockResolvedValue(CURRENT);
  h.loadingWarehouse = false;
  h.current = null;
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('WarehouseForm - same layout as the read view', () => {
  it('renders exactly two tabs: Basic Information then Planning', () => {
    renderForm();
    expect(tabNames()).toEqual(['Basic Information', 'Planning']);
  });

  it('opens on Basic Information, holding the identity fields in the read view order', () => {
    renderForm();

    expect(screen.getByRole('tab', { name: 'Basic Information' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expectTextOrder(activePanel(), [
      'System Location',
      'System Location Description',
      'Warehouse',
      'Active Status',
    ]);
  });

  it('keeps the planning fields on the Planning tab, in the read view order', () => {
    renderForm();
    selectTab('Planning');

    expectTextOrder(activePanel(), [
      'Available for planning',
      'Fulfilment planning',
      'Draws stock from',
    ]);
    // Two switches now (availability, fulfilment planning); the pool is a select.
    expect(screen.getAllByRole('switch')).toHaveLength(2);
    expect(poolTrigger()).toBeTruthy();
  });

  it('gives the Basic Information fields the spans the read view uses', () => {
    renderForm();

    expect(gridSpans()).toEqual(
      expectedSpans([
        'System Location',
        'System Location Description',
        'Warehouse',
        'Active Status',
      ]),
    );
  });

  it('gives the Planning fields the spans the read view uses', () => {
    renderForm();
    selectTab('Planning');

    expect(gridSpans()).toEqual(
      expectedSpans([
        'Available for planning',
        'Fulfilment planning',
        'Draws stock from',
        'Sells to',
      ]),
    );
  });

  it('does not spread the planning fields across the Basic Information tab', () => {
    renderForm();
    const basic = (activePanel().textContent ?? '').replace(/\s+/g, ' ');
    expect(basic).not.toContain('Draws stock from');
    expect(basic).not.toContain('Available for planning');
  });

  it('does not put read-only metadata inside a tab body', () => {
    renderForm();
    const basic = (activePanel().textContent ?? '').replace(/\s+/g, ' ');
    expect(basic).not.toContain('Created');
    expect(basic).not.toContain('Last Updated');
  });

  it('carries no multi-sentence explanatory prose', () => {
    renderForm();
    selectTab('Planning');

    const page = (document.body.textContent ?? '').replace(/\s+/g, ' ');
    for (const prose of BANNED_PROSE) {
      expect(page, `explanatory prose still in the UI: "${prose}"`).not.toContain(prose);
    }
  });
});

describe('WarehouseForm - Fulfilment planning switch (AC-S1-3)', () => {
  /** The switch inside the form item whose label matches, so the two switches never mix up. */
  function switchFor(label: string): HTMLElement {
    const item = screen
      .getByText(label)
      .closest('[data-slot="form-item"]') as HTMLElement | null;
    const control = (item ?? document.body).querySelector(
      '[role="switch"]',
    ) as HTMLElement | null;
    expect(control, `no switch beside "${label}"`).toBeTruthy();
    return control!;
  }

  it('seeds Off from a record whose flag is off', () => {
    renderForm();
    selectTab('Planning');

    expect(switchFor('Fulfilment planning')).toHaveAttribute('aria-checked', 'false');
  });

  it('defaults to Off on create - the column default, not its neighbour true', () => {
    renderCreateForm();
    selectTab('Planning');

    expect(switchFor('Fulfilment planning')).toHaveAttribute('aria-checked', 'false');
    // The neighbouring `counts_as_available` still defaults ON, so the two are not being
    // read off one another.
    expect(switchFor('Available for planning')).toHaveAttribute('aria-checked', 'true');
  });

  it('saves the flipped flag through the existing PUT', async () => {
    renderForm();
    selectTab('Planning');

    fireEvent.click(switchFor('Fulfilment planning'));
    fireEvent.click(screen.getByRole('button', { name: /save|update/i }));

    await waitFor(() => expect(h.updateMutate).toHaveBeenCalledTimes(1));
    expect(h.updateMutate.mock.calls[0][0]).toMatchObject({
      id: CURRENT_ID,
      data: expect.objectContaining({ fulfilment_planning: true }),
    });
  });
});

describe('WarehouseForm - Draws stock from', () => {
  it('shows the pool the record already draws on', async () => {
    renderForm();
    selectTab('Planning');

    // Resolved to a human-readable label, never a UUID (cursor rule).
    await waitFor(() => expect(poolTrigger()!.textContent).toContain('Shared Pool'));
    expect(poolTrigger()!.textContent).not.toContain(POOL_ID);
  });

  it('offers a clear affordance once a pool is set', () => {
    renderForm();
    selectTab('Planning');

    expect(screen.getByRole('button', { name: /clear/i })).toBeInTheDocument();
  });

  it('clears back to empty and saves the pool as null', async () => {
    renderForm();
    selectTab('Planning');
    await waitFor(() => expect(poolTrigger()!.textContent).toContain('Shared Pool'));

    fireEvent.pointerDown(screen.getByRole('button', { name: /clear/i }));

    // The trigger falls back to its placeholder: the pool is genuinely unset, not just hidden.
    await waitFor(() => expect(poolTrigger()!.textContent).not.toContain('Shared Pool'));

    fireEvent.click(screen.getByRole('button', { name: /save|update/i }));

    await waitFor(() => expect(h.updateMutate).toHaveBeenCalledTimes(1));
    expect(h.updateMutate.mock.calls[0][0]).toMatchObject({
      id: CURRENT_ID,
      data: expect.objectContaining({ pool_warehouse_id: null }),
    });
  });

  it('offers the record being edited only as "This warehouse (own pool)"', async () => {
    renderForm();
    selectTab('Planning');

    fireEvent.click(poolTrigger()!);

    await waitFor(() => expect(optionLabels().length).toBeGreaterThan(0));
    const labels = optionLabels().join(' | ');
    expect(labels).toContain('This warehouse (own pool)');
    expect(labels).not.toContain('WH-CUR');
    expect(labels).not.toContain('Main Store');
    expect(labels).toContain('Overflow');
  });

  it('does not send the pool when only another field changed', async () => {
    renderForm();
    await waitFor(() => expect(screen.getByDisplayValue('Main Store')).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue('Main Store'), { target: { value: 'Main Store 2' } });
    fireEvent.click(screen.getByRole('button', { name: /save|update/i }));

    await waitFor(() => expect(h.updateMutate).toHaveBeenCalledTimes(1));
    const { data } = h.updateMutate.mock.calls[0][0];
    expect(data).toMatchObject({ warehouse_name: 'Main Store 2' });
    expect(data).not.toHaveProperty('pool_warehouse_id');
  });
});

describe('WarehouseForm - a warehouse that is its own pool', () => {
  const SELF: Warehouse = {
    ...CURRENT,
    pool_warehouse_id: CURRENT_ID,
    pool_warehouse_code: 'WH-CUR',
  };

  it('shows "This warehouse (own pool)" as the selected pool', async () => {
    h.current = SELF;
    renderForm();
    selectTab('Planning');

    await waitFor(() => expect(poolTrigger()!.textContent).toContain('This warehouse (own pool)'));
    expect(poolTrigger()!.textContent).not.toContain(CURRENT_ID);
  });

  it('keeps the self-pool on a save that never touched the field', async () => {
    h.current = SELF;
    renderForm();
    await waitFor(() => expect(screen.getByDisplayValue('Main Store')).toBeInTheDocument());

    fireEvent.change(screen.getByDisplayValue('Main Store'), { target: { value: 'Main Store 2' } });
    fireEvent.click(screen.getByRole('button', { name: /save|update/i }));

    await waitFor(() => expect(h.updateMutate).toHaveBeenCalledTimes(1));
    const { data } = h.updateMutate.mock.calls[0][0];
    // Never null for an untouched field: the self-pool either goes out unchanged or not at all.
    expect(data.pool_warehouse_id === undefined || data.pool_warehouse_id === CURRENT_ID).toBe(true);
  });

  it('can make a stand-alone warehouse its own pool', async () => {
    h.current = { ...CURRENT, pool_warehouse_id: null, pool_warehouse_code: null };
    renderForm();
    selectTab('Planning');

    fireEvent.click(poolTrigger()!);
    await waitFor(() => expect(optionLabels()).toContain('This warehouse (own pool)'));
    fireEvent.click(screen.getByRole('option', { name: 'This warehouse (own pool)' }));
    await waitFor(() => expect(poolTrigger()!.textContent).toContain('This warehouse (own pool)'));

    fireEvent.click(screen.getByRole('button', { name: /save|update/i }));

    await waitFor(() => expect(h.updateMutate).toHaveBeenCalledTimes(1));
    expect(h.updateMutate.mock.calls[0][0]).toMatchObject({
      id: CURRENT_ID,
      data: { pool_warehouse_id: CURRENT_ID },
    });
  });
});

describe('WarehouseForm - no prev/next on the form', () => {
  /**
   * RecordNavigation pushes `${basePath}/${id}`, the NEIGHBOUR'S READ VIEW. From an edit
   * form that means a chevron discards whatever the user has typed and lands them on a
   * different record, which reads as data loss. prev/next belongs on the detail page
   * (asserted in `[id]/page.test.tsx`), not here.
   */
  it('renders no prev/next navigation in edit mode', () => {
    renderForm();

    expect(screen.queryByLabelText(/^Previous /i)).toBeNull();
    expect(screen.queryByLabelText(/^Next /i)).toBeNull();
    expect(screen.queryByLabelText(/navigation$/i)).toBeNull();
  });

  it('renders no prev/next navigation in create mode either', () => {
    renderCreateForm();

    expect(screen.queryByLabelText(/^Previous /i)).toBeNull();
    expect(screen.queryByLabelText(/^Next /i)).toBeNull();
  });
});

describe('WarehouseForm - states', () => {
  it('shows no tabs while the record is still loading', () => {
    h.loadingWarehouse = true;
    renderForm();

    expect(screen.queryAllByRole('tab')).toHaveLength(0);
  });
});
