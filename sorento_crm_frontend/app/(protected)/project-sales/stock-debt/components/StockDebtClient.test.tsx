/**
 * S2 - the Stock Debt board (AC-S2-10, AC-S2-11, AC-S2-12).
 *
 * The columns ARE the payload: the axis, the TBA header and every balance come from the
 * envelope, so the tests here are mostly about the screen NOT inventing an axis of its own.
 * The arithmetic lives in `supply_assignment` and is not re-derived.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { StockDebtListResponse, StockDebtMonth, StockDebtRow } from '../types/stockDebt.types';

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
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/project-sales/stock-debt',
  useSearchParams: () => new URLSearchParams(''),
}));

// The real column-preferences hook runs here on purpose: the board passes
// `listingKey={null}`, and the point of the last test below is that the hook then reads
// nothing. The service underneath is mocked with a config a real user had saved under the
// pathname fallback, so a board that persisted would be caught reordering itself.
vi.mock('@/lib/listing-column-preferences/listColumnPreferencesService', () => ({
  getUserListColumnConfig: vi.fn(),
  upsertUserListColumnConfig: vi.fn(),
  resetUserListColumnConfig: vi.fn(),
}));

import { getUserListColumnConfig } from '@/lib/listing-column-preferences/listColumnPreferencesService';

// R19: the Copy fallback is asserted against these spies, not real sonner DOM output -
// no `<Toaster>` is mounted in this render tree.
// The toolbar's <SavedViewsMenu> has its own tests (StockDebtClient.savedViews.test.tsx,
// components/list/SavedViewsMenu.test.tsx); stubbed here so its session and per-user
// default reads stay out of what this file asserts about the board.
vi.mock('@/components/list/SavedViewsMenu', () => ({ SavedViewsMenu: () => null }));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));
import { toast } from '@/lib/toast';

const getStockDebtList = vi.fn();
const getStockDebtCell = vi.fn();
const exportStockDebt = vi.fn();

vi.mock('../services/stockDebtService', () => ({
  getStockDebtList: (...args: unknown[]) => getStockDebtList(...args),
  getStockDebtCell: (...args: unknown[]) => getStockDebtCell(...args),
  exportStockDebt: (...args: unknown[]) => exportStockDebt(...args),
  // The Export popover's own rows/sheets line (AC-33) is exercised in
  // `StockDebtExportPopover`'s own tests, not here - the board just needs the import
  // to resolve so it does not crash while mounting the popover.
  previewStockDebtExport: () => ({ rows: 0, sheets: 0 }),
}));

import { StockDebtClient } from './StockDebtClient';

function row(overrides: Partial<StockDebtRow> = {}): StockDebtRow {
  const base = {
    product_id: 'p1',
    product_code: 'SRTWB242',
    product_name: 'Sorento basin 242',
    months: [
      { key: '2026-08', balance: 55, tone: 'green' },
      { key: '2026-09', balance: -16, tone: 'red' },
      { key: '2026-10', balance: -652, tone: 'amber' },
    ] as StockDebtMonth[],
    tba: -100,
    undated: -12,
    unlocated: -7,
    supplier_id: null,
    supplier_name: null,
    category_code: null,
    pool_on_hand: 0,
    pool_po_qty: 0,
    pool_incoming_qty: 0,
    dealer_outstanding: 0,
    ...overrides,
  };
  // AC-5: sums months + tba + undated + unlocated - kept derived here rather than
  // hard-coded, so an override to any one field cannot silently leave `total` stale.
  const total =
    base.months.reduce((sum, month) => sum + month.balance, 0) +
    base.tba +
    base.undated +
    base.unlocated;
  return { ...base, total: overrides.total ?? total };
}

function envelope(rows: StockDebtRow[] = [row()]): StockDebtListResponse {
  const totalsMonths: Record<string, number> = {};
  ['2026-08', '2026-09', '2026-10'].forEach((key) => {
    totalsMonths[key] = rows.reduce(
      (sum, r) => sum + (r.months.find((m) => m.key === key)?.balance ?? 0),
      0,
    );
  });
  return {
    data: rows,
    pagination: { total: rows.length, page: 1, limit: 25 },
    months: ['2026-08', '2026-09', '2026-10'],
    tba_month: '2030-01',
    groups: ['BB', 'IB'],
    locations: [],
    totals: {
      months: totalsMonths,
      tba: rows.reduce((sum, r) => sum + r.tba, 0),
      undated: rows.reduce((sum, r) => sum + r.undated, 0),
      unlocated: rows.reduce((sum, r) => sum + r.unlocated, 0),
      total: rows.reduce((sum, r) => sum + r.total, 0),
      pool_on_hand: 0,
      pool_po_qty: 0,
      pool_incoming_qty: 0,
      dealer_outstanding: 0,
    },
    suppliers: [],
    sheet_counts: { supplier: 0, category: 0, supplier_category: 0 },
  };
}

function renderBoard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <StockDebtClient />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getStockDebtList.mockResolvedValue(envelope());
  getStockDebtCell.mockResolvedValue({ demand: [], supply: [] });
  vi.mocked(getUserListColumnConfig).mockResolvedValue({
    listing_key: '/project-sales/stock-debt',
    config: { version: 1, columnOrder: ['product', 'tba', 'undated'] },
  });
});

describe('StockDebtClient', () => {
  it('opens on the products in debt, not on the whole catalogue', async () => {
    renderBoard();

    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenCalledWith(
        expect.objectContaining({ onlyDebt: true, query: '' }),
      ),
    );
  });

  it('shows skeleton rows while the board loads, not an empty table', () => {
    getStockDebtList.mockReturnValue(new Promise(() => {}));

    const { container } = renderBoard();

    expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
    expect(screen.queryByText('No product is in debt')).not.toBeInTheDocument();
  });

  it('says nothing is in debt and offers the whole book, which flips the toggle', async () => {
    getStockDebtList.mockResolvedValue(envelope([]));

    renderBoard();

    expect(await screen.findByText('No product is in debt')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show every product' }));

    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenLastCalledWith(
        expect.objectContaining({ onlyDebt: false }),
      ),
    );
  });

  it('states a load failure in the server words, with a Retry that asks again', async () => {
    getStockDebtList.mockRejectedValue(new Error('Stock debt is unavailable'));

    renderBoard();

    expect(
      await screen.findByText('Stock debt could not be loaded'),
    ).toBeInTheDocument();
    expect(screen.getByText('Stock debt is unavailable')).toBeInTheDocument();

    // The hook retries once of its own accord, so the count before the press is not 1.
    const before = getStockDebtList.mock.calls.length;
    getStockDebtList.mockResolvedValue(envelope());
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() =>
      expect(getStockDebtList.mock.calls.length).toBeGreaterThan(before),
    );
    expect(await screen.findByText('SRTWB242')).toBeInTheDocument();
  });

  it('takes its month columns from the payload, and reads the TBA header literally (R18)', async () => {
    // R18 (owner's hand test, closes the addendum on the old design): the TBA column
    // header reads "TBA" always - the policy's own month is display-only, in the
    // header's `title` tooltip, never the visible column text. The old assertion here
    // (`getByText('2030-01')`) is the red half of R18, not a separate defect.
    renderBoard();

    expect(await screen.findByText('Aug 26')).toBeInTheDocument();
    expect(screen.getByText('Sep 26')).toBeInTheDocument();
    expect(screen.getByText('Oct 26')).toBeInTheDocument();
    expect(screen.getByText('TBA')).toBeInTheDocument();
    expect(screen.queryByText('2030-01')).not.toBeInTheDocument();
    // R17: "No date" and "No location" leave the screen entirely.
    expect(screen.queryByText('No date')).not.toBeInTheDocument();
    expect(screen.queryByText('No location')).not.toBeInTheDocument();
    // A month the payload does not carry is not a column.
    expect(screen.queryByText('Nov 26')).not.toBeInTheDocument();
  });

  it('renders every cell as a press, signed, and no longer offers No date / No location cells (R17)', async () => {
    renderBoard();

    const surplus = await screen.findByRole('button', {
      name: 'SRTWB242, Aug 26, balance +55',
    });
    expect(surplus).toBeInTheDocument();
    expect(surplus).toHaveTextContent('+55');
    expect(
      screen.getByRole('button', { name: 'SRTWB242, Sep 26, balance -16' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'SRTWB242, 2030-01, balance -100' }),
    ).toBeInTheDocument();
    // R17: the row still carries `undated`/`unlocated` on the wire (unchanged), but
    // neither is a column on the screen any more - no cell button for either exists.
    expect(
      screen.queryByRole('button', { name: 'SRTWB242, No date, balance -12' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'SRTWB242, No location, balance -7' }),
    ).not.toBeInTheDocument();
  });

  it('tones a month by what the payload says, and leaves TBA and No date untoned', async () => {
    renderBoard();

    const red = await screen.findByRole('button', {
      name: 'SRTWB242, Sep 26, balance -16',
    });
    expect(red.className).toContain('text-destructive');
    const amber = screen.getByRole('button', {
      name: 'SRTWB242, Oct 26, balance -652',
    });
    expect(amber.className).toContain('amber');
    // TBA draws no supply at all (R14), so a colour that means "can this still be bought in
    // time" would answer a question nobody asked of it.
    const tba = screen.getByRole('button', {
      name: 'SRTWB242, 2030-01, balance -100',
    });
    expect(tba.className).toContain('bg-muted');
    expect(tba.className).not.toContain('destructive');
  });

  it('opens the drill on the cell that was pressed', async () => {
    renderBoard();

    fireEvent.click(
      await screen.findByRole('button', { name: 'SRTWB242, Sep 26, balance -16' }),
    );

    // The board's own group/cutoff/book narrowing travels with the drill, so the two
    // foot: '' and the defaults here are the unnarrowed board.
    // `StockDebtCellDialog.test.tsx` pins the narrowed case, and `stockDebtService.
    // real.test.ts` pins what it puts on the wire.
    await waitFor(() =>
      expect(getStockDebtCell).toHaveBeenCalledWith('p1', '2026-09', '', undefined, 'all'),
    );
    expect(await screen.findByTestId('stock-debt-cell-dialog')).toBeInTheDocument();
  });

  it('pins the product column through the grid, not through a class that loses', async () => {
    const { container } = renderBoard();
    await screen.findByText('SRTWB242');

    // `position: sticky` as an INLINE style is the whole point: a `sticky left-0` utility
    // sits in the same Tailwind position group as the `relative` the base cell carries, and
    // the stylesheet order decided which won - it computed to `relative` in the browser.
    const pinned = container.querySelectorAll('[data-pinned="left"]');
    expect(pinned.length).toBeGreaterThan(0);
    pinned.forEach((element) => {
      expect((element as HTMLElement).style.position).toBe('sticky');
      expect((element as HTMLElement).style.left).toBe('0px');
    });
  });

  it('keeps the calendar as the axis when the board arrives after the columns are built (R17/R18)', async () => {
    // The list resolves AFTER mount, so at mount the only columns are Product and TBA.
    // That snapshot is exactly what a persisted order used to be reconciled against: TBA
    // walked up next to Product and stayed there, and every month column the board later
    // built was warned about as non-existent. R17 drops "No date"/"No location" from this
    // list entirely (they are no longer columns at all); R18 reads the TBA header
    // literally, never the raw `tba_month` key.
    renderBoard();
    await screen.findByText('SRTWB242');

    expect(
      screen.getAllByRole('columnheader').map((cell) => cell.textContent?.trim()),
    ).toEqual([
      'Product',
      'BRW Retail On Hand Qty',
      'BRW Retail PO Qty',
      'BRW Retail Incoming Qty',
      'Dealer O/standing Qty',
      'Aug 26',
      'Sep 26',
      'Oct 26',
      'TBA',
      'Total',
    ]);
    // The board is `listingKey={null}`, so there is no config to read in the first place.
    expect(getUserListColumnConfig).not.toHaveBeenCalled();
  });

  it('never prints the product id, only the code and the name', async () => {
    const { container } = renderBoard();

    await screen.findByText('SRTWB242');
    expect(screen.getByText('Sorento basin 242')).toBeInTheDocument();
    expect(container.textContent).not.toContain('p1');
  });

  it('opens the drill on a real pointerdown -> pointerup -> click at one cell, and leaves nothing selected (AC-25, browser-pass finding)', async () => {
    // `it('opens the drill on the cell that was pressed', ...)` above only fires a bare
    // `click` - it never runs `onCellPointerDown` at all, so it cannot catch a defect that
    // lives in the INTERACTION between the pointerdown's optimistic single-cell select and
    // the click's own decision. A real browser click is always pointerdown -> pointerup ->
    // click at the same coordinates, which is what this dispatches.
    renderBoard();
    const cellButton = await screen.findByRole('button', {
      name: 'SRTWB242, Sep 26, balance -16',
    });

    fireEvent.pointerDown(cellButton, { button: 0, clientX: 10, clientY: 10 });
    fireEvent.pointerUp(cellButton, { button: 0, clientX: 10, clientY: 10 });
    fireEvent.click(cellButton, { button: 0, clientX: 10, clientY: 10 });

    await waitFor(() =>
      expect(getStockDebtCell).toHaveBeenCalledWith('p1', '2026-09', '', undefined, 'all'),
    );
    expect(await screen.findByTestId('stock-debt-cell-dialog')).toBeInTheDocument();
    // A plain click's own branch calls `clear()` - nothing should carry a selection ring
    // once the drill has opened.
    expect(cellButton.className).not.toMatch(/ring-1 ring-primary/);
  });

  it('grows the rectangle on a SECOND Shift+ArrowRight, focus moved to the new cell after the first (AC-32, browser-pass finding)', async () => {
    renderBoard();
    const first = await screen.findByRole('button', {
      name: 'SRTWB242, Aug 26, balance +55',
    });
    const second = screen.getByRole('button', {
      name: 'SRTWB242, Sep 26, balance -16',
    });
    const third = screen.getByRole('button', {
      name: 'SRTWB242, Oct 26, balance -652',
    });

    first.focus();
    expect(document.activeElement).toBe(first);

    fireEvent.keyDown(first, { key: 'ArrowRight', shiftKey: true });
    // The hook only returns the next coordinate; the CALLER (`StockDebtClient`) is the one
    // that must move DOM focus there (`cellRefs.current.get(...)?.focus()`, line ~308) so a
    // second consecutive key press has something focused to grow from.
    expect(document.activeElement).toBe(second);

    fireEvent.keyDown(second, { key: 'ArrowRight', shiftKey: true });
    expect(document.activeElement).toBe(third);

    expect(first.className).toMatch(/ring-1 ring-primary/);
    expect(second.className).toMatch(/ring-1 ring-primary/);
    expect(third.className).toMatch(/ring-1 ring-primary/);
  });

  /**
   * Radix's `DropdownMenu` trigger (what the "Filters" button is) opens on POINTERDOWN,
   * never a plain `click` - the same gotcha `PurchaseOrdersList.test.tsx`'s own
   * `openFilters()` documents. `fireEvent.click` alone leaves the panel closed and every
   * query inside it absent, which would make an ABSENCE assertion (R16) pass for the
   * wrong reason - vacuously, because nothing in the panel rendered at all.
   */
  async function openFilters() {
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Filters' }), { button: 0 });
    await screen.findByText('Book');
  }

  it('offers ONE Sales order delivery date range control, not two From/To inputs (R14c)', async () => {
    // R14c (owner, reversing R14b on sight): "use the same date range component but I
    // can type; I don't want two different date fields; call it sales order delivery
    // date." RED today: the panel still shows R14b's two `DatePicker`s labelled "From"
    // and "To" under a "Due date" heading - neither the new label nor its removal of
    // the two-field layout has landed.
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    expect(screen.getByText('Sales order delivery date')).toBeInTheDocument();
    expect(screen.queryByText('Due date')).not.toBeInTheDocument();
    expect(screen.queryByText('Cutoff date')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('From')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('To')).not.toBeInTheDocument();
  });

  it('types a delivery date range into the shared range control, shows the Delivery chip, and carries it into the list and the cell drill (R14c/AC-11b)', async () => {
    // RED today: there is no single control labelled "Sales order delivery date" at
    // all - `getByLabelText` throws before typing anything. Once R14c lands, this is
    // the shared `components/ui/date-range-picker.tsx` (own red tests in
    // `date-range-picker.test.tsx`) reused here under the new label, never a
    // Stock-Debt-local field.
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    fireEvent.click(screen.getByRole('radio', { name: 'Retail' }));
    const rangeInput = screen.getByLabelText('Sales order delivery date');
    fireEvent.change(rangeInput, { target: { value: '01/11/2026 - 30/11/2026' } });
    fireEvent.keyDown(rangeInput, { key: 'Enter' });

    await waitFor(() =>
      expect(getStockDebtList).toHaveBeenCalledWith(
        expect.objectContaining({ dateFrom: '2026-11-01', dateTo: '2026-11-30' }),
      ),
    );
    expect(screen.getByText('Delivery: 1 Nov 26 to 30 Nov 26')).toBeInTheDocument();

    // The Filters dropdown (a Radix DropdownMenu) marks the rest of the page
    // `aria-hidden` while it is open, which is exactly right for a real reader but
    // means the grid's own cells are invisible to `getByRole` until the panel closes -
    // Escape is how a user would dismiss it before working the table anyway.
    fireEvent.keyDown(document.body, { key: 'Escape' });

    fireEvent.click(
      await screen.findByRole('button', { name: 'SRTWB242, Sep 26, balance -16' }),
    );

    await waitFor(() =>
      expect(getStockDebtCell).toHaveBeenCalledWith(
        'p1', '2026-09', '2026-11-01', '2026-11-30', 'retail',
      ),
    );
  });

  it('has no Ownership group filter left on the screen (R16)', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    expect(screen.queryByText('Ownership group')).not.toBeInTheDocument();
  });

  it('offers a MULTI-select for Supplier, not the old single select (R15)', async () => {
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    // `SearchableMultiSelect` renders its own `data-slot`, distinct from the single
    // `SearchableSelect` the OLD single-supplier control used - unambiguous either way.
    expect(
      document.querySelector('[data-slot="searchable-multi-select-trigger"]'),
    ).not.toBeNull();
  });

  it('two suppliers picked together show one chip "Suppliers: 2" (R15)', async () => {
    getStockDebtList.mockResolvedValue({
      ...envelope(),
      suppliers: [
        { id: 'sup-alpha', name: 'Alpha supplier' },
        { id: 'sup-beta', name: 'Beta supplier' },
      ],
    });
    renderBoard();
    await screen.findByText('SRTWB242');
    await openFilters();

    const trigger = document.querySelector(
      '[data-slot="searchable-multi-select-trigger"]',
    ) as HTMLElement | null;
    expect(trigger).not.toBeNull();
    fireEvent.click(trigger as HTMLElement);
    fireEvent.click(screen.getByRole('option', { name: 'Alpha supplier' }));
    fireEvent.click(screen.getByRole('option', { name: 'Beta supplier' }));

    expect(screen.getByText('Suppliers: 2')).toBeInTheDocument();
  });

  describe('Copy without navigator.clipboard (R19 - jsdom has no Clipboard API by default, matching the owner\'s http/LAN report)', () => {
    // Vitest isolates mocks per FILE, not per test - a `document.execCommand` stub the
    // first test installs would otherwise still be there for the second, which needs
    // BOTH clipboard and execCommand absent to prove the error path.
    afterEach(() => {
      delete (document as unknown as { execCommand?: unknown }).execCommand;
    });

    async function selectTwoCells() {
      // Every selection-state change remounts the grid's cell buttons rather than
      // patching them in place (measured directly: a button queried again after a
      // click is a DIFFERENT node, `===` fails) - so each cell is RE-QUERIED fresh
      // right before its own click, the way a real pointer hit-tests at the current
      // DOM on every event rather than reusing a stale JS reference.
      await screen.findByRole('button', { name: 'SRTWB242, Aug 26, balance +55' });
      fireEvent.click(
        screen.getByRole('button', { name: 'SRTWB242, Aug 26, balance +55' }),
        { ctrlKey: true },
      );
      fireEvent.click(
        screen.getByRole('button', { name: 'SRTWB242, Sep 26, balance -16' }),
        { ctrlKey: true },
      );
      await screen.findByRole('button', { name: 'Copy' });
    }

    it('falls back to document.execCommand("copy") and toasts success when clipboard is missing', async () => {
      renderBoard();
      await selectTwoCells();

      const execCommand = vi.fn(() => true);
      document.execCommand = execCommand as unknown as typeof document.execCommand;

      fireEvent.click(screen.getByRole('button', { name: 'Copy' }));

      await waitFor(() => expect(execCommand).toHaveBeenCalledWith('copy'));
      expect(toast.success).toHaveBeenCalled();
    });

    it('toasts an error when neither clipboard nor execCommand exists', async () => {
      renderBoard();
      await selectTwoCells();

      // Neither `navigator.clipboard` nor `document.execCommand` exists in this jsdom
      // environment by default - the exact "owner reaches the stack over http on a LAN
      // hostname" non-secure-context case R19 names.
      expect(navigator.clipboard).toBeUndefined();

      fireEvent.click(screen.getByRole('button', { name: 'Copy' }));

      await waitFor(() => expect(toast.error).toHaveBeenCalled());
    });
  });
});
