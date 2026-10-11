/**
 * The breakdown behind one cell (PLAN section 13, journey step 4).
 *
 * A TABLE, on the shared DataGrid, not a stack of cards. The captain: "this needs to be more
 * table based instead of card based, so it is easier to see, and you need to show me the SO
 * order quantity, outstanding quantity also in the table ... then need to show summary
 * row whenever relevant". So the columns are asserted by name, the quantity columns are
 * asserted to total in the table's own footer row, and the verbs are a row action.
 *
 * ONE VOCABULARY: what is still to go out is **outstanding**, and the summary of it is the
 * table's own footer. The balance equation that used to sit at the top said the same figures
 * again, in the word this screen no longer uses.
 *
 * The verbs write into the DRAFT and nothing else. Nothing in this dialog claims a cell
 * committed anything: the commit is the per-order confirmation on the rail behind it (13.4).
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({
    resetToDefaults: vi.fn(),
    isLoading: false,
  }),
}));

/** Only reached when a location row is expanded - the stock table's own documents panel. */
const getStockDetail = vi.fn();
/** Only reached when the trail's own "View the queue" is pressed. */
const getPileQueue = vi.fn();
/** Only reached when a chip's Proof button is pressed - nothing in this file does. */
const getClassificationEvidence = vi.fn();

vi.mock('../../_shared/services/fulfilmentPlanningService', () => ({
  getStockDetail: (...args: unknown[]) => getStockDetail(...args),
  getPileQueue: (...args: unknown[]) => getPileQueue(...args),
  getClassificationEvidence: (...args: unknown[]) =>
    getClassificationEvidence(...args),
}));

import { BoardCellBreakdownDialog, sourceAt } from './BoardCellBreakdownDialog';
import { suggestedDecisionFor } from '../../_shared/lib/boardAmend';
import { LADDER_VERSION } from '../../_shared/lib/supplyVocabulary';
import {
  buildBoard,
  PREVIEW_POLICY,
  type BoardDemandLine,
} from '../../_shared/lib/__testsupport__/boardFixture';
import type {
  BoardCell,
  BoardContribution,
  BoardDraft,
} from '../../_shared/types/fulfilmentPlanning.types';

const TODAY = '2026-08-18';

function demand(overrides: Partial<BoardDemandLine> = {}): BoardDemandLine {
  return {
    sales_order_id: 'so-a',
    so_number: 'SO403340',
    customer_name: 'SETIA-WOOD INDUSTRIES SDN BHD (PROJECT)',
    project_label: 'SETIA-WOOD INDUSTRIES/100U DSTH (DIMINA) @ SETIA',
    line_no: 1,
    item_code: 'WESERP10B',
    qty: '100',
    qty_ordered: '120',
    required_date: '2026-09-04',
    fulfilment_location: 'BRW-BB',
    priority: null,
    ...overrides,
  };
}

function cellOf(
  lines: BoardDemandLine[],
  freeStock: Record<string, string> = {},
) {
  return buildBoard(lines, { today: TODAY, freeStock }).cells[0];
}

/**
 * Opens the info icon beside one row's source strip and reads the sentence(s) behind it (A3,
 * PLAN-demo-followups-19aug-ladder-v2.md): the rung reason and the fair-share note used to be
 * a `title` attribute and a always-visible line under the strip; both now live in one Radix
 * Tooltip, reached the same way a mouse would - `pointerEnter` the trigger, then read the
 * ACCESSIBLE copy of its content (`role="tooltip"`), which Radix mounts as a second,
 * visually-hidden node alongside the visible one so a plain `.textContent` on the visible copy
 * would double the text.
 *
 * Opened via `focus` rather than a pointer event: Radix's trigger only opens on `pointermove`
 * (never `pointerenter`, which jsdom does not synthesize the same way a real mouse does) unless
 * it is delay-gated, while `focus` calls the tooltip's own `onOpen` directly - keyboard users
 * reach the same tooltip this way, so it is also the more honest simulation.
 */
async function sourceNoteOf(key: string): Promise<string> {
  fireEvent.focus(screen.getByTestId(`source-info-${key}`));
  const tooltip = await screen.findByRole('tooltip');
  return tooltip.textContent ?? '';
}

function renderDialog(
  lines: BoardDemandLine[],
  freeStock: Record<string, string> = {},
  draft: BoardDraft = {},
) {
  const cell = cellOf(lines, freeStock);
  const onDecide = vi.fn();
  // D15: the same quiet-bulk path the panel wires up for real, standing in here for it - each
  // key's own suggestion, composed off THIS cell's contributions, posted through the same
  // `onDecide` every test already reads. A mock that merely forwarded keys without a
  // composition would break every existing "Approve selected" assertion on `call[1]`.
  const onDecideMany = vi.fn(async (keys: string[]) => {
    let saved = 0;
    for (const key of keys) {
      const contribution = cell.contributions.find((entry) => entry.key === key);
      if (!contribution) continue;
      onDecide(key, suggestedDecisionFor(contribution));
      saved += 1;
    }
    return { saved, failed: keys.length - saved };
  });
  const onClose = vi.fn();
  // A client is needed even here (nothing in this describe block opens a query itself): every
  // item-flag chip now carries a Proof button (`ClassificationProofPopover`), and `useQuery`
  // requires a provider in the tree to mount at all, whether or not it is `enabled`.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={client}>
      <BoardCellBreakdownDialog
        cell={cell}
        bucketLabel="31 Aug 2026"
        draft={draft}
        onDecide={onDecide}
        onDecideMany={onDecideMany}
        onClose={onClose}
      />
    </QueryClientProvider>,
  );
  return { onDecide, onDecideMany, onClose };
}

/**
 * ONE SECTION, TWO TABS. The dialog's two tables - the stock position per location
 * (`CellStockTable`) and the contributing lines - no longer stack; each gets the whole body
 * under a tab of its own, the way `StockDebtCellDialog` puts its Demand and Supply grids.
 *
 * It opens on STOCK, because it is opened from a CELL and never from a line: there is no line
 * context to default the lines tab from. So a test that reads or decides a LINE presses the
 * other tab first, which is the same press a planner makes.
 *
 * NOT COSMETIC: Radix unmounts the inactive panel, so without the press there is no lines
 * grid in the tree at all. And Radix's TabsTrigger switches on MOUSE DOWN - a bare `click`
 * leaves the old panel up, silently, which reads as the dialog having lost its table.
 */
function switchTab(name: RegExp) {
  const tab = screen.getByRole('tab', { name });
  fireEvent.mouseDown(tab);
  fireEvent.click(tab);
}

/** The Contributing lines tab, opened. */
function openLines() {
  switchTab(/^Contributing lines/);
}

/** Back to the Stock tab, which is the one the dialog opens on. */
function openStock() {
  switchTab(/^Stock$/);
}

/**
 * The CONTRIBUTING LINES table, inside its own tab.
 *
 * Still the last table in the tree, and now the only one: the stock table belongs to the
 * other panel, which Radix has unmounted while this one is up.
 */
function contributionTable(): HTMLElement {
  const tables = screen.getAllByRole('table');
  return tables[tables.length - 1];
}

/** The totals row the grid renders inside the table, under the columns it sums. */
function footerCells(): string[] {
  const table = contributionTable();
  const foot = table.querySelector('tfoot');
  return [...(foot?.querySelectorAll('td') ?? [])].map(
    (cell) => cell.textContent ?? '',
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('BoardCellBreakdownDialog: the cell summary, at the top', () => {
  /**
   * The balance equation is GONE. It restated the heading's own figure and then split it into
   * four terms the table below already carries under Outstanding and Sourced from - a second
   * arithmetic of the same facts, in a vocabulary ("owed") this screen no longer uses.
   */
  it('states no balance equation above the table', () => {
    renderDialog([demand({ qty: '100' })], { 'WESERP10B|BRW-BB': '40' });

    expect(screen.queryByTestId('cell-balance')).not.toBeInTheDocument();
    expect(screen.queryByText(/owed =/)).not.toBeInTheDocument();
  });

  it('sums the outstanding quantity in the table footer instead', () => {
    renderDialog(
      [
        demand({ line_no: 1, qty: '60' }),
        demand({
          line_no: 2,
          qty: '40',
          so_number: 'SO398322',
          sales_order_id: 'so-b',
        }),
      ],
      { 'WESERP10B|BRW-BB': '70' },
    );
    openLines();

    expect(footerCells()).toContain('100');
  });

  it('names the cell, and leads with what is needed and how much is decided', () => {
    // "across 1 line" is gone: the Contributing lines table below IS the lines, and the
    // quantity is what the planner opened this for.
    renderDialog([demand()]);

    expect(screen.getByText('WESERP10B · 31 Aug 2026')).toBeInTheDocument();
    const needed = screen.getByTestId('cell-quantity-needed');
    expect(needed).toHaveTextContent('Quantity needed');
    expect(needed).toHaveTextContent('100');
    expect(needed).toHaveTextContent('0 decided');
    expect(
      screen.queryByText('100 across 1 line, 0 decided'),
    ).not.toBeInTheDocument();
  });

  /**
   * The captain, 28 August 2026, reading a cell whose pool rung said "Project hot-selling at
   * BRW-BB, BRW-IB" only inside the trail popover: "we need to label dealer hot selling /
   * project hot selling here instead of putting it at the tooltip". The verdict decides
   * whether the pool is offered at all, so it stands beside the title.
   */
  it('states the hot-selling verdict beside the title, off the first line the ladder walked', () => {
    const cell = cellOf([demand({ qty: '100' })]);
    cell.contributions[0].item_flags = {
      dealer_hot_selling: false,
      dealer_hot_selling_where: [],
      project_hot_selling: true,
      project_hot_selling_where: ['BRW-BB', 'BRW-IB'],
      dealer_classified: true,
      project_classified: true,
      discontinued: false,
      retail_classification_available: true,
    };
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <BoardCellBreakdownDialog
          cell={cell}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    const chips = screen.getByTestId('trail-flags-cell');
    expect(chips.textContent).toBe('Cold at retailProject hot-selling');
    expect(
      screen.getByTestId('trail-flag-cell-project-hot-selling'),
    ).toHaveAttribute(
      'title',
      'Project hot-selling at BRW-BB, BRW-IB. The shared pool may be drawn while its availability stays positive.',
    );
  });

  it('shows no verdict beside the title for a line the ladder did not judge', () => {
    renderDialog([demand()]);

    expect(screen.queryByTestId('trail-flags-cell')).not.toBeInTheDocument();
  });
});

describe('BoardCellBreakdownDialog: the Suggestion card', () => {
  /**
   * The dialog opens on a DECISION, so the decision leads: what is needed, and what the ladder
   * proposes to do about it, broken down by kind of source. It used to open on a sentence and a
   * table of lines, and working out what the whole cell was being asked to do meant reading a
   * source strip per row.
   */
  const row = (key: string) => screen.getByTestId(`suggestion-${key}`);

  it('states only the kinds it proposes something for, in one order', () => {
    // 100 outstanding, 40 free at the line's own location: Buy and Use own location, and
    // nothing else. The card used to list all four always and mute the empty ones, and on a
    // real cell that was three lines of nothing around the line that said what to do.
    renderDialog([demand({ qty: '100' })], { 'WESERP10B|BRW-BB': '40' });

    const labels = [
      ...screen
        .getByTestId('cell-suggestion')
        .querySelectorAll('[data-testid^="suggestion-"]'),
    ].map((node) => node.textContent ?? '');
    expect(labels).toHaveLength(2);
    // Ladder v5's own order (AC-V7): the questions first, Buy last.
    expect(labels[0]).toContain('Use own location');
    expect(labels[1]).toContain('Buy');
  });

  it('splits the cell between what is taken and what is bought, and says from where', () => {
    // 100 outstanding, 40 free at the line's own location: the ladder reserves 40 there and
    // buys the rest, and the card says exactly that without anybody opening a row.
    renderDialog([demand({ qty: '100' })], { 'WESERP10B|BRW-BB': '40' });

    expect(row('own')).toHaveTextContent('Use own location');
    expect(row('own')).toHaveTextContent('40 from BRW-BB');
    expect(row('buy')).toHaveTextContent('60');
  });

  it('names no location on a Buy, because it is not held anywhere yet', () => {
    renderDialog([demand({ qty: '100' })]);

    expect(row('buy')).toHaveTextContent('100');
    expect(row('buy')).not.toHaveTextContent('from');
  });

  it('sums across every line of the cell', () => {
    renderDialog(
      [
        demand({ line_no: 1, qty: '60' }),
        demand({
          line_no: 2,
          qty: '40',
          so_number: 'SO398322',
          sales_order_id: 'so-b',
        }),
      ],
      { 'WESERP10B|BRW-BB': '70' },
    );

    // 70 free serves the first line whole and 10 of the second; the rest is bought.
    expect(row('own')).toHaveTextContent('70 from BRW-BB');
    expect(row('buy')).toHaveTextContent('30');
  });

  it('leaves out the kinds with no quantity, rather than reading 0 at them', () => {
    renderDialog([demand({ qty: '100' })]);

    expect(screen.queryByTestId('suggestion-shared')).not.toBeInTheDocument();
    expect(
      screen.queryByTestId('suggestion-borrow_other'),
    ).not.toBeInTheDocument();
    expect(row('buy')).toHaveTextContent('100');
  });
});

describe('BoardCellBreakdownDialog: a fulfilled line (#1362)', () => {
  it('says the line is fulfilled, with what was delivered and when it was due, instead of a suggestion', () => {
    // SO382618's 08/06 line: nothing left to plan, 100 delivered. A suggestion here answered
    // a quantity the line no longer has.
    renderDialog([demand({ qty: '0', qty_delivered: '100', required_date: '2026-06-08' })]);

    const fulfilled = screen.getByTestId('cell-fulfilled');
    expect(fulfilled.textContent).toContain('Fulfilled');
    expect(fulfilled.textContent).toContain('100 delivered');
    expect(fulfilled.textContent).toContain('due 08/06/2026');
    expect(screen.queryByTestId('cell-suggestion')).toBeNull();
  });
});

describe('BoardCellBreakdownDialog: the Product column', () => {
  it('is not shown when the cell holds one product - the title already names it', () => {
    renderDialog([demand({ line_no: 1 }), demand({ line_no: 2 })]);

    const table = contributionTable();
    expect(
      within(table).queryByRole('columnheader', { name: 'Product' }),
    ).not.toBeInTheDocument();
  });
});

describe('BoardCellBreakdownDialog: the table', () => {
  /**
   * C1: the columns in order, and none of the three that left (R8, the captain 27 Aug: "rank
   * goes"). Ordered and Delivered moved into the expanded row's read-only strip
   * (`BoardLineDecisionPanel.test.tsx`), so this table no longer states them as columns.
   */
  it('carries the columns the captain named, in order, with no Rank, Ordered or Delivered', () => {
    renderDialog([demand()]);
    openLines();

    const table = contributionTable();
    const headers = within(table)
      .getAllByRole('columnheader')
      .map((node) => node.textContent ?? '');
    const order = [
      'Sales order',
      'Customer',
      'Agent',
      'Project',
      'Outstanding',
      'Delivery date',
      'Location',
      'Sourced from',
      'Order inquiry',
      'Decision',
    ];
    const positions = order.map((title) =>
      headers.findIndex((header) => header.includes(title)),
    );
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));

    for (const gone of ['Rank', 'Ordered', 'Delivered']) {
      expect(headers.some((header) => header === gone)).toBe(false);
    }
  });

  it('totals the outstanding quantity in a summary row inside the table', () => {
    renderDialog([
      demand({ line_no: 1, qty: '60' }),
      demand({
        line_no: 2,
        qty: '40',
        so_number: 'SO398322',
        sales_order_id: 'so-b',
      }),
    ]);
    openLines();

    const cells = footerCells();
    expect(cells.some((cell) => cell === 'Total')).toBe(true);
    expect(cells).toContain('100');
  });

  it('carries the sales order, customer, project and location per row', () => {
    renderDialog([demand()]);
    openLines();

    expect(screen.getByText(/SO403340/)).toBeInTheDocument();
    expect(
      screen.getByText('SETIA-WOOD INDUSTRIES SDN BHD (PROJECT)'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('SETIA-WOOD INDUSTRIES/100U DSTH (DIMINA) @ SETIA'),
    ).toBeInTheDocument();
    expect(screen.getAllByText('BRW-BB').length).toBeGreaterThan(0);
  });

  it('shows where the quantity is sourced from, with the reason the rule wrote', async () => {
    const lines = [demand({ qty: '100' })];
    const freeStock = { 'WESERP10B|BRW-BB': '40' };
    const key = cellOf(lines, freeStock).contributions[0].key;
    renderDialog(lines, freeStock);
    openLines();

    // SECTION 2'S word for the rung, off `SHORT_LABELS` - the same word the bar, the
    // legend and the Suggestion card use for this quantity. The strip used to speak
    // ladder v2's own names (Group take) beside a card saying "Own" about the same 40.
    // Scoped to the VISIBLE pill row (`sources-pills-<key>`), never to the whole document -
    // `PillOverflow`'s own hidden measuring row repeats the same pill text off-screen so it
    // can size itself, and a plain `getByText` finds that copy too.
    const sourceTestId = `sources-pills-${key}`;
    const sourcePills = within(screen.getByTestId(sourceTestId));
    expect(sourcePills.getByText(/Own 40/)).toBeInTheDocument();
    // At this width (jsdom's own zero) only the first pill shows; the rest folds behind
    // "+1", which opens the SAME popover every pill does and states the whole composition.
    fireEvent.click(sourcePills.getByText('+1'));
    expect(screen.getByTestId(`${sourceTestId}-popover`).textContent).toContain(
      'Buy60',
    );
    // The rule's own sentence is behind the info icon now, not a plain `title` - so the
    // numbers above stay directly readable and only the prose needs a hover.
    expect(await sourceNoteOf(key)).toContain(
      'Free unclaimed stock at BRW-BB covers this much by the delivery date.',
    );
  });

  it('says when the stock was already committed to earlier demand (13.5)', () => {
    renderDialog(
      [
        demand({
          sales_order_id: 'so-a',
          so_number: 'SO403340',
          line_no: 1,
          qty: '100',
          required_date: '2026-09-04',
        }),
        demand({
          sales_order_id: 'so-b',
          so_number: 'SO398322',
          line_no: 2,
          qty: '100',
          required_date: '2026-09-02',
        }),
      ],
      { 'WESERP10B|BRW-BB': '100' },
    );

    // The word is gone from the screen (the captain, 27 Aug); the flag stays on the row.
    expect(screen.queryByText('Contested')).toBeNull();
  });

  /**
   * R1/B1: every figure in the stock table is netted of ONE line's own quantity, so a cell
   * holding two lines has two answers and the table has to say whose it is showing. It
   * follows the row the planner opened, and names the line while there is a choice.
   */
  it('shows the EXPANDED line\u2019s own stock position, and says whose it is', async () => {
    const cell = cellOf([
      demand({
        sales_order_id: 'so-a',
        so_number: 'SO403340',
        line_no: 1,
        qty: '24',
      }),
      demand({
        sales_order_id: 'so-b',
        so_number: 'SO398322',
        line_no: 2,
        qty: '24',
      }),
    ]);
    const withPerLine: BoardCell = {
      ...cell,
      // Two lines, two tables. The first row's is the cell's own (the server sends it as
      // `cell.locations` too); the second row's differs in exactly the way the netting does.
      locations: [
        { ...cell.locations[0], location: 'BRW-AM', available_qty: '-15' },
      ],
      contributions: cell.contributions.map((entry, index) => ({
        ...entry,
        locations: [
          {
            ...cell.locations[0],
            location: 'BRW-AM',
            available_qty: index === 0 ? '-15' : '-16',
          },
        ],
      })),
    };
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false, gcTime: 0 } },
          })
        }
      >
        <BoardCellBreakdownDialog
          cell={withPerLine}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    // THE TWO TABS CROSS HERE, and this is the only place they do: the Stock tab answers for
    // whichever row the Contributing lines tab has open. Nothing expanded yet, so it is the
    // first contributing line's, which is what `cell.locations` holds.
    expect(screen.getByTestId('cell-location-BRW-AM').textContent).toContain(
      '-15',
    );
    expect(screen.getByTestId('cell-stock-for-line').textContent).toContain(
      `${withPerLine.contributions[0].so_number} line ${withPerLine.contributions[0].line_no}`,
    );

    // Open the SECOND row and the table answers for that line instead.
    openLines();
    const rows = contributionTable().querySelectorAll('tbody tr');
    fireEvent.click(rows[1]);
    openStock();

    await waitFor(() =>
      expect(screen.getByTestId('cell-location-BRW-AM').textContent).toContain(
        '-16',
      ),
    );
    expect(screen.getByTestId('cell-stock-for-line').textContent).toContain(
      `${withPerLine.contributions[1].so_number} line ${withPerLine.contributions[1].line_no}`,
    );
  });

  it('names no line at all on a cell holding one', () => {
    renderDialog([demand()]);

    expect(screen.queryByTestId('cell-stock-for-line')).not.toBeInTheDocument();
  });

  it('lists the rows in the order the allocation rule served them', () => {
    renderDialog([
      demand({
        sales_order_id: 'so-a',
        so_number: 'SO403340',
        line_no: 1,
        required_date: '2026-09-04',
      }),
      demand({
        sales_order_id: 'so-b',
        so_number: 'SO398322',
        line_no: 2,
        required_date: '2026-09-02',
      }),
    ]);
    openLines();

    const rows = contributionTable().querySelectorAll('tbody tr');
    expect(rows[0].textContent).toContain('SO398322');
    expect(rows[1].textContent).toContain('SO403340');
  });
});

/**
 * The real backend fields, which arrived after this table was first built against assumed
 * names. Three of them change what the screen may say rather than merely what it reads.
 */
describe('BoardCellBreakdownDialog: the facts the server sends', () => {
  /** A cell with the location stock facts the server now carries. */
  function stockedCell(location: Partial<BoardCell['locations'][number]>) {
    const cell = cellOf([demand()]);
    return { ...cell, locations: [{ ...cell.locations[0], ...location }] };
  }

  function renderCell(cell: BoardCell) {
    // The header's item-flag chips carry a Proof button (`useQuery`), so a client is needed.
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <BoardCellBreakdownDialog
          cell={cell}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
  }

  /** The cells of one location's row in the stock table, in column order. */
  function stockRow(location: string): string[] {
    return [
      ...screen.getByTestId(`cell-location-${location}`).querySelectorAll('td'),
    ].map((cell) => cell.textContent ?? '');
  }

  /**
   * AutoCount's own figures, in AutoCount's own words - as a ROW of a table rather than as the
   * run-on sentence a pill had to be (the captain: "can be more tabulated and structured like
   * AutoCount, with expandable details instead of clicking in"). The columns themselves are
   * `CellStockTable`'s tests; what the dialog owes is that the position is on screen at all.
   */
  it('leads with On hand, SO qty, SPO qty and Available', () => {
    renderCell(
      stockedCell({
        location: 'BRW-BB',
        qty_on_hand: '478',
        so_qty: '47009',
        spo_qty: '0',
        available_qty: '-46531',
      }),
    );

    // Location, On hand, SO qty, SPO qty, Available, Available for Project, PO qty, Taken -
    // after the chevron cell, which carries no text. No demand column: the table below says
    // that per line. No Reserved and no Free either: Free was `On hand - Reserved`, and
    // Reserved itself was read by nothing on this screen once `Available` turned out not to
    // use it.
    expect(stockRow('BRW-BB').slice(1)).toEqual([
      'BRW-BB',
      'Own location',
      '478',
      '47009',
      '0',
      // A negative available is the whole point: it is the shortfall, and clamping it to zero
      // would turn the one number that says "this cannot be met" into one that says it can.
      '-46531',
      // Available for Project: on an own location it IS Available (D2, captain 3 Sep) -
      // there is no dealer share to keep back outside a site pool, so everything available
      // there is available to a project. It used to print "-", which read as missing data.
      '-46531',
      // PO qty and Taken: the server stated neither, and a row that names a location reads 0.
      '0',
      '0',
    ]);
  });

  /**
   * Measured on the live board: a location can carry `so_qty` while `qty_on_hand` is null.
   * Gating the whole position on on-hand hid the SO figure the planner came for.
   */
  it('shows whichever of the four the server stated, not all-or-nothing', () => {
    renderCell(
      stockedCell({
        location: 'BRW-IB',
        qty_on_hand: null,
        so_qty: '10805',
        spo_qty: '0',
        available_qty: null,
      }),
    );

    const row = stockRow('BRW-IB');
    expect(row[4]).toBe('10805');
    expect(row[5]).toBe('0');
    // A stated location with no figure of its own reads 0 (AC-B2): an absent stock row means
    // the last upload counted none there. Only a line with NO location keeps its blanks.
    expect(row[3]).toBe('0');
    expect(row[6]).toBe('0');
  });

  it('leaves the figures blank when the sales order named no location', () => {
    // The one row AC-B2's zero rule does not reach: 0 at a location means do not look there,
    // and this row has no location to look at.
    renderCell(
      stockedCell({
        location: null,
        qty_on_hand: null,
        so_qty: null,
        spo_qty: null,
        available_qty: null,
        qty_free: null,
        qty_incoming: null,
      }),
    );

    const row = stockRow('none');
    expect(row[1]).toBe('No location');
    expect(row.slice(3)).toEqual(['-', '-', '-', '-', '-', '-', '-']);
    expect(row).not.toContain('0');
  });

  /**
   * C1: Delivered left with Ordered, into the expanded row's read-only strip
   * (`BoardLineDecisionPanel.test.tsx`). C1: Rank left the table entirely (R8).
   */
  it('carries neither a Delivered column nor a Rank column any more', () => {
    renderDialog([
      demand({ qty_ordered: '120', qty_delivered: '20', qty: '100' }),
    ]);

    const table = contributionTable();
    const headers = within(table)
      .getAllByRole('columnheader')
      .map((node) => node.textContent ?? '');
    expect(headers.some((header) => header === 'Delivered')).toBe(false);
    expect(headers.some((header) => header === 'Rank')).toBe(false);
    expect(
      screen.queryByRole('button', { name: 'Rank' }),
    ).not.toBeInTheDocument();
  });
});

/**
 * Deciding a line IN THE ROW (C3, C4, C5, C6, C9). `BoardLineDecisionPanel.test.tsx` owns the
 * panel's own behaviour (the three verbs, the balance hint, the flag); what belongs here is the
 * dialog's part of the contract - the pill in the Decision column, the row opening the panel in
 * place, and one row open at a time.
 */
describe('BoardCellBreakdownDialog: deciding a line in the row', () => {
  it('shows a pill, never a button, in the Decision column (C2, C3)', () => {
    renderDialog([demand()]);
    openLines();

    expect(
      screen.getByTestId('decision-pill-so-a|1|WESERP10B|2026-08-31'),
    ).toHaveTextContent('Suggested');
    expect(
      screen.queryByRole('button', { name: 'Save decision' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Reject' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Amend' }),
    ).not.toBeInTheDocument();
  });

  it('expands the row in place on a click, and collapses it on a second click (C3, C6)', () => {
    renderDialog([demand()]);
    openLines();
    const key = 'so-a|1|WESERP10B|2026-08-31';

    expect(
      screen.queryByTestId(`line-decision-${key}`),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('SO403340'));
    expect(screen.getByTestId(`line-decision-${key}`)).toBeInTheDocument();
    // No modal opened over the dialog: it is the SAME dialog, one row wider.
    expect(screen.getAllByRole('dialog')).toHaveLength(1);

    fireEvent.click(screen.getByText('SO403340'));
    expect(
      screen.queryByTestId(`line-decision-${key}`),
    ).not.toBeInTheDocument();
  });

  it('opening a second row closes the first (C5)', () => {
    renderDialog([
      demand({ line_no: 1 }),
      demand({ line_no: 2, so_number: 'SO398322', sales_order_id: 'so-b' }),
    ]);
    openLines();
    const first = 'so-a|1|WESERP10B|2026-08-31';
    const second = 'so-b|2|WESERP10B|2026-08-31';

    fireEvent.click(screen.getByText('SO403340'));
    expect(screen.getByTestId(`line-decision-${first}`)).toBeInTheDocument();

    fireEvent.click(screen.getByText('SO398322'));
    expect(
      screen.queryByTestId(`line-decision-${first}`),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId(`line-decision-${second}`)).toBeInTheDocument();
  });

  it('asks before discarding an unsaved edit when another row is opened (C5)', () => {
    renderDialog([
      demand({ line_no: 1 }),
      demand({ line_no: 2, so_number: 'SO398322', sales_order_id: 'so-b' }),
    ]);
    openLines();
    const first = 'so-a|1|WESERP10B|2026-08-31';

    fireEvent.click(screen.getByText('SO403340'));
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Typing, not yet saved.' },
    });

    fireEvent.click(screen.getByText('SO398322'));
    // The row underneath has NOT switched yet: the prompt is answered first.
    expect(screen.getByTestId(`line-decision-${first}`)).toBeInTheDocument();
    expect(
      screen.getByText('Leave this decision unsaved?'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(
      screen.queryByTestId(`line-decision-${first}`),
    ).not.toBeInTheDocument();
  });

  /**
   * THE SAME QUESTION WHEN THE WHOLE DIALOG GOES (C5).
   *
   * Opening another row asked before it threw an unsaved composition away; the X, Escape and a
   * backdrop click did not, and those are the three easiest gestures on the screen. The draft
   * went in silence, which is the loss the prompt exists to prevent - so the close path routes
   * through the same prompt, and the dialog stays open until it is answered.
   */
  it('asks before the dialog itself closes over an unsaved edit (C5)', async () => {
    const { onClose } = renderDialog([demand()]);
    openLines();
    const key = 'so-a|1|WESERP10B|2026-08-31';

    fireEvent.click(screen.getByText('SO403340'));
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Typing, not yet saved.' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));

    expect(onClose).not.toHaveBeenCalled();
    expect(
      screen.getByText('Leave this decision unsaved?'),
    ).toBeInTheDocument();

    // Keep editing leaves everything where it was: the dialog open and the row still typed in.
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(onClose).not.toHaveBeenCalled();
    // The confirm AlertDialog's exit (M2-05) inerts the page behind it for
    // one extra tick beyond the click - wait it out before the next query.
    await waitFor(() => expect(screen.queryByText('Leave this decision unsaved?')).not.toBeInTheDocument());
    expect(screen.getByTestId(`line-decision-${key}`)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes at once when no decision is half-typed', () => {
    const { onClose } = renderDialog([demand()]);
    openLines();

    fireEvent.click(screen.getByText('SO403340'));
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByText('Leave this decision unsaved?'),
    ).not.toBeInTheDocument();
  });

  it('Save on the untouched suggestion, from the row, approves it with no reason and no flag', () => {
    const { onDecide } = renderDialog([demand()]);
    openLines();

    fireEvent.click(screen.getByText('SO403340'));
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));

    // D11: the composition rides along with the approval too - the exact shape is
    // `BoardLineDecisionPanel.test.tsx`'s own job, this only checks the row's Save reaches
    // this dialog's `onDecide` with the right verdict.
    expect(onDecide).toHaveBeenCalledWith(
      'so-a|1|WESERP10B|2026-08-31',
      expect.objectContaining({
        verdict: 'approved',
        suspected_system_issue: false,
      }),
    );
  });

  it('reject, from the row, needs a reason', () => {
    const { onDecide } = renderDialog([demand()]);
    openLines();

    fireEvent.click(screen.getByText('SO403340'));
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Cancelled by the customer.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));

    expect(onDecide).toHaveBeenCalledWith('so-a|1|WESERP10B|2026-08-31', {
      verdict: 'rejected',
      reason: 'Cancelled by the customer.',
      suspected_system_issue: false,
    });
  });

  it('Save on a changed composition, from the row, posts the whole of it (AC-L5, whole-line)', () => {
    // Wholly from stock (AC-L5): the per-order sheet still refuses a mix of stock and a Buy
    // on one line outright. The board (this dialog) may save one, but only with a stated
    // `amend_reason` - the 8 Sep 2026 ruling - which is exactly what makes this composition
    // (a WHOLE-LINE Buy) the simple case: nothing to justify.
    const { onDecide } = renderDialog([demand({ qty: '100' })], {
      'WESERP10B|BRW-BB': '100',
    });
    openLines();

    fireEvent.click(screen.getByText('SO403340'));
    // Buy is a whole-line switch: on, the stock rows clear and the whole 100 is bought.
    fireEvent.click(screen.getByLabelText('Buy the whole line'));
    const save = screen.getByRole('button', { name: 'Save decision' });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: {
        value: 'The site wants new stock, not what is standing there.',
      },
    });
    expect(save).toBeEnabled();
    fireEvent.click(save);

    expect(onDecide).toHaveBeenCalledWith(
      'so-a|1|WESERP10B|2026-08-31',
      expect.objectContaining({
        verdict: 'amended',
        buy_qty: '100',
        reserve: [],
        reason: 'The site wants new stock, not what is standing there.',
      }),
    );
  });

  it('shows Saved on a row the draft already decided, and reads the pill accordingly (S4, R-F)', () => {
    renderDialog(
      [demand()],
      {},
      {
        'so-a|1|WESERP10B|2026-08-31': { verdict: 'approved' },
      },
    );
    openLines();

    expect(
      screen.getByTestId('decision-pill-so-a|1|WESERP10B|2026-08-31'),
    ).toHaveTextContent('Saved');
  });

  it('a row the draft already decided opens unlocked - no undo step before it can be edited again', () => {
    renderDialog(
      [demand({ qty: '100' })],
      { 'WESERP10B|BRW-BB': '40' },
      {
        'so-a|1|WESERP10B|2026-08-31': { verdict: 'approved' },
      },
    );
    openLines();

    fireEvent.click(screen.getByText('SO403340'));

    expect(screen.getByLabelText('Reserve at BRW-BB')).toBeEnabled();
    expect(
      screen.getByRole('button', { name: 'Save decision' }),
    ).toBeInTheDocument();
  });

  it('an amended row is returned to the suggestion by putting it back, and Save then approves (no Undo button)', () => {
    const { onDecide } = renderDialog(
      [demand({ qty: '100' })],
      { 'WESERP10B|BRW-BB': '100' },
      {
        'so-a|1|WESERP10B|2026-08-31': {
          verdict: 'amended',
          reserve: [],
          borrow: [],
          buy_qty: '100',
          timely_spo_qty: '0',
          reason: 'Overridden earlier.',
        },
      },
    );
    openLines();

    expect(
      screen.getByTestId('decision-pill-so-a|1|WESERP10B|2026-08-31'),
    ).toHaveTextContent('Saved');
    expect(
      screen.queryByRole('button', { name: 'Undo' }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('SO403340'));
    // The way back is the composition itself: switch the whole-line Buy off, put the engine's
    // own 100 back at BRW-BB, and the one Save reads that as the approval it is.
    fireEvent.click(screen.getByLabelText('Buy the whole line'));
    fireEvent.change(screen.getByLabelText('Reserve at BRW-BB'), {
      target: { value: '100' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));

    // D11: the composition rides along with the approval too.
    expect(onDecide).toHaveBeenCalledWith(
      'so-a|1|WESERP10B|2026-08-31',
      expect.objectContaining({
        verdict: 'approved',
        suspected_system_issue: false,
      }),
    );
  });
});

/**
 * C11: a line an active decision covers opens locked, with Amend the only way in.
 * `BoardLineDecisionPanel.test.tsx` owns the panel's own field-level behaviour once unlocked.
 */
describe('BoardCellBreakdownDialog: a covered row opens locked, Amend only (C11)', () => {
  const frozen = {
    revision_no: 1,
    confirmed_at: '2026-08-18T02:00:00',
    timely_spo_qty: '0',
    reserve: [],
    borrow: [
      {
        source: 'other_location' as const,
        warehouse_id: 'wh-mwh-ib',
        location: 'MWH-IB',
        donor_project_id: null,
        qty: '10',
        reason: 'The other site can wait a week.',
      },
    ],
    buy_qty: '33',
    amend_reason: 'Borrowed rather than bought, agreed with the other site.',
  };

  it('reads Confirmed on the pill, and expands with an Amend button and nothing else', () => {
    renderDialog([demand({ qty: '43', decision: frozen })]);
    openLines();
    const key = 'so-a|1|WESERP10B|2026-08-31';

    expect(screen.getByTestId(`decision-pill-${key}`)).toHaveTextContent(
      'Confirmed',
    );

    fireEvent.click(screen.getByText('SO403340'));

    const panel = screen.getByTestId(`line-decision-${key}`);
    expect(
      within(panel).getByRole('button', { name: 'Amend' }),
    ).toBeInTheDocument();
    expect(
      within(panel).queryByRole('button', { name: 'Save decision' }),
    ).not.toBeInTheDocument();
    expect(
      within(panel).queryByRole('button', { name: 'Reject' }),
    ).not.toBeInTheDocument();
  });
});

describe('BoardCellBreakdownDialog: a line with no location (AC-FP16)', () => {
  it('offers no verdict at all, and says why', () => {
    renderDialog([demand({ fulfilment_location: null })]);
    openLines();

    expect(
      screen.queryByRole('button', { name: 'Save decision' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText('Needs a location')).toBeInTheDocument();
  });

  it('still shows its quantity, so the demand is not hidden from the reader', () => {
    renderDialog([
      demand({ line_no: 1, qty: '24', fulfilment_location: null }),
      demand({ line_no: 2, qty: '10' }),
    ]);

    expect(screen.getByTestId('cell-quantity-needed')).toHaveTextContent('34');
  });
});

/**
 * The blocker, measured in the browser: at a 560px-tall window the dialog footer painted OVER
 * the row's Approve button, so a planner on a laptop could not decide anything at all. The
 * modal mandate is the fix - the body scrolls in its own region with a max height, and the
 * footer sits OUTSIDE that region so it can never cover a control.
 */
describe('BoardCellBreakdownDialog: the actions can never be covered', () => {
  it('keeps the scrolling region that stops a row action being covered', () => {
    renderDialog([demand()]);
    openLines();
    fireEvent.click(screen.getByText('SO403340'));

    const body = screen.getByTestId('cell-dialog-body');
    expect(body.className).toContain('overflow-y-auto');
    expect(body.className).toContain('min-h-0');
    expect(body.contains(contributionTable())).toBe(true);
    expect(
      body.contains(screen.getByRole('button', { name: 'Save decision' })),
    ).toBe(true);
  });

  /**
   * The captain: "don't need this close button, the cross button at top right is enough". A
   * footer whose only content duplicated the X was a band of chrome earning nothing - and it
   * was the band that used to paint over the row actions. The scroll layout stays; the footer
   * does not.
   */
  it('has no footer, and the corner X still closes', () => {
    const onClose = vi.fn();
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false, gcTime: 0 } },
          })
        }
      >
        <BoardCellBreakdownDialog
          cell={cellOf([demand()])}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={onClose}
        />
      </QueryClientProvider>,
    );

    expect(screen.queryByTestId('cell-dialog-footer')).not.toBeInTheDocument();
    const closers = screen.getAllByRole('button', { name: 'Close' });
    expect(closers).toHaveLength(1);
    fireEvent.click(closers[0]);
    expect(onClose).toHaveBeenCalled();
  });

  it('lays the dialog out as a column so the body is what shrinks, not the footer', () => {
    renderDialog([demand()]);

    const content = screen.getByTestId('cell-dialog-content');
    expect(content.className).toContain('flex');
    expect(content.className).toContain('flex-col');
    // A fixed max-height on the BODY was the bug: it let the content run past the footer.
    // The height belongs to the dialog, and the body takes what is left.
    expect(screen.getByTestId('cell-dialog-body').className).toContain(
      'flex-1',
    );
  });
});

/**
 * THE SCM FAMILY'S LIGHTBOX, AND ITS TWO TABS.
 *
 * The shell is `scm/components/PlanRowDialog.tsx`'s, copied the way `StockDebtCellDialog`
 * copied it: same sizing, same header, same scrolling body, so every lightbox in this family
 * is one object to a reader.
 *
 * And the two tables are TABS rather than two stacked halves. Before this, the stock position
 * lived in a header capped at 45vh and the lines grid in the body under it: each got about half
 * the dialog, so a reader scrolled past one to reach the other and neither was ever whole.
 */
describe('BoardCellBreakdownDialog: the family lightbox, and its two tabs', () => {
  /** A location the group drill can be opened on: it needs a set to net over and a product. */
  function groupedCell(): BoardCell {
    const cell = cellOf([demand()]);
    return {
      ...cell,
      location_group: 'BB',
      locations: [
        {
          ...cell.locations[0],
          location: 'BRW-BB',
          product_id: 'prod-1',
          warehouse_id: 'wh-1',
          qty_on_hand: '478',
          net_of: 'BB',
          net: '-8013',
        },
      ],
    };
  }

  function renderGrouped() {
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false, gcTime: 0 } },
          })
        }
      >
        <BoardCellBreakdownDialog
          cell={groupedCell()}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
  }

  it('wears the family shell: capped height, one header, one scrolling body', () => {
    renderDialog([demand()]);

    // `PlanRowDialog`'s own classes, to the character - the point of a copied shell is that
    // the three lightboxes are the same object, and a divergence here is how they drift.
    const content = screen.getByTestId('cell-dialog-content');
    expect(content.className).toContain('max-h-[85vh]');
    expect(content.className).toContain('sm:max-w-[95vw]');
    expect(content.className).toContain('flex-col');
    expect(content.className).toContain('overflow-hidden');

    // The header does not scroll any more. It used to be `max-h-[45vh] overflow-y-auto`,
    // carrying two cards and a stock table of up to eleven rows.
    const header = content.querySelector('[data-slot="dialog-header"]');
    expect(header?.className).toContain('shrink-0');
    expect(header?.className).not.toContain('overflow-y-auto');

    const body = screen.getByTestId('cell-dialog-body');
    expect(body.className).toContain('overflow-y-auto');
    expect(body.className).toContain('flex-1');
    expect(body.className).toContain('min-h-0');
  });

  it('states the cell in the title and its context in the muted line under it', () => {
    renderGrouped();

    expect(screen.getByText('WESERP10B · 31 Aug 2026')).toBeInTheDocument();
    // The ownership GROUP leads: it is the pile step 1 draws from, and nothing else on this
    // screen prints its name - the stock table below only speaks up when there is none.
    expect(screen.getByTestId('cell-dialog-context')).toHaveTextContent(
      'BB group · 100 to plan · 0 decided',
    );
  });

  it('still describes the dialog when the server resolved no group', () => {
    // Never empty: Radix describes the dialog by this line, and a cell whose group could
    // not be resolved still has a quantity that was opened over.
    renderDialog([demand()]);

    expect(screen.getByTestId('cell-dialog-context')).toHaveTextContent(
      '100 to plan · 0 decided',
    );
    expect(screen.getByTestId('cell-dialog-context').textContent).not.toContain(
      'group',
    );
  });

  it('opens on Stock, and the lines grid is not in the tree until it is asked for', () => {
    // NO LINE CONTEXT TO DEFAULT FROM: `FulfilmentBoardPanel` opens this with `onOpenCell(cell)`
    // and nothing else, so the dialog cannot know a line was what the planner meant. Stock is
    // therefore what it leads with.
    renderDialog([demand()]);

    expect(screen.getByRole('tab', { name: 'Stock' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByTestId('cell-stock-table')).toBeInTheDocument();
    expect(screen.queryByText('SO403340')).not.toBeInTheDocument();
  });

  it('names the second tab with how many lines are behind it', () => {
    renderDialog([
      demand({ line_no: 1 }),
      demand({ line_no: 2, so_number: 'SO398322', sales_order_id: 'so-b' }),
    ]);

    // The count sits in the trigger, where it can be read without pressing anything.
    expect(
      screen.getByRole('tab', { name: 'Contributing lines (2)' }),
    ).toBeInTheDocument();
  });

  it('reaches the lines grid on the second tab, and puts the stock table away', () => {
    renderDialog([demand()]);

    openLines();

    expect(screen.getByText('SO403340')).toBeInTheDocument();
    expect(contributionTable().querySelectorAll('tbody tr')).toHaveLength(1);
    // ONE TABLE AT A TIME, which is the whole point: the panel that is not up is unmounted,
    // so whichever table is being read has the entire body.
    expect(screen.queryByTestId('cell-stock-table')).not.toBeInTheDocument();

    // And back again, with the stock table where it was.
    openStock();
    expect(screen.getByTestId('cell-stock-table')).toBeInTheDocument();
    expect(screen.queryByText('SO403340')).not.toBeInTheDocument();
  });

  it('opens the group drill inside the Stock tab, untouched by the move', async () => {
    // Left in flight on purpose: the documents themselves are `StockDocumentsPanel`'s own
    // suite. What is under test is that the drill still opens where it always did.
    getStockDetail.mockReturnValue(new Promise(() => {}));
    renderGrouped();

    // AC-3.1: the dialog lands on "This line" - the own-location set - without a click, so
    // the drill is open before the chevron is ever pressed.
    expect(
      await screen.findByTestId('stock-set-expansion-BB'),
    ).toBeInTheDocument();
    // Inside the Stock panel, not loose in the dialog.
    const panel = screen.getByRole('tabpanel', { name: 'Stock' });
    expect(panel.contains(screen.getByTestId('stock-set-expansion-BB'))).toBe(
      true,
    );

    // The SET's own chevron still toggles it, the same mechanism the auto-land itself uses.
    fireEvent.click(screen.getByTestId('stock-set-expand-BB'));
    expect(screen.queryByTestId('stock-set-expansion-BB')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('stock-set-expand-BB'));
    expect(
      await screen.findByTestId('stock-set-expansion-BB'),
    ).toBeInTheDocument();
  });

  it('keeps the per-location drill working on the same tab', async () => {
    getStockDetail.mockReturnValue(new Promise(() => {}));
    renderGrouped();

    fireEvent.click(screen.getByTestId('stock-expand-BRW-BB'));

    expect(
      await screen.findByTestId('stock-expansion-BRW-BB'),
    ).toBeInTheDocument();
  });
});

describe('BoardCellBreakdownDialog: the real board’s sources', () => {
  /** A contribution built by hand, in exactly the shape seam B returns. */
  function serverCell(sources: BoardContribution['sources']) {
    const cell = cellOf([demand({ qty: '15' })]);
    return {
      ...cell,
      contributions: [{ ...cell.contributions[0], sources }],
    };
  }

  function renderServerCell(sources: BoardContribution['sources']) {
    const cell = serverCell(sources);
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false, gcTime: 0 } },
          })
        }
      >
        <BoardCellBreakdownDialog
          cell={cell}
          bucketLabel="28 Sep 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
    return cell;
  }

  /**
   * Deviation 3: the real board emits `timely_spo`, which the Phase 1 fixtures never produced.
   * It has to read as incoming stock rather than falling through to a bare code.
   */
  it('renders a timely SPO source as Incoming', () => {
    const cell = renderServerCell([
      {
        kind: 'timely_spo',
        qty: '15',
        location: 'BRW-BB',
        reason:
          'SPO 202601-S0003 arrives at BRW-BB on 12 Sep 2026, before the delivery date.',
        spo_number: null,
        arrival_date: null,
      },
    ]);
    openLines();

    expect(
      within(
        screen.getByTestId(`sources-pills-${cell.contributions[0].key}`),
      ).getByText(/Incoming 15/),
    ).toBeInTheDocument();
  });

  /**
   * Deviation 2: `spo_number` and `arrival_date` are always null, because the SPO and its date
   * are inside the engine's own sentence. So the sentence is what must be reachable, and
   * nothing may render a placeholder where the null fields would have gone.
   */
  it('keeps the engine’s sentence reachable, and never renders a blank for the null fields', async () => {
    const cell = renderServerCell([
      {
        kind: 'timely_spo',
        qty: '15',
        location: 'BRW-BB',
        reason:
          'SPO 202601-S0003 arrives at BRW-BB on 12 Sep 2026, before the delivery date.',
        spo_number: null,
        arrival_date: null,
      },
    ]);
    openLines();

    expect(await sourceNoteOf(cell.contributions[0].key)).toContain(
      'SPO 202601-S0003 arrives at BRW-BB on 12 Sep 2026, before the delivery date.',
    );
    expect(screen.queryByText('null')).not.toBeInTheDocument();
    expect(screen.queryByText('undefined')).not.toBeInTheDocument();
  });

  /**
   * Ladder v6 (AC-F1): a line planned as part of an order unit says so behind the info icon,
   * and the ordinary line says nothing extra.
   */
  it('names the order unit the line was planned inside, and only then', async () => {
    const cell = cellOf([demand({ qty: '10' })], { 'WESERP10B|BRW-BB': '40' });
    cell.contributions[0].unit_qty = '30';
    cell.contributions[0].unit_line_count = 2;
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false, gcTime: 0 } },
          })
        }
      >
        <BoardCellBreakdownDialog
          cell={cell}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
    openLines();

    // LADDER v8 (R-E): the unit is still one cell, but its lines no longer share ONE
    // composition - they walk one at a time, smallest first, each fed what the previous one
    // left - so the sentence names the unit and points at the per-line rows.
    expect(await sourceNoteOf(cell.contributions[0].key)).toContain(
      "Planned with 1 other line of this order for 04/09/2026: 30 in all. Each line's own composition is shown on its own row.",
    );
  });

  it('says nothing about a unit for a line planned on its own', async () => {
    const cell = cellOf([demand({ qty: '10' })], { 'WESERP10B|BRW-BB': '40' });
    cell.contributions[0].unit_qty = '10';
    cell.contributions[0].unit_line_count = 1;
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false, gcTime: 0 } },
          })
        }
      >
        <BoardCellBreakdownDialog
          cell={cell}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
    openLines();

    expect(await sourceNoteOf(cell.contributions[0].key)).not.toContain(
      'Planned with',
    );
  });

  /** Deviation 8: Pool and Borrow never reach the board; they cross locations. */
  it('reads a timely SPO as Incoming in the row strip, not as a Reserve', () => {
    const cell = renderServerCell([
      {
        kind: 'timely_spo',
        qty: '10',
        location: 'BRW-BB',
        reason: 'Incoming covers 10.',
      },
      {
        kind: 'buy',
        qty: '5',
        location: null,
        reason: 'The residual is bought.',
      },
    ]);
    openLines();

    const sourceTestId = `sources-pills-${cell.contributions[0].key}`;
    const sourcePills = within(screen.getByTestId(sourceTestId));
    expect(sourcePills.getByText('Incoming 10 at BRW-BB')).toBeInTheDocument();
    expect(sourcePills.queryByText(/Reserve 10/)).not.toBeInTheDocument();
    // At this width (jsdom's own zero) only the first pill shows; the rest folds behind
    // "+1", which opens the SAME popover every pill does and states the whole composition.
    fireEvent.click(sourcePills.getByText('+1'));
    expect(screen.getByTestId(`${sourceTestId}-popover`).textContent).toContain(
      'Buy5',
    );
  });

  /**
   * S3b, R-J: "clicking any pill ... opens the composition with each part's location,
   * quantity, kind and the option row it came from." The two adopting suites above only
   * ever assert the popover's raw `textContent` for a two-part composition; this pins each
   * PART'S OWN row (`source-popover-row-<key>-<index>`) so a location, a kind or an option
   * label going missing from one part cannot hide behind a passing substring match on the
   * whole popover.
   */
  it('lists kind, qty, location and the option row for each part of a two-part composition (AC-3b.2/AC-3b.3)', () => {
    const base = serverCell([
      {
        kind: 'reserve',
        qty: '10',
        location: 'BRW-BB',
        reason: 'Free unclaimed stock at BRW-BB covers this much.',
        rung: 'group_take',
      },
      {
        kind: 'buy',
        qty: '5',
        location: null,
        reason: 'The residual is bought.',
      },
    ]);
    const cell = {
      ...base,
      contributions: [
        {
          ...base.contributions[0],
          options: [
            { step: 'use' as const, label: 'Use own location', whole: true, chosen: true },
            { step: 'buy' as const, label: 'Buy', whole: true, chosen: false },
          ],
        },
      ],
    };
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false, gcTime: 0 } },
          })
        }
      >
        <BoardCellBreakdownDialog
          cell={cell}
          bucketLabel="28 Sep 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
    openLines();

    const key = cell.contributions[0].key;
    const sourceTestId = `sources-pills-${key}`;
    fireEvent.click(within(screen.getByTestId(sourceTestId)).getByText('+1'));

    const popover = screen.getByTestId(`${sourceTestId}-popover`);
    const reserveRow = within(popover).getByTestId(`source-popover-row-${key}-0`);
    expect(reserveRow).toHaveTextContent('Own10');
    expect(reserveRow).toHaveTextContent('at BRW-BB');
    expect(reserveRow).toHaveTextContent('Use own location');

    // The Buy carries no rung, so `optionForRung` matches nothing for it - the row states
    // its kind and quantity and no location line, and no option row rather than a wrong one.
    const buyRow = within(popover).getByTestId(`source-popover-row-${key}-1`);
    expect(buyRow).toHaveTextContent('Buy5');
    expect(buyRow).not.toHaveTextContent('at ');
    expect(buyRow).not.toHaveTextContent('Use own location');
  });
});

/**
 * WHY the Reserve is the size it is (PLAN 13.7, the fair-share amendment).
 *
 * A line may reserve from its own location only what is left after the demand the active policy
 * ranks ahead of it there, so the row has to say who was ahead and what remained. Three numbers
 * live near each other and NONE may be printed as another: the strip's `available_qty` is the
 * whole pile's position, `available_to_this_line` is what was left for THIS line at its own
 * location, and `qty_proposed_reserve` is what it actually took - which can exceed the second,
 * because the shared pool is a second source with a queue of its own.
 */
describe('BoardCellBreakdownDialog: what was left for this line', () => {
  /** The captain's own card, live: B2154-NL at BRW-BB on SO369758. */
  function captainsCell(overrides: Partial<BoardContribution> = {}) {
    const cell = cellOf([demand()]);
    return {
      ...cell,
      contributions: [
        {
          ...cell.contributions[0],
          fulfilment_location: 'BRW-BB',
          so_qty_ahead: '388',
          lines_ahead: 6,
          available_to_this_line: '627',
          sources: [
            {
              kind: 'reserve' as const,
              qty: '80',
              location: 'BRW-BB',
              warehouse_id: 'wh-BRW-BB',
              reason:
                'Free unclaimed stock at BRW-BB covers this much by the delivery date.',
            },
          ],
          ...overrides,
        },
      ],
    };
  }

  function renderCell(cell: BoardCell) {
    // The header's item-flag chips carry a Proof button (`useQuery`), so a client is needed.
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <BoardCellBreakdownDialog
          cell={cell}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
  }

  /**
   * The share note now lives inside the same tooltip the rung reason does (A3), reached by the
   * shared `sourceNoteOf` helper - so a `.toContain` rather than `.toBe` where the reason is
   * also present, and both sentences may show up in the one tooltip.
   */
  function shareNoteOf(cell: BoardCell): Promise<string> {
    return sourceNoteOf(cell.contributions[0].key);
  }

  it('says how many lines were ahead, what they wanted, and what was left here', async () => {
    const cell = captainsCell();
    renderCell(cell);
    openLines();

    expect(await shareNoteOf(cell)).toContain(
      '6 lines ahead wanting 388 · 627 left for this line at BRW-BB',
    );
  });

  it('says the line was first in the queue when nothing was ahead of it', async () => {
    const cell = captainsCell({
      so_qty_ahead: '0',
      lines_ahead: 0,
      available_to_this_line: '1015',
    });
    renderCell(cell);
    openLines();

    expect(await shareNoteOf(cell)).toContain(
      'First in the queue at BRW-BB · 1015 left for this line',
    );
  });

  it('counts a single line ahead in the singular', async () => {
    const cell = captainsCell({
      so_qty_ahead: '60',
      lines_ahead: 1,
      available_to_this_line: '40',
    });
    renderCell(cell);
    openLines();

    expect(await shareNoteOf(cell)).toContain(
      '1 line ahead wanting 60 · 40 left for this line at BRW-BB',
    );
  });

  /**
   * The reserve MAY exceed what was left at the line's own location, because the shared pool is a
   * second source with its own queue. Live: a line reading "0 left for it" still reserved 9 from
   * the pool. So the sentence states what remained and claims nothing about what may be taken.
   */
  it('states both when the reserve exceeds what was left at this line’s own location', async () => {
    const cell = captainsCell({
      so_qty_ahead: '1015',
      lines_ahead: 12,
      available_to_this_line: '0',
      sources: [
        {
          kind: 'reserve',
          qty: '9',
          location: 'BRW',
          warehouse_id: 'wh-BRW',
          reason: 'The shared pool at BRW covers this much within its cap.',
        },
      ],
    });
    renderCell(cell);
    openLines();

    // A bare site code is the shared pool, whatever the line's own location is.
    expect(
      within(
        screen.getByTestId(`sources-pills-${cell.contributions[0].key}`),
      ).getByText(/BRW 9 at BRW/),
    ).toBeInTheDocument();
    const note = await shareNoteOf(cell);
    expect(note).toContain(
      '12 lines ahead wanting 1015 · 0 left for this line at BRW-BB',
    );
    // Never a verdict on the reserve: the pool is a second source, so "0 left" does not mean
    // nothing may be reserved.
    expect(note).not.toContain('cannot');
  });

  /**
   * The whole pile and this line's share are different numbers and are never shown under one
   * label - the captain's card reads Available -8013 at BRW-BB and 627 left for this line.
   */
  it('never prints the pile’s Available as the line’s share, or the other way round', async () => {
    const cell = {
      ...captainsCell(),
      locations: [
        {
          location: 'BRW-BB',
          qty: '100',
          qty_on_hand: '1015',
          so_qty: '9028',
          spo_qty: '0',
          available_qty: '-8013',
        },
      ],
    };
    renderCell(cell);

    // The PILE, on the Stock tab: the whole warehouse's Available, and nothing about any one
    // line's share of it.
    const position =
      screen.getByTestId('cell-location-BRW-BB').textContent ?? '';
    expect(position).toContain('-8013');
    expect(position).not.toContain('627');
    expect(position).not.toContain('left for this line');

    // The LINE's share, on the other tab. Two numbers, two tabs, neither printed under the
    // other's label.
    openLines();
    const note = await shareNoteOf(cell);
    expect(note).toContain('627 left for this line');
    expect(note).not.toContain('Available');
    expect(note).not.toContain('-8013');
  });

  /**
   * Absent is absent: a line the server sent no SHARE for gets no share sentence, never a 0 -
   * even though the row still carries the rule's own reason and so still shows the icon.
   */
  it('says nothing at all when the server sent no share for the line', async () => {
    const cell = captainsCell({
      so_qty_ahead: undefined,
      lines_ahead: undefined,
      available_to_this_line: undefined,
    });
    renderCell(cell);
    openLines();

    expect(await shareNoteOf(cell)).not.toContain('left for this line');
  });

  /** A line with no location has no own pile to be queued at, so it has nothing to say here. */
  it('says nothing for a line whose sales order states no location', async () => {
    const cell = cellOf([demand({ fulfilment_location: null })]);
    const unplannable = {
      ...cell,
      contributions: [
        {
          ...cell.contributions[0],
          so_qty_ahead: '0',
          lines_ahead: 0,
          available_to_this_line: '0',
        },
      ],
    };
    renderCell(unplannable);
    openLines();

    expect(await shareNoteOf(unplannable)).not.toContain('left for this line');
  });

  /** The fixture emits the same three fields, queued the way the server queues them. */
  it('reads the queue the board fixture built, line by line', async () => {
    const cell = cellOf(
      [
        demand({
          line_no: 1,
          so_number: 'SO000001',
          sales_order_id: 'so-a',
          qty: '60',
        }),
        demand({
          line_no: 2,
          so_number: 'SO000002',
          sales_order_id: 'so-b',
          qty: '40',
        }),
      ],
      { 'WESERP10B|BRW-BB': '100' },
    );
    renderCell(cell);
    openLines();

    expect(await sourceNoteOf(cell.contributions[0].key)).toContain(
      'First in the queue at BRW-BB · 100 left for this line',
    );
    expect(await sourceNoteOf(cell.contributions[1].key)).toContain(
      '1 line ahead wanting 60 · 40 left for this line at BRW-BB',
    );
  });
});

/**
 * Bulk decisions (the captain, pointing at the users list: "I should have a bulk decision
 * function ... so I can bulk approve / reject, like I can select all then approve / reject").
 *
 * The screenshot behind it was eleven identical rows: deciding those one at a time is eleven
 * presses to say one thing. Same idiom as `/user-management/users` - `buildSelectColumn` with a
 * header select-all, and the actions appearing in a strip while rows are selected.
 *
 * AMEND stays per row, and I agree with the instruction: an amendment is a quantity and a
 * reason for ONE line, and a single quantity applied to eleven different owed quantities is not
 * a decision anybody meant to make.
 */
describe('BoardCellBreakdownDialog: bulk approve and reject', () => {
  function threeLines() {
    return [
      demand({ line_no: 1, so_number: 'SO000001', sales_order_id: 'so-a' }),
      demand({ line_no: 2, so_number: 'SO000002', sales_order_id: 'so-b' }),
      demand({ line_no: 3, so_number: 'SO000003', sales_order_id: 'so-c' }),
    ];
  }

  function selectAll() {
    fireEvent.click(
      screen.getByRole('checkbox', { name: 'Select all rows on this page' }),
    );
  }

  it('offers no bulk action until something is selected', () => {
    renderDialog(threeLines());
    expect(
      screen.queryByRole('button', { name: 'Approve selected' }),
    ).not.toBeInTheDocument();
  });

  it('approves every selected row in one press, and says how many are selected', () => {
    const { onDecide } = renderDialog(threeLines());
    openLines();

    selectAll();
    expect(screen.getByText('3 selected')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Approve selected' }));

    expect(onDecide).toHaveBeenCalledTimes(3);
    for (const call of onDecide.mock.calls) {
      // D11: an approval CARRIES the engine's composition. A bare `{verdict: 'approved'}`
      // confirms nothing that was proposed, and the sales order page reads it as Decided "-".
      expect(call[1]).toEqual(
        expect.objectContaining({ verdict: 'approved', buy_qty: '100' }),
      );
    }
  });

  it('rejects every selected row in one press, with the same reason a single reject carries', () => {
    const { onDecide } = renderDialog(threeLines());
    openLines();

    selectAll();
    fireEvent.click(screen.getByRole('button', { name: 'Reject selected' }));

    expect(onDecide).toHaveBeenCalledTimes(3);
    for (const call of onDecide.mock.calls) {
      expect(call[1]).toEqual({
        verdict: 'rejected',
        reason: 'Rejected on the planning board.',
      });
    }
  });

  it('clears the selection once the bulk decision is made', () => {
    renderDialog(threeLines());
    openLines();

    selectAll();
    fireEvent.click(screen.getByRole('button', { name: 'Approve selected' }));

    expect(screen.queryByText('3 selected')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Approve selected' }),
    ).not.toBeInTheDocument();
  });

  it('select-all covers exactly the rows of this cell', () => {
    const { onDecide } = renderDialog(threeLines());
    openLines();

    selectAll();
    fireEvent.click(screen.getByRole('button', { name: 'Approve selected' }));

    const keys = onDecide.mock.calls.map((call) => call[0]).sort();
    expect(keys).toEqual(
      [
        'so-a|1|WESERP10B|2026-08-31',
        'so-b|2|WESERP10B|2026-08-31',
        'so-c|3|WESERP10B|2026-08-31',
      ].sort(),
    );
  });

  it('will not select a line that cannot be decided, and says why on its own checkbox', () => {
    const { onDecide } = renderDialog([
      demand({ line_no: 1, so_number: 'SO000001', sales_order_id: 'so-a' }),
      demand({
        line_no: 2,
        so_number: 'SO000002',
        sales_order_id: 'so-b',
        fulfilment_location: null,
      }),
    ]);
    openLines();

    selectAll();
    expect(screen.getByText('1 selected')).toBeInTheDocument();
    // Twice on purpose: on the checkbox that will not tick, and on the Decision cell that
    // offers no verb. buildSelectColumn's own rule - the SAME string, so the two cannot drift
    // into two explanations of one rule.
    expect(
      screen.getAllByTitle(
        'This line cannot be decided here: its sales order states no fulfilment location.',
      ),
    ).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: 'Approve selected' }));
    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide.mock.calls[0][0]).toBe('so-a|1|WESERP10B|2026-08-31');
  });

  it('leaves the per-row decision alone: bulk is an addition, not a replacement', () => {
    const { onDecide } = renderDialog(threeLines());
    openLines();

    fireEvent.click(
      screen.getByRole('checkbox', { name: 'Select SO000001 line 1' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Approve selected' }));

    // Only the ticked row carries the bulk verdict.
    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith(
      'so-a|1|WESERP10B|2026-08-31',
      expect.objectContaining({ verdict: 'approved', buy_qty: '100' }),
    );

    // Row 2 is untouched: still Suggested, and still expandable and decidable on its own -
    // the bulk verb is an addition, never a replacement for the per-row decision.
    expect(
      screen.getByTestId('decision-pill-so-b|2|WESERP10B|2026-08-31'),
    ).toHaveTextContent('Suggested');

    fireEvent.click(screen.getByText('SO000002'));
    expect(
      screen.getByRole('button', { name: 'Save decision' }),
    ).toBeInTheDocument();
  });
});

/**
 * D14 (the captain: a quick save for the lines that need nothing amended, and an Undo for the
 * one line a quick save was wrong for). "Save as suggested" writes the ENGINE's own
 * composition - the same object an untouched Save on the row would post - so a bulk quick
 * save is byte-identical to opening every one of those rows and pressing Save.
 */
describe('BoardCellBreakdownDialog: quick save as suggested and per-line undo', () => {
  function threeLines() {
    return [
      demand({ line_no: 1, so_number: 'SO000001', sales_order_id: 'so-a' }),
      demand({ line_no: 2, so_number: 'SO000002', sales_order_id: 'so-b' }),
      demand({ line_no: 3, so_number: 'SO000003', sales_order_id: 'so-c' }),
    ];
  }

  function selectAll() {
    fireEvent.click(
      screen.getByRole('checkbox', { name: 'Select all rows on this page' }),
    );
  }

  it('saves the ticked rows with the engine composition, and clears the selection', () => {
    const { onDecide } = renderDialog(threeLines());
    openLines();

    selectAll();
    fireEvent.click(screen.getByRole('button', { name: 'Approve selected' }));

    expect(onDecide).toHaveBeenCalledTimes(3);
    for (const call of onDecide.mock.calls) {
      expect(call[1]).toEqual(
        expect.objectContaining({ verdict: 'approved', buy_qty: '100' }),
      );
    }
    expect(screen.queryByText('3 selected')).not.toBeInTheDocument();
  });

  it('has ONE control for the bulk quick save, not a second one beside Approve', () => {
    renderDialog(threeLines());
    openLines();

    selectAll();
    // "Approve selected" IS the quick save since D11 (an approval carries the composition),
    // so a "Save as suggested (N)" beside it was one verb with two buttons.
    expect(
      screen.queryByRole('button', { name: /^Save as suggested/ }),
    ).not.toBeInTheDocument();
  });

  it('will not tick a line that is already saved, and says why on its own checkbox', () => {
    const draft: BoardDraft = {
      'so-b|2|WESERP10B|2026-08-31': { verdict: 'approved', buy_qty: '100' },
    };
    const { onDecide } = renderDialog(threeLines(), {}, draft);
    openLines();

    selectAll();
    expect(screen.getByText('2 selected')).toBeInTheDocument();
    expect(
      screen.getAllByTitle('Already saved. Undo it before saving it again.'),
    ).not.toHaveLength(0);

    fireEvent.click(screen.getByRole('button', { name: 'Approve selected' }));

    // The saved row keeps whatever was saved on it: a quick save would overwrite an amended
    // composition with the engine's own, which is what Undo exists for.
    expect(onDecide).toHaveBeenCalledTimes(2);
    expect(onDecide.mock.calls.map((call) => call[0]).sort()).toEqual(
      ['so-a|1|WESERP10B|2026-08-31', 'so-c|3|WESERP10B|2026-08-31'].sort(),
    );
  });

  /**
   * D15: both bulk verbs post through `onDecideMany` now, not a loop of `onDecide` calls -
   * the panel's own quiet-bulk path, one toast for the whole press instead of D14's one per
   * line. `renderDialog`'s own `onDecideMany` mock still composes each key's suggestion and
   * forwards it to `onDecide` (the assertions above and below read that), but the PROP
   * actually reached has to be `onDecideMany` for that one-toast behaviour to exist at all.
   */
  it('Approve selected posts through onDecideMany, not a loop of onDecide', () => {
    const { onDecideMany } = renderDialog(threeLines());
    openLines();

    selectAll();
    fireEvent.click(screen.getByRole('button', { name: 'Approve selected' }));

    expect(onDecideMany).toHaveBeenCalledTimes(1);
    expect(onDecideMany.mock.calls[0][0].sort()).toEqual(
      [
        'so-a|1|WESERP10B|2026-08-31',
        'so-b|2|WESERP10B|2026-08-31',
        'so-c|3|WESERP10B|2026-08-31',
      ].sort(),
    );
  });

  it('Save all suggested reaches every selectable line with no selection needed', () => {
    const { onDecide, onDecideMany } = renderDialog(threeLines());
    openLines();

    fireEvent.click(
      screen.getByRole('button', { name: 'Save all suggested' }),
    );

    expect(onDecideMany).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledTimes(3);
    const keys = onDecide.mock.calls.map((call) => call[0]).sort();
    expect(keys).toEqual(
      [
        'so-a|1|WESERP10B|2026-08-31',
        'so-b|2|WESERP10B|2026-08-31',
        'so-c|3|WESERP10B|2026-08-31',
      ].sort(),
    );
  });

  it('Save all suggested skips a confirmed line and a line already saved', () => {
    const draft: BoardDraft = {
      'so-b|2|WESERP10B|2026-08-31': { verdict: 'approved' },
    };
    const { onDecide } = renderDialog(
      [
        demand({ line_no: 1, so_number: 'SO000001', sales_order_id: 'so-a' }),
        demand({ line_no: 2, so_number: 'SO000002', sales_order_id: 'so-b' }),
        demand({
          line_no: 3,
          so_number: 'SO000003',
          sales_order_id: 'so-c',
          decision: {
            revision_no: 1,
            timely_spo_qty: '0',
            reserve: [],
            borrow: [],
            buy_qty: '100',
          },
        }),
      ],
      {},
      draft,
    );
    openLines();

    fireEvent.click(
      screen.getByRole('button', { name: 'Save all suggested' }),
    );

    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide.mock.calls[0][0]).toBe('so-a|1|WESERP10B|2026-08-31');
  });

  it('offers no per-line Undo until a line carries a draft', () => {
    renderDialog(threeLines());
    openLines();

    expect(
      screen.queryByRole('button', { name: /^Undo SO/ }),
    ).not.toBeInTheDocument();
  });

  it('Undo on one saved line removes its draft and leaves the others alone', () => {
    const draft: BoardDraft = {
      'so-a|1|WESERP10B|2026-08-31': { verdict: 'approved' },
    };
    const { onDecide } = renderDialog(threeLines(), {}, draft);
    openLines();

    fireEvent.click(screen.getByRole('button', { name: 'Undo SO000001 line 1' }));

    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith('so-a|1|WESERP10B|2026-08-31', null);
  });

  /**
   * PLAN-board-change-proposed-pill, fix round: the grid's own "Contributing lines" table
   * carries the same rule the List view's Verdict column does - this is the dialog the grid
   * opens the contributing-lines table inside, so the two must agree.
   */
  it('a pre-marked line reads "Change proposed" and offers no Undo; a saved line still reads "Saved" with its Undo', () => {
    const draft: BoardDraft = {
      'so-a|1|WESERP10B|2026-08-31': { verdict: 'approved', preMarked: true },
      'so-b|2|WESERP10B|2026-08-31': { verdict: 'approved' },
    };
    renderDialog(
      [
        demand({ line_no: 1, so_number: 'SO000001', sales_order_id: 'so-a' }),
        demand({ line_no: 2, so_number: 'SO000002', sales_order_id: 'so-b' }),
      ],
      {},
      draft,
    );
    openLines();

    expect(
      screen.getByTestId('decision-pill-so-a|1|WESERP10B|2026-08-31'),
    ).toHaveTextContent('Change proposed');
    expect(
      screen.queryByRole('button', { name: 'Undo SO000001 line 1' }),
    ).not.toBeInTheDocument();

    expect(
      screen.getByTestId('decision-pill-so-b|2|WESERP10B|2026-08-31'),
    ).toHaveTextContent('Saved');
    expect(
      screen.getByRole('button', { name: 'Undo SO000002 line 2' }),
    ).toBeInTheDocument();
  });
});

/**
 * HOW the decision was reached (the captain: "can you justify how you arrive at the buy, like
 * what's the process you have gone through: checking the available quantity first, deciding
 * whether to reserve it or not, then checking the SPO quantity, then checking whether can
 * borrow ... need more justification", then "the justification needs to be STRUCTURED instead
 * of plain text explaining, you can put it under the tooltip").
 *
 * So the ladder is a table of STEPS, every rung of it, including the rungs that gave nothing.
 */
describe('BoardCellBreakdownDialog: how the decision was reached', () => {
  function openTrail(key: string) {
    fireEvent.click(screen.getByTestId(`trail-info-${key}`));
  }

  function stepCells(key: string, kind: string): (string | null)[] {
    return [
      ...screen.getByTestId(`trail-step-${key}-${kind}`).querySelectorAll('td'),
    ].map((node) => node.textContent);
  }

  it('asks the four questions in order, and then Buy (AC-V1)', () => {
    const cell = cellOf([demand({ qty: '100' })], {
      'WESERP10B|BRW-BB': '100',
    });
    renderDialog([demand({ qty: '100' })], { 'WESERP10B|BRW-BB': '100' });
    openLines();
    const key = cell.contributions[0].key;

    const button = screen.getByTestId(`trail-info-${key}`);
    expect(button).toHaveAttribute(
      'aria-label',
      'How this decision was reached',
    );
    openTrail(key);

    const questions = [
      ...screen
        .getByTestId(`trail-${key}`)
        .querySelectorAll('tbody tr[data-step]'),
    ].map((row) => row.querySelectorAll('td')[1]?.textContent);
    expect(questions).toEqual([
      'Can we use our location?',
      'Can we take from the pool?',
      'Can we borrow from another location?',
      "Can we borrow from the same agent's other order in this group?",
      'Buy the rest?',
    ]);
  });

  it('answers each question with a word, a quantity and a place', () => {
    // The whole-line rule: the pool holds exactly what is owed, so it is proposed in full
    // and Buy answers No rather than showing a partial mix.
    const cell = cellOf([demand({ qty: '100' })], {
      'WESERP10B|BRW-BB': '100',
    });
    renderDialog([demand({ qty: '100' })], { 'WESERP10B|BRW-BB': '100' });
    openLines();
    const key = cell.contributions[0].key;
    openTrail(key);

    // # | Question | Answer | Took | From
    expect(stepCells(key, 'pool')).toEqual([
      '2',
      'Can we take from the pool?',
      'Yes',
      '100',
      'BRW-BB',
    ]);
    expect(stepCells(key, 'buy')).toEqual([
      '5',
      'Buy the rest?',
      'No',
      '0',
      '-',
    ]);
  });

  it('carries the queue on question 1, and on no other question', () => {
    // The read-only own-location strip is folded into question 1 under ladder v5: it is the
    // one question with a queue, and the one `QueueLink`'s dialog can open.
    const lines = [
      demand({
        line_no: 1,
        so_number: 'SO000001',
        sales_order_id: 'so-a',
        qty: '60',
      }),
      demand({
        line_no: 2,
        so_number: 'SO000002',
        sales_order_id: 'so-b',
        qty: '40',
      }),
    ];
    const cell = cellOf(lines, { 'WESERP10B|BRW-BB': '70' });
    renderDialog(lines, { 'WESERP10B|BRW-BB': '70' });
    openLines();
    const second = cell.contributions[1].key;
    openTrail(second);

    expect(stepCells(second, 'own').slice(1, 3)).toEqual([
      'Can we use our location?',
      'No',
    ]);
    expect(screen.getByTestId(`trail-queue-${second}`).textContent).toBe(
      'View the queue (1 ahead)',
    );
    expect(screen.getAllByTestId(`trail-queue-${second}`)).toHaveLength(1);
  });

  it('answers a question a rule skipped rather than leaving it out', () => {
    const cell = cellOf([demand({ qty: '100' })], { 'WESERP10B|BRW-BB': '40' });
    renderDialog([demand({ qty: '100' })], { 'WESERP10B|BRW-BB': '40' });
    openLines();
    const key = cell.contributions[0].key;
    openTrail(key);

    expect(stepCells(key, 'own')).toContain('No');
    expect(screen.getByTestId(`trail-why-${key}-own`).textContent).toContain(
      'no ownership group',
    );
  });

  it('says there is no plan at all for a line whose order states no location', () => {
    const cell = cellOf([demand({ fulfilment_location: null })]);
    renderDialog([demand({ fulfilment_location: null })]);
    openLines();
    const key = cell.contributions[0].key;
    openTrail(key);

    expect(screen.getByTestId(`trail-${key}`).textContent).toContain(
      'No plan: No fulfilment location on the sales order line, so nothing can be sourced for it.',
    );
  });

  /**
   * WHY each rung ended that way, and a press away to WHO is in the queue.
   *
   * The captain, reading the numbers: "what does this mean? why do the orders stand ahead of me?
   * why? and why is the donor offered but I did not take, why?" So every rung carries one plain
   * sentence naming the count and the reason; the rung with a queue also opens the whole thing,
   * because a plain sentence cannot show rank ("why do the orders stand ahead of me?").
   */
  function rankedCell(
    lines: BoardDemandLine[],
    freeStock: Record<string, string> = {},
  ) {
    return buildBoard(lines, {
      today: TODAY,
      freeStock,
      policy: PREVIEW_POLICY,
    }).cells[0];
  }

  function renderCell(cell: BoardCell) {
    // The queue the trail offers is a READ, so this branch needs a client. The rest of the
    // suite deliberately renders without one: nothing else here fetches.
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <BoardCellBreakdownDialog
          cell={cell}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
  }

  function queueOfFive() {
    return [1, 2, 3, 4, 5].map((index) =>
      demand({
        sales_order_id: `so-${index}`,
        so_number: `SO40000${index}`,
        line_no: index,
        qty: '100',
        required_date: `2026-09-0${index}`,
      }),
    );
  }

  it('says in words why each question ended the way it did', () => {
    const cell = rankedCell(queueOfFive(), { 'WESERP10B|BRW-BB': '40' });
    renderCell(cell);
    openLines();
    const last = cell.contributions[cell.contributions.length - 1].key;
    openTrail(last);

    expect(screen.getByTestId(`trail-why-${last}-own`).textContent).toContain(
      'no ownership group',
    );
    expect(screen.getByTestId(`trail-why-${last}-buy`).textContent).toBe(
      'Nothing left to take, so the remainder is bought.',
    );
  });

  it('does not repeat the queue as a list - the link already gives the count', () => {
    // The captain: "the explanation ... Ahead of this line ... is not needed, cause you already
    // told me how many lines ahead, that's fine" - the count stays, the repeated list goes.
    const cell = rankedCell(queueOfFive(), { 'WESERP10B|BRW-BB': '40' });
    renderCell(cell);
    openLines();
    const last = cell.contributions[cell.contributions.length - 1].key;
    openTrail(last);

    const trail = screen.getByTestId(`trail-${last}`);
    expect(trail.textContent).not.toContain('Ahead of this line');
    expect(
      trail.querySelectorAll('[data-testid^="trail-ahead-line-"]'),
    ).toHaveLength(0);
    expect(screen.queryByTestId(`trail-ahead-${last}`)).not.toBeInTheDocument();
    expect(screen.getByTestId(`trail-queue-${last}`)).toBeInTheDocument();
  });

  it('offers the whole queue rather than only the three it names', async () => {
    getPileQueue.mockReturnValue(new Promise(() => {}));
    const cell = rankedCell(queueOfFive(), { 'WESERP10B|BRW-BB': '40' });
    const asking = cell.contributions[cell.contributions.length - 1];
    renderCell(cell);
    openLines();
    openTrail(asking.key);

    const button = screen.getByTestId(`trail-queue-${asking.key}`);
    expect(button.textContent).toBe('View the queue (4 ahead)');

    fireEvent.click(button);

    // Asked by IDS and on BEHALF of this line: both change what comes back.
    await waitFor(() =>
      expect(getPileQueue).toHaveBeenCalledWith(
        asking.product_id,
        asking.fulfilment_warehouse_id,
        asking.line_id,
      ),
    );
  });

  it('says a Borrow was found and left alone, and names the donors', () => {
    const cell = cellOf([demand({ qty: '100' })]);
    const contribution = cell.contributions[0];
    const borrow = contribution.trail?.find(
      (step) => step.kind === 'cross_group_borrow',
    );
    Object.assign(borrow ?? {}, {
      note: 'MWH-IB 20 · BRW 5',
      why: 'Borrowing is never automatic: a person names the donor and the reason. Use Amend to borrow.',
    });
    renderCell(cell);
    openLines();
    openTrail(contribution.key);

    const why = screen.getByTestId(
      `trail-why-${contribution.key}-cross_group_borrow`,
    );
    expect(why.textContent).toContain('Use Amend to borrow');
    expect(screen.getByText('MWH-IB 20 · BRW 5')).toBeInTheDocument();
  });

  it('leaves the source strip and the share note exactly as they were, and shows no Contested chip', async () => {
    const lines = [
      demand({
        sales_order_id: 'so-a',
        so_number: 'SO403340',
        line_no: 1,
        qty: '100',
        required_date: '2026-09-04',
      }),
      demand({
        sales_order_id: 'so-b',
        so_number: 'SO398322',
        line_no: 2,
        qty: '100',
        required_date: '2026-09-02',
      }),
    ];
    const freeStock = { 'WESERP10B|BRW-BB': '100' };
    const cell = cellOf(lines, freeStock);
    renderDialog(lines, freeStock);
    openLines();

    expect(
      within(
        screen.getByTestId(`sources-pills-${cell.contributions[0].key}`),
      ).getByText(/Own 100/),
    ).toBeInTheDocument();
    // The word is gone from the screen (the captain, 27 Aug); the flag stays on the row.
    expect(screen.queryByText('Contested')).toBeNull();
    // Both rows still carry a share sentence, now behind the icon rather than always visible.
    expect(await sourceNoteOf(cell.contributions[0].key)).toContain(
      'left for this line',
    );
    expect(await sourceNoteOf(cell.contributions[1].key)).toContain(
      'left for this line',
    );
  });

  /**
   * The flags the ladder judged the item on, and the pool pile behind rung 2.
   *
   * The captain, 19 August 2026: "where is the consideration of dealer hot selling / project hot
   * selling / discontinued, to see if we can take from BRW?" - and, on `Pool BRW | Had 0` beside
   * an Inventory screen showing `Available 1`: "why it shows 0?"
   */
  it('opens the pool rung with no hot-selling verdict in words, and shows no chip for an ordinary item', () => {
    const cell = cellOf([demand({ qty: '100' })], { 'WESERP10B|BRW-BB': '40' });
    renderDialog([demand({ qty: '100' })], { 'WESERP10B|BRW-BB': '40' });
    openLines();
    const key = cell.contributions[0].key;
    openTrail(key);

    expect(screen.getByTestId(`trail-why-${key}-pool`).textContent).toBe(
      'BRW-BB offers 40; this line takes 40.',
    );
    // An unflagged item is the ordinary case: no badge saying so.
    expect(screen.queryByTestId(`trail-flags-${key}`)).not.toBeInTheDocument();
  });

  it('shows the item flags as chips beside the title, each naming its evidence', () => {
    const cell = cellOf([demand({ qty: '100' })]);
    const contribution = cell.contributions[0];
    contribution.item_flags = {
      dealer_hot_selling: true,
      dealer_hot_selling_where: ['BRW', 'BRW-IB'],
      project_hot_selling: false,
      project_hot_selling_where: [],
      dealer_classified: true,
      project_classified: false,
      discontinued: true,
      retail_classification_available: true,
    };
    renderCell(cell);
    openLines();
    openTrail(contribution.key);

    const chips = screen.getByTestId(`trail-flags-${contribution.key}`);
    expect(chips.textContent).toBe('Dealer hot-sellingDiscontinued');
    expect(
      screen.getByTestId(`trail-flag-${contribution.key}-dealer-hot-selling`),
    ).toHaveAttribute(
      // LADDER v8 (R-A): the chip names the classification and no longer promises that the
      // pool is kept for retail - the SHARE is what keeps stock for dealers now, and it
      // keeps it from every project line whatever the item's class.
      'title',
      'Dealer hot-selling at BRW, BRW-IB.',
    );
    expect(
      screen.queryByTestId(`trail-flag-${contribution.key}-not-classified`),
    ).not.toBeInTheDocument();
    expect(chips.textContent).not.toMatch(/ABC/);
  });

  it('AC-23: the discontinued chip reads "Discontinued" and its title no longer says a Buy needs a reason', () => {
    const cell = cellOf([demand({ qty: '100' })]);
    const contribution = cell.contributions[0];
    contribution.item_flags = {
      dealer_hot_selling: false,
      dealer_hot_selling_where: [],
      project_hot_selling: false,
      project_hot_selling_where: [],
      dealer_classified: false,
      project_classified: false,
      discontinued: true,
      retail_classification_available: true,
    };
    renderCell(cell);
    openLines();
    openTrail(contribution.key);

    const chip = screen.getByTestId(`trail-flag-${contribution.key}-discontinued`);
    expect(chip).toHaveTextContent('Discontinued');
    expect(chip).toHaveAttribute('title', 'Discontinued');
    expect(chip.getAttribute('title')).not.toMatch(/needs a reason/);
  });

  it('shows a project hot-selling chip alongside the dealer one when both flags are set', () => {
    const cell = cellOf([demand({ qty: '100' })]);
    const contribution = cell.contributions[0];
    contribution.item_flags = {
      dealer_hot_selling: true,
      dealer_hot_selling_where: ['BRW'],
      project_hot_selling: true,
      project_hot_selling_where: ['BRW-BB'],
      dealer_classified: true,
      project_classified: true,
      discontinued: false,
      retail_classification_available: true,
    };
    renderCell(cell);
    openLines();
    openTrail(contribution.key);

    const chips = screen.getByTestId(`trail-flags-${contribution.key}`);
    expect(chips.textContent).toBe('Dealer hot-sellingProject hot-selling');
  });

  it('shows a "Cold at" chip for a class that is classified but never ranked A', () => {
    const cell = cellOf([demand({ qty: '100' })]);
    const contribution = cell.contributions[0];
    contribution.item_flags = {
      dealer_hot_selling: false,
      dealer_hot_selling_where: [],
      project_hot_selling: false,
      project_hot_selling_where: [],
      dealer_classified: true,
      project_classified: true,
      discontinued: false,
      retail_classification_available: true,
    };
    renderCell(cell);
    openLines();
    openTrail(contribution.key);

    const chips = screen.getByTestId(`trail-flags-${contribution.key}`);
    expect(chips.textContent).toBe('Cold at retailCold at project');
  });

  it('says "Not classified" rather than reading an unclassified item as cold', () => {
    const cell = cellOf([demand({ qty: '100' })]);
    const contribution = cell.contributions[0];
    contribution.item_flags = {
      dealer_hot_selling: false,
      dealer_hot_selling_where: [],
      project_hot_selling: false,
      project_hot_selling_where: [],
      dealer_classified: false,
      project_classified: false,
      discontinued: false,
      retail_classification_available: false,
    };
    renderCell(cell);
    openLines();
    openTrail(contribution.key);

    expect(
      screen.getByTestId(`trail-flags-${contribution.key}`).textContent,
    ).toBe('Not classified');
  });

  it('shows no chips at all on a line the ladder never walked', () => {
    const cell = cellOf([demand({ fulfilment_location: null })]);
    const key = cell.contributions[0].key;
    expect(cell.contributions[0].item_flags).toBeNull();
    renderDialog([demand({ fulfilment_location: null })]);
    openLines();
    openTrail(key);

    expect(screen.queryByTestId(`trail-flags-${key}`)).not.toBeInTheDocument();
  });

  it('lays the pool pile out under the pool rung - on hand, SO, SPO, available, free, claimed ahead, left', () => {
    const cell = cellOf([demand({ qty: '1' })]);
    const contribution = cell.contributions[0];
    const pool = contribution.trail?.find((step) => step.kind === 'pool');
    // The captain's B2155-NL-BLUE rung: BRW holds 1, its own line ahead claims it, 0 left.
    Object.assign(pool ?? {}, {
      location: 'BRW',
      warehouse_id: 'wh-BRW',
      answer: 'no',
      took: '0',
      from: null,
      note: null,
      why: "BRW holds 1 on hand (Available 1 in stock), but BRW's own orders ranked ahead of this line claim 1, so 0 is left.",
      pool: {
        location: 'BRW',
        warehouse_id: 'wh-BRW',
        on_hand: '1',
        so_qty: '1',
        spo_qty: '0',
        available: '0',
        reserved: '0',
        free: '1',
        claimed_ahead_qty: '1',
        claimed_ahead_lines: 1,
        left: '0',
        reorder_level: '0',
        cap: null,
      },
    });
    renderCell(cell);
    openLines();
    openTrail(contribution.key);

    expect(stepCells(contribution.key, 'pool').slice(1, 4)).toEqual([
      'Can we take from the pool?',
      'No',
      '0',
    ]);
    expect(
      screen.getByTestId(`trail-why-${contribution.key}-pool`).textContent,
    ).toBe(
      "BRW holds 1 on hand (Available 1 in stock), but BRW's own orders ranked ahead of this line claim 1, so 0 is left.",
    );
    const table = screen.getByTestId(`trail-pool-${contribution.key}`);
    const headers = [...table.querySelectorAll('th')].map(
      (node) => node.textContent,
    );
    expect(headers).toEqual([
      'On hand',
      'SO qty',
      'SPO qty',
      'Available',
      'Free',
      'Claimed ahead',
      'Left',
    ]);
    const values = [...table.querySelectorAll('td')].map(
      (node) => node.textContent,
    );
    expect(values).toEqual(['1', '1', '0', '0', '1', '1 (1 line)', '0']);
  });

  it('shows no pool sub-table when there is no shared pool', () => {
    const cell = cellOf([demand({ qty: '100' })]);
    renderDialog([demand({ qty: '100' })]);
    openLines();
    const key = cell.contributions[0].key;
    openTrail(key);

    expect(screen.getByTestId(`trail-why-${key}-pool`).textContent).toBe(
      'No shared pool holds this product.',
    );
    expect(screen.queryByTestId(`trail-pool-${key}`)).not.toBeInTheDocument();
  });
});

/**
 * The expansions belong to the CELL that is open, not to the dialog.
 *
 * The dialog stays mounted while the board points it at one cell after another, so an expansion
 * left open would show the previous cell's documents underneath the new cell's location row -
 * the same product code and warehouse in the panel heading, entirely the wrong position.
 */
describe('BoardCellBreakdownDialog: the stock expansions belong to the cell', () => {
  function stockedCell(itemCode: string, bucket: string): BoardCell {
    const cell = cellOf([demand()]);
    return {
      ...cell,
      item_code: itemCode,
      bucket_key: bucket,
      locations: [
        {
          ...cell.locations[0],
          location: 'BRW-BB',
          product_id: 'prod-1',
          warehouse_id: 'wh-1',
          qty_on_hand: '478',
        },
      ],
    };
  }

  function dialogFor(cell: BoardCell) {
    return (
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false, gcTime: 0 } },
          })
        }
      >
        <BoardCellBreakdownDialog
          cell={cell}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>
    );
  }

  it('re-lands on its own line for the NEW cell, not the old one\'s leftover panel (AC-3.1)', async () => {
    // Left in flight on purpose: what is under test is which cell the expansion belongs to, and
    // the documents themselves are `StockDocumentsPanel`'s own suite.
    getStockDetail.mockReturnValue(new Promise(() => {}));

    const { rerender } = render(
      dialogFor(stockedCell('WESERP10B', '2026-08-31')),
    );

    // AC-3.1: the FIRST cell lands on "This line" without a click - the own-location section
    // is already expanded when the dialog opens.
    expect(
      await screen.findByTestId('stock-expansion-BRW-BB'),
    ).toBeInTheDocument();
    const queriesForFirstCell = getStockDetail.mock.calls.length;
    expect(queriesForFirstCell).toBeGreaterThan(0);

    rerender(dialogFor(stockedCell('B2155-NL-BLUE', '2026-09-28')));

    // `CellStockTable` remounted (keyed by the cell), so this is a FRESH panel landing on the
    // NEW cell's own line - the same auto-land AC-3.1 asks for on every open - rather than the
    // first cell's documents surviving, stale, under the new cell's row.
    expect(
      await screen.findByTestId('stock-expansion-BRW-BB'),
    ).toBeInTheDocument();
    expect(getStockDetail.mock.calls.length).toBeGreaterThan(queriesForFirstCell);
  });
});

/**
 * A line an ACTIVE decision already covers (PLAN 13.4).
 *
 * Live on SO403765 line 1: the planner confirmed "borrow 10 from MWH-IB, buy 33" and the board
 * went on offering Approve / Amend / Reject beside a FRESH proposal of Buy 43, a share note
 * reading "First in the queue at BRW-BB · 0 left for this line", and a trail whose first rung
 * said the pile had offered nothing. Every one of those is a statement about a contest the line
 * had already left.
 *
 * A covered row states what was decided, and offers the only decision that is left: Amend.
 */
describe('BoardCellBreakdownDialog: a line a decision already covers', () => {
  const frozen = {
    revision_no: 1,
    confirmed_at: '2026-08-18T02:00:00',
    timely_spo_qty: '0',
    reserve: [],
    borrow: [
      {
        source: 'other_location' as const,
        warehouse_id: 'wh-mwh-ib',
        location: 'MWH-IB',
        donor_project_id: null,
        qty: '10',
        reason: 'The other site can wait a week.',
      },
    ],
    buy_qty: '33',
    amend_reason: 'Borrowed rather than bought, agreed with the other site.',
  };

  const covered = (overrides: Partial<BoardDemandLine> = {}) =>
    demand({ qty: '43', decision: frozen, ...overrides });

  it('shows the frozen composition in the source strip, naming where a borrow came from', () => {
    renderDialog([covered()]);
    openLines();

    const testId = 'sources-pills-so-a|1|WESERP10B|2026-08-31';
    const sourcePills = within(screen.getByTestId(testId));
    expect(
      sourcePills.getByText('Borrow 10 from MWH-IB'),
    ).toBeInTheDocument();
    // At this width (jsdom's own zero) only the first pill shows; the rest folds behind
    // "+1", which opens the SAME popover every pill does and states the whole composition.
    fireEvent.click(sourcePills.getByText('+1'));
    expect(screen.getByTestId(`${testId}-popover`).textContent).toContain(
      'Buy33',
    );
  });

  it('says nothing about a queue it is not in', async () => {
    // "0 left for this line" is a claim about a contest. A covered line left the contest, which
    // is exactly why the pile's own queue does not hold it - even though the frozen composition
    // still carries its own reasons behind the icon.
    renderDialog([covered()]);
    openLines();

    expect(await sourceNoteOf('so-a|1|WESERP10B|2026-08-31')).not.toContain(
      'left for this line',
    );
  });

  it('replaces the ladder with the one fact there is: when it was confirmed', () => {
    renderDialog([covered()]);
    openLines();

    fireEvent.click(
      screen.getByTestId('trail-info-so-a|1|WESERP10B|2026-08-31'),
    );

    const trail = screen.getByTestId('trail-so-a|1|WESERP10B|2026-08-31');
    expect(trail.textContent).toContain('Confirmed in revision 1');
    expect(trail.querySelector('table')).toBeNull();
  });

  it('seeds the amendment from the frozen composition, not from a fresh proposal', () => {
    renderDialog([covered()]);
    openLines();

    fireEvent.click(screen.getByText('SO403340'));
    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));

    // The borrow the planner made is IN the editor, at the quantity they made it, rather
    // than whatever the engine would propose for an undecided line.
    expect(screen.getByLabelText('Borrow from MWH-IB')).toHaveValue(10);
    // Buy is a whole-line switch (AC-L5) and this composition carries stock, so it is off.
    expect(screen.getByLabelText('Buy the whole line')).not.toBeChecked();
  });

  it('behaves like any amended row once it has been amended: the pill reads Saved (S4), and it opens unlocked', () => {
    renderDialog(
      [covered()],
      {},
      {
        'so-a|1|WESERP10B|2026-08-31': {
          verdict: 'amended',
          reserve_qty: '0',
          timely_spo_qty: '0',
          reserve: [],
          borrow: [],
          buy_qty: '43',
          reason: 'The other site needs its stock back.',
        },
      },
    );
    openLines();

    expect(
      screen.getByTestId('decision-pill-so-a|1|WESERP10B|2026-08-31'),
    ).toHaveTextContent('Saved');

    fireEvent.click(screen.getByText('SO403340'));
    expect(screen.getByLabelText('Buy the whole line')).toBeEnabled();
    expect(
      screen.getByRole('button', { name: 'Save decision' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Amend' }),
    ).not.toBeInTheDocument();
  });

  it('cannot be swept into a bulk verdict, because Amend is the only verb it has', () => {
    renderDialog([covered()]);
    openLines();

    const table = contributionTable();
    const boxes = within(table).getAllByRole('checkbox');
    // The row's own box, not the header's.
    expect(boxes[boxes.length - 1]).toBeDisabled();
  });

  it('counts as decided in the header, because it is - in the database', () => {
    renderDialog([covered(), demand({ line_no: 2, qty: '21' })]);

    const needed = screen.getByTestId('cell-quantity-needed');
    expect(needed).toHaveTextContent('64');
    expect(needed).toHaveTextContent('1 decided');
  });

  it('counts the frozen line into the outstanding total, decided or not', () => {
    renderDialog(
      [covered(), demand({ line_no: 2, qty: '21', sales_order_id: 'so-a' })],
      {
        'WESERP10B|BRW-BB': '5',
      },
    );
    openLines();

    // 43 outstanding on the covered line and 21 on the undecided one. A decision is a claim on
    // stock, not a delivery, so it does not take the line out of what is still outstanding.
    expect(footerCells()).toContain('64');
  });
});

describe('BoardCellBreakdownDialog: what purchasing has already been told', () => {
  /**
   * The Decision column says what was PROMISED. It cannot say whether anybody acted on it,
   * and that is the question a planner opens this dialog with a second time: an approved
   * revision with nothing placed against it and one fully placed look identical without the
   * inquiry beside it.
   *
   * Absent is the ordinary case, so the column has to state it rather than leave a blank
   * cell that reads as a rendering fault.
   */
  it('names the inquiry and its state beside the Decision column', () => {
    renderDialog([
      demand({ order_inquiry: { inquiry_no: 'OI-000123', state: 'placed' } }),
    ]);
    openLines();

    const table = contributionTable();
    const headers = within(table)
      .getAllByRole('columnheader')
      .map((node) => node.textContent ?? '');
    expect(headers.some((header) => header.includes('Order inquiry'))).toBe(
      true,
    );

    const row = table.querySelectorAll('tbody tr')[0] as HTMLElement;
    expect(within(row).getByText('OI-000123')).toBeInTheDocument();
    // The worklist's own wording, so the state word cannot mean two things on two screens.
    // S5 (`PLAN-board-oi-mechanical-22sep.md`, AC-B5-1, owner's pick, 22 Sep 2026): `placed`
    // now reads "On PO/SPO", not "Linked".
    expect(within(row).getByText('On PO/SPO')).toBeInTheDocument();
  });

  it('prints a dash for a line nobody has been told anything about', () => {
    renderDialog([demand({ order_inquiry: null })]);
    openLines();

    const row = contributionTable().querySelectorAll(
      'tbody tr',
    )[0] as HTMLElement;
    expect(within(row).queryByText(/^OI-/)).not.toBeInTheDocument();
    expect(within(row).getAllByText('-').length).toBeGreaterThan(0);
  });
});

/**
 * The Suggestion card names the LOCATIONS WITH THEIR QUANTITIES (AC-A1, AC-A2).
 *
 * The captain, on SO415472: "Use own location, 71 from BRW reads wrong" - BRW is the shared
 * pool, and the line's own location is its `-BB` warehouse. And on SO324132 rev 1: a row reading
 * "932 from DC1-BB, MWH-BB, WH3-BB" left the split to be guessed, when the split IS the
 * instruction: three separate movements of stock, each keyed by hand.
 */
describe('BoardCellBreakdownDialog: how the Suggestion card names its sources', () => {
  function renderWithSources(sources: BoardContribution['sources']) {
    const base = cellOf([demand({ qty: '932' })]);
    const cell: BoardCell = {
      ...base,
      // BOTH, the way the server sends them on an undecided line: `sources` is the live
      // proposal and `proposed` is the same list under the key the Suggestion card reads.
      contributions: base.contributions.map((entry) => ({
        ...entry,
        sources,
        // Stamped `v4`, which is what a confirm writes today. An UNSTAMPED frozen proposal
        // is a suggestion from a ladder that no longer runs, and the card says so - see
        // the test below.
        proposed: {
          components: sources.map((part) => ({ ...part, ladder: 'v4' })),
        },
      })),
    };
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <BoardCellBreakdownDialog
          cell={cell}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
  }

  it('calls a pool draw shared stock, and names the pool (AC-A1)', () => {
    renderWithSources([
      {
        kind: 'reserve',
        rung: 'pool',
        qty: '71',
        location: 'BRW',
        reason: 'The pool covers it.',
      },
    ]);

    const card = screen.getByTestId('cell-suggestion');
    expect(card).toHaveTextContent('Use BRW');
    expect(card).toHaveTextContent('71 from BRW');
    expect(card).not.toHaveTextContent('Use own location');
  });

  it('names each location with what is taken from it on a group take (AC-A2)', () => {
    renderWithSources([
      {
        kind: 'reserve',
        rung: 'group_take',
        qty: '454',
        location: 'DC1-BB',
        reason: 'a',
      },
      {
        kind: 'reserve',
        rung: 'group_take',
        qty: '267',
        location: 'MWH-BB',
        reason: 'b',
      },
      {
        kind: 'reserve',
        rung: 'group_take',
        qty: '211',
        location: 'WH3-BB',
        reason: 'c',
      },
    ]);

    const card = screen.getByTestId('cell-suggestion');
    expect(card).toHaveTextContent('Use own location');
    expect(card).toHaveTextContent(
      '454 from DC1-BB, 267 from MWH-BB, 211 from WH3-BB',
    );
  });

  it('labels a frozen suggestion that a ladder no longer in use composed', () => {
    // "MWH-IB has 30 available in the IB group" is v3 reading ONE warehouse's own
    // availability; under v4 that is not a reading anybody makes, and v5 has no Incoming
    // rung at all. A stamp that is not today's - absent, or an older version - says so.
    const base = cellOf([demand({ qty: '60' })]);
    const stale: BoardContribution['sources'] = [
      {
        kind: 'reserve',
        rung: 'group_take',
        qty: '60',
        location: 'MWH-IB',
        reason: 'MWH-IB has 30 available in the IB group.',
      },
    ];
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <BoardCellBreakdownDialog
          cell={{
            ...base,
            contributions: base.contributions.map((entry) => ({
              ...entry,
              covered: true,
              proposed: { components: stale },
            })),
          }}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    const card = screen.getByTestId('cell-suggestion');
    expect(card).toHaveTextContent(
      `Suggestion (before ladder ${LADDER_VERSION})`,
    );
    // One short label and no sentence: what a planner needs is to know they are reading
    // history, not a paragraph about ladder versions.
    expect(card).not.toHaveTextContent(/no longer/);
  });

  it('leaves a suggestion the current ladder composed unlabelled', () => {
    renderWithSources([
      {
        kind: 'reserve',
        rung: 'pool',
        qty: '71',
        location: 'BRW',
        ladder: 'v5',
        reason: 'The pool covers it.',
      },
    ]);

    expect(screen.getByTestId('cell-suggestion')).not.toHaveTextContent(
      'before ladder',
    );
  });

  it('never labels an UNDECIDED line, whatever its suggestion carries (AC-V8)', () => {
    // A line with no decision shows the LIVE suggestion, which is today's answer by
    // definition. This is the bug the criterion is about: the test used to be "no `ladder`
    // key", the server stamped the key on neither the live nor the frozen source, and the
    // label therefore appeared on every line on the board.
    const base = cellOf([demand({ qty: '60' })]);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <BoardCellBreakdownDialog
          cell={{
            ...base,
            contributions: base.contributions.map((entry) => ({
              ...entry,
              covered: false,
              proposed: {
                components: [
                  {
                    kind: 'reserve',
                    rung: 'group_take',
                    qty: '60',
                    location: 'MWH-IB',
                    reason: 'The IB group nets 60.',
                  },
                ],
              },
            })),
          }}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );

    expect(screen.getByTestId('cell-suggestion')).not.toHaveTextContent(
      'before ladder',
    );
  });

  it('tells the two borrows apart by rung (AC-A3)', () => {
    renderWithSources([
      // Names its donor SO: a donor-less one reads as a location borrow
      // (BORROW-LOCATION-ONLY D3).
      {
        kind: 'borrow',
        rung: 'group_borrow',
        qty: '3',
        location: 'BRW-BB',
        reason: 'a',
        donor_so_number: 'SO415472',
      },
      {
        kind: 'borrow',
        rung: 'cross_group_borrow',
        qty: '9',
        location: 'BRW-HP',
        reason: 'b',
      },
    ]);

    const card = screen.getByTestId('cell-suggestion');
    expect(card).toHaveTextContent('Borrow from another order');
    expect(card).toHaveTextContent('Borrow');
  });
});

/**
 * AC-D3: a Decision card BESIDE the Suggestion card, same shape, same words.
 *
 * The captain, on the board: "one page that shows what was SUGGESTED and what was DECIDED, in
 * the same words". Before this, a covered cell showed its frozen composition under the word
 * "Suggestion" - so an amended line looked as though the engine had suggested exactly what
 * somebody had changed it to.
 */
describe('BoardCellBreakdownDialog: the Decision card', () => {
  function renderComposition(
    over: Partial<BoardContribution>,
    draft: BoardDraft = {},
  ) {
    const base = cellOf([demand({ qty: '71' })]);
    const cell: BoardCell = {
      ...base,
      contributions: base.contributions.map((entry) => ({ ...entry, ...over })),
    };
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <BoardCellBreakdownDialog
          cell={cell}
          bucketLabel="31 Aug 2026"
          draft={draft}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
    return cell;
  }

  /**
   * AC-E6 / section E: the Decision card ends with the MOVEMENTS the decision implies, so a
   * planner sees the transfers Approve is about to raise before pressing it.
   */
  it('ends with a Moves line naming what has to be carried, and to where', () => {
    renderComposition({
      covered: true,
      proposed: {
        components: [
          {
            kind: 'buy',
            rung: 'buy',
            qty: '71',
            location: null,
            reason: 'nothing free',
          },
        ],
      },
      sources: [],
      decision: {
        revision_no: 1,
        timely_spo_qty: '0',
        reserve: [
          {
            warehouse_id: 'wh-dc1',
            location: 'DC1-BB',
            qty: '454',
            rung: 'group_take',
          },
          {
            warehouse_id: 'wh-mwh',
            location: 'MWH-BB',
            qty: '267',
            rung: 'group_take',
          },
        ],
        borrow: [],
        buy_qty: '0',
      },
    });

    expect(screen.getByTestId('decision-moves')).toHaveTextContent(
      'Moves: 454 DC1-BB -> BRW-BB · 267 MWH-BB -> BRW-BB',
    );
    // Never on the Suggestion card: nothing has been decided to move.
    expect(screen.queryByTestId('suggestion-moves')).not.toBeInTheDocument();
  });

  it("draws no Moves line when everything is already at the line's own location", () => {
    renderComposition({
      covered: true,
      proposed: { components: [] },
      sources: [],
      decision: {
        revision_no: 1,
        timely_spo_qty: '0',
        reserve: [
          {
            warehouse_id: 'wh-brw-bb',
            location: 'BRW-BB',
            qty: '71',
            rung: 'group_take',
          },
        ],
        borrow: [],
        buy_qty: '0',
      },
    });

    expect(screen.getByTestId('cell-decision')).toBeInTheDocument();
    expect(screen.queryByTestId('decision-moves')).not.toBeInTheDocument();
  });

  it('says Not recorded, never "nothing proposed", on a revision that froze no proposal', () => {
    // Verified live on SO324132 rev 1, whose four lines all predate the field: the card read
    // "Nothing proposed for this cell", which is a claim about the LADDER rather than about
    // the record, and it is not true - the ladder proposed plenty, nobody kept it.
    renderComposition({
      covered: true,
      // Null, the way the server sends a revision written before the field existed.
      proposed: null,
      sources: [
        {
          kind: 'buy',
          rung: 'buy',
          qty: '71',
          location: null,
          reason: 'Bought, as confirmed.',
        },
      ],
      decision: {
        revision_no: 1,
        timely_spo_qty: '0',
        reserve: [],
        borrow: [],
        buy_qty: '71',
      },
    });

    expect(screen.getByTestId('cell-suggestion')).toHaveTextContent(
      'Not recorded for this revision',
    );
  });

  it('is absent while nothing on the cell is decided', () => {
    renderComposition({
      proposed: {
        components: [
          {
            kind: 'reserve',
            rung: 'pool',
            qty: '71',
            location: 'BRW',
            reason: 'pool',
          },
        ],
      },
    });

    expect(screen.getByTestId('cell-suggestion')).toBeInTheDocument();
    expect(screen.queryByTestId('cell-decision')).not.toBeInTheDocument();
  });

  it('appears beside the suggestion once a line is confirmed, and states the decision', () => {
    renderComposition({
      covered: true,
      proposed: {
        components: [
          {
            kind: 'reserve',
            rung: 'pool',
            qty: '71',
            location: 'BRW',
            reason: 'The pool covers it.',
          },
        ],
      },
      sources: [
        {
          kind: 'buy',
          rung: 'buy',
          qty: '71',
          location: null,
          reason: 'Bought, as confirmed.',
        },
      ],
      decision: {
        revision_no: 1,
        timely_spo_qty: '0',
        reserve: [],
        borrow: [],
        buy_qty: '71',
      },
    });

    const suggestion = screen.getByTestId('cell-suggestion');
    const decision = screen.getByTestId('cell-decision');
    expect(suggestion).toHaveTextContent('Use BRW');
    expect(suggestion).toHaveTextContent('71 from BRW');
    expect(decision).toHaveTextContent('Buy');
    expect(decision).toHaveTextContent('71');
    // Beside it, not under it: the two are read against each other.
    expect(suggestion.compareDocumentPosition(decision)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it('appears on a line merely ticked into this session draft, before anything is posted', () => {
    const cell = cellOf([demand({ qty: '71' })]);
    const key = cell.contributions[0].key;
    renderComposition(
      {
        proposed: {
          components: [
            {
              kind: 'reserve',
              rung: 'pool',
              qty: '71',
              location: 'BRW',
              reason: 'pool',
            },
          ],
        },
      },
      { [key]: { verdict: 'approved' } },
    );

    // An approval takes the proposal as it stands, so the two cards agree - which is itself
    // the answer to "did we do what the engine said".
    expect(screen.getByTestId('cell-decision')).toHaveTextContent('Use BRW');
  });
});

/**
 * 375px (ADR-PRODUCT-STANDARDS: "usable and non-clipped at 375px AND 1280px").
 *
 * jsdom does no real layout, so what this pins is structural, not a measured pixel width:
 * the table lives inside the GRID'S OWN horizontal scrollport (`data-grid-scroller`,
 * `overflow-x-auto`), it is the only scrollport on that axis, and nothing on the way up
 * from it carries a fixed pixel width that would force the dialog itself wider than the
 * viewport at 375px.
 *
 * It used to be `PanelDataGrid`'s Radix `ScrollArea`, and that wrapper was the bug. A
 * second horizontal scrollport around the grid's own puts a `display: table` viewport
 * between them; that viewport shrink-fits, so `data-grid-scroller` measured
 * `scrollWidth === clientWidth` and never overflowed, while still swallowing the wheel
 * gesture through `overscroll-x-contain`. 165 lists could not be scrolled sideways at all.
 * The wrapper is gone; `components/ui/data-grid-scroller.inventory.test.ts` keeps it gone
 * across the tree, and this asserts the shape the dialog ends up with.
 */
describe('BoardCellBreakdownDialog: the table scrolls inside its own container at 375px', () => {
  it("puts the table in the grid's own scroller, with no second scrollport and no fixed-width box", () => {
    renderDialog([demand({ qty: '71' })], { 'BRW-BB': '71' });
    openLines();

    const scroller = document.querySelector('[data-slot="data-grid-scroller"]');
    expect(scroller).not.toBeNull();
    expect(scroller).toHaveClass('overflow-x-auto');
    // `min-w-0` is what lets the scroller be narrower than the table it holds: `CardTable`
    // is a grid and `Card` a flex column, and an item of either defaults to
    // `min-width: auto`, i.e. its min-content - which is the table's own min-width.
    expect(scroller).toHaveClass('min-w-0');

    // The table is inside that scroller, with no Radix viewport in between.
    const table = document.querySelector('[data-slot="data-grid-table"]') as HTMLElement | null;
    expect(table).not.toBeNull();
    expect(scroller!.contains(table!)).toBe(true);
    for (let node = table!.parentElement; node && node !== scroller; node = node.parentElement) {
      expect(node.hasAttribute('data-radix-scroll-area-viewport')).toBe(false);
    }

    // Walking up from the scroller to the dialog's own root, two things must hold.
    //
    // No second horizontal scrollport ABOVE it - that is the wrapper this screen used to
    // have, and it is what stopped the grid scrolling at all.
    //
    // And no fixed pixel width: a `w-[960px]` (Tailwind's own fixed-width syntax) or an
    // inline `style.width` in px would force the dialog to overflow a 375px viewport rather
    // than scrolling its table.
    let node: Element | null = scroller;
    while (node) {
      expect(node.getAttribute('data-slot')).not.toBe('scroll-area');
      expect(node.hasAttribute('data-radix-scroll-area-viewport')).toBe(false);
      expect(node.className).not.toMatch(/\bw-\[\d+px\]/);
      const inlineWidth = (node as HTMLElement).style?.width ?? '';
      expect(inlineWidth).not.toMatch(/^\d+px$/);
      node = node.parentElement;
    }
  });
});

/**
 * M5 evidence run 1, review concern B2: the dialog's `DialogBody` already owns ONE scroll
 * viewport (`overflow-y-auto`); the Contributing lines grid's OWN bounded scroller
 * (`data-grid-scroller`, `max-h-(--grid-max-h) overflow-y-auto` by default) would be a
 * SECOND, nested one - two independently-scrollable regions stacked, proved live in the
 * evidence run by moving `DialogBody`'s own `scrollTop` and watching the grid's rows not
 * move with it.
 *
 * `scrollerMaxHeight={false}` (890ac2622) is meant to opt the grid out of that default. This
 * pins that the prop actually REACHES the rendered scroller element rather than being dropped
 * somewhere in `PanelDataGrid`'s pass-through - the class that would reintroduce the bound
 * (`max-h-`) must be absent.
 */
describe('BoardCellBreakdownDialog: the Contributing lines grid does not double-scroll inside DialogBody (B2)', () => {
  it('scrollerMaxHeight={false} reaches the grid scroller: no max-h- class on it', () => {
    renderDialog([demand({ qty: '71' })], { 'BRW-BB': '71' });
    openLines();

    const scroller = document.querySelector('[data-slot="data-grid-scroller"]');
    expect(scroller).not.toBeNull();
    expect(scroller!.className).not.toMatch(/\bmax-h-/);
  });
});

describe('sourceAt names the DONOR for every borrow that has one', () => {
  it('names the donor order and line for a ladder v7.1 step-2 borrow', () => {
    // The gate used to be `rung === 'group_borrow'` - ladder v2's only borrow rung - so an
    // `order_borrow` source printed " from MWH-IB", which is where the goods sit rather
    // than whose order gives them up. The location is the secondary fact here.
    expect(
      sourceAt({
        kind: 'borrow',
        rung: 'order_borrow',
        qty: '30',
        location: 'MWH-IB',
        reason: 'because',
        donor_so_number: 'SO414285',
        donor_line_no: 4,
      } as never),
    ).toBe(' from SO414285 line 4');
  });

  it('names the DOCUMENT and its arrival for a step-3 borrow (AC-S4-5)', () => {
    // Ladder v7.1 step 3 borrows a DOCUMENT, so the row reads the document and the day it
    // lands before it reads whose order gives it up: the arrival is the whole reason a
    // planner takes this row over a Buy, and the bin the container is bound for says
    // nothing about when the goods can be picked.
    expect(
      sourceAt({
        kind: 'borrow',
        rung: 'supply_borrow',
        qty: '50',
        location: 'BRW-IB',
        reason: 'because',
        supply_key: 'spo:0f2b',
        supply_document: 'SPO 202607-S0105',
        arrival_date: '2026-09-15',
        donor_so_number: 'SO414285',
        donor_line_no: 4,
      } as never),
    // DD/MM/YYYY, the screen's own date format (`formatDateInMalaysia`), not the engine
    // sentence's "15 Sep 2026": the required-date column of the SAME row prints DD/MM/YYYY,
    // and two spellings of a date on one row is a second vocabulary. The server's wording
    // survives verbatim inside `reason`, which is what the info tooltip shows.
    ).toBe(' SPO 202607-S0105, arriving 15/09/2026 from SO414285 line 4');
  });

  it('names a FREE document without a donor, because nobody is owed it back', () => {
    // A document nobody was waiting on raises no order-back, so there is no donor to name
    // and the row must not invent one - it is the same fact step 1's free pile carries.
    expect(
      sourceAt({
        kind: 'borrow',
        rung: 'supply_borrow',
        qty: '32',
        location: 'BRW-BB',
        reason: 'because',
        supply_key: 'po:9a1c',
        supply_document: 'PO 202608-S0041 line 1',
        arrival_date: '2026-11-16',
      } as never),
    ).toBe(' PO 202608-S0041 line 1, arriving 16/11/2026');
  });

  it('names the donor for the pool BORROW half and keeps the location for a free draw', () => {
    expect(
      sourceAt({
        kind: 'borrow',
        rung: 'pool',
        qty: '30',
        location: 'BRW',
        reason: 'because',
        donor_so_number: 'SO414285',
      } as never),
    ).toBe(' from SO414285');
    expect(
      sourceAt({
        kind: 'reserve',
        rung: 'pool',
        qty: '30',
        location: 'BRW',
        reason: 'because',
      } as never),
    ).toBe(' at BRW');
  });
});

/**
 * S3 (PLAN-scm-planning-feedback-31aug): the suggestion sentence's donor/document links, the
 * sticky toolbar's conditional buttons, and the Contributing lines search.
 */
describe('BoardCellBreakdownDialog: S3 suggestion sentence, sticky toolbar, lines search', () => {
  function cellWithSource(source: Record<string, unknown>): BoardCell {
    const cell = cellOf([demand()]);
    const contribution = {
      ...cell.contributions[0],
      sources: [{ kind: 'borrow', qty: '30', location: 'BRW', reason: '', ...source }],
    };
    return {
      ...cell,
      contributions: [contribution],
      locations: [
        { ...cell.locations[0], location: 'BRW-BB', product_id: 'prod-1', warehouse_id: 'wh-1' },
        {
          location: 'BRW',
          where: 'site_pool',
          net_of: 'pools',
          net: '1716',
          product_id: 'prod-1',
          warehouse_id: 'wh-2',
          qty: '0',
        },
      ],
    } as unknown as BoardCell;
  }

  function dialogFor(cell: BoardCell) {
    return (
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false, gcTime: 0 } },
          })
        }
      >
        <BoardCellBreakdownDialog
          cell={cell}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>
    );
  }

  beforeEach(() => {
    // Left in flight on purpose - the documents themselves are `StockDocumentsPanel`'s suite.
    getStockDetail.mockReturnValue(new Promise(() => {}));
  });

  it('AC-3.3: the donor SO number in the suggestion sentence is a clickable link', () => {
    render(
      dialogFor(
        cellWithSource({
          reason:
            'Borrow 30 on hand at pool BRW from SO401624, due 7 Jun 2027; its debt lands in Jun 2027.',
          donor_so_number: 'SO401624',
        }),
      ),
    );

    expect(screen.getByTestId('cell-suggestion-sentence')).toBeInTheDocument();
    const link = screen.getByTestId('suggestion-donor-link');
    expect(link.textContent).toBe('SO401624');
    expect(link.tagName).toBe('BUTTON');
  });

  it('AC-3.2/3.9: the Donor toolbar button renders only when the suggestion names one', () => {
    render(
      dialogFor(
        cellWithSource({
          reason: 'Borrow 30 on hand at pool BRW from SO401624.',
          donor_so_number: 'SO401624',
        }),
      ),
    );

    expect(screen.getByTestId('stock-jump-this-line')).toBeInTheDocument();
    expect(screen.getByTestId('stock-jump-donor')).toBeInTheDocument();
    expect(screen.queryByTestId('stock-jump-document')).not.toBeInTheDocument();
  });

  it('AC-3.4/3.13: the SPO number AND the waiting order both link from one sentence', () => {
    render(
      dialogFor(
        cellWithSource({
          reason:
            'Borrow 30 arriving 20 Oct 2026 (SPO 202609-0041) from SO397460 line 2; its debt lands in Jul 2027.',
          donor_so_number: 'SO397460',
          supply_document: 'SPO 202609-0041',
        }),
      ),
    );

    expect(screen.getByTestId('suggestion-donor-link').textContent).toBe('SO397460');
    expect(screen.getByTestId('suggestion-document-link').textContent).toBe(
      'SPO 202609-0041',
    );
    expect(screen.getByTestId('stock-jump-donor')).toBeInTheDocument();
    expect(screen.getByTestId('stock-jump-document').textContent).toBe('SPO 202609-0041');
  });

  /**
   * The captain's own complaint (3 Sep 2026, SO418869 SRTWCX7405-RL-S-PJ): "SPO
   * SPO-2026/08-0085 is 28 days late" jumped the table on click and nowhere opened the
   * document. The chip beside it keeps doing exactly what it always did (a BUTTON, never a
   * link, so it never navigates); a second, tiny control opens the document itself.
   */
  it('AC-3.4 fix: an open-SPO link sits beside the jump chip, and the chip stays a button', () => {
    render(
      dialogFor(
        cellWithSource({
          reason:
            'Borrow 30 arriving 20 Oct 2026 (SPO 202609-0041) from SO397460 line 2; its debt lands in Jul 2027.',
          donor_so_number: 'SO397460',
          supply_document: 'SPO 202609-0041',
        }),
      ),
    );

    // Unchanged: the chip is still what jumps the table below, and it is still a button
    // rather than a second link that would navigate instead of jumping.
    const chip = screen.getByTestId('suggestion-document-link');
    expect(chip.tagName).toBe('BUTTON');
    expect(() => fireEvent.click(chip)).not.toThrow();

    const openLink = screen.getByTestId('suggestion-document-open-link');
    expect(openLink.tagName).toBe('A');
    expect(openLink).toHaveAttribute(
      'href',
      '/procurement-management/spo-allocations/202609-0041',
    );
    expect(openLink).toHaveAttribute('aria-label', 'Open SPO 202609-0041');
  });

  it('AC-3.4 fix: a number the book already writes with its own "SPO-" opens on itself, never doubled', () => {
    // Post the backend fix, a number that already carries the word arrives in the reason
    // AS ITSELF (`SPO-2026/08-0085`), never "SPO SPO-2026/08-0085" - the open link has to
    // resolve to the SAME raw number the SPO detail route expects.
    render(
      dialogFor(
        cellWithSource({
          reason: 'SPO-2026/08-0085 is 28 days late, assumed by 17 Sep 2026',
          supply_document: 'SPO-2026/08-0085',
        }),
      ),
    );

    expect(screen.getByTestId('suggestion-document-link').textContent).toBe(
      'SPO-2026/08-0085',
    );
    expect(screen.getByTestId('suggestion-document-open-link')).toHaveAttribute(
      'href',
      '/procurement-management/spo-allocations/SPO-2026%2F08-0085',
    );
  });

  it('AC-4.5: a use-incoming (step 1 water, no donor) source links and jumps its SPO the same way a borrow-incoming one does', () => {
    // S4 task 3: step 1's own/other-group water now carries `supply_document` too (it used
    // to be step 3 only), and it names nobody - a FREE document is taken, not borrowed, so
    // there is no donor button, only the document one.
    render(
      dialogFor(
        cellWithSource({
          kind: 'timely_spo',
          rung: 'group_take',
          reason:
            'BRW-BB has 15 free outside the BB group, arriving 6 Sep 2026 (SPO 202609-0041), and free stock is owed to nobody',
          supply_document: 'SPO 202609-0041',
          supply_key: 'spo:0f2b',
          arrival_date: '2026-09-06',
        }),
      ),
    );

    expect(screen.getByTestId('suggestion-document-link').textContent).toBe(
      'SPO 202609-0041',
    );
    expect(screen.getByTestId('stock-jump-document').textContent).toBe(
      'SPO 202609-0041',
    );
    expect(screen.queryByTestId('stock-jump-donor')).not.toBeInTheDocument();
    expect(screen.queryByTestId('suggestion-donor-link')).not.toBeInTheDocument();
  });

  it('no sentence and no Donor/document buttons when the suggestion names neither', () => {
    render(dialogFor(cellOf([demand()])));

    expect(screen.queryByTestId('cell-suggestion-sentence')).not.toBeInTheDocument();
    expect(screen.getByTestId('stock-jump-this-line')).toBeInTheDocument();
    expect(screen.queryByTestId('stock-jump-donor')).not.toBeInTheDocument();
    expect(screen.queryByTestId('stock-jump-document')).not.toBeInTheDocument();
  });

  it('AC-3.5: the Contributing lines search filters by SO number', async () => {
    renderDialog([
      demand({ line_no: 1 }),
      demand({
        line_no: 2,
        so_number: 'SO398322',
        sales_order_id: 'so-b',
        customer_name: 'ANOTHER CUSTOMER SDN BHD',
      }),
    ]);

    openLines();
    expect(screen.getByText('SO403340')).toBeInTheDocument();
    expect(screen.getByText('SO398322')).toBeInTheDocument();

    fireEvent.change(
      screen.getByPlaceholderText('Search SO number, customer, agent'),
      { target: { value: 'SO398322' } },
    );

    await waitFor(() => expect(screen.queryByText('SO403340')).not.toBeInTheDocument());
    expect(screen.getByText('SO398322')).toBeInTheDocument();
  });

  it('AC-3.5: an empty state names the search miss, distinct from the ordinary empty state', async () => {
    renderDialog([demand()]);
    openLines();

    fireEvent.change(
      screen.getByPlaceholderText('Search SO number, customer, agent'),
      { target: { value: 'no such order anywhere' } },
    );

    await waitFor(() =>
      expect(screen.getByText('No line matches your search')).toBeInTheDocument(),
    );
  });

  it('AC-3.5: clearing the search restores every line', async () => {
    renderDialog([
      demand({ line_no: 1 }),
      demand({ line_no: 2, so_number: 'SO398322', sales_order_id: 'so-b' }),
    ]);
    openLines();

    const search = screen.getByPlaceholderText('Search SO number, customer, agent');
    fireEvent.change(search, { target: { value: 'SO398322' } });
    await waitFor(() => expect(screen.queryByText('SO403340')).not.toBeInTheDocument());

    fireEvent.change(search, { target: { value: '' } });
    await waitFor(() => expect(screen.getByText('SO403340')).toBeInTheDocument());
    expect(screen.getByText('SO398322')).toBeInTheDocument();
  });
});

/**
 * AC-S5-1 (`board-received-stock-own-arrival-acceptance-criteria.md` S5,
 * `PLAN-board-received-stock-own-arrival.md` R7): the own-arrival credit (S3) composes as a
 * Reserve carrying `source: 'own_arrival'` on the wire
 * (`FulfilmentBoardService._source`, `project_fulfilment_board_service.py:~4056`). The board
 * says so with a "Received N" chip, beside the existing per-source pills in the "Sourced
 * from" cell - the same cell the `buy_origin === 'local'` Badge already sits in
 * (`BoardCellBreakdownDialog.tsx`, the `sources` column) - carrying the PO number
 * (`supply_document`) in its `title`. RED: nothing in `BoardCellBreakdownDialog` reads
 * `source` today, so no such chip exists at any quantity.
 */
describe('BoardCellBreakdownDialog: own arrival (S5)', () => {
  /** One line whose ladder composed 20 as an own-arrival Reserve off PO 202510-S0101. */
  function cellWithOwnArrival(source: Record<string, unknown> = {}): BoardCell {
    const cell = cellOf([demand({ qty: '20' })]);
    const contribution = {
      ...cell.contributions[0],
      sources: [
        {
          kind: 'reserve',
          rung: 'group_take',
          qty: '20',
          location: 'BRW-BB',
          reason: '20 landed for this line on PO 202510-S0101, taken first.',
          source: 'own_arrival',
          supply_document: 'PO 202510-S0101 line 2',
          ...source,
        },
      ],
    };
    return { ...cell, contributions: [contribution] } as unknown as BoardCell;
  }

  function renderDialogFor(cell: BoardCell) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <BoardCellBreakdownDialog
          cell={cell}
          bucketLabel="31 Aug 2026"
          draft={{}}
          onDecide={vi.fn()}
          onDecideMany={vi.fn()}
          onClose={vi.fn()}
        />
      </QueryClientProvider>,
    );
  }

  it('AC-S5-1: a source carrying source: own_arrival renders a "Received N" chip naming the PO in its title', () => {
    const cell = cellWithOwnArrival();
    const key = cell.contributions[0].key;
    renderDialogFor(cell);
    openLines();

    const chip = screen.getByTestId(`received-own-arrival-${key}`);
    expect(chip.textContent).toBe('Received 20');
    expect(chip).toHaveAttribute('title', expect.stringContaining('PO 202510-S0101'));
  });

  it('AC-S5-1: no chip when no source carries source: own_arrival', () => {
    const cell = cellOf([demand({ qty: '20' })]);
    const key = cell.contributions[0].key;
    renderDialogFor(cell);
    openLines();

    expect(
      screen.queryByTestId(`received-own-arrival-${key}`),
    ).not.toBeInTheDocument();
  });

  it('AC-S5-1: an ordinary group-take Reserve with source: null renders no chip', () => {
    const cell = cellWithOwnArrival({ source: null });
    const key = cell.contributions[0].key;
    renderDialogFor(cell);
    openLines();

    expect(
      screen.queryByTestId(`received-own-arrival-${key}`),
    ).not.toBeInTheDocument();
  });
});

/**
 * `board-verdict-actions-chips-acceptance-criteria.md` AC-B1/AC-B11: the grid dialog's own
 * Decision column carries the SAME Verdict trio the list view does, through the shared
 * `BoardVerdictActions` component - so the two readings of the board can never drift about
 * what a Suggested line offers. RED today: the Decision column shows only the pill and (once
 * drafted) Undo - no Reject, no Change decision, and the Save icon lives outside this column
 * entirely (the select checkbox's own bulk path).
 */
describe('AC-B1/AC-B11: the Decision column carries the same Verdict trio as the list view', () => {
  it('a Suggested row (no draft) shows Accept, Reject and Change decision', () => {
    renderDialog([demand()]);
    openLines();

    expect(
      screen.getByRole('button', { name: 'Save SO403340 line 1 as suggested' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Reject SO403340 line 1' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Change decision for SO403340 line 1' }),
    ).toBeInTheDocument();
  });

  /**
   * SF-6 (reviewer, fix round 2): the reject popover lives inside a DIALOG here, which has
   * its own Escape handler and its own focus scope, and the popover is portalled to the
   * document root - outside that scope. So the two things worth pinning are that Escape
   * reaches the popover and stops there (the breakdown a planner is mid-decision in must not
   * close under them), and that the refusal it submits is addressed to THIS row's key.
   */
  it('SF-6: Escape closes the reject popover and leaves the breakdown dialog open', async () => {
    renderDialog([demand()]);
    openLines();

    fireEvent.click(screen.getByRole('button', { name: 'Reject SO403340 line 1' }));
    const textarea = await screen.findByPlaceholderText('In your own words');
    fireEvent.keyDown(textarea, { key: 'Escape', code: 'Escape' });

    await waitFor(() =>
      expect(screen.queryByPlaceholderText('In your own words')).not.toBeInTheDocument(),
    );
    // The breakdown itself is still up, with its own tabs and its own table.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject SO403340 line 1' })).toBeInTheDocument();
  });

  it('SF-6: submitting the popover posts the refusal for THIS row key, with the reason and the flag', async () => {
    const cell = cellOf([demand()]);
    const key = cell.contributions[0].key;
    const { onDecide } = renderDialog([demand()]);
    openLines();

    fireEvent.click(screen.getByRole('button', { name: 'Reject SO403340 line 1' }));
    const textarea = await screen.findByPlaceholderText('In your own words');
    fireEvent.change(textarea, { target: { value: '  Customer put it on hold  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));

    await waitFor(() => expect(onDecide).toHaveBeenCalledTimes(1));
    expect(onDecide).toHaveBeenCalledWith(key, {
      verdict: 'rejected',
      reason: 'Customer put it on hold',
      suspected_system_issue: false,
    });
  });

  it('a Saved row (real draft) shows Undo and Change decision only - no Accept, no Reject', () => {
    const cell = cellOf([demand()]);
    const key = cell.contributions[0].key;
    renderDialog([demand()], {}, { [key]: { verdict: 'approved' } });
    openLines();

    expect(
      screen.getByRole('button', { name: 'Undo SO403340 line 1' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Change decision for SO403340 line 1' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Save SO403340 line 1 as suggested' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Reject SO403340 line 1' }),
    ).not.toBeInTheDocument();
  });

  it('AC-DT-5 (round 2, PLAN-oi-decision-trail-ui.md): the Decision column carries the same History icon the list view does, for a covered line', () => {
    const frozen = {
      revision_no: 1,
      timely_spo_qty: '0',
      reserve: [],
      borrow: [],
      buy_qty: '43',
    };
    renderDialog([demand({ qty: '43', decision: frozen })]);
    openLines();

    expect(screen.getByRole('button', { name: /decision trail/i })).toBeInTheDocument();
  });

  it('hides it for a bare suggested line', () => {
    renderDialog([demand()]);
    openLines();

    expect(
      screen.queryByRole('button', { name: /decision trail/i }),
    ).not.toBeInTheDocument();
  });
});
