/**
 * S2 - the cell drill (AC-S2-7, AC-S2-11, R28/R30/R31/R37).
 *
 * Two TABS behind one cell, and four sentences that are easy to get subtly wrong: Plan
 * hands the ORDER to the board, an overdue document says it counts as nothing, a PO's
 * bought-for date is stated as what it is and never as an arrival, and the two footers
 * foot with the cell that opened them (`Free` less `Uncovered` is the balance in the
 * title, R37).
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  StockDebtCell,
  StockDebtDemandLine,
  StockDebtSupplyEvent,
} from '../types/stockDebt.types';

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

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

const getStockDebtCell = vi.fn();

vi.mock('../services/stockDebtService', () => ({
  getStockDebtList: vi.fn(),
  getStockDebtCell: (...args: unknown[]) => getStockDebtCell(...args),
}));

// R30: the SPO cell is the SAME `OrderInquiryDocumentLink` the OI screens use, real, not
// stood in for - it opens its own dialog off `getOrderInquirySpoDetail`
// (`OrderInquiryDocumentDialog.test.tsx`'s own mock target), so this file mocks the
// SERVICE call the dialog makes, not the component itself.
const getOrderInquirySpoDetail = vi.fn();
const getOrderInquiryPoDetail = vi.fn();

vi.mock('../../_shared/services/orderInquiryService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../_shared/services/orderInquiryService')
  >();
  return {
    ...actual,
    getOrderInquirySpoDetail: (...args: unknown[]) => getOrderInquirySpoDetail(...args),
    getOrderInquiryPoDetail: (...args: unknown[]) => getOrderInquiryPoDetail(...args),
  };
});

import { StockDebtCellDialog } from './StockDebtCellDialog';

// R22/R25: `qty_ordered`/`qty_delivered` (on each demand line) and `demand_total_qty`/
// `supply_total_qty` (on the envelope) are not on the types yet - the coder's
// schema/type change this round. Cast (an `as` assertion, not a `:` annotation) so the
// fixture states the WIRE shape the coder is adding without an excess-property error
// blocking the whole file from compiling in the meantime.
//
// The SHARED fixture carries `on_hand`/`spo` kinds only; the PO kind (R42, 28 Sep 2026,
// superseding R23's "got PO doesn't mean got supply") has its own tests below.
// `free_qty` on BOTH rows stays 0:
// the R37 footing test below (`Free 0`) depends on the shared fixture's total free
// quantity being zero, so a Free-sum fixture with real free stock lives in its own
// dedicated test instead of here.
const CELL = {
  demand: [
    {
      so_number: 'SO390918',
      agent_code: 'CONTACT O',
      warehouse_code: 'BRW-BB',
      required_date: '2026-10-15',
      // Ordered/Delivered/Outstanding/Assigned/Short are FIVE distinct numbers on
      // purpose (20/8/12/5/0) - `open_qty` and `assigned_qty` both reading 12 made
      // `within(row).getByText('12')` match two cells at once (the R22 fixture bug the
      // "prints Ordered, Delivered and Outstanding" test caught).
      open_qty: 12,
      qty_ordered: 20,
      qty_delivered: 8,
      assigned_qty: 5,
      assigned_source: 'On hand BRW-BB',
      short_qty: 0,
      status: 'covered',
    } as StockDebtDemandLine,
    {
      so_number: 'SO375875',
      agent_code: 'JAY',
      warehouse_code: 'MWH-BB',
      required_date: '2026-10-26',
      open_qty: 32,
      qty_ordered: 32,
      qty_delivered: 0,
      assigned_qty: 16,
      assigned_source: 'On hand BRW-BB',
      short_qty: 16,
      status: 'short',
    } as StockDebtDemandLine,
  ],
  supply: [
    {
      kind: 'spo',
      ref: 'SPO 2026/09-0088',
      warehouse_code: 'MWH-BB',
      date: '2026-10-12',
      bought_for: null,
      qty: 40,
      free_qty: 0,
      overdue: false,
      assigned_to: [{ so_number: 'SO407114', qty: 40 }],
    },
    {
      kind: 'spo',
      ref: 'SPO 2026/07-0021',
      warehouse_code: 'BRW-BB',
      date: '2026-08-16',
      bought_for: null,
      qty: 12,
      free_qty: 0,
      overdue: true,
      assigned_to: [],
    },
  ],
  // R25: the envelope's own quantity totals - sum of `open_qty` (44 = 12 + 32) and sum
  // of `qty` (52 = 40 + 12) over these same rows, echoed rather than recomputed by the
  // FE (`app/services/scm/stock_debt_service.py` owns the arithmetic).
  demand_total_qty: 44,
  supply_total_qty: 52,
} as StockDebtCell;

function renderDialog(cell: StockDebtCell = CELL) {
  getStockDebtCell.mockResolvedValue(cell);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <StockDebtCellDialog
        productId="p1"
        productCode="SRTWB242"
        productName="Sorento basin 242"
        month="2026-10"
        monthLabel="Oct 26"
        balance={-16}
        // R16 retired the ownership group entirely - the board narrows the drill with a
        // due date range and a book now, never a group; `dateFrom`/`dateTo`/`book` are
        // real props on the dialog (the coder's rename landed).
        dateFrom="2026-11-01"
        dateTo="2026-11-30"
        book="retail"
        onClose={() => {}}
      />
    </QueryClientProvider>,
  );
}

/** Radix's TabsTrigger switches on mouse down; a bare `click` leaves the old panel up. */
function switchTab(name: string) {
  const tab = screen.getByRole('tab', { name });
  fireEvent.mouseDown(tab);
  fireEvent.click(tab);
}

beforeEach(() => vi.clearAllMocks());

describe('StockDebtCellDialog', () => {
  it('asks for the cell it was opened on', async () => {
    renderDialog();
    // AC-11/AC-11b/R14: the drill carries the board's due date range and book - never an
    // ownership group, which R16 retired.
    await waitFor(() =>
      expect(getStockDebtCell).toHaveBeenCalledWith(
        'p1', '2026-10', '2026-11-01', '2026-11-30', 'retail',
      ),
    );
  });

  it('lists the demand with its bin, its due date and its status', async () => {
    renderDialog();

    expect(await screen.findByText('SO390918')).toBeInTheDocument();
    expect(screen.getByText('CONTACT O')).toBeInTheDocument();
    // The Bin column (AC-S2-7): which pile the line sits in decides everything else. Both
    // codes also appear in the Supply table, so the assertion is scoped to Demand's row.
    const shortRow = screen.getByText('SO375875').closest('tr') as HTMLElement;
    expect(within(shortRow).getByText('MWH-BB')).toBeInTheDocument();
    const coveredRow = screen.getByText('SO390918').closest('tr') as HTMLElement;
    expect(within(coveredRow).getByText('BRW-BB')).toBeInTheDocument();
    expect(screen.getByText('covered')).toBeInTheDocument();
    // A short line says HOW short, because the quantity is the thing to act on.
    expect(screen.getByText('short 16')).toBeInTheDocument();
  });

  it('lists the R31 Demand columns, in order, with no Plan button and no inline "via" (R21/R22/R31a)', async () => {
    // R21 retires the Plan button column from the Demand grid entirely.
    // R31a (owner, 25 Sep) supersedes R30's three columns (From/SPO/OI) with ONE
    // "Covered by" - RED today because the grid still carries all three separately.
    renderDialog();
    await screen.findByText('SO390918');

    const headers = screen.getAllByRole('columnheader').map((cell) => cell.textContent);
    expect(headers).toEqual([
      'Sales order', 'Agent', 'Bin', 'Due', 'Ordered', 'Delivered', 'Outstanding',
      'Assigned', 'Covered by', 'Status',
    ]);
    expect(screen.queryByRole('link', { name: 'Plan' })).not.toBeInTheDocument();
    expect(screen.queryByText(/via/)).not.toBeInTheDocument();
  });

  it('sorts the Demand grid on every column, toggling asc/desc (R20)', async () => {
    // RED today: the grid is not sortable at all - `PanelDataGrid` is not given
    // `sortable`, so clicking "Due" does nothing and the row order never changes.
    renderDialog({
      demand: [
        {
          so_number: 'SO-LATE', agent_code: 'A', warehouse_code: 'W1',
          required_date: '2026-10-26', open_qty: 1, qty_ordered: 1, qty_delivered: 0,
          assigned_qty: 1, assigned_source: null, short_qty: 0, status: 'covered',
        } as StockDebtDemandLine,
        {
          so_number: 'SO-EARLY', agent_code: 'B', warehouse_code: 'W2',
          required_date: '2026-10-05', open_qty: 1, qty_ordered: 1, qty_delivered: 0,
          assigned_qty: 1, assigned_source: null, short_qty: 0, status: 'covered',
        } as StockDebtDemandLine,
      ],
      supply: [],
    });
    await screen.findByText('SO-LATE');

    function firstRowSo() {
      return within(screen.getAllByRole('row')[1]).getAllByRole('cell')[0].textContent;
    }

    // Unsorted (service order): the later-due row is fed first and stays first.
    expect(firstRowSo()).toBe('SO-LATE');

    fireEvent.click(screen.getByText('Due'));
    await waitFor(() => expect(firstRowSo()).toBe('SO-EARLY'));

    fireEvent.click(screen.getByText('Due'));
    await waitFor(() => expect(firstRowSo()).toBe('SO-LATE'));
  });

  it('searches the Demand grid by sales order, agent or bin, and the tab follows the filter BY QUANTITY (R20/R25)', async () => {
    // RED today: there is no search box on the dialog at all - `PanelDataGrid` is not
    // given `searchOf`, so `getByRole('searchbox')` throws. R25 (folded in here rather
    // than kept a record-count check that would go stale the moment R25 lands beside
    // R20): the filtered half of the label is the SUM of Outstanding over the rows still
    // matching, not how many rows there are - 7 and 3 are deliberately unequal so a
    // record-count reading ("1 of 2") and a quantity reading ("7 of 10") cannot be
    // confused for one another.
    renderDialog({
      demand: [
        {
          so_number: 'SO-A', agent_code: 'CONTACT N', warehouse_code: 'W1',
          required_date: '2026-10-05', open_qty: 7, qty_ordered: 7, qty_delivered: 0,
          assigned_qty: 7, assigned_source: null, short_qty: 0, status: 'covered',
        } as StockDebtDemandLine,
        {
          so_number: 'SO-B', agent_code: 'MARIA', warehouse_code: 'W2',
          required_date: '2026-10-06', open_qty: 3, qty_ordered: 3, qty_delivered: 0,
          assigned_qty: 3, assigned_source: null, short_qty: 0, status: 'covered',
        } as StockDebtDemandLine,
      ],
      supply: [],
      demand_total_qty: 10,
      supply_total_qty: 0,
    } as StockDebtCell);
    await screen.findByText('SO-A');
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Demand (10)', 'Supply (0)',
    ]);

    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'CONTACT N' } });

    await waitFor(() => expect(screen.queryByText('SO-B')).not.toBeInTheDocument());
    expect(screen.getByText('SO-A')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Demand (7 of 10)' })).toBeInTheDocument();
  });

  it('prints Ordered, Delivered and Outstanding from the fixture (R22)', async () => {
    renderDialog();
    const row = (await screen.findByText('SO390918')).closest('tr') as HTMLElement;

    expect(within(row).getByText('20')).toBeInTheDocument();
    expect(within(row).getByText('8')).toBeInTheDocument();
    expect(within(row).getByText('12')).toBeInTheDocument();
  });

  it('shows a Total footer row on both grids, over ALL rows of the tab (R24)', async () => {
    // RED today: neither grid has a footer row at all. Demand totals Ordered/Delivered/
    // Outstanding/Assigned (52/8/44/21, from `CELL.demand`'s own two rows: 5 + 16); Supply
    // totals Qty and Free - a dedicated fixture here, not the shared `CELL` (whose two
    // supply rows both carry `free_qty: 0` on purpose, for the R37 footing test above).
    renderDialog({
      demand: CELL.demand,
      supply: [
        {
          kind: 'spo', ref: 'SPO A', warehouse_code: 'W1', date: '2026-10-01',
          bought_for: null, qty: 40, free_qty: 10, overdue: false, assigned_to: [],
        },
        {
          kind: 'spo', ref: 'SPO B', warehouse_code: 'W2', date: '2026-10-02',
          bought_for: null, qty: 20, free_qty: 5, overdue: false, assigned_to: [],
        },
      ],
      demand_total_qty: 44,
      supply_total_qty: 60,
    } as StockDebtCell);
    await screen.findByText('SO390918');

    const demandTotalRow = screen.getByText('Total').closest('tr') as HTMLElement;
    expect(within(demandTotalRow).getByText('52')).toBeInTheDocument();
    expect(within(demandTotalRow).getByText('8')).toBeInTheDocument();
    expect(within(demandTotalRow).getByText('44')).toBeInTheDocument();
    // Assigned: 5 (SO390918, changed from 12 to keep this row's five numbers distinct) +
    // 16 (SO375875) = 21.
    expect(within(demandTotalRow).getByText('21')).toBeInTheDocument();
    // R24 addendum: Short (sum of `short_qty`, 0 + 16) sits in the Status column of the
    // Demand Total row - there is no separate "Uncovered" line under the grid any more.
    expect(within(demandTotalRow).getByText('Short 16')).toBeInTheDocument();

    switchTab('Supply (60)');
    const supplyTotalRow = (await screen.findByText('Total')).closest('tr') as HTMLElement;
    expect(within(supplyTotalRow).getByText('60')).toBeInTheDocument();
    // R24 addendum: Free (sum of `free_qty`, 10 + 5) sits in the Supply Total row itself.
    expect(within(supplyTotalRow).getByText('Free 15')).toBeInTheDocument();
  });

  it('lists the supply with its document, arrival and who holds it', async () => {
    renderDialog();
    await screen.findByText('SO390918');
    switchTab('Supply (52)');

    expect(await screen.findByText('SPO 2026/09-0088')).toBeInTheDocument();
    expect(screen.getByText('SO407114 (40)')).toBeInTheDocument();
    // Nobody has it: "Free" rather than a blank cell, which reads as a missing figure.
    expect(screen.getByText('Free')).toBeInTheDocument();
  });

  it('says an overdue document counts as nothing, and only a PO says what it was bought for', async () => {
    // A `po` row whose `bought_for` differs from its date (the board's reading, R29):
    // the dialog states it as what it is. Under R42 the view parks a PO on its delivery
    // date and sends no `bought_for`, which the R42 Supply test below pins.
    renderDialog({
      demand: CELL.demand,
      supply: [
        {
          kind: 'spo',
          ref: 'SPO 2026/09-0088',
          warehouse_code: 'MWH-BB',
          date: '2026-10-12',
          bought_for: null,
          qty: 40,
          free_qty: 0,
          overdue: false,
          assigned_to: [{ so_number: 'SO407114', qty: 40 }],
        },
        {
          kind: 'po',
          ref: 'PO 202605-S0072 line 5',
          warehouse_code: 'BRW-BB',
          date: '2026-08-16',
          bought_for: '2026-10-15',
          qty: 12,
          free_qty: 0,
          overdue: true,
          assigned_to: [],
        },
      ],
      demand_total_qty: 44,
      supply_total_qty: 52,
    } as StockDebtCell);
    await screen.findByText('SO390918');
    switchTab('Supply (52)');

    expect(await screen.findByText('overdue, not counted')).toBeInTheDocument();
    // R30: the PO's `expected_date` is the SO date it was typed against, so it is worded as
    // that and never as an arrival - and an SPO has no such date to state.
    expect(screen.getByText('bought for 15/10/2026')).toBeInTheDocument();
    expect(screen.queryByText(/bought for 12\/10\/2026/)).not.toBeInTheDocument();
  });

  it('renders each tab own empty state rather than a blank table', async () => {
    renderDialog({
      demand: [], supply: [], demand_total_qty: 0, supply_total_qty: 0,
    } as StockDebtCell);

    expect(await screen.findByText('Nothing is due here')).toBeInTheDocument();
    switchTab('Supply (0)');
    expect(await screen.findByText('Nothing arrives here')).toBeInTheDocument();
  });

  it('names the product, the column and the balance the reader pressed', async () => {
    renderDialog();

    const dialog = await screen.findByTestId('stock-debt-cell-dialog');
    // The SCM family's shell: `<Kind> · <code>` with the qualifier beside it, and the
    // product name on the muted line under it.
    expect(within(dialog).getByText('Product · SRTWB242')).toBeInTheDocument();
    // The month and the signed balance - R16 retired the ownership-group qualifier that
    // used to sit beside them, and no dateFrom/dateTo/book slot has replaced it on this
    // dialog's own subtitle yet, so the context line is just the two.
    expect(within(dialog).getByText('Oct 26 · -16')).toBeInTheDocument();
    expect(within(dialog).getByText('Sorento basin 242')).toBeInTheDocument();
  });

  it('never repeats the code as the muted description line when the name equals the code (R27)', async () => {
    // RED today: the dialog renders `productName ?? productCode` unconditionally as the
    // description - when the two are the same string, the code prints TWICE (once in the
    // title, once again on the muted line under it). R27: the second line renders ONLY
    // when `product_name` is set AND differs from `product_code` (the BE already nulls
    // an equal name on LIST rows - AC-9 - the dialog must not reintroduce the repeat).
    getStockDebtCell.mockResolvedValue({
      demand: [], supply: [], demand_total_qty: 0, supply_total_qty: 0,
    } as StockDebtCell);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <StockDebtCellDialog
          productId="p1"
          productCode="SRTWB242"
          productName="SRTWB242"
          month="2026-10"
          monthLabel="Oct 26"
          balance={-16}
          dateFrom="2026-11-01"
          dateTo="2026-11-30"
          book="retail"
          onClose={() => {}}
        />
      </QueryClientProvider>,
    );

    const dialog = await screen.findByTestId('stock-debt-cell-dialog');
    expect(within(dialog).getByText('Product · SRTWB242')).toBeInTheDocument();
    const description = dialog.querySelector('[data-slot="dialog-description"]');
    expect(description?.textContent?.trim() ?? '').toBe('');
  });

  it('is two tabs, Demand first, each saying the total QUANTITY the tab holds, not a record count (R25)', async () => {
    // RED today: the tab label is `demand.length`/`supply.length` (a record count) - "2"
    // either way regardless of the envelope's own `demand_total_qty`/`supply_total_qty`
    // fields, which do not exist on the wire yet at all.
    renderDialog();
    await screen.findByText('SO390918');

    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual(['Demand (44)', 'Supply (52)']);
    // Demand is what the reader came for: which orders go without.
    expect(tabs[0]).toHaveAttribute('data-state', 'active');
  });

  it('formats the tab quantity with thousands separators (R25)', async () => {
    renderDialog({
      demand: CELL.demand,
      supply: CELL.supply,
      demand_total_qty: 5619,
      supply_total_qty: 1000,
    } as StockDebtCell);
    await screen.findByText('SO390918');

    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Demand (5,619)', 'Supply (1,000)',
    ]);
  });

  it('foots with the cell that opened it: Free less Short is the balance, both in the Total rows now (R37/R24 addendum)', async () => {
    // R24 addendum (owner, 24 Sep): the standalone "Uncovered N" / "Free N" footer LINES
    // under each grid retire - their numbers move INTO the Total row instead (Status
    // column on Demand, sum of `short_qty`; the Supply Total row's own Free, sum of
    // `free_qty`). RED today: `getByText('Uncovered 16')` still finds the old standalone
    // line, and there is no Total row for either grid to carry "Short 16" / "Free 0"
    // instead.
    renderDialog();
    await screen.findByText('SO390918');

    // The short line went without 16 on its own date; nothing in the month is free, so the
    // cell reads -16 - which is the balance in the title.
    expect(screen.queryByText('Uncovered 16')).not.toBeInTheDocument();
    const demandTotalRow = screen.getByText('Total').closest('tr') as HTMLElement;
    expect(within(demandTotalRow).getByText('Short 16')).toBeInTheDocument();

    switchTab('Supply (52)');
    // Not a "the old line is gone" check here (unlike Demand's `Uncovered` above): the
    // Supply Total row legitimately reads "Free 0" too (R24 addendum put Free there),
    // and the tab content mounts synchronously with the tab switch, so a negative
    // `queryByText('Free 0')` before the Total row assertion would contradict the very
    // thing this test proves.
    const supplyTotalRow = (await screen.findByText('Total')).closest('tr') as HTMLElement;
    expect(within(supplyTotalRow).getByText('Free 0')).toBeInTheDocument();
  });

  it('states the short quantity a LATE line still books, although it ends fully assigned (R37)', async () => {
    // Gap case: a `late` line is one later supply cleared - `assigned_qty` equals
    // `open_qty` by the end of the walk - but it still went without ON ITS OWN DATE, and
    // `short_qty` is the server's own figure for that (re-deriving `open - assigned` gives
    // 0 for exactly this row, which is the defect R37's fixture correction exists to catch:
    // "the drill for those columns was empty" while the cell it opened from was in debt).
    renderDialog({
      demand: [
        {
          so_number: 'SO398214',
          agent_code: 'CYNDI',
          warehouse_code: 'BRW-BB',
          required_date: '2026-10-20',
          open_qty: 20,
          assigned_qty: 20,
          assigned_source: 'SPO 2026/09-0088',
          short_qty: 20,
          status: 'late',
        },
      ],
      supply: [],
    });

    expect(await screen.findByText('SO398214')).toBeInTheDocument();
    const row = screen.getByText('SO398214').closest('tr') as HTMLElement;
    // Fully assigned by the time the whole walk is over: Open and Assigned both read 20.
    expect(within(row).getAllByText('20')).toHaveLength(2);
    // ...but the status cell still states what it went without on its own date, not just
    // the word "late" with the figure that made it so left unsaid.
    expect(within(row).getByText('short 20')).toBeInTheDocument();
  });

  it('shows Qty, Received and Outstanding per SPO row, blank for an on-hand row, and all four in the Supply Total (R26)', async () => {
    // RED today: the Supply columns are still Kind/Document/Bin/Arrival/Qty/Assigned to/
    // Note - no Received or Outstanding column exists, and `qty` on a supply row is
    // still the single netted figure (R26 splits it into three: Qty is the SPO line's
    // raw ordered quantity, Received/Outstanding are new fields).
    renderDialog({
      demand: [],
      supply: [
        {
          kind: 'on_hand', ref: null, warehouse_code: 'BRW-BB', date: '2026-10-01',
          bought_for: null, qty: 40, free_qty: 40, overdue: false, assigned_to: [],
        },
        {
          kind: 'spo', ref: 'SPO 2026/09-0088', warehouse_code: 'MWH-BB',
          date: '2026-10-12', bought_for: null, qty: 100, received_qty: 30,
          outstanding_qty: 70, free_qty: 20, overdue: false,
          assigned_to: [{ so_number: 'SO390918', qty: 50 }],
        } as StockDebtSupplyEvent,
      ],
      demand_total_qty: 0,
      supply_total_qty: 110,
    } as StockDebtCell);
    await screen.findByText('Nothing is due here');
    switchTab('Supply (110)');

    const headers = screen.getAllByRole('columnheader').map((cell) => cell.textContent);
    expect(headers).toEqual([
      'Kind', 'Document', 'Bin', 'Arrival', 'Qty', 'Received', 'Outstanding',
      'Assigned to', 'Note',
    ]);

    const onHandRow = screen.getByText('On hand').closest('tr') as HTMLElement;
    expect(within(onHandRow).getByText('40')).toBeInTheDocument();

    const spoRow = (await screen.findByText('SPO 2026/09-0088')).closest('tr') as HTMLElement;
    expect(within(spoRow).getByText('100')).toBeInTheDocument();
    expect(within(spoRow).getByText('30')).toBeInTheDocument();
    expect(within(spoRow).getByText('70')).toBeInTheDocument();

    const totalRow = screen.getByText('Total').closest('tr') as HTMLElement;
    expect(within(totalRow).getByText('140')).toBeInTheDocument(); // Qty: 40 + 100
    expect(within(totalRow).getByText('30')).toBeInTheDocument(); // Received: 0 + 30
    expect(within(totalRow).getByText('70')).toBeInTheDocument(); // Outstanding: 0 + 70
    expect(within(totalRow).getByText('Free 60')).toBeInTheDocument(); // Free: 40 + 20
  });

  it('shows on-hand plain text, an SPO document number only, both together, or a dash in Covered by, and keeps the Sales order page link (R31a)', async () => {
    // R31a (owner, 25 Sep) supersedes R30's three columns with ONE "Covered by": on-hand
    // is plain text with NO quantity suffix, an SPO source is the OI-style trigger with
    // NO "line N (qty)" beside it, both print together (on-hand text then the link) when
    // a line drew from both, and a dash when nothing did. RED today: there is no
    // "Covered by" column at all - the grid still carries From/SPO/OI separately, each
    // with its own quantity/line-number text this ruling drops.
    renderDialog({
      demand: [
        {
          so_number: 'SO-ONHAND',
          agent_code: 'CONTACT O',
          warehouse_code: 'BRW-BB',
          required_date: '2026-10-15',
          open_qty: 14,
          qty_ordered: 14,
          qty_delivered: 0,
          assigned_qty: 14,
          assigned_source: null,
          status: 'covered',
          short_qty: 0,
          sales_order_id: 'so-id-1',
          assigned_from: [
            {
              kind: 'on_hand', ref: 'On hand BRW-BB',
              spo_number: null, spo_line_number: null, qty: 14,
            },
          ],
        } as StockDebtDemandLine,
        {
          so_number: 'SO-SPO',
          agent_code: 'JAY',
          warehouse_code: 'BRW-BB',
          required_date: '2026-10-16',
          open_qty: 100,
          qty_ordered: 100,
          qty_delivered: 0,
          assigned_qty: 100,
          assigned_source: null,
          status: 'covered',
          short_qty: 0,
          sales_order_id: 'so-id-2',
          assigned_from: [
            {
              kind: 'spo', ref: 'SPO-2026/06-0131 line 4',
              spo_number: 'SPO-2026/06-0131', spo_line_number: 4, qty: 100,
              oi_number: null, oi_id: null,
            },
          ],
        } as StockDebtDemandLine,
        {
          so_number: 'SO-BOTH',
          agent_code: 'MARIA',
          warehouse_code: 'BRW-BB',
          required_date: '2026-10-17',
          open_qty: 50,
          qty_ordered: 50,
          qty_delivered: 0,
          assigned_qty: 50,
          assigned_source: null,
          status: 'covered',
          short_qty: 0,
          sales_order_id: 'so-id-3',
          assigned_from: [
            {
              kind: 'on_hand', ref: 'On hand MWH-BB',
              spo_number: null, spo_line_number: null, qty: 20,
            },
            {
              kind: 'spo', ref: 'SPO-2026/07-0021 line 1',
              spo_number: 'SPO-2026/07-0021', spo_line_number: 1, qty: 30,
              oi_number: null, oi_id: null,
            },
          ],
        } as StockDebtDemandLine,
        {
          so_number: 'SO-NONE',
          agent_code: 'KIM',
          warehouse_code: 'BRW-BB',
          required_date: '2026-10-18',
          open_qty: 10,
          qty_ordered: 10,
          qty_delivered: 0,
          assigned_qty: 0,
          assigned_source: null,
          status: 'short',
          short_qty: 10,
          sales_order_id: 'so-id-4',
          assigned_from: [],
        } as StockDebtDemandLine,
      ],
      supply: [],
      demand_total_qty: 174,
      supply_total_qty: 0,
    } as StockDebtCell);

    const soLink = await screen.findByRole('link', { name: 'SO-ONHAND' });
    expect(soLink).toHaveAttribute('href', '/scm/sales-orders/so-id-1');
    expect(soLink).toHaveAttribute('target', '_blank');

    // On hand only: plain text, NO quantity.
    const onHandRow = screen.getByText('SO-ONHAND').closest('tr') as HTMLElement;
    expect(within(onHandRow).getByText('On hand BRW-BB')).toBeInTheDocument();
    expect(within(onHandRow).queryByText(/\(14\)/)).not.toBeInTheDocument();

    // SPO only: the OI-style trigger, NO "line N" text.
    const spoRow = screen.getByText('SO-SPO').closest('tr') as HTMLElement;
    const spoTrigger = within(spoRow).getByTestId('document-detail-trigger-SPO-2026/06-0131');
    expect(spoTrigger).toHaveTextContent('SPO-2026/06-0131');
    expect(within(spoRow).queryByText(/line 4/)).not.toBeInTheDocument();
    expect(within(spoRow).queryByText(/\(100\)/)).not.toBeInTheDocument();

    // Both: on-hand text THEN the link, same row.
    const bothRow = screen.getByText('SO-BOTH').closest('tr') as HTMLElement;
    expect(within(bothRow).getByText('On hand MWH-BB')).toBeInTheDocument();
    expect(
      within(bothRow).getByTestId('document-detail-trigger-SPO-2026/07-0021'),
    ).toBeInTheDocument();

    // Nothing: a dash.
    const noneRow = screen.getByText('SO-NONE').closest('tr') as HTMLElement;
    expect(within(noneRow).getByText('-')).toBeInTheDocument();
  });

  it('opens the document dialog with the covering line marked and jumpable (R31b)', async () => {
    // RED today: `OrderInquiryDocumentLink` is called with no `highlightLines` prop at
    // all, so the dialog it opens never marks any row - there is no "Linked" badge for
    // the mocked detail payload's own `spo_line_number: 4` to show up on.
    getOrderInquirySpoDetail.mockResolvedValue({
      spo_number: 'SPO-2026/06-0131',
      supplier_name: 'CHAOSHENG',
      eta: '2026-09-15',
      lines: [
        {
          sku: 'SRTWB242', product_name: 'Sorento basin 242', allocated: '100',
          received: '0', remaining: '100', location: 'BRW-BB', spo_line_number: 4,
        },
        {
          sku: 'ZZT-OTHER', product_name: 'Other item', allocated: '10',
          received: '0', remaining: '10', location: 'BRW', spo_line_number: 5,
        },
      ],
      allocations: [],
    });
    renderDialog({
      demand: [
        {
          so_number: 'SO382618',
          agent_code: 'CONTACT O',
          warehouse_code: 'BRW-BB',
          required_date: '2026-10-15',
          open_qty: 100,
          qty_ordered: 100,
          qty_delivered: 0,
          assigned_qty: 100,
          assigned_source: null,
          status: 'covered',
          short_qty: 0,
          sales_order_id: 'so-id-1',
          assigned_from: [
            {
              kind: 'spo', ref: 'SPO-2026/06-0131 line 4',
              spo_number: 'SPO-2026/06-0131', spo_line_number: 4, qty: 100,
              oi_number: null, oi_id: null,
            },
          ],
        } as StockDebtDemandLine,
      ],
      supply: [],
      demand_total_qty: 100,
      supply_total_qty: 0,
    } as StockDebtCell);
    await screen.findByText('SO382618');

    fireEvent.click(screen.getByTestId('document-detail-trigger-SPO-2026/06-0131'));

    const linkedRow = (await screen.findByText('SRTWB242')).closest('tr') as HTMLElement;
    expect(within(linkedRow).getByText('Linked')).toBeInTheDocument();
    const otherRow = screen.getByText('ZZT-OTHER').closest('tr') as HTMLElement;
    expect(within(otherRow).queryByText('Linked')).not.toBeInTheDocument();
  });

  it('links the Supply Document cell with the same OI-style trigger, document number only, and keeps Assigned to naming the SO line (R31a)', async () => {
    // RED today: the Document cell still carries a muted " line 4" beside the trigger -
    // R31a drops it, the link alone.
    renderDialog({
      demand: [],
      supply: [
        {
          kind: 'spo',
          ref: 'SPO-2026/06-0131 line 4',
          spo_number: 'SPO-2026/06-0131',
          spo_line_number: 4,
          warehouse_code: 'MWH-BB',
          date: '2026-10-12',
          bought_for: null,
          qty: 100,
          received_qty: 0,
          outstanding_qty: 100,
          free_qty: 0,
          overdue: false,
          assigned_to: [{ so_number: 'SO382618', line_no: 2, qty: 100 }],
        } as StockDebtSupplyEvent,
      ],
      demand_total_qty: 0,
      supply_total_qty: 100,
    } as StockDebtCell);
    await screen.findByText('Nothing is due here');
    switchTab('Supply (100)');

    const trigger = await screen.findByTestId('document-detail-trigger-SPO-2026/06-0131');
    const documentCell = trigger.closest('td') as HTMLElement;
    expect(within(documentCell).queryByText(/line 4/)).not.toBeInTheDocument();

    expect(screen.getByText('SO382618 line 2 (100)')).toBeInTheDocument();
  });
  it('prints a covering PO in Covered by as the PO document link, opened on its own line (R42)', async () => {
    // R42 (owner, 28 Sep 2026): "the covered by should consider PO also". The PO entry is
    // the SAME `OrderInquiryDocumentLink` an SPO uses, kind `po`, the PO number as the
    // trigger, opened on the PO line (`poLineId`), which marks the line and offers the jump.
    getOrderInquiryPoDetail.mockResolvedValue({
      id: 'po-202609-S0029',
      po_number: '202609-S0029',
      supplier_name: 'CHAOSHENG',
      status: 'confirmed',
      expected_date: '2026-09-14',
      lines: [
        {
          id: 'pol-41', sku: 'CSK14A-NL', product_name: 'Basin', qty_ordered: '41',
          qty_received: '0', remaining: '41', location: 'BRW',
        },
        {
          id: 'pol-1305', sku: 'CSK14A-NL', product_name: 'Basin', qty_ordered: '1305',
          qty_received: '0', remaining: '1305', location: 'BRW-BB',
        },
      ],
      allocations: [],
    });
    renderDialog({
      demand: [
        {
          so_number: 'SO419208',
          agent_code: 'LEENA',
          warehouse_code: 'BRW-BB',
          required_date: '2026-10-20',
          open_qty: 1305,
          qty_ordered: 1305,
          qty_delivered: 0,
          assigned_qty: 1305,
          assigned_source: null,
          status: 'pinned',
          short_qty: 0,
          sales_order_id: 'so-419208',
          assigned_from: [
            {
              kind: 'po', ref: 'PO 202609-S0029 line 2', spo_number: null,
              spo_line_number: null, qty: 1305, oi_number: null, oi_id: null,
              po_number: '202609-S0029', po_line_number: 2,
              po_id: 'po-202609-S0029', po_line_id: 'pol-1305',
            },
          ],
        } as StockDebtDemandLine,
      ],
      supply: [],
      demand_total_qty: 1305,
      supply_total_qty: 0,
    } as StockDebtCell);
    const row = (await screen.findByText('SO419208')).closest('tr') as HTMLElement;

    const trigger = within(row).getByTestId('document-detail-trigger-202609-S0029');
    // The line rides beside the number: one PO often covers one order from two lines.
    expect(within(row).getByText('line 2')).toBeInTheDocument();
    fireEvent.click(trigger);

    expect(await screen.findByText('Purchase order')).toBeInTheDocument();
    expect(getOrderInquiryPoDetail).toHaveBeenCalledWith('po-202609-S0029');
    const lines = (await screen.findAllByText('CSK14A-NL')).map(
      (cell) => cell.closest('tr') as HTMLElement,
    );
    const linked = lines.filter((line) => line.getAttribute('data-linked-line') === 'true');
    expect(linked).toHaveLength(1);
    expect(within(linked[0]).getByText('BRW-BB')).toBeInTheDocument();
  });

  it('lists a PO line on the Supply tab with its delivery date, Qty, Received and Outstanding (R42)', async () => {
    renderDialog({
      demand: [],
      supply: [
        {
          kind: 'po',
          ref: 'PO 202609-S0029 line 1',
          spo_number: null,
          spo_line_number: null,
          po_number: '202609-S0029',
          po_line_number: 1,
          po_id: 'po-202609-S0029',
          po_line_id: 'pol-41',
          warehouse_code: 'BRW',
          date: '2026-10-12',
          stated_date: '2026-09-10',
          days_late: 18,
          bought_for: null,
          qty: 45,
          received_qty: 4,
          outstanding_qty: 41,
          free_qty: 41,
          overdue: false,
          assigned_to: [],
        } as StockDebtSupplyEvent,
      ],
      demand_total_qty: 0,
      supply_total_qty: 41,
    } as StockDebtCell);
    await screen.findByText('Nothing is due here');
    switchTab('Supply (41)');

    const trigger = await screen.findByTestId('document-detail-trigger-202609-S0029');
    const row = trigger.closest('tr') as HTMLElement;
    expect(within(row).getByText('PO')).toBeInTheDocument();
    expect(within(row).getByText('line 1')).toBeInTheDocument();
    expect(within(row).getByText('12/10/2026')).toBeInTheDocument();
    expect(within(row).getByText('45')).toBeInTheDocument();
    expect(within(row).getByText('4')).toBeInTheDocument();
    expect(within(row).getAllByText('41').length).toBeGreaterThan(0);
    expect(within(row).getByText('Free')).toBeInTheDocument();
    // Parked on its delivery date, the PO has no separate "bought for" to state.
    expect(within(row).queryByText(/bought for/)).not.toBeInTheDocument();
  });

  it('reads a fully pinned order as Short 0, each row covered by its own PO line, Supply listing both (R43, #1346)', async () => {
    // The owner's production case after #1332: SO419208 x CSK14A-NL, a 4 row and a 1,305
    // row, PO 202609-S0029 line 2 (4) and line 3 (1,305) naming SO419208, past its date.
    const poEntry = (line: number, lineId: string, qty: number) => ({
      kind: 'po', ref: `PO 202609-S0029 line ${line}`, spo_number: null,
      spo_line_number: null, qty, oi_number: null, oi_id: null,
      po_number: '202609-S0029', po_line_number: line,
      po_id: 'po-202609-S0029', po_line_id: lineId,
    });
    const demandRow = (qty: number, ordered: number, delivered: number, entry: object) =>
      ({
        so_number: 'SO419208',
        agent_code: 'CONTACT Y',
        warehouse_code: 'BRW-BB',
        required_date: '2026-09-14',
        open_qty: qty,
        qty_ordered: ordered,
        qty_delivered: delivered,
        assigned_qty: qty,
        assigned_source: null,
        status: 'pinned',
        short_qty: 0,
        sales_order_id: 'so-419208',
        assigned_from: [entry],
      }) as StockDebtDemandLine;
    const supplyRow = (line: number, lineId: string, qty: number, soLine: number) =>
      ({
        kind: 'po', ref: `PO 202609-S0029 line ${line}`, spo_number: null,
        spo_line_number: null, po_number: '202609-S0029', po_line_number: line,
        po_id: 'po-202609-S0029', po_line_id: lineId, warehouse_code: 'BRW-BB',
        date: '2026-09-10', stated_date: null, days_late: 0, bought_for: null, qty,
        received_qty: 0, outstanding_qty: qty, free_qty: 0, overdue: true,
        assigned_to: [{ so_number: 'SO419208', line_no: soLine, qty }],
      }) as StockDebtSupplyEvent;
    renderDialog({
      demand: [
        demandRow(4, 135, 131, poEntry(2, 'pol-2', 4)),
        demandRow(1305, 1305, 0, poEntry(3, 'pol-3', 1305)),
      ],
      supply: [supplyRow(2, 'pol-2', 4, 1), supplyRow(3, 'pol-3', 1305, 2)],
      demand_total_qty: 1309,
      supply_total_qty: 1309,
    } as StockDebtCell);

    await screen.findByText('Short 0');
    const rows = screen
      .getAllByText('SO419208')
      .map((cell) => cell.closest('tr') as HTMLElement);
    expect(rows).toHaveLength(2);
    const byOutstanding = (qty: string) =>
      rows.find((row) => within(row).queryAllByText(qty).length > 0) as HTMLElement;
    const row4 = byOutstanding('131');
    const row1305 = rows.find((row) => row !== row4) as HTMLElement;
    // Covered by: exactly the one PO line that gave the row its quantity.
    expect(within(row4).getAllByTestId('document-detail-trigger-202609-S0029')).toHaveLength(1);
    expect(within(row4).getByText('line 2')).toBeInTheDocument();
    expect(within(row4).queryByText('line 3')).not.toBeInTheDocument();
    expect(within(row1305).getAllByTestId('document-detail-trigger-202609-S0029')).toHaveLength(1);
    expect(within(row1305).getByText('line 3')).toBeInTheDocument();
    expect(within(row1305).queryByText('line 2')).not.toBeInTheDocument();
    for (const row of rows) {
      expect(within(row).getByText('pinned')).toBeInTheDocument();
    }
    // The Total row: Outstanding 1,309 and Assigned 1,309, beside Short 0 above.
    expect(screen.getAllByText('1,309').length).toBeGreaterThanOrEqual(2);

    switchTab('Supply (1,309)');
    expect(await screen.findByText('SO419208 line 1 (4)')).toBeInTheDocument();
    expect(screen.getByText('SO419208 line 2 (1,305)')).toBeInTheDocument();
  });
});
