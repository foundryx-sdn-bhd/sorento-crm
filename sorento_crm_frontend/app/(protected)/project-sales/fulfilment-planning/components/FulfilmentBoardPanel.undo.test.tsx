/**
 * Review round (reviewer + security-reviewer finding, `PLAN-board-undo-last-confirm.md`):
 * the board's own gear-menu undo entries, over a MOCKED board payload carrying the real
 * `undo` field (S1 already lands this on the wire; this pins the PANEL's own read of it).
 *
 * AC-UC-02 two entries for two undoable orders of three; AC-UC-03 a refused entry is
 * disabled AND its reason text is VISIBLE in the item (not only in a `title` attribute -
 * the review round's own amendment to AC-UC-03, since a `title` never surfaces on a
 * screen reader or a touch device); AC-UC-04 no entry and no separator when nothing on
 * the board is undoable.
 *
 * Mirrors `FulfilmentBoardPanel.autobatch.test.tsx`'s scaffolding (same mocks, same
 * `renderPanel` shape, `buildBoard` fixture) rather than copying its assertions.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/project-sales/fulfilment-planning',
  useSearchParams: () => new URLSearchParams('view=grid&scope=all'),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({
    resetToDefaults: vi.fn(),
    isLoading: false,
  }),
}));

const getPlanningBoard = vi.fn();
const deleteLineDraft = vi.fn().mockResolvedValue(undefined);

vi.mock('../../_shared/services/fulfilmentPlanningService', () => ({
  getPlanningBoard: (...args: unknown[]) => getPlanningBoard(...args),
  listFulfilmentPlanning: vi.fn(),
  getReconciliation: vi.fn(),
  rerunReconciliation: vi.fn(),
  adoptSalesOrder: vi.fn(),
  getSupply: vi.fn(),
  confirmSupply: vi.fn(),
  confirmMany: vi.fn(),
  putLineDraft: vi.fn().mockResolvedValue({
    decision: { verdict: 'approved' },
    saved_by: 'Test Planner',
    saved_at: '2026-09-03T00:00:00Z',
  }),
  deleteLineDraft: (...args: unknown[]) => deleteLineDraft(...args),
  ConfirmSupplyError: class ConfirmSupplyError extends Error {
    readonly failingLines: unknown[] = [];
  },
}));

// AC-B13: captured (not an inline `vi.fn()`) so the new describe block below can seed a
// real `PlanningChangeBatch` per test - the same pattern `FulfilmentBoardPanel.change.test.tsx`
// uses for the same mock.
const getPlanningChangeBatch = vi.fn();

vi.mock('../../_shared/services/planningChangeService', () => ({
  listPlanningChangeBatches: vi.fn(),
  getPlanningChangeBatch: (...args: unknown[]) => getPlanningChangeBatch(...args),
  updatePlanningChangeRow: vi.fn(),
  applyPlanningChanges: vi.fn(),
}));

vi.mock('@/lib/toast', () => ({
  toast: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
    // The pending-entity store takes its own countdown toast down once the
    // parked undo settles - without this the store's follow-through timer
    // throws an unhandled rejection if it fires after the test ends.
    dismiss: vi.fn(),
  },
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { id: 'user-1', name: 'Test Planner' } },
    status: 'authenticated',
  }),
}));

const createPendingAction = vi.fn().mockResolvedValue({
  id: 'pending-1',
  commit_at: '2026-09-18T00:00:10Z',
  entity_id: 'pso-1',
});

vi.mock('@/services/pendingActionService', () => ({
  createPendingAction: (...args: unknown[]) => createPendingAction(...args),
  cancelPendingAction: vi.fn(),
  // DELTA-1 (reviewer, fix round 3): the REAL shape, never a bare `null`.
  // `settleFromServer` (`lib/pending-entity-store.ts`) reads `current.pending` straight off
  // this resolution outside the try/catch that guards the read itself, so a `null` threw an
  // unhandled rejection in roughly two runs in five - the file exited 1 with every test
  // still reported as passing, which is the worst possible way to find out.
  getCurrentPendingAction: vi
    .fn()
    .mockResolvedValue({ pending: null, last_outcome: null }),
}));

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => false,
}));
vi.mock('../../_shared/hooks/useBoardTransfers', () => ({
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
import { FulfilmentBoardPanel } from './FulfilmentBoardPanel';
import { buildBoard, type BoardDemandLine } from '../../_shared/lib/__testsupport__/boardFixture';
import { MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE } from '../../_shared/__mocks__/planningChanges';
import { pendingEntityStore } from '@/lib/pending-entity-store';

const TODAY = '2026-09-17';

function demand(overrides: Partial<BoardDemandLine> = {}): BoardDemandLine {
  return {
    sales_order_id: 'so-1',
    so_number: 'SO000001',
    customer_name: 'Test Customer',
    project_sales_order_id: 'pso-1',
    project_line_id: 'pl-1-1',
    line_no: 1,
    item_code: 'ZZT-ITEM-1',
    qty: '10',
    required_date: '2026-09-20',
    fulfilment_location: 'BRW-IB',
    priority: null,
    ...overrides,
  } as BoardDemandLine;
}

type UndoOverlay = {
  revision_no: number;
  refusal: 'linked' | 'actioned' | 'changed' | null;
  decision_id: string;
  /** `PLAN-scm-oi-handover-r2-undo.md` S6. Optional here so the pre-r2 AC-UC-02/03/04
   * fixtures above (written before `mode` existed) still compile - the component's own
   * fallback (`order.undo?.mode ?? 'journal'`) is what a real omitted field would read
   * as too. */
  mode?: 'journal' | 'reconstructed';
};

/** Overlays a mocked `undo` onto `board.orders`, keyed by `sales_order_id` - the same
 * shape `withPendingBatch` in the autobatch spec uses for `pending_change_batch_id`. */
function withUndo(
  board: ReturnType<typeof buildBoard>,
  mapping: Record<string, UndoOverlay | null>,
) {
  return {
    ...board,
    orders: board.orders.map((order) => ({
      ...order,
      undo: mapping[order.sales_order_id] ?? null,
    })),
  } as ReturnType<typeof buildBoard>;
}

function renderPanel(soNumbers: string[]) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <FulfilmentBoardPanel soNumbers={soNumbers} batchId={null} onBack={vi.fn()} />
    </QueryClientProvider>,
  );
}

async function openBoardActions() {
  // Radix's `DropdownMenuTrigger` opens on keyboard activation more reliably than a
  // plain `click` under jsdom - the same pattern `FulfilmentBoardPanel.test.tsx` uses
  // for every other gear-menu interaction in this file's own suite.
  const trigger = await screen.findByRole('button', { name: 'Board actions' });
  fireEvent.keyDown(trigger, { key: 'Enter' });
  await screen.findByRole('menuitem', { name: 'Undo all' });
  return screen.getByRole('menu');
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  // AC-R2-F04 starts an undo whose fixture `commit_at` is already in the
  // past, so the store's own follow-through timer is armed for real; putting
  // it down here (rather than waiting for it to fire on its own after the
  // test ends) is what keeps a later suite from seeing an unhandled
  // rejection from a timer this test left running.
  pendingEntityStore.clear('project_sales_order', 'pso-1');
});

describe('AC-UC-02: one gear entry per undoable order, of three', () => {
  it('lists exactly two entries for two undoable orders, none for the third', async () => {
    const board = buildBoard(
      [
        demand({ sales_order_id: 'so-1', so_number: 'SO000001', project_line_id: 'pl-1-1' }),
        demand({ sales_order_id: 'so-2', so_number: 'SO000002', project_sales_order_id: 'pso-2', project_line_id: 'pl-2-1' }),
        demand({ sales_order_id: 'so-3', so_number: 'SO000003', project_sales_order_id: 'pso-3', project_line_id: 'pl-3-1' }),
      ],
      { today: TODAY, freeStock: {}, granularity: 'week' },
    );
    getPlanningBoard.mockResolvedValue(
      withUndo(board, {
        'so-1': { revision_no: 1, refusal: null, decision_id: 'dec-1' },
        'so-2': { revision_no: 1, refusal: null, decision_id: 'dec-2' },
        'so-3': null,
      }),
    );

    renderPanel(['SO000001', 'SO000002', 'SO000003']);
    const menu = await openBoardActions();

    const items = within(menu).getAllByText(/^Undo SO0000\d+ confirm \(rev 1\)$/);
    expect(items).toHaveLength(2);
    expect(within(menu).queryByText(/SO000003/)).not.toBeInTheDocument();
  });
});

describe('AC-UC-03: a refused entry is disabled and states its reason as visible text', () => {
  it('shows "Purchasing linked a PO line" in the item itself, not only as a title', async () => {
    const board = buildBoard(
      [demand({ sales_order_id: 'so-1', so_number: 'SO000001', project_line_id: 'pl-1-1' })],
      { today: TODAY, freeStock: {}, granularity: 'week' },
    );
    getPlanningBoard.mockResolvedValue(
      withUndo(board, {
        'so-1': { revision_no: 1, refusal: 'linked', decision_id: 'dec-1' },
      }),
    );

    renderPanel(['SO000001']);
    const menu = await openBoardActions();

    const item = within(menu).getByText(/Undo SO000001 confirm \(rev 1\)/).closest(
      '[role="menuitem"]',
    ) as HTMLElement;
    expect(item).toHaveAttribute('aria-disabled', 'true');
    // The reason must be readable TEXT inside the item - a `title` attribute is invisible
    // to a screen reader announcing the item and to anyone on a touch device.
    expect(within(item).getByText('Purchasing linked a PO line')).toBeInTheDocument();
  });
});

describe('AC-UC-04: nothing undoable renders no entry and no separator', () => {
  it('renders neither an undo entry nor its separator when no order is undoable', async () => {
    const board = buildBoard(
      [demand({ sales_order_id: 'so-1', so_number: 'SO000001', project_line_id: 'pl-1-1' })],
      { today: TODAY, freeStock: {}, granularity: 'week' },
    );
    getPlanningBoard.mockResolvedValue(withUndo(board, { 'so-1': null }));

    renderPanel(['SO000001']);
    const menu = await openBoardActions();

    expect(within(menu).queryByText(/Undo SO000001 confirm/)).not.toBeInTheDocument();
    const separators = within(menu).queryAllByRole('separator');
    // "Undo all" keeps its own leading separator; no SECOND one is added for an
    // undo-entries block that has nothing to show.
    expect(separators.length).toBeLessThanOrEqual(1);
  });
});

// --------------------------------------------------------------------------- //
// `PLAN-scm-oi-handover-r2-undo.md` S6 - the three FE cases the plan names:
// mode label, `changed` refusal, mode in the parked payload. The panel's own
// `mode`/`RECONSTRUCTED_UNDO_NOTE`/`UNDO_REFUSAL_TITLES.changed` logic already
// shipped in Phase 1 (frontend-first, against `NEXT_PUBLIC_BOARD_UNDO_MOCK`) -
// these pin that contract with a real `undo.mode` on the mocked board payload,
// the same way `AC-UC-02/03/04` above pin the journal-only shape.
// --------------------------------------------------------------------------- //

describe('AC-R2-F02: a reconstructed entry names itself and what it will not restore', () => {
  it('shows ", reconstructed" on the label and the drafts/notes note as its second line', async () => {
    const board = buildBoard(
      [demand({ sales_order_id: 'so-1', so_number: 'SO000001', project_line_id: 'pl-1-1' })],
      { today: TODAY, freeStock: {}, granularity: 'week' },
    );
    getPlanningBoard.mockResolvedValue(
      withUndo(board, {
        'so-1': { revision_no: 3, refusal: null, decision_id: 'dec-1', mode: 'reconstructed' },
      }),
    );

    renderPanel(['SO000001']);
    const menu = await openBoardActions();

    const item = within(menu)
      .getByText('Undo SO000001 confirm (rev 3), reconstructed')
      .closest('[role="menuitem"]') as HTMLElement;
    expect(item).not.toHaveAttribute('aria-disabled', 'true');
    expect(
      within(item).getByText('Saved drafts and row notes are not restored'),
    ).toBeInTheDocument();
  });
});

describe('AC-R2-F03: a "changed" refusal is disabled and states its own reason', () => {
  it('shows "A row changed since this confirm" as visible text in the item', async () => {
    const board = buildBoard(
      [demand({ sales_order_id: 'so-1', so_number: 'SO000001', project_line_id: 'pl-1-1' })],
      { today: TODAY, freeStock: {}, granularity: 'week' },
    );
    getPlanningBoard.mockResolvedValue(
      withUndo(board, {
        'so-1': { revision_no: 1, refusal: 'changed', decision_id: 'dec-1', mode: 'journal' },
      }),
    );

    renderPanel(['SO000001']);
    const menu = await openBoardActions();

    const item = within(menu)
      .getByText(/Undo SO000001 confirm \(rev 1\)/)
      .closest('[role="menuitem"]') as HTMLElement;
    expect(item).toHaveAttribute('aria-disabled', 'true');
    expect(within(item).getByText('A row changed since this confirm')).toBeInTheDocument();
  });
});

describe('AC-R2-F04 (payload half): mode travels with decision_id in the parked payload', () => {
  it('parks {decision_id, mode} together when a reconstructed entry is pressed', async () => {
    const board = buildBoard(
      [demand({ sales_order_id: 'so-1', so_number: 'SO000001', project_line_id: 'pl-1-1' })],
      { today: TODAY, freeStock: {}, granularity: 'week' },
    );
    getPlanningBoard.mockResolvedValue(
      withUndo(board, {
        'so-1': { revision_no: 2, refusal: null, decision_id: 'dec-9', mode: 'reconstructed' },
      }),
    );

    renderPanel(['SO000001']);
    const menu = await openBoardActions();
    const item = within(menu).getByText('Undo SO000001 confirm (rev 2), reconstructed');
    fireEvent.click(item);

    await waitFor(() => {
      expect(createPendingAction).toHaveBeenCalledWith(
        expect.objectContaining({
          actionKey: 'project_sales_order.undo_confirm',
          payload: { decision_id: 'dec-9', mode: 'reconstructed' },
        }),
      );
    });
  });
});

// --------------------------------------------------------------------------- //
// Review round 1, item 10: the reconstructed label must never be visually
// elided at a desktop viewport - the menu's own width cap (`sm:max-w-80`,
// 20rem) is narrower than "Undo SO000001 confirm (rev 3), reconstructed" plus
// its own second line needs, so the coder must widen it (`sm:max-w-md` or
// wider). Checked at the CSS-class level, since jsdom has no layout engine to
// measure actual visual truncation against.
// --------------------------------------------------------------------------- //

describe('review round: the reconstructed entry is not clipped at desktop width', () => {
  it('does not carry the old sm:max-w-80 cap on the menu content', async () => {
    const board = buildBoard(
      [demand({ sales_order_id: 'so-1', so_number: 'SO000001', project_line_id: 'pl-1-1' })],
      { today: TODAY, freeStock: {}, granularity: 'week' },
    );
    getPlanningBoard.mockResolvedValue(
      withUndo(board, {
        'so-1': { revision_no: 3, refusal: null, decision_id: 'dec-1', mode: 'reconstructed' },
      }),
    );

    renderPanel(['SO000001']);
    const menu = await openBoardActions();

    expect(
      within(menu).getByText('Undo SO000001 confirm (rev 3), reconstructed'),
    ).toBeInTheDocument();
    // `DropdownMenuContent` splits its own passed className onto an INNER
    // `motion.div` (components/ui/dropdown-menu.tsx) - the Radix Content node
    // `role="menu"` resolves to only ever carries the hardcoded `z-50`, so the
    // width cap under test lives on the menu's own first child.
    const contentDiv = menu.firstElementChild as HTMLElement;
    expect(contentDiv.className).not.toMatch(/\bsm:max-w-80\b/);
  });
});

// --------------------------------------------------------------------------- //
// AC-B13 (`board-verdict-actions-chips-acceptance-criteria.md`, "Undo returns to the
// pre-mark", owner finding 22 Sep: "it becomes suggested instead of change proposed"):
// a line the OPEN change batch names arrives pre-marked `Change proposed`
// (`{ verdict: 'approved', preMarked: true }`, `FulfilmentBoardPanel`'s own seeding
// effect ~line 546). Saving it writes a real draft (pill `Saved`); Undo today
// (`decide(key, null)` ~line 695) deletes the draft key outright, so the pill falls
// through `verdictOf` to `suggested` - the addendum's own diagnosis of the bug. The fix
// keeps `decide(null)` writing `{ verdict: 'approved', preMarked: true }` back over a key
// the seeding effect once pre-marked, instead of deleting it, while the SERVER delete
// still fires. RED today: Undo removes the key outright and the pill reads "Suggested".
// --------------------------------------------------------------------------- //

describe('AC-B13: Undo on a pre-marked line returns to "Change proposed", not "Suggested"', () => {
  const NAMED_BATCH = MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE;

  function namedDemand(): BoardDemandLine {
    // The batch's own row (`pcr-381895-1`) names exactly this `project_line_id` as
    // changed - `preMarkedKeys` (`boardChangeAnnotations.ts`) matches on it alone.
    return demand({
      sales_order_id: 'so-381895',
      so_number: 'SO381895',
      project_sales_order_id: 'pso-381895',
      project_line_id: 'pl-381895-1',
      line_no: 1,
      item_code: 'SRTWCX7405-RL-S-PJ',
      qty: '25',
      required_date: '2026-09-20',
    });
  }

  function unnamedDemand(): BoardDemandLine {
    // A line on the SAME board, opened on the SAME batch, whose `project_line_id` the
    // batch's rows never mention - `preMarkedKeys` leaves it out, so it opens plain
    // `Suggested` rather than `Change proposed`.
    return demand({
      sales_order_id: 'so-999',
      so_number: 'SO000999',
      project_sales_order_id: 'pso-999',
      project_line_id: 'pl-999-1',
      line_no: 1,
      item_code: 'ZZT-ITEM-9',
      qty: '5',
      required_date: '2026-09-21',
    });
  }

  function renderOnBatch(soNumbers: string[]) {
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
          batchId={NAMED_BATCH.id}
          onBack={vi.fn()}
        />
      </QueryClientProvider>,
    );
  }

  /** Switches to the List view, waits for the row's OPENING pill, saves it from the
   * expanded panel, waits for "Saved", then presses Undo - returning a live getter for
   * the row so the caller can read whatever the pill settles on afterwards. */
  async function saveThenUndo(soNumber: string, lineNo: number, openingPill: string) {
    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    await screen.findByText(soNumber);
    const row = () => screen.getByText(soNumber).closest('tr') as HTMLElement;

    await waitFor(() => expect(within(row()).getByText(openingPill)).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('board-list-expand-all'));
    fireEvent.click(await screen.findByRole('button', { name: 'Save decision' }));

    await waitFor(() => expect(within(row()).getByText('Saved')).toBeInTheDocument());

    fireEvent.click(
      within(row()).getByRole('button', { name: `Undo ${soNumber} line ${lineNo}` }),
    );
    return row;
  }

  beforeEach(() => {
    getPlanningChangeBatch.mockResolvedValue(NAMED_BATCH);
  });

  it('a line the open batch names is "Change proposed" again after Save then Undo, and the server draft is deleted', async () => {
    getPlanningBoard.mockResolvedValue(
      buildBoard([namedDemand()], { today: TODAY, freeStock: {}, granularity: 'week' }),
    );

    renderOnBatch(['SO381895']);
    const row = await saveThenUndo('SO381895', 1, 'Change proposed');

    // The server DELETE still runs (the addendum: "the server DELETE still runs"); only
    // the LOCAL draft entry changes shape.
    await waitFor(() => expect(deleteLineDraft).toHaveBeenCalled());
    await waitFor(() =>
      expect(within(row()).getByText('Change proposed')).toBeInTheDocument(),
    );
    expect(within(row()).queryByText('Suggested')).not.toBeInTheDocument();
  });

  it('a line the batch does NOT name still goes back to "Suggested" after Save then Undo, as today', async () => {
    getPlanningBoard.mockResolvedValue(
      buildBoard([unnamedDemand()], { today: TODAY, freeStock: {}, granularity: 'week' }),
    );

    renderOnBatch(['SO000999']);
    const row = await saveThenUndo('SO000999', 1, 'Suggested');

    await waitFor(() => expect(within(row()).getByText('Suggested')).toBeInTheDocument());
    expect(within(row()).queryByText('Change proposed')).not.toBeInTheDocument();
  });
});

/**
 * SF-5 (reviewer, fix round 2): AC-B13 is a rule about UNDOING, not about one button. The
 * board-wide discard ("Board actions" > "Undo all") deletes every draft key straight through
 * `removeDraftKey` and then wrote `setDraft({})`, which threw the pre-marks away with them -
 * so a line the open batch names came back reading `Suggested` from that path even after the
 * per-row Undo had been fixed.
 */
describe('SF-5: "Undo all" returns a batch-named line to "Change proposed" too', () => {
  const NAMED_BATCH = MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE;

  function namedDemand(): BoardDemandLine {
    return demand({
      sales_order_id: 'so-381895',
      so_number: 'SO381895',
      project_sales_order_id: 'pso-381895',
      project_line_id: 'pl-381895-1',
      line_no: 1,
      item_code: 'SRTWCX7405-RL-S-PJ',
      qty: '25',
      required_date: '2026-09-20',
    });
  }

  /** The batch's THIRD row names `pl-381895-3`, so this line opens pre-marked too - and
   * nothing here ever saves it, which is what makes its DELETE a 404 waiting to happen. */
  function preMarkOnlyDemand(): BoardDemandLine {
    return demand({
      sales_order_id: 'so-381895',
      so_number: 'SO381895',
      project_sales_order_id: 'pso-381895',
      project_line_id: 'pl-381895-3',
      line_no: 3,
      item_code: 'SRTWCX7405-RL-S-PJ',
      qty: '5',
      required_date: '2026-10-05',
    });
  }

  beforeEach(() => {
    getPlanningChangeBatch.mockResolvedValue(NAMED_BATCH);
  });

  /**
   * AC-B14 (browser pass, 22 Sep 2026): the board-wide discard said NOTHING when it landed -
   * the `N lines undone` toast lived on `undoMany`, which only the grid cell's own undo icon
   * calls - and it fired a DELETE for every key in the draft, including the bare pre-marks
   * the seeding effect put there: 17 requests, 16 of them 404, on the owner's own run.
   */
  it('AC-B14: toasts the lines it actually undid, and never DELETEs a bare pre-mark', async () => {
    getPlanningBoard.mockResolvedValue(
      buildBoard([namedDemand(), preMarkOnlyDemand()], {
        today: TODAY,
        freeStock: {},
        granularity: 'week',
      }),
    );

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });
    render(
      <QueryClientProvider client={client}>
        <FulfilmentBoardPanel
          soNumbers={['SO381895']}
          batchId={NAMED_BATCH.id}
          onBack={vi.fn()}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    // S6 (`PLAN-board-oi-mechanical-22sep.md`, AC-B6-13/AC-B6-14) split the old
    // "SO381895 (Line 1)" cell title: the Sales order cell now titles the number alone,
    // which every row of this one order shares. The row's own select checkbox still names
    // the line ("Select SO381895 line 1", `buildSelectColumn`'s `rowLabel`), so it is what
    // tells two rows of one order apart here.
    const rowOf = (lineNo: number) =>
      screen
        .getByRole('checkbox', { name: `Select SO381895 line ${lineNo}` })
        .closest('tr') as HTMLElement;
    await screen.findByRole('checkbox', { name: 'Select SO381895 line 1' });

    // BOTH lines are named by the open batch, so both open pre-marked.
    await waitFor(() =>
      expect(within(rowOf(1)).getByText('Change proposed')).toBeInTheDocument(),
    );
    expect(within(rowOf(3)).getByText('Change proposed')).toBeInTheDocument();

    // Only line 1 is actually saved, so only line 1 has anything on the server. (The row
    // opens on a click anywhere but the sales-order link, which navigates instead.)
    fireEvent.click(within(rowOf(1)).getByText('SRTWCX7405-RL-S-PJ'));
    fireEvent.click(await screen.findByRole('button', { name: 'Save decision' }));
    await waitFor(() => expect(within(rowOf(1)).getByText('Saved')).toBeInTheDocument());
    vi.mocked(toast.success).mockClear();

    fireEvent.keyDown(screen.getByRole('button', { name: 'Board actions' }), {
      key: 'Enter',
    });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Undo all' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));

    // ONE delete, for the one line that had a decision saved against it.
    await waitFor(() => expect(deleteLineDraft).toHaveBeenCalledTimes(1));
    expect(String(deleteLineDraft.mock.calls[0][0])).toContain('so-381895|1|');
    expect(
      deleteLineDraft.mock.calls.some(([key]) => String(key).includes('|3|')),
    ).toBe(false);

    // And it says so, counting the line it undid rather than every key in the draft.
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('1 line undone'));

    // Both lines are back to the book's own proposal.
    await waitFor(() =>
      expect(within(rowOf(1)).getByText('Change proposed')).toBeInTheDocument(),
    );
    expect(within(rowOf(3)).getByText('Change proposed')).toBeInTheDocument();
  });

  it('discards the saved draft on the server and leaves the pre-mark reading "Change proposed"', async () => {
    getPlanningBoard.mockResolvedValue(
      buildBoard([namedDemand()], { today: TODAY, freeStock: {}, granularity: 'week' }),
    );

    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });
    render(
      <QueryClientProvider client={client}>
        <FulfilmentBoardPanel
          soNumbers={['SO381895']}
          batchId={NAMED_BATCH.id}
          onBack={vi.fn()}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    await screen.findByText('SO381895');
    const row = () => screen.getByText('SO381895').closest('tr') as HTMLElement;
    await waitFor(() =>
      expect(within(row()).getByText('Change proposed')).toBeInTheDocument(),
    );

    // Save it, so there is a real draft for the board-wide discard to act on.
    fireEvent.click(screen.getByTestId('board-list-expand-all'));
    fireEvent.click(await screen.findByRole('button', { name: 'Save decision' }));
    await waitFor(() => expect(within(row()).getByText('Saved')).toBeInTheDocument());

    // Board actions > Undo all > Discard.
    fireEvent.keyDown(screen.getByRole('button', { name: 'Board actions' }), {
      key: 'Enter',
    });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Undo all' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));

    // The server draft goes (AC-4.3) and the pre-mark comes back in its place.
    await waitFor(() => expect(deleteLineDraft).toHaveBeenCalled());
    await waitFor(() =>
      expect(within(row()).getByText('Change proposed')).toBeInTheDocument(),
    );
    expect(within(row()).queryByText('Suggested')).not.toBeInTheDocument();
  });
});
