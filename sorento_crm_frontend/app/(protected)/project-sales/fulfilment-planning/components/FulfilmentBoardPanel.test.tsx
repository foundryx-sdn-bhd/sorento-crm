/**
 * The multi-order planning board on screen (PLAN section 13).
 *
 * What is worth pinning is the honesty of the thing rather than its pixels: Overdue is first
 * and No date is last, a cell that draws on two locations says both, a line whose sales order
 * states no location is visible AND blocks its order, and above all the per-order "N of M lines
 * decided" counter gates Confirm with its reason showing. That counter is the whole reason the
 * ownership question in 13.4 is a question, so a board that smoothed it over would be
 * misrepresenting the design.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const routerReplace = vi.fn();
let currentSearchParams = new URLSearchParams('');

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: (...args: unknown[]) => routerReplace(...args),
  }),
  usePathname: () => '/project-sales/fulfilment-planning',
  useSearchParams: () => currentSearchParams,
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({
    resetToDefaults: vi.fn(),
    isLoading: false,
  }),
}));

const getPlanningBoard = vi.fn();
const confirmSupply = vi.fn();
const adoptSalesOrder = vi.fn();
const confirmMany = vi.fn();
const previewConfirmMany = vi.fn();

vi.mock('../../_shared/services/fulfilmentPlanningService', () => ({
  getPlanningBoard: (...args: unknown[]) => getPlanningBoard(...args),
  listFulfilmentPlanning: vi.fn(),
  getReconciliation: vi.fn(),
  rerunReconciliation: vi.fn(),
  adoptSalesOrder: (...args: unknown[]) => adoptSalesOrder(...args),
  getSupply: vi.fn(),
  confirmSupply: (...args: unknown[]) => confirmSupply(...args),
  confirmMany: (...args: unknown[]) => confirmMany(...args),
  previewConfirmMany: (...args: unknown[]) => previewConfirmMany(...args),
  // S4 (`useLineDraftMutation`): no test here presses Save deep enough to reach these -
  // that interaction is `BoardLineDecisionPanel.test.tsx`'s, against a plain `vi.fn()`
  // `onDecide` - but `decide()` closes over them regardless, so an undefined export would
  // throw the moment it did.
  putLineDraft: vi.fn().mockResolvedValue({
    decision: { verdict: 'approved' },
    saved_by: 'Test Planner',
    saved_at: '2026-09-03T00:00:00Z',
  }),
  deleteLineDraft: vi.fn().mockResolvedValue(undefined),
  // Declared INSIDE the factory: `vi.mock` is hoisted above every top-level binding, so a
  // class declared outside it is not initialised yet when the factory runs.
  ConfirmSupplyError: class ConfirmSupplyError extends Error {
    readonly failingLines: {
      line_no?: number | null;
      item_code?: string | null;
      reason: string;
    }[];
    constructor(
      message: string,
      failingLines: {
        line_no?: number | null;
        item_code?: string | null;
        reason: string;
      }[] = [],
    ) {
      super(message);
      this.name = 'ConfirmSupplyError';
      this.failingLines = failingLines;
    }
  },
}));

const getPlanningChangeBatch = vi.fn();
vi.mock('../../_shared/services/planningChangeService', () => ({
  listPlanningChangeBatches: vi.fn(),
  getPlanningChangeBatch: (...args: unknown[]) =>
    getPlanningChangeBatch(...args),
  updatePlanningChangeRow: vi.fn(),
  applyPlanningChanges: vi.fn(),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));

// S4: `decide()` names the saver off the session (R-F). A harmless authenticated session -
// the popover-content assertion is left to `BoardDecisionPill.test.tsx`.
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { id: 'user-1', name: 'Test Planner' } },
    status: 'authenticated',
  }),
}));

/**
 * `BoardTransfersPanel` (D4) is on this screen now, above the matrix. Its own behaviour is
 * `BoardTransfersPanel.test.tsx`'s; what this file owes is a harmless mock so the board can
 * render without a real query or a real permission check.
 */
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => false,
}));

/**
 * #1312, fix round 1 should-fix 3: the ONE line-attachments lookup call now lives
 * here (moved out of `FulfilmentBoardListView`, which is unit-tested standalone
 * with no `QueryClientProvider` in scope) - `FulfilmentBoardPanel: line
 * attachments lookup` below is where the "one call, not one per row" assertion
 * that used to live in `FulfilmentBoardListView.attachments.test.tsx` moved to.
 */
const soLineAttachmentLookup = vi.fn().mockReturnValue({ data: {}, isLoading: false });
vi.mock('../../_shared/hooks/useSoLineAttachments', () => ({
  useSoLineAttachmentLookup: (lineIds: string[]) => soLineAttachmentLookup(lineIds),
}));
vi.mock('../../_shared/hooks/useBoardTransfers', () => ({
  // The real key, because the confirm hook invalidates it by name (D6).
  BOARD_TRANSFERS_KEY: 'board-stock-transfers',
  useBoardTransfers: () => ({
    data: { data: [] },
    isLoading: false,
    error: undefined,
  }),
  useBoardTransferMutations: () => ({
    approve: { mutate: vi.fn(), isPending: false },
    approveAll: { mutate: vi.fn(), isPending: false },
  }),
}));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    id,
  }: {
    value: string;
    onChange: (next: string) => void;
    options?: { value: string; label: string }[];
    id?: string;
  }) => (
    <select
      aria-label={id ?? 'granularity'}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {(options ?? []).map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));

import { toast } from '@/lib/toast';
// The mocked module's own `putLineDraft` / `deleteLineDraft` (B1): imported here, rather
// than only referenced inside the factory above, so a test can control ONE call's outcome
// (`mockImplementationOnce`) without touching every other test's default resolved value.
import {
  deleteLineDraft,
  putLineDraft,
} from '../../_shared/services/fulfilmentPlanningService';
import { FulfilmentBoardPanel } from './FulfilmentBoardPanel';
import { unpostableNotices } from '../../_shared/lib/unpostableNotices';
import {
  buildBoard,
  type BoardDemandLine,
} from '../../_shared/lib/__testsupport__/boardFixture';
import type {
  BoardContribution,
  BoardGranularity,
  BoardPolicy,
  PlanningBoard,
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
    required_date: '2026-09-04',
    fulfilment_location: 'BRW-BB',
    priority: null,
    ...overrides,
  };
}

function boardOf(
  lines: BoardDemandLine[],
  freeStock: Record<string, string> = {},
  granularity: BoardGranularity = 'week',
) {
  return buildBoard(lines, { today: TODAY, freeStock, granularity });
}

/**
 * Applies `transform` to every matching contribution across BOTH `cells[].contributions` and
 * the board's own top-level `contributions` (the review finding's fix): Confirm, the previews,
 * the unpostable-line detection and Approve all all read the top-level list now, never the
 * cells, so a test simulating a server-stated fact on one contribution has to state it on both
 * copies or the mutation is invisible to half the component.
 */
function withContribution(
  board: PlanningBoard,
  match: (entry: BoardContribution) => boolean,
  transform: (entry: BoardContribution) => BoardContribution,
): PlanningBoard {
  const apply = (entry: BoardContribution) =>
    match(entry) ? transform(entry) : entry;
  return {
    ...board,
    cells: board.cells.map((cell) => ({
      ...cell,
      contributions: cell.contributions.map(apply),
    })),
    contributions: board.contributions.map(apply),
  };
}

/**
 * Marks every contribution SAVED (an approved draft), the 8 Sep 2026 baseline (reverses R11)
 * for fixtures that used to rely on silence being agreement: Confirm posts only a line the
 * planner saved, so a test whose real point is something else (window vs selection, per-order
 * grouping, a server refusal) gives its lines a saved draft here rather than leaving them
 * untouched and hoping Confirm still counts them.
 */
function allSaved(board: PlanningBoard): PlanningBoard {
  return withContribution(board, () => true, (entry) => ({
    ...entry,
    draft: {
      decision: { verdict: 'approved' as const },
      saved_by: 'Test Planner',
      saved_at: '2026-09-08T00:00:00Z',
    },
  }));
}

function renderPanel(
  soNumbers = ['SO403340', 'SO398322'],
  onBack: () => void = vi.fn(),
  batchId: string | null = null,
) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <FulfilmentBoardPanel
        soNumbers={soNumbers}
        onBack={onBack}
        batchId={batchId}
      />
    </QueryClientProvider>,
  );
}

/**
 * The dialog carries two buttons named Close: Radix's own corner dismiss and the footer one.
 * The footer is the last in document order, and naming that here keeps the ambiguity in one
 * place rather than in every test that decides a cell.
 */
function closeDialog() {
  const buttons = screen.getAllByRole('button', { name: 'Close' });
  fireEvent.click(buttons[buttons.length - 1]);
}

/**
 * The cell dialog opens on its STOCK tab, so a test that reads or decides a LINE presses
 * the other tab first - the same press a planner makes.
 *
 * Not cosmetic: Radix unmounts the inactive panel, so without this there is no lines grid
 * in the tree at all. The dialog is opened from a cell and never from a line, which is why
 * Stock is what it defaults to.
 *
 * Radix's TabsTrigger switches on MOUSE DOWN; a bare `click` leaves the old panel up.
 */
function openLinesTab() {
  const tab = screen.getByRole('tab', { name: /^Contributing lines/ });
  fireEvent.mouseDown(tab);
  fireEvent.click(tab);
}

/**
 * FULFIL-CONFIRM-SCOPE v2: Confirm is only offered after a Preview, so a test that used to press
 * the confirm dialog now previews first. The default preview answer echoes the lines the body
 * names (line number read off the fixture's `pl-<so>-<line_no>` ids), with no purchase rows.
 */
function defaultPreview(body: {
  orders: { pso_id: string; lines: { project_line_id: string }[]; rejected_line_ids?: string[] }[];
}) {
  return Promise.resolve({
    results: body.orders.map((order) => ({
      pso_id: order.pso_id,
      ok: true,
      preview: true,
      decision_revision: 1,
      lines_confirmed: order.lines.map((line) => ({
        project_line_id: line.project_line_id,
        line_no: Number(line.project_line_id.split('-').pop()),
        item_code: null,
      })),
      lines_carried: 0,
      lines_held_back: [],
      lines_withdrawn: (order.rejected_line_ids ?? []).map((id) => ({
        project_line_id: id,
        line_no: null,
        item_code: null,
      })),
      lines_fulfilled_skipped: 0,
      inquiry_rows: [],
      transfers: [],
    })),
  });
}

async function pressPreviewThenConfirm() {
  await waitFor(() => expect(screen.getByTestId('board-confirm')).toBeEnabled());
  fireEvent.click(screen.getByTestId('board-confirm'));
}

beforeEach(() => {
  vi.clearAllMocks();
  previewConfirmMany.mockImplementation(defaultPreview);
  // R-J (List is now the default view): every spec here exercises the GRID matrix,
  // so `?view=grid` is seeded by default rather than clicking the Grid button in
  // each test - one place, per the coordinator's repair note.
  // `scope=all`: the fixtures here are saved lines; the Others default is tested on its own.
  currentSearchParams = new URLSearchParams('view=grid&scope=all');
});

/**
 * Title left, actions right (captain, with a screenshot of Back overlapping the title).
 *
 * `flex items-center justify-between` does not wrap, so a long title and a control row
 * collide at narrow widths and push the whole page sideways - the exact failure the
 * responsive-header rule in CLAUDE.md exists to prevent.
 */
describe('FulfilmentBoardPanel: the header', () => {
  /**
   * R12/D2: "Back to sales orders" moved off the header row and under the gear on the action
   * bar, beside Undo all - the header row is now only the controls that decide what the board
   * SHOWS, and the way off the screen no longer competes with them for the same glance.
   */
  it('has no Back to sales orders button in the header actions any more', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    const actions = screen.getByTestId('board-header-actions');
    expect(
      within(actions).queryByRole('button', { name: 'Back to sales orders' }),
    ).not.toBeInTheDocument();
  });

  it('calls back from the gear on the action bar, which is now the sales-order list', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));
    const onBack = vi.fn();

    renderPanel(['SO403340'], onBack);
    await screen.findByTestId('fulfilment-board-matrix');

    fireEvent.keyDown(await screen.findByRole('button', { name: 'Board actions' }), {
      key: 'Enter',
    });
    fireEvent.click(
      screen.getByRole('menuitem', { name: 'Back to sales orders' }),
    );

    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('lets the header wrap instead of overlapping the title', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    const header = screen.getByTestId('board-header');
    expect(header.className).toContain('flex-col');
    expect(header.className).toContain('sm:flex-row');
    expect(screen.getByTestId('board-header-title').className).toContain(
      'min-w-0',
    );
    expect(screen.getByTestId('board-header-actions').className).toContain(
      'flex-wrap',
    );
  });
});

describe('FulfilmentBoardPanel: the axes', () => {
  it('orders the buckets chronologically and pins No date last, with no Overdue column at all', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({ line_no: 1, required_date: '2026-09-04' }),
        demand({ line_no: 2, required_date: '2022-07-03' }),
        demand({ line_no: 3, required_date: null, fulfilment_location: null }),
      ]),
    );

    renderPanel();

    const matrix = await screen.findByTestId('fulfilment-board-matrix');
    const headers = within(matrix)
      .getAllByRole('columnheader')
      .map((node) => node.textContent ?? '');
    expect(headers[0]).toBe('Product');
    expect(headers[1]).toContain('27 Jun 2022');
    expect(headers[2]).toContain('31 Aug 2026');
    expect(headers[headers.length - 1]).toContain('No date');
    expect(headers.some((header) => header.includes('Overdue'))).toBe(false);
  });

  /**
   * The captain, verbatim: "what does w/c 2 Nov 2026 mean? what does w/c mean?" - and then,
   * on being offered replacements, "just remove it". Having to ask IS the verdict: "w/c" is
   * week-commencing, a scheduling abbreviation, and the granularity control already says the
   * board is by week, so the column has nothing left to restate.
   */
  it('heads a week column with the date alone, with no abbreviation to decode', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([demand({ required_date: '2026-11-04' })]),
    );

    renderPanel();

    const matrix = await screen.findByTestId('fulfilment-board-matrix');
    const header = matrix.querySelector('[data-bucket="2026-11-02"]');
    expect(header?.textContent).toBe('2 Nov 2026');
  });

  it('strips the abbreviation from the cell dialog’s title too', async () => {
    const board = boardOf([
      demand({ required_date: '2026-11-04', item_code: 'WESERP10B' }),
    ]);
    getPlanningBoard.mockResolvedValue({
      ...board,
      dateBuckets: board.dateBuckets.map((bucket) => ({
        ...bucket,
        label: `w/c ${bucket.label}`,
      })),
    });

    renderPanel(['SO403340']);
    fireEvent.click(
      await screen.findByRole('button', {
        name: /WESERP10B, 100 across 1 sales order/,
      }),
    );

    expect(
      await screen.findByText('WESERP10B · 2 Nov 2026'),
    ).toBeInTheDocument();
  });

  it('strips the abbreviation even when the server is still sending it', async () => {
    // Bridge: the label is formatted server-side, so until that lane lands the client must not
    // put jargon on screen. Idempotent - it does nothing once the server stops sending it.
    const board = boardOf([demand({ required_date: '2026-11-04' })]);
    getPlanningBoard.mockResolvedValue({
      ...board,
      dateBuckets: board.dateBuckets.map((bucket) => ({
        ...bucket,
        label: `w/c ${bucket.label}`,
      })),
    });

    renderPanel();

    const matrix = await screen.findByTestId('fulfilment-board-matrix');
    expect(
      matrix.querySelector('[data-bucket="2026-11-02"]')?.textContent,
    ).toBe('2 Nov 2026');
  });

  /**
   * The captain's instruction, and the thing that made this a bug rather than a preference:
   * a 2022 date is its own column, tinted, and its quantity is NOT summed into anything else.
   */
  it('gives a bucket dated years back its own column, tinted, outside any aggregate', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({ line_no: 1, qty: '40', required_date: '2022-07-03' }),
        demand({ line_no: 2, qty: '100', required_date: '2026-09-04' }),
      ]),
    );

    renderPanel();

    const matrix = await screen.findByTestId('fulfilment-board-matrix');
    const past = matrix.querySelector('[data-bucket="2022-06-27"]');
    expect(past).not.toBeNull();
    expect(past?.getAttribute('data-past')).toBe('true');
    expect(
      matrix
        .querySelector('[data-bucket="2026-08-31"]')
        ?.getAttribute('data-past'),
    ).toBe('false');
    // Its quantity stands alone in its own cell rather than being rolled into a later one.
    expect(
      within(matrix).getByRole('button', {
        name: /WESERP10B, 40 across 1 sales order/,
      }),
    ).toBeInTheDocument();
    expect(
      within(matrix).getByRole('button', {
        name: /WESERP10B, 100 across 1 sales order/,
      }),
    ).toBeInTheDocument();
  });

  /**
   * The two `is_past` flags answer different questions, and this is the one that catches it:
   * a line due two days ago sits in the week that CONTAINS as_of, so its bucket is not past
   * (some of that week is still to come) while the line certainly is. Counting off the bucket
   * would report zero late lines on a board full of them.
   */
  it('counts late LINES, not late buckets, so a line due this week still counts', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({ line_no: 1, required_date: '2026-08-17' }),
        demand({ line_no: 2, required_date: '2026-08-20' }),
      ]),
    );

    renderPanel();

    const matrix = await screen.findByTestId('fulfilment-board-matrix');
    // One bucket, the week of as_of, and it is NOT tinted.
    expect(
      matrix
        .querySelector('[data-bucket="2026-08-17"]')
        ?.getAttribute('data-past'),
    ).toBe('false');
    // AC-C5 (26 August 2026): the banner that used to say so is gone. What remains is the
    // per-bucket tint, which is what the header already announces.
    expect(
      screen.queryByText(/lines are already past their delivery date/),
    ).toBeNull();
  });

  it('renders the products down the side', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({ item_code: 'WESERP10B' }),
        demand({ item_code: 'TPE-9204', line_no: 2 }),
      ]),
    );

    renderPanel();

    const matrix = await screen.findByTestId('fulfilment-board-matrix');
    const rows = within(matrix)
      .getAllByRole('rowheader')
      .map((node) => node.textContent);
    expect(rows).toEqual(['TPE-9204', 'WESERP10B']);
  });
});

/**
 * S1 (`PLAN-board-oi-mechanical-22sep.md`, AC-B1-1..5): the board's exact-date grid. The
 * period select gains a fourth, DEFAULT option that keys every column on the line's own
 * required date rather than a calendar bucket - day/week/month keep behaving exactly as
 * before, both as options and as explicit URL values.
 */
describe('FulfilmentBoardPanel: AC-B1 grid date (`PLAN-board-oi-mechanical-22sep.md`)', () => {
  it('AC-B1-1: offers By date (default), By day, By week, By month, in that order', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()], {}, 'date'));

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    const select = screen.getByLabelText('granularity');
    const options = within(select)
      .getAllByRole('option')
      .map((option) => option.textContent);
    expect(options).toEqual(['By date', 'By day', 'By week', 'By month']);
    expect(select).toHaveValue('date');
  });

  it('AC-B1-2: three distinct dates each get their own column, in date order, No date last', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf(
        [
          demand({ line_no: 1, required_date: '2026-11-01' }),
          demand({ line_no: 2, required_date: '2027-02-01' }),
          demand({ line_no: 3, required_date: '2027-04-01' }),
          demand({ line_no: 4, required_date: null, fulfilment_location: null }),
        ],
        {},
        'date',
      ),
    );

    renderPanel();

    const matrix = await screen.findByTestId('fulfilment-board-matrix');
    const headers = within(matrix)
      .getAllByRole('columnheader')
      .map((node) => node.textContent ?? '');
    expect(headers[0]).toBe('Product');
    // Product + three dated columns + No date - nothing folded, nothing extra.
    expect(headers).toHaveLength(5);
    expect(headers[1]).toContain('01/11/2026');
    expect(headers[2]).toContain('01/02/2027');
    expect(headers[3]).toContain('01/04/2027');
    expect(headers[4]).toContain('No date');
  });

  it('AC-B1-3: forty distinct dates all become their own columns - no folding, no paging', async () => {
    const lines = Array.from({ length: 42 }, (_unused, index) =>
      demand({
        line_no: index + 1,
        required_date: new Date(Date.UTC(2026, 0, 1) + index * 86_400_000)
          .toISOString()
          .slice(0, 10),
      }),
    );
    getPlanningBoard.mockResolvedValue(boardOf(lines, {}, 'date'));

    renderPanel();

    const matrix = await screen.findByTestId('fulfilment-board-matrix');
    // 42 distinct dates + the Product corner - every one of them is a column, at once.
    expect(within(matrix).getAllByRole('columnheader')).toHaveLength(43);
    // The container scrolls rather than paging or folding anything off screen.
    expect(matrix.className).toContain('overflow-auto');
    expect(screen.queryByText(/Page \d/i)).not.toBeInTheDocument();
  });

  it('AC-B1-4: a past date column under By date carries the same Already past treatment week columns do', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf(
        [
          demand({ line_no: 1, qty: '40', required_date: '2022-07-03' }),
          demand({ line_no: 2, qty: '100', required_date: '2026-09-04' }),
        ],
        {},
        'date',
      ),
    );

    renderPanel();

    const matrix = await screen.findByTestId('fulfilment-board-matrix');
    const past = matrix.querySelector('[data-bucket="2022-07-03"]');
    expect(past).not.toBeNull();
    expect(past?.getAttribute('data-past')).toBe('true');
    expect(past?.className).toContain('destructive');
    expect(past?.textContent).toContain('Already past');
    expect(
      matrix.querySelector('[data-bucket="2026-09-04"]')?.getAttribute('data-past'),
    ).toBe('false');
  });
});

/**
 * AC-S4-1 (panel), owner ruling S4 fix round #1076: the LIST view reads sales order then
 * AutoCount line number, never the grid's product axis - at the PANEL, the level that
 * actually wires `board.data.productRows` and `orderListRows`/`orderByProductRows` together
 * (the unit-level coverage of the two functions themselves lives in
 * `boardViewRowOrder.test.tsx`, which this does not replace).
 *
 * `productRows` is overridden AFTER `boardOf` builds it (that fixture always sorts the axis
 * alphabetically) to a sequence - Z, A, Q - that disagrees with BOTH the alphabet and
 * (so_number, line_no), so a panel that fed the list `orderByProductRows(contributions,
 * productRows)` would print Z, A, Q (the axis order) while the correct behaviour prints by
 * sales order then line number: SO-A's line 2 (item Q), SO-A's line 10 (item A), then SO-B's
 * line 1 (item Z) - `Q, A, Z`. The two sequences share no adjacent pair, so this fails loudly
 * under either regression: a panel that reverted to `orderByProductRows`, or one that
 * inherited a plain alphabetical/insertion order instead.
 */
describe('FulfilmentBoardPanel: AC-S4-1 (panel) - the List view reads AutoCount order, not the grid axis', () => {
  it('lists SO-A line 2, SO-A line 10, then SO-B line 1 - never the Z, A, Q product axis', async () => {
    const board = boardOf([
      demand({
        sales_order_id: 'so-b',
        so_number: 'SO-B',
        line_no: 1,
        item_code: 'Z',
        required_date: '2026-09-04',
      }),
      demand({
        sales_order_id: 'so-a',
        so_number: 'SO-A',
        line_no: 10,
        item_code: 'A',
        required_date: '2026-09-04',
      }),
      demand({
        sales_order_id: 'so-a',
        so_number: 'SO-A',
        line_no: 2,
        item_code: 'Q',
        required_date: '2026-09-04',
      }),
    ]);
    getPlanningBoard.mockResolvedValue({
      ...board,
      productRows: [
        { item_code: 'Z', description: null },
        { item_code: 'A', description: null },
        { item_code: 'Q', description: null },
      ],
    });

    renderPanel(['SO-A', 'SO-B']);
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));

    const table = await screen.findByRole('table');
    // Select, Line, Sales order, Agent, Customer, PRODUCT, OI, ... - S6
    // (`PLAN-board-oi-mechanical-22sep.md`, AC-B6-13) inserted the leftmost Line column
    // ahead of Sales order, so PRODUCT_CELL shifts from 4 to 5.
    const PRODUCT_CELL = 5;
    const productSequence = within(table)
      .getAllByRole('row')
      .slice(1) // the header row
      .map((row) => within(row).getAllByRole('cell')[PRODUCT_CELL]?.textContent ?? '');

    expect(productSequence).toEqual(['Q', 'A', 'Z']);
  });
});

describe('FulfilmentBoardPanel: the cells', () => {
  it('aggregates several orders into one cell and says how many', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({
          sales_order_id: 'so-a',
          so_number: 'SO403340',
          line_no: 1,
          qty: '100',
        }),
        demand({
          sales_order_id: 'so-b',
          so_number: 'SO398322',
          line_no: 2,
          qty: '74',
        }),
      ]),
    );

    renderPanel();

    const cell = await screen.findByRole('button', {
      name: /WESERP10B, 174 across 2 sales orders/,
    });
    expect(within(cell).getByText('174')).toBeInTheDocument();
    expect(within(cell).getByText('2 orders')).toBeInTheDocument();
  });

  it('shows the source strip when one cell draws on several locations (13.7)', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({ line_no: 1, qty: '22', fulfilment_location: 'BRW-BB' }),
        demand({ line_no: 2, qty: '21', fulfilment_location: 'BRW' }),
      ]),
    );

    renderPanel();

    // S3b: the strip is pills now, not a `·`-joined title string - one pill per location,
    // folding what does not fit into "+N". jsdom lays every element out at width 0, so only
    // the first pill shows and the second folds behind "+1" (the same trap
    // `PillOverflow.test.tsx` documents); both locations are still present either way.
    const cell = await screen.findByRole('button', {
      name: /WESERP10B, 43 across 1 sales order/,
    });
    // Scoped to the visible pill row (`role="group"`), not just the cell: the hidden
    // measuring row repeats the same label text off-screen so it can size itself, and a
    // plain `getByText` finds that copy too.
    const pills = within(cell).getByRole('group', { name: 'Locations' });
    expect(within(pills).getByText('BRW-BB 22')).toBeInTheDocument();
    expect(within(pills).getByText('+1')).toBeInTheDocument();
  });

  it('marks a cell that carries a line with no location', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({ line_no: 1, qty: '24', fulfilment_location: null }),
        demand({ line_no: 2, qty: '10' }),
      ]),
    );

    renderPanel();

    expect(await screen.findByText('1 needs a location')).toBeInTheDocument();
  });

  it('marks a cell whose stock an earlier-dated line already took', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf(
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
      ),
    );

    renderPanel();

    // The matrix cell never printed a sales-order number - this waited for text that could
    // never appear, and timed out instead of asserting anything. `fulfilment-board-matrix`
    // is what the other tests in this file already wait on.
    await screen.findByTestId('fulfilment-board-matrix');
    // The count is gone from the cell (the captain, 27 Aug); the flag stays in the data.
    expect(screen.queryByText('1 contested')).toBeNull();
  });

  it('leaves a product-and-date nobody owes blank, because a blank cell is not a zero', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({
          item_code: 'WESERP10B',
          line_no: 1,
          required_date: '2026-09-04',
        }),
        demand({
          item_code: 'TPE-9204',
          line_no: 2,
          required_date: '2026-12-01',
        }),
      ]),
    );

    renderPanel();

    const matrix = await screen.findByTestId('fulfilment-board-matrix');
    const empty = matrix.querySelector('[data-cell="TPE-9204|2026-08-31"]');
    expect(empty).not.toBeNull();
    expect(empty?.textContent).toBe('');
    // Scoped to /across/ - a cell button's own accessible name - because the source strip's
    // pills (S3b) are `role="button"` too, and an unscoped query would also count those.
    expect(
      within(matrix).getAllByRole('button', { name: /across/ }).length,
    ).toBe(2);
  });
});

/**
 * NO COMMIT SECTION (R13, UAC D5). The per-order commit cards, their own "N of M lines
 * decided" counter and "Confirms N, leaves M undecided" copy are gone with it: the one counter
 * left is the board-wide bar (`board-confirm-summary`, UAC D1/D3), and a plannable line nobody
 * touches is confirmed as the engine's own suggestion (R11) rather than needing an explicit
 * Approve to count.
 */
describe('FulfilmentBoardPanel: no commit section (D5)', () => {
  it('carries no per-order commit card, decided counter or "this order" Confirm button', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({ sales_order_id: 'so-a', so_number: 'SO403340', line_no: 1 }),
        demand({ sales_order_id: 'so-b', so_number: 'SO398322', line_no: 2 }),
      ]),
    );

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(screen.queryByText(/lines decided/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Confirms .* leaves/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Confirm this order' }),
    ).not.toBeInTheDocument();
  });
});

/**
 * AC-DT-1/AC-DT-4 (`PLAN-oi-decision-trail-ui.md`): the board header's own "Revision N,
 * confirmed by <name>, N lines" line, read off `board.data.orders[].decision` - never
 * recomputed client-side, since it is the server's own read of `so_supply_decisions`.
 */
function withOrderDecision(
  board: PlanningBoard,
  soNumber: string,
  decision: PlanningBoard['orders'][number]['decision'],
): PlanningBoard {
  return {
    ...board,
    orders: board.orders.map((order) =>
      order.so_number === soNumber ? { ...order, decision } : order,
    ),
  };
}

describe('FulfilmentBoardPanel: the decision header (AC-DT-1/AC-DT-4, PLAN-oi-decision-trail-ui.md)', () => {
  it('prints "No decision yet" for an order with no active decision', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    expect(screen.getByTestId('board-decision-header')).toHaveTextContent('No decision yet');
  });

  it('prints the revision, confirmer and line count for an order with an active decision', async () => {
    getPlanningBoard.mockResolvedValue(
      withOrderDecision(boardOf([demand()]), 'SO403340', {
        revision_no: 1,
        confirmed_by_name: 'Nurain',
        confirmed_at: '2026-09-25T01:20:34',
        line_count: 11,
      }),
    );

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    const header = screen.getByTestId('board-decision-header');
    expect(header).toHaveTextContent('Revision 1');
    expect(header).toHaveTextContent('confirmed by Nurain');
    expect(header).toHaveTextContent('11 lines');
  });

  it('prints one segment per order, order number first, when several are planned together', async () => {
    const board = withOrderDecision(
      boardOf([
        demand({ sales_order_id: 'so-a', so_number: 'SO403340', line_no: 1 }),
        demand({ sales_order_id: 'so-b', so_number: 'SO398322', line_no: 2 }),
      ]),
      'SO403340',
      { revision_no: 2, confirmed_by_name: 'Nurain', confirmed_at: '2026-09-25T01:20:34', line_count: 1 },
    );
    getPlanningBoard.mockResolvedValue(board);

    renderPanel(['SO403340', 'SO398322']);
    await screen.findByTestId('fulfilment-board-matrix');

    const header = screen.getByTestId('board-decision-header');
    expect(header).toHaveTextContent('SO403340: Revision 2');
    expect(header).toHaveTextContent('SO398322: No decision yet');
  });
});

/**
 * The counter the captain asked to see (D1/D3), at the granularity that used to lie about it.
 *
 * The board-wide bar is built off `board.data.contributions` - the SELECTION, never `cells`,
 * which at day granularity is a 30-day window. So a forty-line order reads "40 to confirm" at
 * both granularities, never "3 to confirm" because only three cells made it onto the day view.
 */
describe('FulfilmentBoardPanel: the confirm counter is selection-scoped, not window-scoped', () => {
  /** Three lines inside the first day window, thirty-seven far outside it. */
  function fortyLines() {
    const inside = Array.from({ length: 3 }, (_unused, index) =>
      demand({
        line_no: index + 1,
        item_code: `IN-${index}`,
        required_date: '2026-09-04',
      }),
    );
    // A Monday: `weekStart` of a Monday is itself, so the day and week bucket keys for this
    // date coincide and a contribution's `key` (which embeds the bucket key) survives the
    // granularity switch below - a Tuesday would not (bucketKeyFor: 3 Jan / 4 Jan bucket
    // themselves differently for day vs week), and the verdict would silently un-decide.
    const outside = Array.from({ length: 37 }, (_unused, index) =>
      demand({
        line_no: 100 + index,
        item_code: `OUT-${index}`,
        required_date: '2028-01-03',
      }),
    );
    return [...inside, ...outside];
  }

  /** Expands the one row of the just-opened cell dialog - every line here is on SO403340. */
  function expandRow() {
    openLinesTab();
    fireEvent.click(screen.getByText('SO403340'));
  }

  it('counts all forty lines to confirm at day granularity, where only three are on screen', async () => {
    const lines = fortyLines();
    getPlanningBoard.mockImplementation(
      (_orders: unknown, granularity: BoardGranularity) =>
        Promise.resolve(allSaved(boardOf(lines, {}, granularity))),
    );

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    // Every one of the forty lines is SAVED (8 Sep 2026 ruling, reverses R11), so all forty
    // count regardless of the window.
    expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
      '40 to confirm · 0 rejected',
    );

    fireEvent.change(screen.getByLabelText('granularity'), {
      target: { value: 'day' },
    });
    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(
        ['SO403340'],
        'day',
        false,
        {},
      ),
    );

    // The window shows three of the forty; the counter must still say forty. Scoped to
    // /across/ - a cell button's own accessible name - because the source strip's pills
    // (S3b) are `role="button"` too, and an unscoped query would also count those.
    const matrix = await screen.findByTestId('fulfilment-board-matrix');
    expect(
      within(matrix).getAllByRole('button', { name: /across/ }),
    ).toHaveLength(3);
    await waitFor(() =>
      expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
        '40 to confirm · 0 rejected',
      ),
    );
  });

  it('counts a rejection against the whole selection, not the window', async () => {
    const lines = fortyLines();
    getPlanningBoard.mockImplementation(
      (_orders: unknown, granularity: BoardGranularity) =>
        Promise.resolve(allSaved(boardOf(lines, {}, granularity))),
    );

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    fireEvent.change(screen.getByLabelText('granularity'), {
      target: { value: 'day' },
    });
    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(
        ['SO403340'],
        'day',
        false,
        {},
      ),
    );

    fireEvent.click(
      await screen.findByRole('button', {
        name: /IN-0, 100 across 1 sales order/,
      }),
    );
    expandRow();
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'This line is being replaced.' },
    });
    // Reject is async now (mirrors Save): closing the dialog before the write settles would
    // still find the row `dirty` and prompt to discard it instead of closing outright.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    });
    closeDialog();

    await waitFor(() =>
      expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
        '39 to confirm · 1 rejected',
      ),
    );
  });

  it('keeps a rejected verdict counted after the window scrolls past the cell it was made on', async () => {
    const lines = fortyLines();
    getPlanningBoard.mockImplementation(
      (_orders: unknown, granularity: BoardGranularity) =>
        Promise.resolve(allSaved(boardOf(lines, {}, granularity))),
    );

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    // Decide a line that only the WEEK board shows...
    fireEvent.click(
      await screen.findByRole('button', {
        name: /OUT-0, 100 across 1 sales order/,
      }),
    );
    expandRow();
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'This line is being replaced.' },
    });
    // Reject is async now (mirrors Save): closing the dialog before the write settles would
    // still find the row `dirty` and prompt to discard it instead of closing outright.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    });
    closeDialog();
    await waitFor(() =>
      expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
        '39 to confirm · 1 rejected',
      ),
    );

    // ...then move to a window that does not contain it. The verdict is still the planner's.
    fireEvent.change(screen.getByLabelText('granularity'), {
      target: { value: 'day' },
    });
    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(
        ['SO403340'],
        'day',
        false,
        {},
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
        '39 to confirm · 1 rejected',
      ),
    );
  });
});

/**
 * D16 (captain, 3 Sep, screenshots: "very choppy"). A save or Undo no longer reaches this at
 * all - `useLineDraftMutation` patches the cache without asking for a refetch - so what is
 * left to prove here is Confirm's OWN background refetch, on the SAME selection: the rows
 * already on screen must stay mounted and merely dim, never drop to a skeleton or an empty
 * state, for the length of that round trip. (A granularity/day-window turn is a genuinely
 * DIFFERENT selection and keeps the true skeleton - see the note on `usePlanningBoard`.)
 */
describe('FulfilmentBoardPanel: a background refetch dims the board, never blanks it (D16)', () => {
  it('keeps the matrix mounted and dims it while Confirm’s own refetch is in flight', async () => {
    getPlanningBoard.mockResolvedValueOnce(allSaved(boardOf([demand()])));
    confirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          decision_revision: 1,
          inquiry_rows_created: 0,
          transfers_written: 0,
        },
      ],
    });

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    expect(screen.getByTestId('board-content')).toHaveClass('opacity-100');

    // Confirm's own success invalidates the board (it changes what every OTHER order can be
    // offered) - held open here so the in-flight moment can be asserted.
    let resolveRefetch: (value: PlanningBoard) => void = () => {};
    getPlanningBoard.mockImplementationOnce(
      () =>
        new Promise<PlanningBoard>((resolve) => {
          resolveRefetch = resolve;
        }),
    );

    await pressPreviewThenConfirm();
    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));

    // In flight: the row already on screen stays mounted and dims - never a skeleton, never
    // the empty state - which is the flicker itself (the captain, 3 Sep: "very choppy").
    await waitFor(() =>
      expect(screen.getByTestId('board-content')).toHaveClass('opacity-60'),
    );
    expect(screen.getByTestId('fulfilment-board-matrix')).toBeInTheDocument();
    expect(screen.queryByText(/No lines (to plan|in these dates)/)).not.toBeInTheDocument();

    resolveRefetch(allSaved(boardOf([demand()])));

    await waitFor(() =>
      expect(screen.getByTestId('board-content')).toHaveClass('opacity-100'),
    );
  });
});

/**
 * C4 (code review round 3 batch 2): a stale saved line is dropped from Confirm with no
 * trace beyond the pill, which is easy to miss on a board of forty lines - `changed` names
 * it in the header and again in the Confirm dialog.
 */
describe('FulfilmentBoardPanel: a stale saved line is named as "changed" (C4)', () => {
  function boardWithOneStaleLine() {
    const board = boardOf([demand()]);
    return withContribution(
      board,
      () => true,
      (entry) => ({
        ...entry,
        draft: {
          decision: { verdict: 'amended' },
          saved_by: 'Test Planner',
          saved_at: '2026-09-03T00:00:00Z',
          stale: true,
        },
      }),
    );
  }

  it('says nothing about "changed" when nothing saved is stale', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));
    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    expect(screen.getByTestId('board-confirm-summary')).not.toHaveTextContent('changed');
  });

  it('adds "· N changed" to the header summary once a saved line is stale', async () => {
    getPlanningBoard.mockResolvedValue(boardWithOneStaleLine());
    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent('1 changed');
  });

});

/**
 * The ranking policy is NOT stated at the top of the board (13.5, amended 18 August).
 *
 * The banner named the rule and listed its weights above every board, and the captain's verdict
 * was "this text is not needed at the top". It was answering a question nobody had asked, in the
 * place where the columns and the grid have to be. The name survives where somebody IS asking -
 * above the factor table in the rank popover - so nothing is lost, it has simply moved to the
 * moment it is wanted.
 */
describe('FulfilmentBoardPanel: no ranking banner at the top (13.5)', () => {
  /** A board with the policy the server actually sends, flag and all. */
  function boardWithPolicy(policy: Partial<BoardPolicy>) {
    const board = boardOf([demand()]);
    return { ...board, policy: { ...board.policy, ...policy } };
  }

  it('does not name the policy above the board', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(
      screen.queryByText("Today's rule (PO document sequence)"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Ranked by/)).not.toBeInTheDocument();
  });

  it('does not print the weights above the board either', async () => {
    getPlanningBoard.mockResolvedValue(
      boardWithPolicy({
        name: 'Fulfilment board preview',
        factors: { need_by_date: 3, document_age: 1 },
        discriminates_nothing: false,
      }),
    );

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(
      screen.queryByText('Delivery date 3 · Order date 1'),
    ).not.toBeInTheDocument();
  });

  /**
   * The flatness sentence goes with it. It was written when the live policy weighted only
   * `po_document_sequence` and ranked nothing at all; the fair policy is live now and STILL
   * reports `discriminates_nothing` on a single-order board, because customer, order date and
   * demand class are constant across one order. So the sentence fired on healthy boards, and a
   * warning that is usually wrong teaches people to ignore warnings. What a cell could not
   * separate is still said inside the cell, by `rankingNote`, where it is about those rows.
   */
  it('does not warn that the ranking is flat, even when the server says it is', async () => {
    getPlanningBoard.mockResolvedValue(
      boardWithPolicy({
        name: 'Weighted but useless here',
        factors: { demand_class: 3, need_by_date: 0 },
        discriminates_nothing: true,
      }),
    );

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(screen.queryByText(/the ranking is flat/)).not.toBeInTheDocument();
  });

  it('carries no preview controls at all, in either direction', async () => {
    getPlanningBoard.mockResolvedValue(
      boardWithPolicy({ name: 'Fulfilment board preview', is_preview: true }),
    );

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(screen.queryByText('Preview, not live')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Back to the live policy' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Preview a fairer weighting' }),
    ).not.toBeInTheDocument();
  });
});

describe('FulfilmentBoardPanel: the calendar control (13.3)', () => {
  it('offers day, week and month', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel();

    const select = await screen.findByLabelText('granularity');
    expect(
      within(select).getByRole('option', { name: 'By day' }),
    ).toBeInTheDocument();
    expect(
      within(select).getByRole('option', { name: 'By week' }),
    ).toBeInTheDocument();
    expect(
      within(select).getByRole('option', { name: 'By month' }),
    ).toBeInTheDocument();
  });

  it('asks the service for the granularity the planner chose', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    fireEvent.change(screen.getByLabelText('granularity'), {
      target: { value: 'month' },
    });

    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(
        ['SO403340'],
        'month',
        false,
        {},
      ),
    );
  });

  /** Deviation 7: the day view scrolls a 30-day window, and the server is told which one. */
  it('offers a window to scroll only at day granularity, and sends it', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    expect(
      screen.queryByRole('button', { name: 'Later days' }),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('granularity'), {
      target: { value: 'day' },
    });
    // Wait for the day board to land before scrolling: the window is anchored on the board's
    // own first dated bucket, so scrolling a board that has not arrived anchors on nothing.
    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(
        ['SO403340'],
        'day',
        false,
        {},
      ),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Later days' }));

    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(
        ['SO403340'],
        'day',
        false,
        expect.objectContaining({
          dayWindow: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/),
        }),
      ),
    );
  });

  /**
   * The step is the contract's thirty days, and the anchor is the window the planner asked
   * for. Stepping by the dated columns that came back skipped or re-showed days whenever the
   * server sent fewer than thirty, and a window with no dated column at all could not move.
   */
  it('steps by thirty days even when the server sent fewer dated columns', async () => {
    const dayBoard = boardOf(
      [demand({ required_date: '2026-09-04' })],
      {},
      'day',
    );
    getPlanningBoard.mockResolvedValue({
      ...dayBoard,
      dateBuckets: dayBoard.dateBuckets
        .filter((bucket) => bucket.kind === 'dated')
        .slice(0, 5),
    });
    currentSearchParams = new URLSearchParams('scope=all&view=grid&granularity=day');

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    const first = dayBoard.dateBuckets.find(
      (bucket) => bucket.kind === 'dated',
    )?.start;
    expect(first).toBe('2026-09-04');

    fireEvent.click(await screen.findByRole('button', { name: 'Later days' }));
    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenLastCalledWith(
        ['SO403340'],
        'day',
        false,
        {
          dayWindow: '2026-10-04',
        },
      ),
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Later days' }));
    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenLastCalledWith(
        ['SO403340'],
        'day',
        false,
        {
          dayWindow: '2026-11-03',
        },
      ),
    );
  });

  it('still moves off a window with no dated column in it', async () => {
    const dayBoard = boardOf(
      [demand({ required_date: '2026-09-04' })],
      {},
      'day',
    );
    getPlanningBoard.mockResolvedValue(dayBoard);
    currentSearchParams = new URLSearchParams('scope=all&view=grid&granularity=day');

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    fireEvent.click(await screen.findByRole('button', { name: 'Later days' }));
    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenLastCalledWith(
        ['SO403340'],
        'day',
        false,
        {
          dayWindow: '2026-10-04',
        },
      ),
    );

    // The next window has nothing owed in it: no cells, no dated columns.
    getPlanningBoard.mockResolvedValue({
      ...dayBoard,
      dateBuckets: [],
      cells: [],
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Later days' }));
    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenLastCalledWith(
        ['SO403340'],
        'day',
        false,
        {
          dayWindow: '2026-11-03',
        },
      ),
    );
    await screen.findByText('No lines in these dates');

    fireEvent.click(screen.getByRole('button', { name: 'Earlier days' }));
    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenLastCalledWith(
        ['SO403340'],
        'day',
        false,
        {
          dayWindow: '2026-10-04',
        },
      ),
    );
  });

  it('asks the server for the live policy, the preview offer having been retired', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    // The what-if existed to show a fair weighting before one was switched on. It is now the
    // live one, so every board is fetched against the live policy and nothing offers to
    // preview it against itself. S1 (`PLAN-board-oi-mechanical-22sep.md`, AC-B1-1): an
    // absent granularity param now resolves to `date`, not `week`.
    expect(getPlanningBoard).toHaveBeenCalledWith(
      ['SO403340'],
      'date',
      false,
      {},
    );
    expect(
      screen.queryByRole('button', { name: 'Preview a fairer weighting' }),
    ).not.toBeInTheDocument();
  });
});

describe('FulfilmentBoardPanel: states', () => {
  it('reports a failure instead of an empty grid', async () => {
    getPlanningBoard.mockRejectedValue(new Error('Backend is down'));

    renderPanel();

    expect(
      await screen.findByText('The planning board could not be loaded'),
    ).toBeInTheDocument();
    expect(screen.getByText('Backend is down')).toBeInTheDocument();
  });

  /**
   * AC-S3-1 (14 September 2026 ruling). The board asks who decided where a line's stock comes
   * from, not whether delivery is still outstanding - a delivered line nobody decided is ON
   * this board - so the only genuinely empty selection is one whose every line is cancelled or
   * marked no purchase needed, and the copy has to say that. "Nothing is outstanding on these
   * sales orders that can be planned" told a planner looking at an unplanned completed order
   * that there was nothing to do, which was the bug the owner reported off SO421404.
   */
  it('says so when the selection owes nothing plannable', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([]));

    renderPanel();

    expect(
      await screen.findByText('No lines to plan on these sales orders'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Every line is cancelled or marked no purchase needed.'),
    ).toBeInTheDocument();
    // The retired wording is gone from the component AND from this file.
    expect(screen.queryByText(/Nothing is outstanding/)).not.toBeInTheDocument();
  });

  /**
   * The same window-scoped mistake as the banner, in the emptiest possible form: a day window
   * scrolled to a stretch nobody owes has no cells, and "these orders owe nothing" is then a
   * flat contradiction of the 161 lines the selection is holding.
   */
  it('does not call the selection empty when the window is merely showing nothing', async () => {
    const board = boardOf([demand()]);
    getPlanningBoard.mockResolvedValue({
      ...board,
      granularity: 'day',
      cells: [],
      line_count: 161,
      past_line_count: 130,
    });

    renderPanel();

    expect(await screen.findByText('No lines in these dates')).toBeInTheDocument();
    expect(
      screen.getByText('The selection holds 161 lines on other dates.'),
    ).toBeInTheDocument();
    expect(
      screen.queryByText('No lines to plan on these sales orders'),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Nothing is outstanding/)).not.toBeInTheDocument();
  });

  it('does not flash an empty or error state while loading', () => {
    getPlanningBoard.mockReturnValue(new Promise(() => {}));

    renderPanel();

    expect(
      screen.queryByText('These sales orders owe nothing that can be planned'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('The planning board could not be loaded'),
    ).not.toBeInTheDocument();
  });
});

/**
 * Confirm, which the captain asked about as a question: "so now when i click the confirm 8
 * lines, it won't work and won't flow to order inquiries isit?" It did not - the button had no
 * onClick at all. It posts to the SAME per-order endpoint the sheet uses (13.4: the board is a
 * lens, and grows no second write path).
 *
 * The two things worth pinning hardest are what happens when it goes wrong: a refusal must not
 * cost the planner the work they did, and it must say which lines were refused and why.
 */
/**
 * ONE CONFIRM (UAC D1/D2/D4/D6). The board no longer builds its body from ticked rows;
 * `confirmLinesFor` posts every plannable, SAVED, non-rejected line as its own suggestion once
 * the planner presses the single header button, and every order writes in ONE `confirmMany`
 * call. Since the 8 Sep 2026 ruling (reverses R11) a line must be SAVED to post, so this
 * describe block's fixture saves both lines of its order.
 */
describe('FulfilmentBoardPanel: Confirm actually confirms', () => {
  function twoLineOrder() {
    return allSaved(
      boardOf([
        demand({ line_no: 1, item_code: 'WESERP10B' }),
        demand({ line_no: 2, item_code: 'TPE-9204' }),
      ]),
    );
  }

  async function openConfirmDialog() {
    await waitFor(() => expect(screen.getByTestId('board-confirm')).toBeEnabled());
    fireEvent.click(screen.getByTestId('board-confirm'));
  }

  it('posts every plannable line as the engine’s own suggestion, in one confirmMany call (D2)', async () => {
    getPlanningBoard.mockResolvedValue(twoLineOrder());
    confirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          decision_revision: 1,
          inquiry_rows_created: 0,
          transfers_written: 0,
        },
      ],
    });

    renderPanel(['SO403340']);
    await openConfirmDialog();

    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    const [body] = confirmMany.mock.calls[0];
    expect(body.orders).toHaveLength(1);
    expect(body.orders[0].pso_id).toBe('pso-so-a');
    expect(
      body.orders[0].lines
        .map((line: { project_line_id: string }) => line.project_line_id)
        .sort(),
    ).toEqual(['pl-so-a-1', 'pl-so-a-2']);
  });

  it('names the D6 toast numbers: lines confirmed, transfers proposed, inquiry rows', async () => {
    getPlanningBoard.mockResolvedValue(twoLineOrder());
    confirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          decision_revision: 1,
          inquiry_rows_created: 1,
          transfers_written: 1,
        },
      ],
    });

    renderPanel(['SO403340']);
    await openConfirmDialog();

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        '2 lines confirmed · 1 transfer proposed · 1 inquiry row',
      ),
    );
  });

  it('names the movements it KEPT beside the ones it raised (R16)', async () => {
    getPlanningBoard.mockResolvedValue(twoLineOrder());
    confirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          decision_revision: 2,
          inquiry_rows_created: 0,
          transfers_written: 1,
          transfers_kept: 3,
        },
      ],
    });

    renderPanel(['SO403340']);
    await openConfirmDialog();

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        '2 lines confirmed · 1 transfer proposed · 3 kept · 0 inquiry rows',
      ),
    );
  });

  it('refetches the board once the confirmation lands', async () => {
    getPlanningBoard.mockResolvedValue(twoLineOrder());
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-so-a', ok: true, decision_revision: 1 }],
    });

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    const boardCallsBefore = getPlanningBoard.mock.calls.length;

    await openConfirmDialog();

    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(getPlanningBoard.mock.calls.length).toBeGreaterThan(
        boardCallsBefore,
      ),
    );
  });

  it('keeps the draft and shows the order’s own refusal when the server refuses it', async () => {
    getPlanningBoard.mockResolvedValue(twoLineOrder());
    confirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: false,
          error: 'The sales order moved on underneath this plan.',
        },
      ],
    });

    renderPanel(['SO403340']);
    await openConfirmDialog();

    expect(
      await screen.findByText(
        'SO403340: The sales order moved on underneath this plan.',
      ),
    ).toBeInTheDocument();
    // The planner does not lose their work to a refusal: still 2 plannable to confirm.
    expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
      '2 to confirm · 0 rejected',
    );
  });

  /**
   * Adoption mirrored the order's open lines when it ran, so a later upload can add a core line
   * with no mirror. The order is still confirmable; that line is not, and it is NAMED rather
   * than silently dropped. Both lines here are SAVED (8 Sep 2026 ruling, reverses R11: an
   * uncovered line has to be saved before Confirm counts it at all), so the unpostable one is
   * a TOUCHED (named) line, never the "N untouched" count.
   */
  it('names a plannable line that has no mirror, and leaves it out of the Confirm count', async () => {
    const board = twoLineOrder();
    getPlanningBoard.mockResolvedValue(
      withContribution(
        board,
        (entry) => entry.item_code === 'TPE-9204',
        (entry) => ({ ...entry, project_line_id: null }),
      ),
    );
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-so-a', ok: true, decision_revision: 1 }],
    });

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    const banner = await screen.findByTestId('board-left-out-banner');
    expect(banner).toHaveAttribute('role', 'alert');
    expect(banner).toHaveTextContent(
      'is not on the planning record yet, so this confirmation leaves it out. Re-sync the sales order to add it.',
    );
    expect(
      within(banner).getByRole('button', { name: 'TPE-9204 line 2' }),
    ).toBeInTheDocument();
    // FULFIL-CONFIRM-SCOPE fix round 1: on an ADOPTED order a line with no mirror is left out
    // and named, so the count no longer includes it - title, list, button and body agree.
    expect(screen.getByTestId('board-confirm')).toHaveTextContent('Confirm (1)');

    await openConfirmDialog();
    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    expect(confirmMany.mock.calls[0][0].orders[0].lines).toHaveLength(1);
  });

  /**
   * The other two ways a line cannot be posted used to be SILENT: the body left the line out
   * and the button said "Confirm 7 lines" beside eight verdicts, with nothing saying which one
   * was missing or why. Both are named now, and the count agrees.
   */
  it('names a SAVED line whose Reserve the board cannot address, and leaves it out', async () => {
    const board = twoLineOrder();
    getPlanningBoard.mockResolvedValue(
      withContribution(
        board,
        (entry) => entry.item_code === 'TPE-9204',
        (entry) => ({
          ...entry,
          qty_proposed_reserve: '100',
          qty_proposed_buy: '0',
          sources: [
            {
              kind: 'reserve' as const,
              qty: '100',
              location: 'BRW-BB',
              reason: 'Covered.',
            },
          ],
        }),
      ),
    );

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    const banner = await screen.findByTestId('board-left-out-banner');
    expect(banner).toHaveTextContent(
      'reserves at a warehouse the board cannot address, so this confirmation leaves it out. Amend it to place the Reserve.',
    );
    expect(
      within(banner).getByRole('button', { name: 'TPE-9204 line 2' }),
    ).toBeInTheDocument();
    expect(screen.getByTestId('board-confirm')).toHaveTextContent(
      'Confirm (1)',
    );
  });

  it('AC-20: includes a SAVED, approved-as-is Buy of a discontinued product that carries no reason - no banner, full count', async () => {
    const board = twoLineOrder();
    getPlanningBoard.mockResolvedValue(
      withContribution(
        board,
        (entry) => entry.item_code === 'TPE-9204',
        (entry) => ({
          ...entry,
          item_flags: {
            dealer_hot_selling: false,
            dealer_hot_selling_where: [],
            project_hot_selling: false,
            project_hot_selling_where: [],
            dealer_classified: false,
            project_classified: false,
            discontinued: true,
            retail_classification_available: true,
          },
        }),
      ),
    );
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-so-a', ok: true, decision_revision: 1 }],
    });

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    // AC-20: no banner renders for a discontinued Buy with no reason - it is no longer
    // "left out" for that cause.
    expect(screen.queryByTestId('board-left-out-banner')).not.toBeInTheDocument();
    expect(screen.getByTestId('board-confirm')).toHaveTextContent('Confirm (2)');

    await openConfirmDialog();
    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    expect(
      confirmMany.mock.calls[0][0].orders[0].lines
        .map((line: { project_line_id: string }) => line.project_line_id)
        .sort(),
    ).toEqual(['pl-so-a-1', 'pl-so-a-2']);
  });

  /**
   * AC-21: one line left out for `no_mirror`, and one discontinued Buy with no reason on the
   * SAME order - the banner must name only the `no_mirror` line, and the discontinued cause's
   * old clause ("discontinued product with no reason given") must not appear anywhere on the
   * page, since D2 removed that reason kind from the left-out banner entirely.
   */
  it('AC-21: names only the no_mirror line when a discontinued Buy with no reason sits alongside it, and never mentions the discontinued clause', async () => {
    const board = allSaved(
      boardOf([
        demand({ line_no: 1, item_code: 'WESERP10B' }),
        demand({ line_no: 2, item_code: 'TPE-9204' }),
        demand({ line_no: 3, item_code: 'OLD-1' }),
      ]),
    );
    const withNoMirror = withContribution(
      board,
      (entry) => entry.item_code === 'TPE-9204',
      (entry) => ({ ...entry, project_line_id: null }),
    );
    const withDiscontinued = withContribution(
      withNoMirror,
      (entry) => entry.item_code === 'OLD-1',
      (entry) => ({
        ...entry,
        item_flags: {
          dealer_hot_selling: false,
          dealer_hot_selling_where: [],
          project_hot_selling: false,
          project_hot_selling_where: [],
          dealer_classified: false,
          project_classified: false,
          discontinued: true,
          retail_classification_available: true,
        },
      }),
    );
    getPlanningBoard.mockResolvedValue(withDiscontinued);

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    const banner = await screen.findByTestId('board-left-out-banner');
    expect(
      within(banner).getByRole('button', { name: 'TPE-9204 line 2' }),
    ).toBeInTheDocument();
    expect(
      within(banner).queryByRole('button', { name: 'OLD-1 line 3' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/discontinued product with no reason given/i),
    ).not.toBeInTheDocument();
  });

  /**
   * Board-confirm-left-out AC-4/AC-5: the owner's own words on SO420745 - "we should have a
   * banner to raise attention, with hyperlink to go to that line directly". The link switches
   * the board to List (the grid's cells pivot by product/date and do not carry one row per
   * line) and opens that line's own decision panel, scrolled into view.
   */
  it('the banner link switches to List and opens that line’s decision panel (AC-4/AC-5)', async () => {
    // D2/AC-19-AC-20 note: this fixture used to be a discontinued Buy with no reason. That
    // cause no longer produces a left-out banner at all, so it would no longer exercise this
    // test's actual subject (the banner LINK mechanism) - switched to the `no_mirror` cause,
    // which is incidental flavor here, same as it is for the neighboring
    // "reaches the left-out line, beyond page 1..." and "states both counts in an AMBER
    // toast..." tests below.
    const board = twoLineOrder();
    getPlanningBoard.mockResolvedValue(
      withContribution(
        board,
        (entry) => entry.item_code === 'TPE-9204',
        (entry) => ({ ...entry, project_line_id: null }),
      ),
    );

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    const banner = await screen.findByTestId('board-left-out-banner');
    fireEvent.click(within(banner).getByRole('button', { name: 'TPE-9204 line 2' }));

    // Grid gone, List on screen, and the named line's own decision panel is open.
    await waitFor(() =>
      expect(screen.queryByTestId('fulfilment-board-matrix')).not.toBeInTheDocument(),
    );
    expect(
      await screen.findByTestId(/^line-decision-so-a\|2\|TPE-9204/),
    ).toBeInTheDocument();
  });

  /**
   * Fix round 2 (reviewer, B1, blocking): `setProductSearchInput('')` only moved the BOX -
   * `productSearch` (`useDebouncedSearch`'s `debouncedValue`, what the list actually filters
   * by) still lagged it by 200ms, so a banner click made while an excluding search was active
   * found the row still filtered out, the page jump saw index -1, the scroll no-op'd, and the
   * search only caught up a beat later - too late for anything still waiting on it.
   * `focusLeftOutLine` now calls the hook's own `reset('')`, which clears both halves
   * synchronously.
   */
  /**
   * B2 (fix round 3, reviewer, blocking): the round-2 version of this test used
   * `await screen.findByTestId(...)`, which POLLS for up to a second - long enough for
   * `useDebouncedSearch`'s real 200ms timer to fire on its own even with the round-2 bug
   * back in place (`setProductSearchInput('')` instead of `reset('')`), and S3's node-guard
   * (fix round 2) then found the row on ITS OWN once that later render arrived. A 2-line
   * fixture never exercised the page jump either. This version asserts SYNCHRONOUSLY, right
   * on the commit the click itself produces - no `findBy`/`waitFor`, no timer advance - which
   * only the SAME-TICK `reset('')` can satisfy, over 30 lines so the target genuinely sits
   * beyond `PanelDataGrid`'s first 25-row page.
   */
  it('reaches the left-out line, beyond page 1, on the SAME commit as the click, even while a search that excludes it is still active (AC-5, fix round 3, B1/B2)', async () => {
    const scrollSpy = vi
      .spyOn(Element.prototype, 'scrollIntoView')
      .mockImplementation(() => {});
    const lines = Array.from({ length: 30 }, (_, index) =>
      demand({ line_no: index + 1, item_code: `ITEM${index + 1}` }),
    );
    // The 28th line: index 27 in `so_number`/`line_no` order, page floor(27 / 25) = page 2.
    // D2/AC-19-AC-20 note: this fixture used to be a discontinued Buy with no reason, which no
    // longer produces a left-out banner - switched to `no_mirror` (incidental flavor, same as
    // the "banner link" test above) so this test still exercises the banner it needs.
    const board = withContribution(
      allSaved(boardOf(lines)),
      (entry) => entry.line_no === 28,
      (entry) => ({ ...entry, project_line_id: null }),
    );
    getPlanningBoard.mockResolvedValue(board);

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    const searchBox = screen.getByPlaceholderText(
      'Search sales order, customer, project or product',
    );
    // A search that EXCLUDES the left-out line (ITEM28) - not just typed, but SETTLED:
    // waiting for the grid to actually narrow is what proves the debounced value (not only
    // the box) now reads "ITEM1", which is the state the bug needed to reproduce.
    fireEvent.change(searchBox, { target: { value: 'ITEM1' } });
    await waitFor(() => expect(screen.queryByText('ITEM28')).not.toBeInTheDocument());

    const banner = await screen.findByTestId('board-left-out-banner');
    fireEvent.click(within(banner).getByRole('button', { name: 'ITEM28 line 28' }));

    // SYNCHRONOUS: `fireEvent.click` already flushed every cascading render and effect this
    // click produces (React's `act()` drains the whole chain - the state updates, the page
    // jump, the expand, the scroll - before returning), so if the search reset lagged (the
    // B1 bug) this row is STILL filtered out right here, and `getByTestId` throws immediately
    // rather than retrying past it.
    expect(screen.getByTestId(/^line-decision-so-a\|28\|ITEM28/)).toBeInTheDocument();
    // The click switched to the list, whose toolbar draws its own box: read that one.
    expect(
      screen.getByPlaceholderText('Search sales order, customer, project or product'),
    ).toHaveValue('');
    expect(scrollSpy).toHaveBeenCalledTimes(1);

    scrollSpy.mockRestore();
  });

  /**
   * Board-confirm-left-out AC-6: the owner's own words - "confirming silently is dangerous".
   * The success toast already states what Confirm committed; once a press leaves lines out it
   * states that too, in the SAME toast rather than a second one.
   */
  it('states both counts in an AMBER toast when a line is left out (AC-6, fix round 2, S4)', async () => {
    // D2/AC-19-AC-20 note: this fixture used to be a discontinued Buy with no reason, which no
    // longer leaves a line out - switched to `no_mirror` (incidental flavor) so this test still
    // exercises a real left-out line.
    const board = twoLineOrder();
    getPlanningBoard.mockResolvedValue(
      withContribution(
        board,
        (entry) => entry.item_code === 'TPE-9204',
        (entry) => ({ ...entry, project_line_id: null }),
      ),
    );
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-so-a', ok: true, decision_revision: 1 }],
    });

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    await openConfirmDialog();

    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    // S4: a press that left something out is not an unqualified success (the owner's own
    // words: "confirming silently is dangerous") - `toast.warning`, never `toast.success`.
    expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining('1 line confirmed'));
    expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining('1 left out'));
    expect(toast.success).not.toHaveBeenCalled();
  });

  // The "no planning record, so no Confirm" state is deliberately GONE: pressing Confirm on
  // such an order now adopts it first. See "Confirm adopts first when it has to" below.
});

/**
 * Adopt on confirm (the captain: "why i cannot confirm the sales order partially? like i
 * decided few lines then i should be able to confirm partially right").
 *
 * They selected nine orders, decided lines across several of them, and every Confirm refused:
 * none had been adopted, so there was no planning record to confirm against. We let them do the
 * whole job and said no at the last step. Deciding lines and pressing Confirm IS the act, so the
 * press adopts first and then confirms, as one action.
 *
 * The refetch in the middle is not optional: `project_line_id` is null on every contribution
 * until the mirror lines exist, so a body built before adoption names nothing.
 */
describe('FulfilmentBoardPanel: Confirm adopts first when it has to', () => {
  /** A board for an order nobody has adopted: no planning record, no mirror lines. */
  function unadopted() {
    const board = boardOf([
      demand({ line_no: 1, item_code: 'WESERP10B' }),
      demand({ line_no: 2, item_code: 'TPE-9204' }),
    ]);
    return allSaved(
      withContribution(
        {
          ...board,
          orders: board.orders.map((order) => ({
            ...order,
            project_sales_order_id: null,
          })),
        },
        () => true,
        (entry) => ({ ...entry, project_line_id: null }),
      ),
    );
  }

  function adopted() {
    return allSaved(
      boardOf([
        demand({ line_no: 1, item_code: 'WESERP10B' }),
        demand({ line_no: 2, item_code: 'TPE-9204' }),
      ]),
    );
  }

  async function openConfirmDialog() {
    await waitFor(() => expect(screen.getByTestId('board-confirm')).toBeEnabled());
    fireEvent.click(screen.getByTestId('board-confirm'));
  }

  it('offers Confirm on an order nobody has adopted, and never says planning has not started', async () => {
    getPlanningBoard.mockResolvedValue(unadopted());

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    // Both lines are SAVED (8 Sep 2026 ruling, reverses R11), so both plannable lines post
    // as their own suggestion.
    expect(screen.getByTestId('board-confirm')).toHaveTextContent(
      'Confirm (2)',
    );
    // Confirm waits for a Preview (v2); Preview itself is on offer.
    expect(screen.getByTestId('board-confirm')).toBeEnabled();
    expect(
      screen.queryByText('Nobody has started planning this sales order yet.'),
    ).not.toBeInTheDocument();
  });

  it('adopts, refetches, then posts the body built from the ids that arrived', async () => {
    getPlanningBoard
      .mockResolvedValueOnce(unadopted())
      .mockResolvedValue(adopted());
    adoptSalesOrder.mockResolvedValue({
      project_sales_order_id: 'pso-so-a',
      so_number: 'SO403340',
      review_state: 'needs_cs_review',
      already_adopted: false,
    });
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-so-a', ok: true, decision_revision: 1 }],
    });

    renderPanel(['SO403340']);
    await openConfirmDialog();

    await waitFor(() => expect(adoptSalesOrder).toHaveBeenCalledWith('so-a'));
    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    const [body] = confirmMany.mock.calls[0];
    // Built from the REFETCHED board: guessing an id before the mirror exists names nothing.
    expect(body.orders).toEqual([
      expect.objectContaining({
        pso_id: 'pso-so-a',
        lines: expect.arrayContaining([
          expect.objectContaining({ project_line_id: 'pl-so-a-1' }),
          expect.objectContaining({ project_line_id: 'pl-so-a-2' }),
        ]),
      }),
    ]);
  });

  it('does not adopt an order that already has a planning record', async () => {
    getPlanningBoard.mockResolvedValue(adopted());
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-so-a', ok: true, decision_revision: 2 }],
    });

    renderPanel(['SO403340']);
    await openConfirmDialog();

    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    expect(adoptSalesOrder).not.toHaveBeenCalled();
  });

  it('says so when adoption fails, and confirms nothing', async () => {
    getPlanningBoard.mockResolvedValue(unadopted());
    adoptSalesOrder.mockRejectedValue(
      new Error('Another planning record already holds SO403340.'),
    );

    renderPanel(['SO403340']);
    fireEvent.click(await screen.findByTestId('board-confirm'));

    await waitFor(() => expect(adoptSalesOrder).toHaveBeenCalledWith('so-a'));
    expect(previewConfirmMany).not.toHaveBeenCalled();
    expect(confirmMany).not.toHaveBeenCalled();
    // Nothing was committed, so the work is still the planner's.
    expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
      '2 to confirm · 0 rejected',
    );
  });

  it('shows no unmirrored notice on an order that was simply never adopted', async () => {
    getPlanningBoard.mockResolvedValue(unadopted());

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    // That notice is for a mirror that is genuinely missing a line, which is a different
    // problem with a different fix. On a not-yet-adopted order it would name every line.
    expect(
      screen.queryByText(/not on the planning record yet/),
    ).not.toBeInTheDocument();
  });
});

/**
 * `runConfirmAll` rebuilds `psoIdBySalesOrder` from the REFETCHED board only. `adopt` itself
 * already returns the id the server just created - it is the whole reason the mutation
 * resolves with a body rather than `void` - but that return value is discarded, and if the
 * refetch still reports `project_sales_order_id: null` for the order (an eventually-consistent
 * read, or a mirror the refetch's own cache has not caught up on yet), the order is silently
 * left out of the batch: `if (!psoId) continue`, then `if (orders.length === 0) return` ends
 * the press having posted nothing and rendered nothing.
 */
describe('FulfilmentBoardPanel: Confirm posts against the id adopt itself returned', () => {
  /** One order, one saved-but-cancelled line, never adopted - the refetch keeps saying so. */
  function boardStillNull() {
    const built = boardOf([
      demand({
        sales_order_id: 'so-419851',
        so_number: 'SO419851',
        line_no: 9,
        item_code: 'WESERP10B',
        qty: '0',
        project_line_id: 'pl-9',
      }),
    ]);
    return allSaved(
      withContribution(
        {
          ...built,
          orders: built.orders.map((order) => ({
            ...order,
            project_sales_order_id: null,
            pending_change_batch_id: 'b-1',
          })),
        },
        () => true,
        (entry) => ({
          ...entry,
          cancelled: true,
          covered: false,
          unplannable: false,
          qty: '0',
          qty_outstanding: '0',
        }),
      ),
    );
  }

  async function openConfirmDialog() {
    await waitFor(() => expect(screen.getByTestId('board-confirm')).toBeEnabled());
    fireEvent.click(screen.getByTestId('board-confirm'));
  }

  it('posts against the id the adopt call returned, even though the refetched board still names none', async () => {
    // The refetch (`board.refetch()`, `mockResolvedValue` covers every call) is the SAME
    // board: `project_sales_order_id` is still null, exactly the eventually-consistent read
    // that leaves the real bug room to bite.
    getPlanningBoard.mockResolvedValue(boardStillNull());
    adoptSalesOrder.mockResolvedValue({
      project_sales_order_id: 'pso-1',
      so_number: 'SO419851',
      review_state: 'needs_cs_review',
      already_adopted: true,
    });
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-1', ok: true, decision_revision: 1 }],
    });

    renderPanel(['SO419851']);
    await openConfirmDialog();

    await waitFor(() =>
      expect(adoptSalesOrder).toHaveBeenCalledWith('so-419851'),
    );
    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    const [body] = confirmMany.mock.calls[0] as [
      { orders: { pso_id: string; batch_id: string | null; lines: { project_line_id: string }[] }[] },
    ];
    expect(body.orders).toEqual([
      expect.objectContaining({
        pso_id: 'pso-1',
        batch_id: 'b-1',
        lines: expect.arrayContaining([
          expect.objectContaining({ project_line_id: 'pl-9' }),
        ]),
      }),
    ]);
  });

  it('names the sales order in the result, rather than ending the press with nothing shown', async () => {
    getPlanningBoard.mockResolvedValue(boardStillNull());
    adoptSalesOrder.mockRejectedValue(
      new Error('Another planning record already holds SO419851.'),
    );

    renderPanel(['SO419851']);
    expect(screen.queryByText(/SO419851/)).not.toBeInTheDocument();

    fireEvent.click(await screen.findByTestId('board-confirm'));

    await waitFor(() =>
      expect(adoptSalesOrder).toHaveBeenCalledWith('so-419851'),
    );
    expect(await screen.findByText(/SO419851/)).toBeInTheDocument();
    expect(confirmMany).not.toHaveBeenCalled();
  });
});

/**
 * An ADOPTED order whose lines still carry no mirror (`project_line_id: null` on every
 * contribution - a core-line re-ingest under new ids, or a mirror the adoption itself never
 * wrote). `confirmLinesFor` -> `lineFor` returns the `'no_mirror'` STRING for each of them,
 * which `confirmLinesFor` filters out (it only pushes an actual `ConfirmLine`), so `lines`
 * comes back empty. Nothing is added to `orders` for this SO, and nothing is added to
 * `skipped` either - that branch only fires when `psoId` itself is falsy, and this order has
 * one. `orders.length === 0` with `skipped` also empty ends the press with `batchResults`
 * never set: no toast, no results block, nothing on screen says the press did anything at all.
 */
describe('FulfilmentBoardPanel: a Confirm that posts nothing says so', () => {
  /** One already-adopted order; every contribution has no mirror line to post against. */
  function boardAdoptedNoMirror() {
    const built = boardOf([
      demand({
        sales_order_id: 'so-419852',
        so_number: 'SO419852',
        line_no: 1,
        item_code: 'WESERP10B',
      }),
    ]);
    return allSaved(
      withContribution(
        {
          ...built,
          orders: built.orders.map((order) => ({
            ...order,
            project_sales_order_id: 'pso-1',
          })),
        },
        () => true,
        (entry) => ({ ...entry, project_line_id: null }),
      ),
    );
  }

  it('names SO419852 in the banner and offers no press that would post nothing', async () => {
    getPlanningBoard.mockResolvedValue(boardAdoptedNoMirror());

    renderPanel(['SO419852']);
    const banner = await screen.findByTestId('board-left-out-banner');
    expect(banner).toHaveTextContent(/not on the planning record yet/i);
    // FULFIL-CONFIRM-SCOPE fix round 1: the count excludes a no-mirror line on an adopted
    // order, so there is nothing to press: the button is disabled at 0.
    expect(screen.getByTestId('board-confirm')).toBeDisabled();
    expect(adoptSalesOrder).not.toHaveBeenCalled();
    expect(confirmMany).not.toHaveBeenCalled();
  });
});

/**
 * Searching the board (the captain: "i need the search here also btw").
 *
 * The board is ONE already-fetched payload, so this filters the product ROWS in the browser: it
 * must not refetch, and it must not touch the selection. The headline totals do NOT move with
 * it - they describe the selection, not the rows on screen, which is the same lesson the day
 * window taught - so the filter states its own count instead, or a planner reads a filtered
 * board as the whole one.
 */
describe('FulfilmentBoardPanel: searching the product rows', () => {
  function catalogue() {
    return boardOf([
      demand({
        line_no: 1,
        item_code: 'WESERP10B',
        product_name: 'Wall socket 10A white',
      }),
      demand({
        line_no: 2,
        item_code: 'TPE-9204',
        product_name: 'Trunking 92mm',
      }),
      demand({
        line_no: 3,
        item_code: 'CKS1050',
        product_name: 'Ceiling kit 50',
      }),
    ]);
  }

  function productRows() {
    return [
      ...screen
        .getByTestId('fulfilment-board-matrix')
        .querySelectorAll('tbody tr'),
    ].map((row) => row.querySelector('th')?.textContent ?? '');
  }

  async function searchFor(term: string) {
    const box = screen.getByPlaceholderText(
      'Search sales order, customer, project or product',
    );
    fireEvent.change(box, { target: { value: term } });
    await waitFor(() => expect(box).toHaveValue(term));
  }

  it('narrows the rows by item code, case-insensitively', async () => {
    getPlanningBoard.mockResolvedValue(catalogue());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');
    expect(productRows()).toEqual(['CKS1050', 'TPE-9204', 'WESERP10B']);

    await searchFor('tpe');

    await waitFor(() => expect(productRows()).toEqual(['TPE-9204']));
  });

  it('narrows the rows by product name too', async () => {
    getPlanningBoard.mockResolvedValue(catalogue());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    await searchFor('ceiling');

    await waitFor(() => expect(productRows()).toEqual(['CKS1050']));
  });

  it('never refetches: the board is one payload and this is a filter over its rows', async () => {
    getPlanningBoard.mockResolvedValue(catalogue());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');
    const calls = getPlanningBoard.mock.calls.length;

    await searchFor('tpe');
    await waitFor(() => expect(productRows()).toEqual(['TPE-9204']));

    expect(getPlanningBoard.mock.calls.length).toBe(calls);
  });

  it('says how many rows are shown of how many, but only while a filter is on', async () => {
    getPlanningBoard.mockResolvedValue(catalogue());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');
    expect(screen.queryByText(/products$/)).not.toBeInTheDocument();

    await searchFor('tpe');

    expect(await screen.findByText('1 of 3 products')).toBeInTheDocument();
  });

  it('leaves the selection-scoped sentence exactly where it was', async () => {
    const board = catalogue();
    getPlanningBoard.mockResolvedValue({
      ...board,
      line_count: 161,
      past_line_count: 130,
    });

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    await searchFor('tpe');

    // The product filter narrows the GRID and nothing else: the selection sentence describes
    // what is being planned, not what a search is showing.
    await waitFor(() => expect(productRows()).toEqual(['TPE-9204']));
    expect(
      screen.getByText('Planning 2 sales orders together'),
    ).toBeInTheDocument();
  });

  it('says nothing matches, rather than claiming the selection owes nothing', async () => {
    getPlanningBoard.mockResolvedValue(catalogue());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    await searchFor('zzzz');

    expect(await screen.findByText('No products match')).toBeInTheDocument();
    expect(screen.getByText('0 of 3 products')).toBeInTheDocument();
    // The "owes nothing" copy would be a flat lie: the selection owes plenty.
    expect(
      screen.queryByText('These sales orders owe nothing that can be planned'),
    ).not.toBeInTheDocument();
  });

  it('carries the term in the URL, so a filtered board is shareable', async () => {
    getPlanningBoard.mockResolvedValue(catalogue());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    await searchFor('tpe');

    await waitFor(() =>
      expect(routerReplace).toHaveBeenCalledWith(
        '/project-sales/fulfilment-planning?view=grid&scope=all&product=tpe',
        expect.objectContaining({ scroll: false }),
      ),
    );
  });

  it('opens on the term the URL carries', async () => {
    currentSearchParams = new URLSearchParams('scope=all&view=grid&product=ceiling');
    getPlanningBoard.mockResolvedValue(catalogue());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(
      screen.getByPlaceholderText(
        'Search sales order, customer, project or product',
      ),
    ).toHaveValue('ceiling');
    await waitFor(() => expect(productRows()).toEqual(['CKS1050']));
  });

  it('clears with the X, and the whole board comes back', async () => {
    getPlanningBoard.mockResolvedValue(catalogue());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');
    await searchFor('tpe');
    await waitFor(() => expect(productRows()).toEqual(['TPE-9204']));

    fireEvent.click(
      screen.getByRole('button', { name: 'Clear search' }),
    );

    await waitFor(() =>
      expect(productRows()).toEqual(['CKS1050', 'TPE-9204', 'WESERP10B']),
    );
    expect(screen.queryByText(/of 3 products/)).not.toBeInTheDocument();
  });
});

/**
 * The board is always fetched against the LIVE policy.
 *
 * The what-if existed to show what a fair weighting would do before one was switched on. The
 * fair policy is the live one now, so the offer was retired, and the banner that carried the way
 * back went with the banner itself (the captain: "this text is not needed at the top").
 */
describe('FulfilmentBoardPanel: the live policy, and only it', () => {
  function withPolicy(policy: Partial<BoardPolicy>) {
    const board = boardOf([demand()]);
    return { ...board, policy: { ...board.policy, ...policy } };
  }

  it('never asks the server for a previewed ranking', async () => {
    getPlanningBoard.mockResolvedValue(
      withPolicy({
        name: 'Fair weighting',
        is_preview: false,
        discriminates_nothing: false,
      }),
    );

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    // S1 (`PLAN-board-oi-mechanical-22sep.md`, AC-B1-1): an absent granularity param now
    // resolves to `date`, not `week`.
    expect(getPlanningBoard).toHaveBeenCalledWith(
      ['SO403340'],
      'date',
      false,
      {},
    );
  });

  it('prints no policy identifiers anywhere above the grid', async () => {
    getPlanningBoard.mockResolvedValue(
      withPolicy({
        name: 'Fair weighting',
        factors: {
          need_by_date: 3,
          document_age: 1,
          customer_credit: 1,
          demand_class: 0,
        },
        discriminates_nothing: false,
      }),
    );

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(screen.queryByText(/need_by_date/)).not.toBeInTheDocument();
    expect(screen.queryByText(/po_document_sequence/)).not.toBeInTheDocument();
    expect(
      screen.queryByText('Delivery date 3 · Order date 1 · Payment terms 1'),
    ).not.toBeInTheDocument();
  });
});

/**
 * The granularity travels with the selection (PLAN 13.3), so the whole board is one link.
 */
describe('FulfilmentBoardPanel: granularity in the URL', () => {
  it('opens on the granularity the URL names', async () => {
    currentSearchParams = new URLSearchParams('scope=all&view=grid&granularity=month');
    getPlanningBoard.mockResolvedValue(boardOf([demand()], {}, 'month'));

    renderPanel(['SO403340']);

    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(
        ['SO403340'],
        'month',
        false,
        {},
      ),
    );
    expect(screen.getByLabelText('granularity')).toHaveValue('month');
  });

  it('AC-B1-5: a URL naming granularity=week opens week view, not the date default', async () => {
    currentSearchParams = new URLSearchParams('scope=all&view=grid&granularity=week');
    getPlanningBoard.mockResolvedValue(boardOf([demand()], {}, 'week'));

    renderPanel(['SO403340']);

    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(
        ['SO403340'],
        'week',
        false,
        {},
      ),
    );
    expect(screen.getByLabelText('granularity')).toHaveValue('week');
  });

  it('falls back to date on a granularity nobody defined', async () => {
    // S1 (`PLAN-board-oi-mechanical-22sep.md`, AC-B1-1/AC-B1-5): `date` is what an
    // unrecognised param resolves to now, the same default an ABSENT param gets - `week`
    // is still a real, explicitly-named option (`?granularity=week` is honoured as-is).
    currentSearchParams = new URLSearchParams('scope=all&view=grid&granularity=fortnightly');
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel(['SO403340']);

    await waitFor(() =>
      expect(getPlanningBoard).toHaveBeenCalledWith(
        ['SO403340'],
        'date',
        false,
        {},
      ),
    );
  });

  it('writes the granularity back when the planner turns the control', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    fireEvent.change(screen.getByLabelText('granularity'), {
      target: { value: 'month' },
    });

    await waitFor(() =>
      expect(routerReplace).toHaveBeenCalledWith(
        '/project-sales/fulfilment-planning?view=grid&scope=all&granularity=month',
        expect.objectContaining({ scroll: false }),
      ),
    );
  });

  /**
   * A link can name an order that has since been delivered, closed, or was simply mistyped.
   * Opening a board of four when the link asked for five, and saying nothing, is the kind of
   * quiet subtraction that makes a shared link untrustworthy.
   */
  it('reports an order the board came back without, rather than swallowing it', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel(['SO403340', 'SO999999']);

    expect(
      await screen.findByText('SO999999 has nothing to plan on this board.'),
    ).toBeInTheDocument();
  });

  it('says nothing when every order asked for came back', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    expect(
      screen.queryByText(/has nothing to plan on this board/),
    ).not.toBeInTheDocument();
  });
});

/**
 * The row axis, on screen (the captain: "how about if we want vertical is sales order, is
 * customer, is project").
 */
describe('FulfilmentBoardPanel: pivoting the rows', () => {
  function twoOrders() {
    return boardOf([
      demand({
        sales_order_id: 'so-a',
        so_number: 'SO000001',
        customer_name: 'ALPHA SDN BHD',
        project_label: 'TOWER A',
        line_no: 1,
        item_code: 'AAA',
        qty: '10',
      }),
      demand({
        sales_order_id: 'so-a',
        so_number: 'SO000001',
        customer_name: 'ALPHA SDN BHD',
        project_label: 'TOWER A',
        line_no: 2,
        item_code: 'BBB',
        qty: '20',
      }),
      demand({
        sales_order_id: 'so-b',
        so_number: 'SO000002',
        customer_name: 'ZULU SDN BHD',
        project_label: 'TOWER Z',
        line_no: 3,
        item_code: 'AAA',
        qty: '5',
      }),
    ]);
  }

  function rowHeaders() {
    return [
      ...screen
        .getByTestId('fulfilment-board-matrix')
        .querySelectorAll('tbody tr'),
    ].map((row) => (row.querySelector('th')?.textContent ?? '').trim());
  }

  async function pivotTo(value: string) {
    fireEvent.change(screen.getByLabelText('rows'), { target: { value } });
    await waitFor(() =>
      expect(screen.getByLabelText('rows')).toHaveValue(value),
    );
  }

  it('offers the four axes, product first', async () => {
    getPlanningBoard.mockResolvedValue(twoOrders());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    const select = screen.getByLabelText('rows');
    for (const label of ['Product', 'Sales order', 'Customer', 'Project']) {
      expect(
        within(select).getByRole('option', { name: label }),
      ).toBeInTheDocument();
    }
    expect(select).toHaveValue('product');
  });

  it('puts one row per sales order, holding every product that order owes', async () => {
    getPlanningBoard.mockResolvedValue(twoOrders());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');
    expect(rowHeaders()).toEqual(['AAA', 'BBB']);

    await pivotTo('sales_order');

    await waitFor(() => expect(rowHeaders()).toEqual(['SO000001', 'SO000002']));
    // SO000001's two products in one cell: 10 + 20.
    expect(
      screen.getByRole('button', { name: /SO000001, 30 across 1 sales order/ }),
    ).toBeInTheDocument();
  });

  it('pivots to customer and to project', async () => {
    getPlanningBoard.mockResolvedValue(twoOrders());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    await pivotTo('customer');
    await waitFor(() =>
      expect(rowHeaders()).toEqual(['ALPHA SDN BHD', 'ZULU SDN BHD']),
    );

    await pivotTo('project');
    await waitFor(() => expect(rowHeaders()).toEqual(['TOWER A', 'TOWER Z']));
  });

  it('opens the same breakdown from a pivoted cell, with the same lines', async () => {
    getPlanningBoard.mockResolvedValue(twoOrders());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');
    await pivotTo('sales_order');

    fireEvent.click(
      await screen.findByRole('button', {
        name: /SO000001, 30 across 1 sales order/,
      }),
    );
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());

    // The Contributing lines tab lists LINES, which is what the dialog always listed.
    // Scoped to the dialog because the board itself is a table too.
    openLinesTab();
    const dialog = await screen.findByRole('dialog');
    const table = within(dialog).getByRole('table');
    expect(table.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(table.textContent).toContain('AAA');
    expect(table.textContent).toContain('BBB');
  });

  it('keeps a decision made under one axis visible under another', async () => {
    // The other two lines are SAVED (8 Sep 2026 ruling, reverses R11), or Confirm would read
    // 0 before this test ever gets to reject the third.
    getPlanningBoard.mockResolvedValue(allSaved(twoOrders()));

    renderPanel(['SO000001', 'SO000002']);
    await screen.findByTestId('fulfilment-board-matrix');

    // Decide a line while the rows are products. BBB is owed by one order only, so the cell
    // holds exactly the line this test is deciding. Rejecting it is what moves the counter;
    // the other two lines already count because they are saved.
    fireEvent.click(
      await screen.findByRole('button', {
        name: /BBB, 20 across 1 sales order/,
      }),
    );
    openLinesTab();
    fireEvent.click(screen.getByText('SO000001'));
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The tower plan changed.' },
    });
    // Reject is async now (mirrors Save): closing the dialog before the write settles would
    // still find the row `dirty` and prompt to discard it instead of closing outright.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    });
    closeDialog();
    await waitFor(() =>
      expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
        '2 to confirm · 1 rejected',
      ),
    );

    // ...and it is still the same line's decision when the rows become sales orders: the
    // aggregate stays the count, and the row itself still reads Rejected under the pivot.
    await pivotTo('sales_order');
    expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
      '2 to confirm · 1 rejected',
    );

    fireEvent.click(
      await screen.findByRole('button', { name: /30 across 1 sales order/ }),
    );
    openLinesTab();
    const bbbRow = screen.getByText('Line 2').closest('tr') as HTMLElement;
    expect(within(bbbRow).getByText('Rejected')).toBeInTheDocument();
  });

  it('carries the axis in the URL, absent when it is the default', async () => {
    getPlanningBoard.mockResolvedValue(twoOrders());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    await pivotTo('customer');

    await waitFor(() =>
      expect(routerReplace).toHaveBeenCalledWith(
        '/project-sales/fulfilment-planning?view=grid&scope=all&rows=customer',
        expect.objectContaining({ scroll: false }),
      ),
    );
  });

  it('opens on the axis the URL names, and falls back to product on nonsense', async () => {
    currentSearchParams = new URLSearchParams('scope=all&view=grid&rows=project');
    getPlanningBoard.mockResolvedValue(twoOrders());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(screen.getByLabelText('rows')).toHaveValue('project');
    await waitFor(() => expect(rowHeaders()).toEqual(['TOWER A', 'TOWER Z']));
  });

  it('falls back to product on an axis nobody defined', async () => {
    currentSearchParams = new URLSearchParams('scope=all&view=grid&rows=warehouse');
    getPlanningBoard.mockResolvedValue(twoOrders());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(screen.getByLabelText('rows')).toHaveValue('product');
  });

  it('searches the four fields, and keeps whole cells while doing it', async () => {
    getPlanningBoard.mockResolvedValue(twoOrders());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    // A customer needle keeps only the product rows that customer owes...
    fireEvent.change(
      screen.getByPlaceholderText(
        'Search sales order, customer, project or product',
      ),
      {
        target: { value: 'zulu' },
      },
    );

    await waitFor(() => expect(rowHeaders()).toEqual(['AAA']));
    // ...and AAA's cell still holds BOTH orders' lines: filtering inside a cell would print a
    // total that is not the cell's.
    expect(
      screen.getByRole('button', { name: /AAA, 15 across 2 sales orders/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('1 of 2 products')).toBeInTheDocument();
  });

  it('counts the rows of whichever axis is showing', async () => {
    getPlanningBoard.mockResolvedValue(twoOrders());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');
    await pivotTo('sales_order');

    fireEvent.change(
      screen.getByPlaceholderText(
        'Search sales order, customer, project or product',
      ),
      {
        target: { value: 'SO000002' },
      },
    );

    await waitFor(() => expect(rowHeaders()).toEqual(['SO000002']));
    expect(screen.getByText('1 of 2 sales orders')).toBeInTheDocument();
  });
});

/**
 * Approve all / Confirm all approved (D3, PLAN-demo-followups-19aug-ladder-v2).
 *
 * Two orders, one undecided approvable line each: Approve all fills both blanks in the
 * draft (never a decision already taken), Confirm all approved asks before it writes
 * anything ("Confirm N decisions across M orders?"), and the write is ONE
 * `confirmMany` call grouped per order - never one call per order from the panel.
 */
/**
 * NO APPROVE ALL, NO CONFIRM ALL APPROVED (UAC D1). There is one header Confirm rather than a
 * bulk all-approved button beside it - "Save all suggested" is the bulk way to agree with the
 * engine, and Confirm then posts only what was saved (8 Sep 2026 ruling, reverses R11: silence
 * on a plannable line no longer agrees with it). What survives from the old flow - one
 * `confirmMany` call grouped per order, a per-order result, and a selection- not window-scoped
 * population - is exercised through the single Confirm (N).
 */
describe('FulfilmentBoardPanel: one Confirm, not Approve all (D1, D4)', () => {
  /** Both lines SAVED (8 Sep 2026 ruling, reverses R11), or Confirm reads 0 from the start. */
  function twoUndecidedOrders() {
    return allSaved(
      boardOf([
        demand({
          sales_order_id: 'so-a',
          so_number: 'SO403340',
          line_no: 1,
          item_code: 'WESERP10B',
        }),
        demand({
          sales_order_id: 'so-b',
          so_number: 'SO398322',
          line_no: 1,
          item_code: 'WESERP20B',
        }),
      ]),
    );
  }

  it('carries no Approve all and no Confirm all approved button anywhere', async () => {
    getPlanningBoard.mockResolvedValue(twoUndecidedOrders());

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(
      screen.queryByRole('button', { name: 'Approve all' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Confirm all approved' }),
    ).not.toBeInTheDocument();
  });

  it('posts ONE confirm-all call grouped per order, and renders each order its own result', async () => {
    getPlanningBoard.mockResolvedValue(twoUndecidedOrders());
    confirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          decision_revision: 1,
          inquiry_rows_created: 0,
        },
        {
          pso_id: 'pso-so-b',
          ok: false,
          error: 'This line is not on this sales order any more.',
        },
      ],
    });

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');
    await pressPreviewThenConfirm();

    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    const body = confirmMany.mock.calls[0][0] as {
      orders: { pso_id: string; lines: { project_line_id: string }[] }[];
    };
    expect(body.orders).toHaveLength(2);
    const byPsoId = new Map(body.orders.map((order) => [order.pso_id, order]));
    expect(
      byPsoId.get('pso-so-a')?.lines.map((line) => line.project_line_id),
    ).toEqual(['pl-so-a-1']);
    expect(
      byPsoId.get('pso-so-b')?.lines.map((line) => line.project_line_id),
    ).toEqual(['pl-so-b-1']);

    expect(
      await screen.findByText('1 of 2 orders confirmed'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'SO403340: confirmed as revision 1 (0 purchase rows handed over)',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'SO398322: This line is not on this sales order any more.',
      ),
    ).toBeInTheDocument();
  });

  it('leaves Confirm disabled at Confirm (0) once the one SAVED, plannable line is rejected', async () => {
    // SAVED (8 Sep 2026 ruling, reverses R11), so Confirm starts enabled at (1) and this test
    // can show that a REJECT is what drops it back to (0), not that it was never counted.
    getPlanningBoard.mockResolvedValue(allSaved(boardOf([demand()])));

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');
    expect(screen.getByTestId('board-confirm')).toBeEnabled();

    fireEvent.click(
      await screen.findByRole('button', {
        name: /WESERP10B, 100 across 1 sales order/,
      }),
    );
    openLinesTab();
    fireEvent.click(screen.getByText('SO403340'));
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Cancelled by the customer.' },
    });
    // Reject is async now (mirrors Save): closing the dialog before the write settles would
    // still find the row `dirty` and prompt to discard it instead of closing outright.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    });
    closeDialog();

    expect(screen.getByTestId('board-confirm')).toHaveTextContent(
      'Confirm (0)',
    );
    expect(screen.getByTestId('board-confirm')).toBeDisabled();
  });

  /**
   * BLOCKER (review, still true under the single Confirm): at day granularity `cells` only
   * covers the visible 30-day window (`DAY_WINDOW_COLUMNS`), so a line dated outside it is
   * invisible to `cells` - the panel has to read the server's top-level `contributions` for
   * the count AND for the body Confirm posts, or a line the window scrolled past is silently
   * left out of both.
   */
  it('confirms a line the day window has scrolled away from, not only the ones on screen', async () => {
    const board = buildBoard(
      [
        demand({
          sales_order_id: 'so-a',
          so_number: 'SO403340',
          line_no: 1,
          item_code: 'WESERP10B',
          required_date: '2026-09-04',
        }),
        // Outside the 30-day window that opens on the earliest future date (2026-09-04): no
        // cell is emitted for this line, but it is still a real, decidable line of the
        // selection.
        demand({
          sales_order_id: 'so-b',
          so_number: 'SO398322',
          line_no: 1,
          item_code: 'WESERP20B',
          required_date: '2028-01-01',
        }),
      ],
      { today: TODAY, granularity: 'day' },
    );
    // The window really did leave one line off screen, so the assertions below mean something.
    expect(board.cells).toHaveLength(1);
    expect(board.contributions).toHaveLength(2);
    // Both SAVED (8 Sep 2026 ruling, reverses R11), or the counter reads 0 regardless of window.
    getPlanningBoard.mockResolvedValue(allSaved(board));
    currentSearchParams = new URLSearchParams('scope=all&view=grid&granularity=day');
    confirmMany.mockResolvedValue({
      results: [
        { pso_id: 'pso-so-a', ok: true, decision_revision: 1 },
        { pso_id: 'pso-so-b', ok: true, decision_revision: 1 },
      ],
    });

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    // The counter reads both lines, not only the one the window shows.
    expect(screen.getByTestId('board-confirm')).toHaveTextContent(
      'Confirm (2)',
    );

    await pressPreviewThenConfirm();
    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    const body = confirmMany.mock.calls[0][0] as {
      orders: { pso_id: string }[];
    };
    expect(body.orders.map((order) => order.pso_id).sort()).toEqual([
      'pso-so-a',
      'pso-so-b',
    ]);
  });
});

/**
 * CONFIRM POSTS SAVED LINES ONLY (8 Sep 2026 captain's ruling, reverses R11): the exact case
 * from the captain's screenshot - a board of eighteen lines on one order, seventeen SAVED and
 * one left alone, must read "17 to confirm" and post exactly those seventeen, the untouched
 * eighteenth staying on the board undecided.
 */
describe('FulfilmentBoardPanel: Confirm counts only saved lines (8 Sep 2026 ruling)', () => {
  it('reads "17 to confirm · 0 rejected", shows "Confirm (17)", and posts 17 ids, the untouched line’s id absent', async () => {
    const lines = Array.from({ length: 18 }, (_unused, index) =>
      demand({ line_no: index + 1, item_code: `LINE-${index + 1}` }),
    );
    const board = withContribution(
      boardOf(lines),
      (entry) => entry.line_no !== 18,
      (entry) => ({
        ...entry,
        draft: {
          decision: { verdict: 'approved' as const },
          saved_by: 'Test Planner',
          saved_at: '2026-09-08T00:00:00Z',
        },
      }),
    );
    getPlanningBoard.mockResolvedValue(board);
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-so-a', ok: true, decision_revision: 1 }],
    });

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
      '17 to confirm · 0 rejected',
    );
    expect(screen.getByTestId('board-confirm')).toHaveTextContent(
      'Confirm (17)',
    );

    await pressPreviewThenConfirm();

    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    const body = confirmMany.mock.calls[0][0] as {
      orders: { pso_id: string; lines: { project_line_id: string }[] }[];
    };
    const ids = body.orders[0].lines.map((line) => line.project_line_id);
    expect(ids).toHaveLength(17);
    expect(ids).not.toContain('pl-so-a-18');
  });

  it('reads "0 to confirm · 0 rejected" and disables Confirm (0) when nothing is saved (UAC D3 reversed)', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
      '0 to confirm · 0 rejected',
    );
    expect(screen.getByTestId('board-confirm')).toHaveTextContent('Confirm (0)');
    expect(screen.getByTestId('board-confirm')).toBeDisabled();
  });
});

/**
 * AC-C5 (retired 26 August 2026): NO legend row, and NO "already past" banner. The decision
 * strip's cards carry every label in its own colour, and the column headers already say
 * "Already past" over the periods they mean - so both were the screen restated in words.
 */
/**
 * Undo all throws away every decision taken since the board was opened and there is no way
 * back to them, so it asks first, with the count - PRINCIPLES: never one click, never
 * `confirm()`.
 */
describe('FulfilmentBoardPanel: Undo all asks first (D2)', () => {
  async function decideOneLineInTheList() {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));
    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    // The list's own rows: click one to open its decision panel, then Save the suggestion it
    // opens on, which is the approval.
    fireEvent.click(await screen.findByText('WESERP10B'));
    fireEvent.click(await screen.findByRole('button', { name: 'Save decision' }));
  }

  it('opens a confirmation naming how many drafts would go, and keeps them on Cancel', async () => {
    await decideOneLineInTheList();

    fireEvent.keyDown(await screen.findByRole('button', { name: 'Board actions' }), {
      key: 'Enter',
    });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Undo all' }));

    expect(await screen.findByRole('alertdialog')).toHaveTextContent(
      'Discard 1 draft decision?',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Keep them' }));
    await waitFor(() =>
      expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument(),
    );
    // Saved (S4, R-F), not Approved: the pill reads the plain word once a decision exists.
    expect(await screen.findByTestId(/^decision-pill-/)).toHaveTextContent(
      'Saved',
    );
  });

  it('clears every draft decision once the discard is confirmed', async () => {
    await decideOneLineInTheList();

    fireEvent.keyDown(await screen.findByRole('button', { name: 'Board actions' }), {
      key: 'Enter',
    });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Undo all' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));

    await waitFor(() =>
      expect(screen.getByTestId(/^decision-pill-/)).toHaveTextContent(
        'Suggested',
      ),
    );
  });

  /** Two lines, one board, each on its own order - so Undo all has more than one key to walk. */
  async function decideTwoLinesInTheList() {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand(),
        demand({
          sales_order_id: 'so-b',
          so_number: 'SO398322',
          line_no: 1,
          item_code: 'WESERP20B',
        }),
      ]),
    );
    renderPanel(['SO403340', 'SO398322']);
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));

    fireEvent.click(await screen.findByText('WESERP10B'));
    fireEvent.click(await screen.findByRole('button', { name: 'Save decision' }));
    await waitFor(() => expect(pillFor('WESERP10B')).toHaveTextContent('Saved'));

    fireEvent.click(await screen.findByText('WESERP20B'));
    fireEvent.click(await screen.findByRole('button', { name: 'Save decision' }));
    await waitFor(() => expect(pillFor('WESERP20B')).toHaveTextContent('Saved'));
  }

  function pillFor(itemCode: string): HTMLElement {
    const pill = screen
      .getAllByTestId(/^decision-pill-/)
      .find((el) => el.getAttribute('data-testid')?.includes(itemCode));
    if (!pill) throw new Error(`No decision pill carries ${itemCode} in its key`);
    return pill;
  }

  /**
   * The list's OPEN expansion panel for one line, by the line's own item code rather than
   * by its position among however many panels happen to be open (S4, #1076: the list order
   * moved from product-first to sales-order-then-line-number, so an index into
   * `findAllByRole` is no longer stable). The DataGrid renders an expanded row as the
   * clicked row's `<tr>`'s own NEXT SIBLING (`DataGridTableBodyRowExpandded`), never nested
   * inside it, so the panel is reached from the item code's row rather than containing it.
   */
  async function panelFor(itemCode: string): Promise<HTMLElement> {
    const row = (await screen.findByText(itemCode)).closest('tr');
    const panel = row?.nextElementSibling as HTMLElement | null;
    if (!panel) throw new Error(`No expanded panel is open for ${itemCode}`);
    return panel;
  }

  /**
   * B1 (code review round 3): "Undo all" used to loop `decide(key, null)`, each call reading
   * `draft` off the SAME stale closure - N batched calls each dropped only their own key and
   * the last write wins, so a second saved line was still reading Saved after Undo all said
   * it had discarded both. Both lines have to come back Suggested TOGETHER.
   */
  it('undoes BOTH saved lines together, one line never surviving the other’s write', async () => {
    await decideTwoLinesInTheList();

    fireEvent.keyDown(await screen.findByRole('button', { name: 'Board actions' }), {
      key: 'Enter',
    });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Undo all' }));
    expect(await screen.findByRole('alertdialog')).toHaveTextContent(
      'Discard 2 draft decisions?',
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));

    await waitFor(() => expect(pillFor('WESERP10B')).toHaveTextContent('Suggested'));
    expect(pillFor('WESERP20B')).toHaveTextContent('Suggested');

    // The count the dialog's own title reads off is back to zero: Undo all disables itself.
    fireEvent.keyDown(await screen.findByRole('button', { name: 'Board actions' }), {
      key: 'Enter',
    });
    expect(await screen.findByRole('menuitem', { name: 'Undo all' })).toHaveAttribute(
      'data-disabled',
    );
  });

  /**
   * B1: a DELETE failure on one key reverts ONLY that key, not the whole map - and not the
   * OTHER key's successful discard either. `setDraft(previous)` used to resurrect every line
   * Undo all had touched, including the one whose DELETE actually succeeded.
   */
  it('keeps only the line whose DELETE failed, on a partial-failure Undo all', async () => {
    await decideTwoLinesInTheList();

    vi.mocked(deleteLineDraft).mockImplementationOnce(() => Promise.resolve(undefined));
    vi.mocked(deleteLineDraft).mockImplementationOnce(() =>
      Promise.reject(new Error('Failed to remove the saved decision')),
    );

    fireEvent.keyDown(await screen.findByRole('button', { name: 'Board actions' }), {
      key: 'Enter',
    });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Undo all' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));

    await waitFor(() => expect(pillFor('WESERP10B')).toHaveTextContent('Suggested'));
    expect(pillFor('WESERP20B')).toHaveTextContent('Saved');
  });

  /**
   * C1 (code review round 3 batch 2, extends B1): a rejected PUT on one line must not
   * touch a DIFFERENT line's own successful save - `decide`'s revert already restores only
   * the key it was called for (B1's own fix), so this pins that the two-line case actually
   * holds end to end.
   */
  it('a rejected save on one line leaves a different line’s own successful save alone (C1)', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand(),
        demand({
          sales_order_id: 'so-b',
          so_number: 'SO398322',
          line_no: 1,
          item_code: 'WESERP20B',
        }),
      ]),
    );
    renderPanel(['SO403340', 'SO398322']);
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));

    vi.mocked(putLineDraft).mockRejectedValueOnce(new Error('Failed to save the decision'));

    fireEvent.click(await screen.findByText('WESERP10B'));
    fireEvent.click(await screen.findByRole('button', { name: 'Save decision' }));
    await waitFor(() => expect(pillFor('WESERP10B')).toHaveTextContent('Suggested'));

    // The coder's multi-open rework (8d7b06766): opening B never closes A any more, so
    // A's own rejected-and-reopened panel is still up - two "Save decision" buttons on
    // screen. Found by B's OWN row rather than by index (S4, #1076): the list now sorts
    // by sales order then AutoCount line number rather than by product, so which row -
    // and therefore which "Save decision" button - comes first is no longer something
    // this test may assume.
    fireEvent.click(await screen.findByText('WESERP20B'));
    fireEvent.click(
      within(await panelFor('WESERP20B')).getByRole('button', { name: 'Save decision' }),
    );
    await waitFor(() => expect(pillFor('WESERP20B')).toHaveTextContent('Saved'));

    // A's rejection must not have resurrected or otherwise touched B.
    expect(pillFor('WESERP10B')).toHaveTextContent('Suggested');
  });
});

/**
 * C1 (code review round 3 batch 2), updated for D16: the seeding effect that carries a
 * server-saved line back onto the board (S4, AC-4.5) must not re-seed a key whose OWN Undo
 * is still on the wire, even when a DIFFERENT line's save touches the SAME cached board.
 *
 * The trigger changed with D16 (a save no longer invalidates/refetches the board - it
 * patches the cache), but the hazard did not: `patchContributionDraft` only replaces the ONE
 * contribution its own write named, so Y's save still hands `FulfilmentBoardPanel` a board
 * object with a NEW top-level `contributions` array - which re-runs the seeding effect - while
 * X's OWN entry inside it is untouched and still carries X's server draft, because X's own
 * DELETE has not landed yet. `pendingDeletes` is the ref that closes that window.
 */
describe('FulfilmentBoardPanel: a line mid-Undo is not re-seeded by a sibling save (C1, D16)', () => {
  function pillFor(itemCode: string): HTMLElement {
    const pill = screen
      .getAllByTestId(/^decision-pill-/)
      .find((el) => el.getAttribute('data-testid')?.includes(itemCode));
    if (!pill) throw new Error(`No decision pill carries ${itemCode} in its key`);
    return pill;
  }

  it('keeps X off the board once its Undo settles, even though a Y save patched the SAME cached board mid-flight', async () => {
    const twoLines = [
      demand(),
      demand({
        sales_order_id: 'so-b',
        so_number: 'SO398322',
        line_no: 1,
        item_code: 'WESERP20B',
      }),
    ];

    getPlanningBoard.mockResolvedValue(boardOf(twoLines));
    renderPanel(['SO403340', 'SO398322']);
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));

    // X is saved (D16: patches the cache, no refetch).
    fireEvent.click(await screen.findByText('WESERP10B'));
    fireEvent.click(await screen.findByRole('button', { name: 'Save decision' }));
    await waitFor(() => expect(pillFor('WESERP10B')).toHaveTextContent('Saved'));

    // Undo all starts X's DELETE, held open on a promise this test controls.
    let settleDelete: (() => void) | null = null;
    vi.mocked(deleteLineDraft).mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          settleDelete = resolve;
        }),
    );
    fireEvent.keyDown(await screen.findByRole('button', { name: 'Board actions' }), {
      key: 'Enter',
    });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Undo all' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(settleDelete).not.toBeNull());

    // While X's DELETE is still unsettled, Y is saved too - its own patch replaces the
    // board's top-level array (re-running the seeding effect) but leaves X's OWN cached
    // contribution untouched, still carrying X's server draft (the DELETE has not landed).
    fireEvent.click(await screen.findByText('WESERP20B'));
    fireEvent.click(await screen.findByRole('button', { name: 'Save decision' }));
    await waitFor(() => expect(pillFor('WESERP20B')).toHaveTextContent('Saved'));

    // X's own Undo settles.
    settleDelete!();
    await waitFor(() => expect(pillFor('WESERP10B')).toHaveTextContent('Suggested'));
    // Still Suggested a beat later - nothing re-seeds it back off the sibling's write.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(pillFor('WESERP10B')).toHaveTextContent('Suggested');
  });
});

/**
 * A planning change already applied to ONE sales order is not applied to it twice (AC-P3-4).
 * The board-wide block only reads `applied_at` on the batch itself, which is set by the LAST
 * press, so without this an order whose own rows already read applied went back up with the
 * next one.
 */
describe('FulfilmentBoardPanel: an order whose change is already applied is not sent again', () => {
  it('leaves it out of the post and says so in the results', async () => {
    // SAVED (8 Sep 2026 ruling, reverses R11), or Confirm is disabled before the press.
    getPlanningBoard.mockResolvedValue(allSaved(boardOf([demand()])));
    getPlanningChangeBatch.mockResolvedValue({
      id: 'pcb-1',
      applied_at: null,
      applied_by_name: null,
      orders: [
        {
          so_number: 'SO403340',
          rows: [{ id: 'pcr-1', applied_state: 'applied' }],
        },
      ],
    });

    renderPanel(['SO403340'], vi.fn(), 'pcb-1');
    await screen.findByTestId('fulfilment-board-matrix');

    // The applied order is left out at Preview already, and said so in the results.
    fireEvent.click(await screen.findByTestId('board-confirm'));

    expect(
      await screen.findByText(
        /This planning change was already applied to this sales order\./,
      ),
    ).toBeInTheDocument();
    expect(confirmMany).not.toHaveBeenCalled();
  });
});

/**
 * D15: a board-wide "Save all suggested" button, left of the gear - every line the board is
 * showing that a quick save could still touch, in one press, one toast.
 */
describe('FulfilmentBoardPanel: Save all suggested (D15)', () => {
  function twoLines() {
    return [
      demand({ item_code: 'WESERP10B', line_no: 1 }),
      demand({
        item_code: 'WESERP20B',
        line_no: 1,
        sales_order_id: 'so-b',
        so_number: 'SO398322',
      }),
    ];
  }

  it('is disabled at zero before there is a board to save', () => {
    getPlanningBoard.mockResolvedValue(boardOf([]));
    renderPanel(['SO403340']);

    expect(
      screen.getByRole('button', { name: 'Save all suggested (0)' }),
    ).toBeDisabled();
  });

  it('counts every eligible line across the board, not just what is on screen', async () => {
    getPlanningBoard.mockResolvedValue(boardOf(twoLines()));
    renderPanel(['SO403340', 'SO398322']);
    await screen.findByTestId('fulfilment-board-matrix');

    expect(
      screen.getByRole('button', { name: 'Save all suggested (2)' }),
    ).not.toBeDisabled();
  });

  it('drops a line from the count once a product search hides it', async () => {
    getPlanningBoard.mockResolvedValue(boardOf(twoLines()));
    renderPanel(['SO403340', 'SO398322']);
    await screen.findByTestId('fulfilment-board-matrix');

    fireEvent.change(
      screen.getByPlaceholderText('Search sales order, customer, project or product'),
      { target: { value: 'WESERP10B' } },
    );

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Save all suggested (1)' }),
      ).toBeInTheDocument(),
    );
  });

  it('saves every eligible line quietly and toasts ONCE with the count, not once per line', async () => {
    getPlanningBoard.mockResolvedValue(boardOf(twoLines()));
    renderPanel(['SO403340', 'SO398322']);
    await screen.findByTestId('fulfilment-board-matrix');

    fireEvent.click(screen.getByRole('button', { name: 'Save all suggested (2)' }));

    await waitFor(() => expect(putLineDraft).toHaveBeenCalledTimes(2));
    expect(toast.success).toHaveBeenCalledTimes(1);
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringMatching(/^2 lines saved · \d+ to confirm$/),
    );
    // Never the D14/S4 per-line wording - a bulk press is one action, one toast.
    expect(toast.success).not.toHaveBeenCalledWith(expect.stringMatching(/^Line \d/));
  });

  it('both lines read Saved once the press settles', async () => {
    getPlanningBoard.mockResolvedValue(boardOf(twoLines()));
    renderPanel(['SO403340', 'SO398322']);
    await screen.findByTestId('fulfilment-board-matrix');

    fireEvent.click(screen.getByRole('button', { name: 'Save all suggested (2)' }));
    fireEvent.click(screen.getByRole('button', { name: 'List' }));

    await waitFor(() =>
      expect(screen.getAllByTestId(/^decision-pill-/)).toHaveLength(2),
    );
    for (const pill of screen.getAllByTestId(/^decision-pill-/)) {
      expect(pill).toHaveTextContent('Saved');
    }
  });
});

/**
 * Review round 2, Should fix 2: `decideBatch`'s own `silent`/`onFailure` wiring (review
 * round 1, Should fix 1) shipped with no test - reverting it to a per-row `toast.error` plus
 * the generic "could not be saved" left every existing test green, because none of them made a
 * Decide save actually fail on the wire. A real PUT failure has to reach the Decide strip's OWN
 * lenient toast (R10) with the server's own sentence, and nowhere else: a second toast off the
 * mutation's own `onError` would be exactly the "too many errors" the owner asked Decide to stop
 * doing.
 */
describe('FulfilmentBoardPanel: a failed Decide save toasts the server message once (review round 2, Should fix 2)', () => {
  it('toasts exactly once, carrying the server sentence, when one row of a Decide batch fails on the wire', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({ sales_order_id: 'so-a', so_number: 'SO403340', line_no: 1 }),
        demand({
          sales_order_id: 'so-b',
          so_number: 'SO398322',
          line_no: 1,
          item_code: 'WESERP20B',
        }),
      ]),
    );

    // Ticked in this order below, and `tickedRows`/`decideBatch` walk `contributions` in the
    // same order the board lists them - SO403340 first, SO398322 second - so the two `Once`
    // calls line up with which row they answer. `Once` rather than a persistent
    // `mockImplementation` (B1): this file's `beforeEach` only `clearAllMocks`es, which does
    // not touch a standing implementation, so a persistent override here would leak into
    // every test that runs after this one in the file.
    vi.mocked(putLineDraft)
      .mockResolvedValueOnce({
        decision: { verdict: 'approved' },
        saved_by: 'Test Planner',
        saved_at: '2026-09-03T00:00:00Z',
      })
      .mockRejectedValueOnce(new Error('Not enough free stock left.'));

    renderPanel(['SO403340', 'SO398322']);
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    await screen.findByRole('table');

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select SO403340 line 1' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select SO398322 line 1' }));
    fireEvent.keyDown(await screen.findByTestId('board-decide-button'), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'As suggested' }));

    await waitFor(() => expect(putLineDraft).toHaveBeenCalledTimes(2));
    // Exactly ONE toast for the whole batch (R10) - not a second one off the mutation's own
    // `onError`, which `silent` exists to suppress.
    expect(toast.success).toHaveBeenCalledTimes(1);
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining('Not enough free stock left.'),
    );
    expect(toast.error).not.toHaveBeenCalled();
  });
});

/**
 * D15: a grid cell's own undo icon, for a cell holding only drafted lines - `undoMany` deletes
 * every one of them and toasts once, rather than the per-line Undo's silent single delete.
 */
describe('FulfilmentBoardPanel: a cell’s own Undo saves and toasts once (D15)', () => {
  it('undoes every drafted line in the cell together, with one toast', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({ line_no: 1, sales_order_id: 'so-a', so_number: 'SO403340' }),
        demand({ line_no: 1, sales_order_id: 'so-b', so_number: 'SO398322' }),
      ]),
    );
    renderPanel(['SO403340', 'SO398322']);
    await screen.findByTestId('fulfilment-board-matrix');

    // Both lines share one product and one date, so they land in the same cell - drafting
    // both through the board-wide button first.
    fireEvent.click(screen.getByRole('button', { name: /^Save all suggested/ }));
    await waitFor(() => expect(putLineDraft).toHaveBeenCalledTimes(2));
    vi.mocked(toast.success).mockClear();

    fireEvent.click(
      screen.getByRole('button', { name: /^Undo WESERP10B/ }),
    );

    await waitFor(() => expect(deleteLineDraft).toHaveBeenCalledTimes(2));
    expect(toast.success).toHaveBeenCalledTimes(1);
    // SF-5 (reviewer, fix round 2): "undone", never "back to suggested" - a line the open
    // change batch named goes back to `Change proposed` (AC-B13), so the old wording
    // described the wrong outcome for half the presses this button takes.
    expect(toast.success).toHaveBeenCalledWith('2 lines undone');

    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    for (const pill of screen.getAllByTestId(/^decision-pill-/)) {
      expect(pill).toHaveTextContent('Suggested');
    }
  });
});

/**
 * A rejection used to skip `decide()`'s own per-line toast outright (`decision.verdict !==
 * 'rejected'`), for no documented reason beyond the wording not fitting - so a planner who
 * pressed Reject got no system feedback at all, unlike Save's toast beside its own button
 * state. A rejection is a decision too now, and gets the same toast, worded for what it is.
 */
describe('FulfilmentBoardPanel: a rejection toasts too (owner, 22 Sep 2026)', () => {
  it('toasts "Line N rejected · M to confirm · K rejected" on a single-line Reject', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));
    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));

    fireEvent.click(await screen.findByText('WESERP10B'));
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The customer cancelled this line.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        'Line 1 rejected · 0 to confirm · 1 rejected',
      ),
    );
  });
});

/**
 * The notice beside the banner's own links (board-confirm-left-out, AC-4): since the 8 Sep
 * 2026 ruling (reverses R11) every line reaching here carries a SAVED decision, so every one
 * of them is NAMED, capped at five - the CLAUSE names none of them any more, because the
 * caller (`FulfilmentBoardPanel`) puts each one on its own link instead of in the prose.
 */
// D2 (AC-19): `'buy_reason_missing'` is removed from `UnpostableReason` entirely by this fix -
// the discontinued-with-no-reason cause no longer exists as a left-out reason, so a notice can
// no longer be constructed for it. The `'names a line and states the fix, with no name baked
// into the clause'` case (a single-name, single-reason call) is a mechanical removal of a
// reason kind the type itself is dropping, not a product-behavior deletion; AC-19/AC-20's
// actual red coverage for the discontinued cause lives in `fulfilmentBoard.test.ts`
// (`describe('confirmLinesFor and a discontinued product', ...)` and the COVERED-line describe
// beside it), which is where `lineFor`/`unpostableDecidedFor` are exercised directly.
//
// The five-name cap and the plural grammar ("reserve ... leaves them out", "are not ... add
// them") are NOT specific to `buy_reason_missing`: they are generic `unpostableNotices`
// behaviour, so the S1 review (PR #1218) asked for this case to be re-pointed at a reason that
// still exists rather than deleted with it.
describe('unpostableNotices', () => {
  function line(lineNo: number) {
    return {
      contribution: {
        item_code: 'TPE-9204',
        line_no: lineNo,
      } as unknown as BoardContribution,
      reason: 'no_reserve_warehouse' as const,
    };
  }

  it('caps the names at five and counts the rest', () => {
    const [notice] = unpostableNotices(
      'no_reserve_warehouse',
      [1, 2, 3, 4, 5, 6, 7].map((no) => line(no)),
    );
    expect(notice.named.map((entry) => entry.label)).toEqual([
      'TPE-9204 line 1',
      'TPE-9204 line 2',
      'TPE-9204 line 3',
      'TPE-9204 line 4',
      'TPE-9204 line 5',
    ]);
    expect(notice.moreCount).toBe(2);
    // Seven lines: the clause reads "reserve"/"them", not the one-line "reserves"/"it".
    expect(notice.clause).toContain('reserve at a warehouse the board cannot address');
    expect(notice.clause).toContain('leaves them out');
    expect(notice.clause).toContain('Amend them to place the Reserve.');
  });
});

describe('FulfilmentBoardPanel: what was taken off the page', () => {
  it('shows no legend row on either view', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');
    expect(screen.queryByTestId('supply-legend')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    await waitFor(() =>
      expect(
        screen.queryByTestId('fulfilment-board-matrix'),
      ).not.toBeInTheDocument(),
    );
    expect(screen.queryByTestId('supply-legend')).not.toBeInTheDocument();
  });

  it('shows no "already past" banner, and the strip still carries the labels', async () => {
    getPlanningBoard.mockResolvedValue(boardOf([demand()]));

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(
      screen.queryByText(/lines are already past their delivery date/),
    ).toBeNull();
    const strip = screen.getByTestId('decision-strip');
    expect(strip.textContent).toContain('Buy');
    expect(strip.textContent).toContain('Use BRW');
    expect(strip.textContent).toContain('Use own location');
  });
});

/**
 * AC-D2: the decision strip.
 *
 * The captain asked for "one page that shows, per line, what was SUGGESTED and what was
 * DECIDED, in the same words", and ruled that the page is this board, with cards.
 */
describe('FulfilmentBoardPanel: the decision strip', () => {
  /** The engine offered the pool; the planner bought the line whole. */
  const amendedBoard = () =>
    withContribution(
      boardOf([demand({ item_code: 'SRT382-6-DIY', qty: '71' })], {}),
      () => true,
      (entry) => ({
        ...entry,
        covered: true,
        proposed: {
          components: [
            {
              kind: 'reserve',
              rung: 'pool',
              qty: '71',
              location: 'BRW',
              warehouse_id: 'wh-BRW',
              reason: 'Free stock at BRW covers the need.',
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
      }),
    );

  it('states both figures per kind and marks the pair that moved', async () => {
    getPlanningBoard.mockResolvedValue(amendedBoard());

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    const shared = screen.getByTestId('decision-strip-shared');
    expect(within(shared).getByText('71')).toBeInTheDocument();
    expect(
      screen.getByTestId('decision-strip-changed-shared'),
    ).toBeInTheDocument();

    const buy = screen.getByTestId('decision-strip-buy');
    expect(within(buy).getByText('71')).toBeInTheDocument();
    expect(
      screen.getByTestId('decision-strip-changed-buy'),
    ).toBeInTheDocument();
  });

  it('sits above whichever view is on screen', async () => {
    getPlanningBoard.mockResolvedValue(amendedBoard());

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    const strip = screen.getByTestId('decision-strip');
    const matrix = screen.getByTestId('fulfilment-board-matrix');
    expect(strip.compareDocumentPosition(matrix)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it('filters the grid to the cells carrying that kind, and clears on a second press', async () => {
    // Two products: one bought, one covered from the pool. Pressing Shared has to leave the
    // pooled one and take the bought one away.
    const board = withContribution(
      boardOf(
        [
          demand({ line_no: 1, item_code: 'SRT382-6-DIY', qty: '71' }),
          demand({ line_no: 2, item_code: 'WESERP10B', qty: '10' }),
        ],
        { 'WESERP10B|BRW-BB': '999' },
      ),
      () => true,
      (entry) => entry,
    );
    getPlanningBoard.mockResolvedValue(board);

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    expect(screen.getByText('SRT382-6-DIY')).toBeInTheDocument();
    expect(screen.getByText('WESERP10B')).toBeInTheDocument();

    // WESERP10B has free stock at its own location, so the ladder takes it on the group rung
    // and SRT382-6-DIY is bought. Own keeps the first and drops the second.
    fireEvent.click(screen.getByTestId('decision-strip-own'));
    await waitFor(() => {
      expect(screen.queryByText('SRT382-6-DIY')).not.toBeInTheDocument();
    });
    expect(screen.getByText('WESERP10B')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('decision-strip-own'));
    await waitFor(() => {
      expect(screen.getByText('SRT382-6-DIY')).toBeInTheDocument();
    });
  });
});

/**
 * The card, the figures above it and BOTH views are one reading or they are none.
 *
 * Two ways they came apart, both found in review: the list view ignored the pressed card
 * entirely (it kept receiving every contribution while the card sat aria-pressed), and the
 * strip was summed over the whole selection while the grid was filtered over its cells - which
 * at day granularity is a 30-day window, so a card could read a figure off lines the board
 * could not show and then empty itself when pressed.
 */
describe('FulfilmentBoardPanel: the strip and the views agree', () => {
  /** One bought line, one covered from its own location. */
  const mixedBoard = () =>
    boardOf(
      [
        demand({ line_no: 1, item_code: 'SRT382-6-DIY', qty: '71' }),
        demand({ line_no: 2, item_code: 'WESERP10B', qty: '10' }),
      ],
      { 'WESERP10B|BRW-BB': '999' },
    );

  it('filters the LIST view by the pressed card, not only the grid', async () => {
    getPlanningBoard.mockResolvedValue(mixedBoard());

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    await screen.findByText('SRT382-6-DIY');
    expect(screen.getByText('WESERP10B')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('decision-strip-own'));
    await waitFor(() => {
      expect(screen.queryByText('SRT382-6-DIY')).not.toBeInTheDocument();
    });
    expect(screen.getByText('WESERP10B')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('decision-strip-own'));
    await waitFor(() => {
      expect(screen.getByText('SRT382-6-DIY')).toBeInTheDocument();
    });
  });

  it('never shows a figure the view cannot produce: pressing a card leaves something on screen', async () => {
    getPlanningBoard.mockResolvedValue(mixedBoard());

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    // Every card carrying a figure must leave at least one product row when pressed. A card
    // that empties the board is the exact defect this rule exists to prevent.
    for (const kind of ['buy', 'own']) {
      fireEvent.click(screen.getByTestId(`decision-strip-${kind}`));
      await waitFor(() => {
        expect(
          screen.getByTestId('fulfilment-board-matrix'),
        ).toBeInTheDocument();
      });
      expect(screen.queryByText('No products match')).not.toBeInTheDocument();
      fireEvent.click(screen.getByTestId(`decision-strip-${kind}`));
    }
  });

  it('disables a card nothing on the board is that kind of supply', async () => {
    getPlanningBoard.mockResolvedValue(mixedBoard());

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    // Nothing here is borrowed, so that card reads 0 / 0 and cannot be pressed - it keeps
    // its place, because it stands for a step of the ladder's walk and a card that came
    // and went would move every card beside it.
    expect(screen.getByTestId('decision-strip-borrow_order')).toBeDisabled();
    expect(screen.getByTestId('decision-strip-buy')).not.toBeDisabled();
    // The same is true of the two steps split out on 30 August 2026: the group's water and
    // the document borrow. `borrow_incoming` reads 0 on every board until S4 lands its
    // candidates, and it is rendered anyway - a step nobody can see is a step nobody knows
    // was asked.
    expect(screen.getByTestId('decision-strip-incoming')).toBeInTheDocument();
    expect(screen.getByTestId('decision-strip-borrow_incoming')).toBeDisabled();
    // `borrow_other` is the exception (re-ruled 30 August 2026): it is not a step, it is
    // the retired `cross_group_borrow` a decision frozen under an older ladder carries, so
    // at 0 / 0 it is not shown at all.
    expect(
      screen.queryByTestId('decision-strip-borrow_other'),
    ).not.toBeInTheDocument();
  });
});

/**
 * R3 (`PLAN-board-draft-on-confirmed-line.md`): a Confirm the tab never heard back from left
 * eleven confirmed lines reading Saved/Rejected on prod (SO314595, 17 Sep 2026), because the
 * local draft map only ever gained entries, never lost one on the server's own say-so. The
 * seeding effect (`FulfilmentBoardPanel.tsx` around the "seeded on EVERY board read" comment)
 * has to drop a local entry once a fresh board read says the line is covered and carries no
 * server draft - TEST-FIRST: it carries no such drop yet, so AC-F4 is red against current code.
 *
 * TWO orders, so-a (the line under test, WESERP10B) and so-b (WESERP20B), because ONE order
 * confounds this with a mechanism `runConfirmAll` ALREADY has (the `setDraft` a few dozen
 * lines under "What the press produced" in `FulfilmentBoardPanel.tsx`, which deletes a key
 * this SAME press just confirmed successfully) - a naive one-order version of this test
 * passes today for that reason alone, never having exercised R3's own drop at all. so-b is
 * what actually gets confirmed (and its own key legitimately clears through the existing
 * mechanism); so-a's own confirm attempt is answered `ok: false` in `confirmMany`'s result,
 * so the existing per-press clear never touches its key, and only R3's board-read drop can
 * explain a "Confirmed" pill on it afterwards.
 *
 * Granularity stays fixed at the default `week` throughout - `weekStart('2026-09-04')` is a
 * Friday, not the Monday the "confirm counter is selection-scoped" describe block's own
 * comment claims, so switching granularity would move the bucket key (and so the contribution
 * `key`) mid-test. Confirm is the key-preserving second read instead: "refetches the board
 * once the confirmation lands" (above) already proves a successful Confirm invalidates
 * `PLANNING_BOARD_KEY` and the SAME query (same orders/granularity/window) refetches.
 */
describe('FulfilmentBoardPanel: a local draft is dropped once the server confirms it (R3)', () => {
  const KEY_A = 'so-a|1|WESERP10B|2026-08-31';

  function orderB() {
    return demand({
      sales_order_id: 'so-b',
      so_number: 'SO398322',
      line_no: 1,
      item_code: 'WESERP20B',
    });
  }

  function draftedInitialBoard() {
    return withContribution(
      boardOf([demand(), orderB()]),
      () => true,
      (entry) => ({
        ...entry,
        draft: {
          decision: { verdict: 'approved' as const },
          saved_by: 'Test Planner',
          saved_at: '2026-09-03T00:00:00Z',
        },
      }),
    );
  }

  function refetchedBoardCoveringSoA() {
    return boardOf([
      demand({
        decision: {
          revision_no: 1,
          confirmed_at: '2026-09-05T00:00:00',
          timely_spo_qty: '0',
          reserve: [],
          borrow: [],
          buy_qty: '100',
        },
      }),
      orderB(),
    ]);
  }

  /** so-a's own confirm attempt is refused, so-b's succeeds - see the block comment above. */
  const PARTIAL_RESULT = {
    results: [
      { pso_id: 'pso-so-a', ok: false, error: 'refused' },
      { pso_id: 'pso-so-b', ok: true, decision_revision: 1 },
    ],
  };

  it('AC-F4: drops a key once the board reports it covered with no server draft, and the pill reads Confirmed', async () => {
    getPlanningBoard
      .mockResolvedValueOnce(draftedInitialBoard())
      .mockResolvedValue(refetchedBoardCoveringSoA());
    confirmMany.mockResolvedValue(PARTIAL_RESULT);

    renderPanel(['SO403340', 'SO398322']);
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));

    // The seeding effect has already picked up the server's own draft: the pill reads Saved
    // before the confirmation below ever fires.
    expect(await screen.findByTestId(`decision-pill-${KEY_A}`)).toHaveTextContent('Saved');

    await pressPreviewThenConfirm();
    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));

    await waitFor(() =>
      expect(screen.getByTestId(`decision-pill-${KEY_A}`)).toHaveTextContent('Confirmed'),
    );
  });

  /**
   * AC-F5, the other half of R3: a key whose own save is still on the wire must not be
   * dropped by the same read AC-F4 exists to make drop an ABANDONED one - or a planner's own
   * Save would flicker Confirmed for a frame before flipping back to Saved once the write
   * lands. `putLineDraft` (mocked at the top of this file) is made to hang, so so-a's own save
   * is genuinely still in flight when the Confirm below lands a board read that already shows
   * it covered with no server draft - the exact race, rather than reaching into
   * `pendingDeletes` or a same-named save ref that does not exist in the source yet. This pins
   * the OBSERVABLE promise, not the mechanism the coder picks for it.
   */
  it('AC-F5: does not drop a key while its own save is still in flight', async () => {
    getPlanningBoard
      .mockResolvedValueOnce(
        withContribution(
          boardOf([demand(), orderB()]),
          (entry) => entry.sales_order_id === 'so-b',
          (entry) => ({
            ...entry,
            draft: {
              decision: { verdict: 'approved' as const },
              saved_by: 'Test Planner',
              saved_at: '2026-09-03T00:00:00Z',
            },
          }),
        ),
      )
      .mockResolvedValue(refetchedBoardCoveringSoA());
    confirmMany.mockResolvedValue(PARTIAL_RESULT);
    (putLineDraft as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      () => new Promise(() => {}), // never resolves: so-a's own save, held open
    );

    renderPanel(['SO403340', 'SO398322']);
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));

    fireEvent.click(await screen.findByText('WESERP10B'));
    fireEvent.click(await screen.findByRole('button', { name: 'Save decision' }));
    // Optimistic (S4, R-F): the pill answers the click before the network write settles, and
    // `putLineDraft` above never will.
    expect(await screen.findByTestId(`decision-pill-${KEY_A}`)).toHaveTextContent('Saved');

    // Confirm succeeds overall (so-b's own line goes through) and its refetch lands a board
    // read that already shows so-a covered with no server draft - so-a's own save is still on
    // the wire underneath it, and its own confirm attempt was refused, never committed.
    await pressPreviewThenConfirm();
    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(getPlanningBoard).toHaveBeenCalledTimes(2));

    expect(screen.getByTestId(`decision-pill-${KEY_A}`)).toHaveTextContent('Saved');
  });
});

/**
 * S1 (fix round, review): `runConfirmAll`'s own wiring for a covered line's staged
 * reject (`wantedOrders`' own `rejected` branch, the per-order body's `rejected_line_ids`,
 * and the post-confirm toast's `withdrawn` clause) had NOTHING pinning it - a kill test
 * proved reverting all three together left every test in this file green. These three
 * close that gap, against a board whose only saved decision on its one line is the
 * staged reject (owner ruling 23 Sep 2026, `PLAN-board-reject-on-confirmed-line.md`).
 */
describe("FulfilmentBoardPanel: Confirm carries a covered line's staged reject (S1, fix round)", () => {
  function coveredRejectedBoard() {
    return withContribution(
      boardOf([
        demand({
          decision: {
            revision_no: 1,
            timely_spo_qty: '0',
            reserve: [],
            borrow: [],
            buy_qty: '100',
          },
        }),
      ]),
      () => true,
      (entry) => ({
        ...entry,
        draft: {
          decision: { verdict: 'rejected' as const, reason: 'wrong site' },
          saved_by: 'Test Planner',
          saved_at: '2026-09-23T00:00:00Z',
        },
      }),
    );
  }

  it('puts the order in the press and reads Confirm (1)', async () => {
    getPlanningBoard.mockResolvedValue(coveredRejectedBoard());

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    expect(screen.getByTestId('board-confirm')).toHaveTextContent('Confirm (1)');
    expect(screen.getByTestId('board-confirm')).toBeEnabled();
  });

  it("posts lines: [] and the mirror id in rejected_line_ids", async () => {
    getPlanningBoard.mockResolvedValue(coveredRejectedBoard());
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-so-a', ok: true, decision_revision: 2, rejected_count: 1 }],
    });

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    await pressPreviewThenConfirm();

    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    const [body] = confirmMany.mock.calls[0] as [
      {
        orders: {
          pso_id: string;
          lines: unknown[];
          rejected_line_ids: string[];
        }[];
      },
    ];
    expect(body.orders).toEqual([
      expect.objectContaining({
        pso_id: 'pso-so-a',
        lines: [],
        rejected_line_ids: ['pl-so-a-1'],
      }),
    ]);
  });

  it('renders "· 1 withdrawn" in the toast when rejected_count comes back non-zero', async () => {
    getPlanningBoard.mockResolvedValue(coveredRejectedBoard());
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-so-a', ok: true, decision_revision: 2, rejected_count: 1 }],
    });

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    await pressPreviewThenConfirm();

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('· 1 withdrawn')),
    );
  });
});

describe('FulfilmentBoardPanel: line attachments lookup (#1312, AC-U2, fix round 1 should-fix 3)', () => {
  it('batches every contribution into the lookup, never a per-row call, over the WHOLE selection', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({ line_no: 1, item_code: 'WESERP10B' }),
        demand({
          sales_order_id: 'so-b',
          so_number: 'SO398322',
          line_no: 2,
          item_code: 'CSH2072',
        }),
      ]),
    );

    renderPanel(['SO403340', 'SO398322']);
    await screen.findByTestId('fulfilment-board-matrix');

    // Never once per row: once the board has loaded, the lookup is handed BOTH
    // ids together in one batched call - not two separate calls each naming one.
    // (An earlier render, before `board.data` has arrived, calls it with `[]` -
    // the hook itself gates on `ids.length > 0` and issues no request for that.)
    expect(soLineAttachmentLookup).toHaveBeenLastCalledWith(
      expect.arrayContaining(['core-so-a-1', 'core-so-b-2']),
    );
    const lastCall = soLineAttachmentLookup.mock.calls.at(-1)?.[0] as string[];
    expect(lastCall).toHaveLength(2);
  });
});

/**
 * FULFIL-CONFIRM-SCOPE (`PLAN-fulfil-confirm-scope-30sep.md`): Confirm runs the server dry run
 * first and posts what it said it would write; the toast and results block read the lines the
 * SERVER says it wrote (AC-R1..R4).
 */
describe('FulfilmentBoardPanel: the toast and results block read the server echo (FULFIL-CONFIRM-SCOPE)', () => {

  function lines6And9() {
    return allSaved(
      boardOf([
        demand({ line_no: 6, item_code: 'SRTWT6808' }),
        demand({ line_no: 9, item_code: 'SRTWT6808' }),
      ]),
    );
  }

  function threeLines() {
    return allSaved(
      boardOf([
        demand({ line_no: 1, item_code: 'WESERP10B' }),
        demand({ line_no: 2, item_code: 'TPE-9204' }),
        demand({ line_no: 3, item_code: 'WESERP20B' }),
      ]),
    );
  }

  async function press() {
    await pressPreviewThenConfirm();
    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
  }

  it('toasts the echoed count and lists the lines and the carried count (AC-R1, R2)', async () => {
    getPlanningBoard.mockResolvedValue(lines6And9());
    confirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          decision_revision: 2,
          inquiry_rows_created: 2,
          lines_confirmed: [
            { project_line_id: 'pl-so-a-6', line_no: 6, item_code: 'SRTWT6808' },
            { project_line_id: 'pl-so-a-9', line_no: 9, item_code: 'SRTWT6808' },
          ],
          lines_carried: 4,
        },
      ],
    });

    renderPanel(['SO403340']);
    await press();

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(String((toast.success as unknown as { mock: { calls: unknown[][] } }).mock.calls[0][0])).toMatch(
      /^2 lines confirmed/,
    );
    const block = await screen.findByTestId('board-confirm-results');
    expect(block).toHaveTextContent('line 6 SRTWT6808');
    expect(block).toHaveTextContent('line 9 SRTWT6808');
    expect(block).toHaveTextContent('4 carried forward');
    expect(block).not.toHaveTextContent(/Posted \d+, server confirmed/);
  });

  it('warns in amber when the server confirmed fewer lines than were posted (AC-R3)', async () => {
    getPlanningBoard.mockResolvedValue(threeLines());
    confirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          decision_revision: 1,
          inquiry_rows_created: 0,
          lines_confirmed: [
            { project_line_id: 'pl-so-a-1', line_no: 1, item_code: 'WESERP10B' },
            { project_line_id: 'pl-so-a-2', line_no: 2, item_code: 'TPE-9204' },
          ],
          lines_carried: 0,
        },
      ],
    });

    renderPanel(['SO403340']);
    await press();

    // AC-R1: the toast counts what the server echoed (2), not what was posted (3).
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(
      String((toast.success as unknown as { mock: { calls: unknown[][] } }).mock.calls[0][0]),
    ).toMatch(/^2 lines confirmed/);
    const block = await screen.findByTestId('board-confirm-results');
    const warning = await within(block).findByText(/Posted 3, server confirmed 2/);
    expect(warning.className).toMatch(/amber/);
    expect(warning).toHaveTextContent(/line 3 WESERP20B/);
  });

  it('clears the local Saved pill only on the lines the server echoed (AC-R4)', async () => {
    getPlanningBoard.mockResolvedValueOnce(threeLines());
    // The refetch after Confirm reads a board carrying NO server drafts (a read that raced the
    // draft writes), so the Saved pills left on screen are the panel's own local draft state:
    // exactly the lines the server echoed lose theirs, the posted-but-not-echoed one keeps it.
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({ line_no: 1, item_code: 'WESERP10B' }),
        demand({ line_no: 2, item_code: 'TPE-9204' }),
        demand({ line_no: 3, item_code: 'WESERP20B' }),
      ]),
    );
    confirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          decision_revision: 1,
          inquiry_rows_created: 0,
          lines_confirmed: [
            { project_line_id: 'pl-so-a-1', line_no: 1, item_code: 'WESERP10B' },
            { project_line_id: 'pl-so-a-2', line_no: 2, item_code: 'TPE-9204' },
          ],
          lines_carried: 0,
        },
      ],
    });

    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    const pillFor = (itemCode: string): HTMLElement => {
      const pill = screen
        .getAllByTestId(/^decision-pill-/)
        .find((el) => el.getAttribute('data-testid')?.includes(itemCode));
      if (!pill) throw new Error(`No decision pill carries ${itemCode} in its key`);
      return pill;
    };
    await waitFor(() => expect(pillFor('WESERP20B')).toHaveTextContent('Saved'));

    await press();

    await waitFor(() => expect(pillFor('WESERP10B')).not.toHaveTextContent('Saved'));
    expect(pillFor('TPE-9204')).not.toHaveTextContent('Saved');
    expect(pillFor('WESERP20B')).toHaveTextContent('Saved');
  });
});

describe('FulfilmentBoardPanel: confirm scope fix round 1 (FULFIL-CONFIRM-SCOPE)', () => {
  const threeLines = () =>
    allSaved(
      boardOf([
        demand({ line_no: 1, item_code: 'WESERP10B' }),
        demand({ line_no: 2, item_code: 'TPE-9204' }),
        demand({ line_no: 3, item_code: 'WESERP20B' }),
      ]),
    );

  it('falls back to the posted count and says the server named no lines (AC-R1 fallback)', async () => {
    getPlanningBoard.mockResolvedValue(threeLines());
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-so-a', ok: true, decision_revision: 1, inquiry_rows_created: 0 }],
    });

    renderPanel(['SO403340']);
    await pressPreviewThenConfirm();
    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(
      String((toast.success as unknown as { mock: { calls: unknown[][] } }).mock.calls.at(-1)?.[0]),
    ).toMatch(/^3 lines confirmed/);
    const block = await screen.findByTestId('board-confirm-results');
    expect(block).toHaveTextContent('The server did not name the lines it confirmed.');
  });

});

describe('FulfilmentBoardPanel: Confirm runs the dry run first (FULFIL-CONFIRM-SCOPE v5)', () => {
  const twoLines = () =>
    allSaved(
      boardOf([
        demand({ line_no: 6, item_code: 'SRTWT6808' }),
        demand({ line_no: 9, item_code: 'SRTWT6808' }),
      ]),
    );

  it('posts the body narrowed to the lines the dry run confirmed, with no popup and no preview key', async () => {
    getPlanningBoard.mockResolvedValue(twoLines());
    previewConfirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          preview: true,
          lines_confirmed: [{ project_line_id: 'pl-so-a-6', line_no: 6, item_code: 'SRTWT6808' }],
          lines_withdrawn: [],
          lines_held_back: [{ line_no: 9, item_code: 'SRTWT6808', reason: 'only 3 free' }],
        },
      ],
    });
    confirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          decision_revision: 2,
          lines_confirmed: [{ project_line_id: 'pl-so-a-6', line_no: 6, item_code: 'SRTWT6808' }],
          lines_carried: 0,
        },
      ],
    });

    renderPanel(['SO403340']);
    fireEvent.click(await screen.findByTestId('board-confirm'));

    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(previewConfirmMany).toHaveBeenCalledTimes(1);
    const [body] = confirmMany.mock.calls[0] as [
      { orders: { lines: { project_line_id: string }[]; only_line_ids?: string[] }[]; preview?: boolean },
    ];
    expect(body).not.toHaveProperty('preview');
    expect(body.orders[0].only_line_ids).toEqual(['pl-so-a-6']);
    expect(body.orders[0].lines.map((line) => line.project_line_id)).toEqual(['pl-so-a-6']);
    await screen.findByTestId('board-confirm-results');
    expect(screen.queryByTestId('board-confirm-mismatch-pso-so-a')).toBeNull();
  });

  it('posts nothing for an order the dry run refuses and shows the refusal in the results', async () => {
    getPlanningBoard.mockResolvedValue(twoLines());
    previewConfirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: false,
          preview: true,
          error: '2 lines cannot be confirmed. Nothing was written.',
          failing_lines: [],
        },
      ],
    });

    renderPanel(['SO403340']);
    fireEvent.click(await screen.findByTestId('board-confirm'));

    const block = await screen.findByTestId('board-confirm-results');
    expect(block).toHaveTextContent('Nothing was written');
    expect(confirmMany).not.toHaveBeenCalled();
  });

  const twoOrders = () =>
    allSaved(
      boardOf([
        demand({ line_no: 6, item_code: 'SRTWT6808' }),
        demand({ sales_order_id: 'so-b', so_number: 'SO398322', line_no: 1, item_code: 'WESERP20B' }),
      ]),
    );

  it('posts only the order the dry run confirmed something for, not one that only held lines back', async () => {
    getPlanningBoard.mockResolvedValue(twoOrders());
    previewConfirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          preview: true,
          lines_confirmed: [],
          lines_withdrawn: [],
          lines_held_back: [{ line_no: 6, item_code: 'SRTWT6808', reason: 'only 3 free' }],
        },
        {
          pso_id: 'pso-so-b',
          ok: true,
          preview: true,
          lines_confirmed: [{ project_line_id: 'pl-so-b-1', line_no: 1, item_code: 'WESERP20B' }],
          lines_withdrawn: [],
        },
      ],
    });
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-so-b', ok: true, decision_revision: 1, lines_confirmed: [], lines_carried: 0 }],
    });

    renderPanel(['SO403340', 'SO398322']);
    fireEvent.click(await screen.findByTestId('board-confirm'));

    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    const [body] = confirmMany.mock.calls[0] as [{ orders: { pso_id: string }[] }];
    expect(body.orders.map((order) => order.pso_id)).toEqual(['pso-so-b']);
  });

  it('posts the ok order and shows the refusal of the other in the results', async () => {
    getPlanningBoard.mockResolvedValue(twoOrders());
    previewConfirmMany.mockResolvedValue({
      results: [
        { pso_id: 'pso-so-a', ok: false, preview: true, error: 'Line 6 has no supply.' },
        {
          pso_id: 'pso-so-b',
          ok: true,
          preview: true,
          lines_confirmed: [{ project_line_id: 'pl-so-b-1', line_no: 1, item_code: 'WESERP20B' }],
          lines_withdrawn: [],
        },
      ],
    });
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-so-b', ok: true, decision_revision: 1, lines_confirmed: [], lines_carried: 0 }],
    });

    renderPanel(['SO403340', 'SO398322']);
    fireEvent.click(await screen.findByTestId('board-confirm'));

    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    const [body] = confirmMany.mock.calls[0] as [{ orders: { pso_id: string }[] }];
    expect(body.orders.map((order) => order.pso_id)).toEqual(['pso-so-b']);
    expect(await screen.findByTestId('board-confirm-results')).toHaveTextContent('Line 6 has no supply.');
  });

  it('still posts an order whose named lines are all already fulfilled, and names them in the results', async () => {
    getPlanningBoard.mockResolvedValue(twoLines());
    previewConfirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          preview: true,
          revision_no: null,
          lines_confirmed: [],
          lines_withdrawn: [],
          lines_fulfilled_skipped: 2,
        },
      ],
    });
    confirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          decision_revision: null,
          lines_confirmed: [],
          lines_carried: 0,
          lines_fulfilled_skipped: 2,
        },
      ],
    });

    renderPanel(['SO403340']);
    fireEvent.click(await screen.findByTestId('board-confirm'));

    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    const block = await screen.findByTestId('board-confirm-results');
    expect(block).toHaveTextContent('2 lines already fulfilled, decision cleared');
    expect(block).toHaveTextContent('line 6 SRTWT6808');
    expect(block).toHaveTextContent('line 9 SRTWT6808');
  });

  it('shows a "nothing to confirm" entry for an order the dry run answers with nothing at all', async () => {
    getPlanningBoard.mockResolvedValue(twoLines());
    previewConfirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          preview: true,
          lines_confirmed: [],
          lines_withdrawn: [],
          lines_fulfilled_skipped: 0,
        },
      ],
    });

    renderPanel(['SO403340']);
    fireEvent.click(await screen.findByTestId('board-confirm'));

    const block = await screen.findByTestId('board-confirm-results');
    expect(block).toHaveTextContent('SO403340: has nothing to confirm');
    expect(confirmMany).not.toHaveBeenCalled();
  });

  it('posts nothing when the dry run itself fails', async () => {
    getPlanningBoard.mockResolvedValue(twoLines());
    previewConfirmMany.mockRejectedValue(new Error('Network down'));

    renderPanel(['SO403340']);
    fireEvent.click(await screen.findByTestId('board-confirm'));

    await waitFor(() => expect(previewConfirmMany).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId('board-confirm')).toBeEnabled());
    expect(confirmMany).not.toHaveBeenCalled();
  });


  it('names the lines the dry run held back in the results, and counts them in the toast', async () => {
    getPlanningBoard.mockResolvedValue(twoLines());
    previewConfirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          preview: true,
          lines_confirmed: [{ project_line_id: 'pl-so-a-6', line_no: 6, item_code: 'SRTWT6808' }],
          lines_withdrawn: [],
          lines_held_back: [{ line_no: 9, item_code: 'SRTWT6808', reason: 'only 3 free' }],
        },
      ],
    });
    confirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: true,
          decision_revision: 2,
          lines_confirmed: [{ project_line_id: 'pl-so-a-6', line_no: 6, item_code: 'SRTWT6808' }],
          lines_carried: 0,
        },
      ],
    });

    renderPanel(['SO403340']);
    fireEvent.click(await screen.findByTestId('board-confirm'));

    const block = await screen.findByTestId('board-confirm-results');
    expect(block).toHaveTextContent('only 3 free');
    expect(block).toHaveTextContent('Line 9');
    const toasts = [
      ...(toast.success as unknown as { mock: { calls: unknown[][] } }).mock.calls,
      ...(toast.warning as unknown as { mock: { calls: unknown[][] } }).mock.calls,
    ].map((call) => String(call[0]));
    expect(toasts.some((text) => text.includes('1 held back'))).toBe(true);
  });

});

describe('FulfilmentBoardPanel: search and Status live under the cards, not in the header (owner, 30 Sep)', () => {
  const SEARCH = 'Search sales order, customer, project or product';
  const twoProducts = () =>
    withContribution(
      boardOf([
        demand({ line_no: 1, item_code: 'WESERP10B' }),
        demand({ line_no: 2, item_code: 'TPE-9204' }),
      ]),
      (entry) => entry.item_code === 'WESERP10B',
      (entry) => ({
        ...entry,
        draft: {
          decision: { verdict: 'approved' as const },
          saved_by: 'Test Planner',
          saved_at: '2026-09-08T00:00:00Z',
        },
      }),
    );

  it('grid view: no search in the header; the filter strip has the search and Status, and no Columns', async () => {
    getPlanningBoard.mockResolvedValue(twoProducts());
    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    expect(within(screen.getByTestId('board-header')).queryByPlaceholderText(SEARCH)).toBeNull();
    const strip = screen.getByTestId('board-grid-filter-strip');
    expect(within(strip).getByPlaceholderText(SEARCH)).toBeInTheDocument();
    expect(within(strip).getByText('Status')).toBeInTheDocument();
    expect(within(strip).queryByRole('button', { name: 'Columns' })).toBeNull();
    expect(within(strip).queryByRole('button', { name: 'Expand all' })).toBeNull();
    expect(within(strip).queryByTestId('board-decide-button')).toBeNull();
  });

  it('grid view: the strip is ONE flex row with the search and the compact Status control as siblings', async () => {
    getPlanningBoard.mockResolvedValue(twoProducts());
    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    const strip = screen.getByTestId('board-grid-filter-strip');
    // The same Card > CardHeader shell PanelDataGrid draws around the list toolbar, so the search
    // box shares the list's left edge instead of jumping between views.
    expect(strip.closest('[data-slot="card-header"]')).not.toBeNull();
    expect(strip.className).toMatch(/\bflex\b/);
    expect(strip.className).not.toMatch(/flex-col/);
    // Search, the Saved | All toggle, then the compact Status control: siblings in one row.
    expect(strip.children).toHaveLength(3);
    expect(within(strip.children[0] as HTMLElement).getByPlaceholderText(SEARCH)).toBeInTheDocument();
    expect(strip.children[1]).toHaveAttribute('data-testid', 'board-scope-toggle');
    const status = strip.children[2] as HTMLElement;
    expect(status).toHaveTextContent('Status');
    expect(status.className).toMatch(/\bw-40\b/);
    expect(status.className).not.toMatch(/\bw-full\b/);
  });

  it('list view: no title row, no header search; the list toolbar carries the search, Filters, Columns and Decide', async () => {
    getPlanningBoard.mockResolvedValue(twoProducts());
    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));

    const toolbar = await waitFor(() => {
      const found = document.querySelector('[data-slot="data-grid-list-toolbar"]');
      if (!found) throw new Error('toolbar not yet mounted');
      return found as HTMLElement;
    });
    expect(within(screen.getByTestId('board-header')).queryByPlaceholderText(SEARCH)).toBeNull();
    expect(within(toolbar).getByPlaceholderText(SEARCH)).toBeInTheDocument();
    expect(within(toolbar).getByRole('button', { name: /^Filters/ })).toBeInTheDocument();
    expect(within(toolbar).getByRole('button', { name: 'Columns' })).toBeInTheDocument();
    expect(within(toolbar).getByRole('button', { name: 'Expand all' })).toBeInTheDocument();
    expect(within(toolbar).getByRole('button', { name: 'Collapse all' })).toBeInTheDocument();
    expect(within(toolbar).getByTestId('board-decide-button')).toBeInTheDocument();
    expect(screen.queryByText('Every contributing line')).toBeNull();
  });

  it('the Status filter narrows the matrix rows to the products that have a saved line', async () => {
    getPlanningBoard.mockResolvedValue(twoProducts());
    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    expect(screen.getByText('TPE-9204')).toBeInTheDocument();

    const strip = screen.getByTestId('board-grid-filter-strip');
    await userEvent.click(within(strip).getByText('Status'));
    await userEvent.click(await screen.findByRole('option', { name: /Saved/ }));
    await userEvent.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByText('TPE-9204')).not.toBeInTheDocument());
    expect(screen.getByText('WESERP10B')).toBeInTheDocument();
  });
});

describe('FulfilmentBoardPanel: Saved | All toggle, carried-over behaviour (owner, 30 Sep)', () => {
  /** A saved, B suggested (no draft), C confirmed (covered by an active decision). */
  const threeStates = () => {
    const base = boardOf([
      demand({ line_no: 1, item_code: 'WESERP10B' }),
      demand({ line_no: 2, item_code: 'TPE-9204' }),
      demand({ line_no: 3, item_code: 'WESERP20B' }),
    ]);
    const saved = withContribution(
      base,
      (entry) => entry.item_code === 'WESERP10B',
      (entry) => ({
        ...entry,
        draft: {
          decision: { verdict: 'approved' as const },
          saved_by: 'Test Planner',
          saved_at: '2026-09-08T00:00:00Z',
        },
      }),
    );
    return withContribution(
      saved,
      (entry) => entry.item_code === 'WESERP20B',
      (entry) => ({
        ...entry,
        covered: true,
        decision: { revision_no: 1, timely_spo_qty: '0', reserve: [], borrow: [], buy_qty: '100' },
      }),
    );
  };
  const segment = (name: RegExp) => screen.getByRole('button', { name });

  beforeEach(() => {
    currentSearchParams = new URLSearchParams('view=grid');
  });

  it('keeps the chosen segment across a Confirm press', async () => {
    getPlanningBoard.mockResolvedValue(threeStates());
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-so-a', ok: true, decision_revision: 2, lines_confirmed: [], lines_carried: 0 }],
    });
    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');
    await userEvent.click(segment(/^Saved/));

    fireEvent.click(await screen.findByTestId('board-confirm'));
    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    await screen.findByTestId('board-confirm-results');

    expect(segment(/^Saved/)).toHaveAttribute('aria-pressed', 'true');
  });

  it('the pressed segment uses the same primary variant as the Grid | List switch', async () => {
    getPlanningBoard.mockResolvedValue(threeStates());
    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    const pressedHeaderItem = screen.getByRole('button', { name: 'Grid' });
    expect(pressedHeaderItem).toHaveAttribute('aria-pressed', 'true');
    const primaryToken = 'bg-primary';
    expect(pressedHeaderItem.className).toContain(primaryToken);
    expect(segment(/^All/).className).toContain(primaryToken);
    expect(segment(/^Saved/).className).not.toContain(primaryToken);

    await userEvent.click(segment(/^Saved/));
    await waitFor(() => expect(segment(/^Saved/).className).toContain(primaryToken));
    expect(segment(/^All/).className).not.toContain(primaryToken);
  });

  it('reads "Nothing to confirm yet" in the grid and in the list when Saved has nothing', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([demand({ line_no: 1, item_code: 'WESERP10B' })]),
    );
    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    await userEvent.click(segment(/^Saved/));
    expect(await screen.findByText('Nothing to confirm yet')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    expect(await screen.findByText('Nothing to confirm yet')).toBeInTheDocument();
    expect(screen.queryByText('Nothing is outstanding on this board')).not.toBeInTheDocument();
  });


  it('leaves the Saved and All counts alone when a Status is chosen', async () => {
    getPlanningBoard.mockResolvedValue(threeStates());
    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    const strip = screen.getByTestId('board-grid-filter-strip');
    await userEvent.click(within(strip).getByText('Status'));
    await userEvent.click(await screen.findByRole('option', { name: /Confirmed/ }));
    await userEvent.keyboard('{Escape}');

    expect(segment(/^Saved/)).toHaveTextContent('Saved (1)');
    expect(segment(/^All/)).toHaveTextContent('All (3)');
  });

  it('the left-out link reaches a saved line from the Saved segment', async () => {
    const board = allSaved(
      boardOf([
        demand({ line_no: 1, item_code: 'WESERP10B' }),
        demand({ line_no: 2, item_code: 'TPE-9204' }),
      ]),
    );
    getPlanningBoard.mockResolvedValue(
      withContribution(
        board,
        (entry) => entry.item_code === 'TPE-9204',
        (entry) => ({ ...entry, project_line_id: null }),
      ),
    );
    renderPanel(['SO403340']);
    await screen.findByTestId('board-grid-filter-strip');
    await userEvent.click(segment(/^Saved/));

    const banner = await screen.findByTestId('board-left-out-banner');
    fireEvent.click(within(banner).getByRole('button', { name: 'TPE-9204 line 2' }));

    expect(await screen.findByTestId(/^line-decision-so-a\|2\|TPE-9204/)).toBeInTheDocument();
    // The link sets All so it reaches the row.
    const toggle = within(screen.getByTestId('board-scope-toggle'));
    expect(toggle.getByRole('button', { name: /^Saved/ })).toHaveAttribute('aria-pressed', 'false');
    expect(toggle.getByRole('button', { name: /^All/ })).toHaveAttribute('aria-pressed', 'true');
  });

});

describe('FulfilmentBoardPanel: Saved | All toggle (owner hand test, 1 Oct)', () => {
  /** A saved, B suggested (no draft), C confirmed (covered by an active decision). */
  const threeStates = () => {
    const base = boardOf([
      demand({ line_no: 1, item_code: 'WESERP10B' }),
      demand({ line_no: 2, item_code: 'TPE-9204' }),
      demand({ line_no: 3, item_code: 'WESERP20B' }),
    ]);
    const saved = withContribution(
      base,
      (entry) => entry.item_code === 'WESERP10B',
      (entry) => ({
        ...entry,
        draft: {
          decision: { verdict: 'approved' as const },
          saved_by: 'Test Planner',
          saved_at: '2026-09-08T00:00:00Z',
        },
      }),
    );
    return withContribution(
      saved,
      (entry) => entry.item_code === 'WESERP20B',
      (entry) => ({
        ...entry,
        covered: true,
        decision: { revision_no: 1, timely_spo_qty: '0', reserve: [], borrow: [], buy_qty: '100' },
      }),
    );
  };
  // Inside the toggle: a saved row's "Saved by ..." pill is also a button starting with Saved.
  const segment = (name: RegExp) =>
    within(screen.getByTestId('board-scope-toggle')).getByRole('button', { name });
  // The list shows order numbers, not item codes: a row is told by its select box.
  const rowOf = (line: number, query = false) =>
    query
      ? screen.queryByRole('checkbox', { name: `Select SO403340 line ${line}` })
      : screen.getByRole('checkbox', { name: `Select SO403340 line ${line}` });
  const toList = async () => {
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));
  };
  const findRow = (line: number) =>
    screen.findByRole('checkbox', { name: `Select SO403340 line ${line}` });

  beforeEach(() => {
    // No `scope=` at all: the default segment is what is under test.
    currentSearchParams = new URLSearchParams('view=grid');
  });

  it('AC-ALL-1: defaults to All: Saved (1) | All (3), All pressed and primary, every row visible', async () => {
    getPlanningBoard.mockResolvedValue(threeStates());
    renderPanel(['SO403340']);
    await toList();

    expect(await findRow(1)).toBeInTheDocument();
    expect(rowOf(2)).toBeInTheDocument();
    expect(rowOf(3)).toBeInTheDocument();
    expect(segment(/^Saved/)).toHaveTextContent('Saved (1)');
    expect(segment(/^All/)).toHaveTextContent('All (3)');
    expect(segment(/^All/)).toHaveAttribute('aria-pressed', 'true');
    expect(segment(/^All/).className).toContain('bg-primary');
    expect(segment(/^Saved/)).toHaveAttribute('aria-pressed', 'false');
    expect(segment(/^Saved/).className).not.toContain('bg-primary');
    expect(screen.queryByRole('button', { name: /^Others/ })).not.toBeInTheDocument();
  });

  it('AC-ALL-2: a line stays visible after it is saved; Saved goes 0 to 1, All stays 2', async () => {
    getPlanningBoard.mockResolvedValue(
      boardOf([
        demand({ line_no: 1, item_code: 'WESERP10B' }),
        demand({ line_no: 2, item_code: 'TPE-9204' }),
      ]),
    );
    renderPanel(['SO403340']);
    await toList();

    expect(await findRow(1)).toBeInTheDocument();
    expect(segment(/^Saved/)).toHaveTextContent('Saved (0)');
    expect(segment(/^All/)).toHaveTextContent('All (2)');

    await userEvent.click(screen.getByRole('button', { name: 'Save SO403340 line 1 as suggested' }));

    await waitFor(() => expect(putLineDraft).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(segment(/^Saved/)).toHaveTextContent('Saved (1)'));
    expect(segment(/^All/)).toHaveTextContent('All (2)');
    expect(rowOf(1)).toBeInTheDocument();
    expect(rowOf(2)).toBeInTheDocument();
  });

  it('AC-ALL-3: Saved shows only the posting set; All shows every row again', async () => {
    getPlanningBoard.mockResolvedValue(threeStates());
    renderPanel(['SO403340']);
    await toList();
    await findRow(1);

    await userEvent.click(segment(/^Saved/));
    await waitFor(() => expect(rowOf(2, true)).not.toBeInTheDocument());
    expect(rowOf(1)).toBeInTheDocument();
    expect(rowOf(3, true)).not.toBeInTheDocument();
    expect(segment(/^Saved/)).toHaveAttribute('aria-pressed', 'true');

    await userEvent.click(segment(/^All/));
    await waitFor(() => expect(rowOf(2)).toBeInTheDocument());
    expect(rowOf(1)).toBeInTheDocument();
    expect(rowOf(3)).toBeInTheDocument();
    expect(segment(/^All/)).toHaveAttribute('aria-pressed', 'true');
  });

  it('AC-ALL-4: the grid strip reads Saved (1) | All (3) and All shows every matrix row', async () => {
    currentSearchParams = new URLSearchParams('view=grid');
    getPlanningBoard.mockResolvedValue(threeStates());
    renderPanel(['SO403340']);
    await screen.findByTestId('fulfilment-board-matrix');

    const strip = screen.getByTestId('board-grid-filter-strip');
    expect(within(strip).getByRole('button', { name: 'Saved (1)' })).toBeInTheDocument();
    expect(within(strip).getByRole('button', { name: 'All (3)' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByText('WESERP10B')).toBeInTheDocument();
    expect(screen.getByText('TPE-9204')).toBeInTheDocument();
    expect(screen.getByText('WESERP20B')).toBeInTheDocument();
  });

  it('AC-ALL-5: under All the "No other lines" copy never appears, a fully saved board shows its rows', async () => {
    getPlanningBoard.mockResolvedValue(
      allSaved(boardOf([demand({ line_no: 1, item_code: 'WESERP10B' })])),
    );
    renderPanel(['SO403340']);
    await toList();

    expect(await findRow(1)).toBeInTheDocument();
    expect(screen.queryByText('No other lines')).not.toBeInTheDocument();
    expect(segment(/^All/)).toHaveTextContent('All (1)');

    fireEvent.click(screen.getByRole('button', { name: 'Grid' }));
    await screen.findByTestId('fulfilment-board-matrix');
    expect(screen.getByText('WESERP10B')).toBeInTheDocument();
    expect(screen.queryByText('No other lines')).not.toBeInTheDocument();
  });
});
