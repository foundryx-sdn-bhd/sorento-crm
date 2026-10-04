/**
 * The board opened ON a planning-change batch (`PLAN-scm-cs-planning-uat.md` part 3).
 *
 * AC-P3-2 (the Was / Now table on the changed cell, `Closed` on a line the book closed),
 * AC-P3-3 (the cell arrives pre-marked, in board words only), AC-P3-4 (Confirm carries the
 * batch and a batch already applied refuses a second press), AC-P3-9 (the moved transfer is
 * stated on the cell).
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

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/project-sales/fulfilment-planning',
  // R-J (List is now the default view): every spec here exercises the GRID matrix, so
  // `?view=grid` is seeded rather than clicking the Grid button in each test.
  useSearchParams: () => new URLSearchParams('view=grid&scope=all'),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({
    resetToDefaults: vi.fn(),
    isLoading: false,
  }),
}));

const getPlanningBoard = vi.fn();
const confirmSupply = vi.fn();
const confirmMany = vi.fn();
const previewConfirmMany = vi.fn();

vi.mock('../../_shared/services/fulfilmentPlanningService', () => ({
  getPlanningBoard: (...args: unknown[]) => getPlanningBoard(...args),
  listFulfilmentPlanning: vi.fn(),
  getReconciliation: vi.fn(),
  rerunReconciliation: vi.fn(),
  adoptSalesOrder: vi.fn(),
  getSupply: vi.fn(),
  confirmSupply: (...args: unknown[]) => confirmSupply(...args),
  confirmMany: (...args: unknown[]) => confirmMany(...args),
  previewConfirmMany: (...args: unknown[]) => previewConfirmMany(...args),
  // S4 (`useLineDraftMutation`): `decide()` closes over these regardless of whether a test
  // presses Save deep enough to reach them.
  putLineDraft: vi.fn().mockResolvedValue({
    decision: { verdict: 'approved' },
    saved_by: 'Test Planner',
    saved_at: '2026-09-03T00:00:00Z',
  }),
  deleteLineDraft: vi.fn().mockResolvedValue(undefined),
  ConfirmSupplyError: class ConfirmSupplyError extends Error {
    readonly failingLines: unknown[] = [];
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

// S4: `decide()` names the saver off the session (R-F).
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { id: 'user-1', name: 'Test Planner' } },
    status: 'authenticated',
  }),
}));

/**
 * `BoardTransfersPanel` (D4) is on this screen now, above the matrix. Its own behaviour is
 * `BoardTransfersPanel.test.tsx`'s; this mock only keeps the board itself renderable.
 */
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => false,
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

import { FulfilmentBoardPanel } from './FulfilmentBoardPanel';
import {
  buildBoard,
  type BoardDemandLine,
} from '../../_shared/lib/__testsupport__/boardFixture';
import {
  MOCK_PLANNING_CHANGE_BATCH_PENDING,
  MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE,
} from '../../_shared/__mocks__/planningChanges';

const TODAY = '2026-08-18';

function demand(overrides: Partial<BoardDemandLine> = {}): BoardDemandLine {
  return {
    sales_order_id: 'so-381895',
    so_number: 'SO381895',
    customer_name: 'YOTU BUILDER',
    project_sales_order_id: 'pso-381895',
    project_line_id: 'pl-381895-1',
    line_no: 1,
    item_code: 'SRTWCX7405-RL-S-PJ',
    qty: '25',
    required_date: '2026-08-19',
    fulfilment_location: 'BRW-IB',
    priority: null,
    ...overrides,
  } as BoardDemandLine;
}

function renderPanel(
  batchId: string | null = MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE.id,
  soNumbers: string[] = ['SO381895'],
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
        batchId={batchId}
        onBack={vi.fn()}
      />
    </QueryClientProvider>,
  );
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
  getPlanningBoard.mockResolvedValue(
    buildBoard([demand()], {
      today: TODAY,
      freeStock: {},
      granularity: 'week',
    }),
  );
  getPlanningChangeBatch.mockResolvedValue(
    MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE,
  );
});

/**
 * Owner feedback, 13 September 2026 (Slice C board display, AC-C9/AC-C10): was the inline
 * Was / Now table's own suite. The table is retired from the matrix cell; a changed line -
 * the advanced one and both cancelled ones alike - shows the hazard icon instead, and the
 * dialog it opens is where every fact this block used to read off the table now lives.
 */
describe('the changed cell', () => {
  it('shows the hazard icon, not the inline table, for the changed line and for both cancelled ones', async () => {
    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(await screen.findByTestId('board-change-icon-pcr-381895-1')).toBeInTheDocument();
    // A closed line has left the board, so it is annotated on the surviving cell of the same
    // product on the same order rather than disappearing with its own cell - same as before,
    // only the icon stands in for the table now.
    expect(screen.getByTestId('board-change-icon-pcr-381895-2')).toBeInTheDocument();
    expect(screen.getByTestId('board-change-icon-pcr-381895-3')).toBeInTheDocument();

    expect(screen.queryByTestId('board-change-pcr-381895-1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('board-change-pcr-381895-2')).not.toBeInTheDocument();
    expect(screen.queryByTestId('board-change-pcr-381895-3')).not.toBeInTheDocument();
  });

  it('reads Cancelled once for a line the book closed, with the suggestion beneath it', async () => {
    renderPanel();
    fireEvent.click(await screen.findByTestId('board-change-icon-pcr-381895-2'));
    const dialog = await screen.findByTestId('board-change-dialog');

    expect(within(dialog).getByText('What changed, SO381895 (Line 2)')).toBeInTheDocument();
    // The coder's own follow-up (ffeec9576): a cancelled line is a STATEMENT, not a Qty/
    // Date/Decision field each moving to the same place, so the lightbox prints "Cancelled"
    // once - what the held 10 became lives in the suggestion line beneath it instead.
    expect(within(dialog).getByText('Cancelled')).toBeInTheDocument();
    expect(within(dialog).queryByText(/^Qty /)).not.toBeInTheDocument();
    expect(
      within(dialog).getByText('Release Buy 10, line cancelled'),
    ).toBeInTheDocument();
  });

  it('says a transfer already moved for a cancelled line, and proposes no reversal', async () => {
    renderPanel();
    fireEvent.click(await screen.findByTestId('board-change-icon-pcr-381895-2'));
    const dialog = await screen.findByTestId('board-change-dialog');

    expect(
      within(dialog).getByText('10 moved BRW -> BRW-IB, line cancelled'),
    ).toBeInTheDocument();
  });

  it('never prints a retired reaction word in the dialog, and does print the composed suggestion', async () => {
    renderPanel();
    fireEvent.click(await screen.findByTestId('board-change-icon-pcr-381895-1'));
    const dialog = await screen.findByTestId('board-change-dialog');
    const printed = dialog.textContent ?? '';
    // Retired with the rule table (Slice C): a verb the row agreed with executed nothing.
    // Keep / Reduce / Release / Reallocate are now the SUGGESTION's own words, so they are
    // expected on screen - printed verbatim from the server's own sentence (AC-C1).
    for (const verb of ['Retire', 'Replan', 'Accept']) {
      expect(printed).not.toContain(verb);
    }
    expect(printed).toContain('Buy 25 (was 10)');
  });

  it('shows no table and no icon at all on a board opened without a batch', async () => {
    renderPanel(null);
    await screen.findByTestId('fulfilment-board-matrix');
    expect(screen.queryByTestId('board-change-pcr-381895-1')).toBeNull();
    expect(screen.queryByTestId('board-change-icon-pcr-381895-1')).toBeNull();
    expect(getPlanningChangeBatch).not.toHaveBeenCalled();
  });
});

/**
 * Owner feedback, 13 September 2026 (Slice C board display, AC-C9/AC-C10): through the FULL
 * panel this time, not the isolated `BoardChangeTable` component
 * (`BoardChangeTable.suggestion.test.tsx` owns that half) - the matrix cell must wire the
 * SAME icon-and-dialog through to a real batch, not only when handed an annotation directly.
 * RED: the matrix cell still renders `BoardChangeTable`'s full inline table today.
 */
describe('the changed cell shows the hazard icon and lightbox (owner feedback 13 Sep)', () => {
  it('shows one hazard icon in the matrix cell instead of the inline Was/Now block', async () => {
    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(await screen.findByTestId('board-change-icon-pcr-381895-1')).toBeInTheDocument();
    expect(screen.queryByTestId('board-change-pcr-381895-1')).not.toBeInTheDocument();
  });

  it('opens the lightbox on click, naming the SO and line, then the composed suggestion', async () => {
    renderPanel();
    const icon = await screen.findByTestId('board-change-icon-pcr-381895-1');
    fireEvent.click(icon);

    const dialog = await screen.findByTestId('board-change-dialog');
    expect(within(dialog).getByText('What changed, SO381895 (Line 1)')).toBeInTheDocument();
    expect(within(dialog).getByText('Buy 25 (was 10)')).toBeInTheDocument();
  });

  /**
   * AC-C9's list half: `pcr-381895-1` (kind `advanced`) moved BOTH qty (10 -> 25) and date
   * (25 Aug -> 19 Aug), so the icon must sit in BOTH the Required date and Outstanding
   * columns of the row it lands on - never a single icon that leaves one column silent
   * about what moved.
   */
  it('in the List view, shows the icon in both the Required date and Outstanding columns for a line where both moved', async () => {
    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    await screen.findByText('SO381895');

    const icons = screen.getAllByTestId('board-change-icon-pcr-381895-1');
    const columns = icons.map((icon) => icon.getAttribute('data-column')).sort();
    expect(columns).toEqual(['outstanding', 'required_date']);
  });
});

/**
 * S7 (AC-C5), through the FULL panel this time - `BoardChangeTable.suggestion.test.tsx`'s
 * own S7 test calls `annotationOf` directly and never exercises `annotationsByCell`'s real
 * cell-keying. Tester two saw SO400884's product-changed line render with NO icon at all in
 * the live walk: the board's live line carries the NEW product code (B2155-NL-WHITE, what
 * the SO now says) while the row (`pcr-s7`, reused verbatim from `MOCK_PLANNING_CHANGE_
 * BATCH_PENDING`) carries the SAME new code as `item_code` and the OLD one only on
 * `from.item_code` (`outstanding_diff.py`'s `_change_for_pair`: `Change.item_code` is always
 * the AFTER side) - the fixture the captain asked for, run through the real pipeline rather
 * than asserted as an isolated annotation.
 */
describe('the product-changed row, through the full panel (S7)', () => {
  const ROW_S7 = MOCK_PLANNING_CHANGE_BATCH_PENDING.orders
    .find((order) => order.so_number === 'SO400875')!
    .rows.find((row) => row.id === 'pcr-s7')!;

  function renderProductChangedPanel() {
    getPlanningChangeBatch.mockResolvedValue({
      ...MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE,
      id: 'pcb-so400875',
      orders: [
        {
          ...MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE.orders[0],
          project_sales_order_id: 'pso-400875',
          so_number: 'SO400875',
          core_sales_order_id: 'so-400875',
          rows: [ROW_S7],
        },
      ],
    });
    getPlanningBoard.mockResolvedValue(
      buildBoard(
        [
          demand({
            sales_order_id: 'so-400875',
            so_number: 'SO400875',
            line_no: 2,
            // The board's live line: the product the SO says TODAY - the NEW one, the same
            // code `pcr-s7.item_code` carries (never the one on `from.item_code`).
            item_code: 'B2155-NL-WHITE',
            qty: '134',
            project_line_id: 'pl-400875-2',
          }),
        ],
        { today: TODAY, freeStock: {}, granularity: 'week' },
      ),
    );
    return renderPanel('pcb-so400875', ['SO400875']);
  }

  it('shows the icon on the product row in the grid', async () => {
    renderProductChangedPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    expect(await screen.findByTestId('board-change-icon-pcr-s7')).toBeInTheDocument();
  });

  it('shows the icon in the List view, on the Outstanding or Suggested column', async () => {
    renderProductChangedPanel();
    await screen.findByTestId('fulfilment-board-matrix');
    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    await screen.findByText('SO400875');

    const icons = screen.getAllByTestId('board-change-icon-pcr-s7');
    expect(icons.length).toBeGreaterThan(0);
    const columns = icons.map((icon) => icon.getAttribute('data-column'));
    expect(columns.some((column) => column === 'outstanding' || column === 'suggested')).toBe(
      true,
    );
  });

  /**
   * Measured, not assumed: with `project_line_id` set (as the fixture above does), the List
   * view already finds the icon - `annotationsByLine` keys directly off it. The genuine red
   * is the row this order's OWN header already states is possible: `SO400875` in the shared
   * mock reads `is_adopted: false`, meaning a project_line_id is exactly what a row on an
   * unadopted order does NOT reliably carry. `annotationsByCell` (the grid) has a FALLBACK
   * for this - the FIRST cell of the same (so_number, item_code) pair - but `annotationsByLine`
   * (`_shared/lib/boardChangeAnnotations.ts` ~348-364) has none: `if (!lineId) continue;`
   * drops the row on the floor. This is the shape closest to tester two's "no icon at all" -
   * the List view is silent on a changed line the grid still manages to show.
   */
  it('still shows the icon in the List view when the row carries no project_line_id (an unadopted order)', async () => {
    getPlanningChangeBatch.mockResolvedValue({
      ...MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE,
      id: 'pcb-so400875',
      orders: [
        {
          ...MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE.orders[0],
          project_sales_order_id: 'pso-400875',
          so_number: 'SO400875',
          core_sales_order_id: 'so-400875',
          rows: [{ ...ROW_S7, project_line_id: null }],
        },
      ],
    });
    getPlanningBoard.mockResolvedValue(
      buildBoard(
        [
          demand({
            sales_order_id: 'so-400875',
            so_number: 'SO400875',
            line_no: 2,
            item_code: 'B2155-NL-WHITE',
            qty: '134',
          }),
        ],
        { today: TODAY, freeStock: {}, granularity: 'week' },
      ),
    );
    renderPanel('pcb-so400875', ['SO400875']);
    await screen.findByTestId('fulfilment-board-matrix');
    // The grid still finds it (its own cellByOrderItem fallback), so this is not a data gap.
    expect(await screen.findByTestId('board-change-icon-pcr-s7')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    await screen.findByText('SO400875');

    expect(screen.queryByTestId('board-change-icon-pcr-s7')).toBeInTheDocument();
  });

  it('reads "Product changed, was <old item code>" then the sourcing lines in the dialog', async () => {
    renderProductChangedPanel();
    const icon = await screen.findByTestId('board-change-icon-pcr-s7');
    fireEvent.click(icon);

    const dialog = await screen.findByTestId('board-change-dialog');
    expect(within(dialog).getByText(/Product changed, was B2155-NL-BLUE/)).toBeInTheDocument();
    expect(
      within(dialog).getByText('Release 134 B2155-NL-BLUE, free at BRW-IB'),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText('Buy 134 B2155-NL-WHITE for 4 Sep'),
    ).toBeInTheDocument();
  });
});

/**
 * R13/D1/D5 retired the per-order commit rail this describe block was written against: no
 * `commit-row-*` card, no `commit-blocked` per order, no "Confirm this order" button. The
 * board's ONE Confirm posts through `confirmMany`, and AC-P3-4 still holds: when the board was
 * opened on a planning-change batch, the body names it (`batch_id`) so the apply and the
 * confirmation stay one atomic write on the server.
 */
describe('the pre-marked decision, and Confirm', () => {
  it('arrives with the changed line already decided', async () => {
    renderPanel();
    await screen.findByTestId('board-change-icon-pcr-381895-1');
    fireEvent.click(
      await screen.findByRole('button', {
        name: /SRTWCX7405-RL-S-PJ, .* across 1 sales order/,
      }),
    );

    // The cell dialog opens on its Stock tab, so the pill lives one press away. Radix's
    // TabsTrigger switches on MOUSE DOWN; a bare `click` leaves the old panel up.
    const linesTab = await screen.findByRole('tab', {
      name: /^Contributing lines/,
    });
    fireEvent.mouseDown(linesTab);
    fireEvent.click(linesTab);

    // "Change proposed" (PLAN-board-change-proposed-pill, owner ruling 18 Sep 2026), not
    // "Saved": nothing has actually been written yet, only the board's own pre-mark. A real
    // Save on this line, or a Confirm, would read "Saved"/"Confirmed" as before.
    await waitFor(() => {
      expect(
        screen.getByTestId(
          'decision-pill-so-381895|1|SRTWCX7405-RL-S-PJ|2026-08-17',
        ),
      ).toHaveTextContent('Change proposed');
    });
  });

  /**
   * REVERSED by the S5 ruling (owner, 25 Sep 2026, issue #1245, `PLAN-esb-change-row-
   * refresh.md`): this test used to press Confirm with NOTHING saved and assert the
   * pre-marked line rode along anyway - exactly the SO419122 defect ("Confirm (119)" with
   * nothing ticked handed 49 rows to purchasing). Confirm now carries a line ONLY once a
   * person has actually saved it (a row Approve, "Save all suggested", or an Amend) - see
   * the `describe` block below for the pre-mark-alone case (AC-16) and the mixed
   * saved/unsaved case (AC-18). Kept here, renamed, as the still-true other half: ONCE
   * saved, a pre-marked line posts through `confirmMany` exactly like any other, with no
   * `preMarked` flag riding along in the write.
   */
  it('posts through confirmMany once Confirm is pressed, carrying a pre-marked line the planner actually saved first', async () => {
    confirmMany.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-381895',
          ok: true,
          decision_revision: 3,
          inquiry_rows_created: 1,
        },
      ],
    });
    renderPanel();
    await screen.findByTestId('board-change-icon-pcr-381895-1');
    // Nothing saved yet - the S5 ruling's own premise.
    await waitFor(() =>
      expect(screen.getByTestId('board-confirm')).toHaveTextContent(
        'Confirm (0)',
      ),
    );

    fireEvent.click(
      await screen.findByRole('button', {
        name: /SRTWCX7405-RL-S-PJ, .* across 1 sales order/,
      }),
    );
    const linesTab = await screen.findByRole('tab', {
      name: /^Contributing lines/,
    });
    fireEvent.mouseDown(linesTab);
    fireEvent.click(linesTab);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Save SO381895 line 1 as suggested' }),
    );
    await waitFor(() => {
      expect(
        screen.getByTestId('decision-pill-so-381895|1|SRTWCX7405-RL-S-PJ|2026-08-17'),
      ).toHaveTextContent('Saved');
    });
    await waitFor(() =>
      expect(screen.getByTestId('board-confirm')).toHaveTextContent(
        'Confirm (1)',
      ),
    );

    await pressPreviewThenConfirm();

    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    const [body] = confirmMany.mock.calls[0];
    expect(body.orders).toHaveLength(1);
    // The board fixture's own `pso-${sales_order_id}` (`ordersFor`), not the mock batch's
    // (unrelated) `pso-381895` - the confirm body is addressed off the BOARD, never the batch.
    expect(body.orders[0].pso_id).toBe('pso-so-381895');
    expect(body.orders[0].lines).toHaveLength(1);
    // AC-P3-4: the batch the board was opened on rides on the confirm body.
    expect(body.batch_id).toBe('pcb-so381895');
    // The pre-mark flag is SESSION-ONLY (PLAN-board-change-proposed-pill): it tells the pill
    // and the Verdict column this entry has nothing saved behind it yet, and it must never
    // ride along in the write that saves it.
    expect(body.orders[0].lines[0]).not.toHaveProperty('preMarked');
  });

  /**
   * One press confirms every plannable order on the board now (R11) - there is no per-order
   * card left to block selectively. So a batch NOT yet applied blocks nothing, even though one
   * order's own rows already read `applied_state: 'applied'`: the board-wide block reads only
   * `changeBatch.data.applied_at`.
   *
   * Amended for the S5 ruling (owner, 25 Sep 2026, issue #1245): Confirm is disabled whenever
   * nothing has been SAVED, whatever any row's `applied_state` says - so a plain pre-mark, with
   * nothing saved, is no longer evidence either way about the row-level-`applied_state` block
   * this test is actually about. The line is saved first, through its own row Save icon (not
   * the board-wide "Save all suggested" button - that one has its own separate fix, S5's
   * `canQuickSave`, pinned by AC-17 above and irrelevant to what THIS test checks), so the
   * only thing left that COULD still disable Confirm is the block this test pins.
   */
  it('does not block Confirm while the batch itself has not been applied, even if a row says it was', async () => {
    getPlanningChangeBatch.mockResolvedValue({
      ...MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE,
      applied_at: null,
      applied_by_name: null,
      orders: MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE.orders.map((order) => ({
        ...order,
        rows: order.rows.map((row) => ({
          ...row,
          applied_state: 'applied' as const,
        })),
      })),
    });
    renderPanel();
    await screen.findByTestId('board-change-icon-pcr-381895-1');

    fireEvent.click(
      await screen.findByRole('button', {
        name: /SRTWCX7405-RL-S-PJ, .* across 1 sales order/,
      }),
    );
    const linesTab = await screen.findByRole('tab', {
      name: /^Contributing lines/,
    });
    fireEvent.mouseDown(linesTab);
    fireEvent.click(linesTab);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Save SO381895 line 1 as suggested' }),
    );

    await waitFor(() =>
      expect(screen.getByTestId('board-confirm')).toHaveTextContent('Confirm (1)'),
    );
    expect(screen.queryByTestId('confirm-blocked')).not.toBeInTheDocument();
    expect(screen.getByTestId('board-confirm')).toBeEnabled();
  });

  it('refuses Confirm once the batch itself was applied, and says when and by whom', async () => {
    getPlanningChangeBatch.mockResolvedValue({
      ...MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE,
      applied_at: '2026-08-19T10:00:00Z',
      applied_by_name: 'Cyndi Tee',
    });
    renderPanel();
    await screen.findByTestId('board-change-icon-pcr-381895-1');

    const blocked = await screen.findByTestId('confirm-blocked');
    expect(blocked).toHaveTextContent('This planning change was applied');
    expect(blocked).toHaveTextContent('Cyndi Tee');
    expect(screen.getByTestId('board-confirm')).toBeDisabled();
  });
});

/**
 * S5 (`PLAN-esb-change-row-refresh.md`, owner ruling 25 Sep 2026, issue #1245): "we should
 * mark it as Change proposed, but it is not considered Saved". SO419122 read "Confirm (119)"
 * with nothing ticked, and one press handed 49 rows to purchasing - the pre-mark effect still
 * seeds `{ verdict: 'approved', preMarked: true }` (the pill, the icons and the per-row
 * proposal keep working), but `confirmSummary` / `runConfirmAll` must now skip every draft
 * that is STILL just a pre-mark: only a verdict a person actually saved (a row Approve, an
 * Amend, a quick save, or "Save all suggested") counts toward Confirm and rides in its body.
 * "Save all suggested" itself counts and saves the pre-marked lines too - `decide()` already
 * overwrites the pre-mark object on save, so the flag drops itself. Reject is unchanged: a
 * planner rejecting a pre-marked line has made a real decision, not left it alone.
 *
 * Supersedes the Confirm half of `PLAN-board-change-proposed-pill.md`'s 18 Sep 2026 ruling
 * (AC-C7 of that plan - "Confirm is this pre-marked path").
 */
describe('S5: Change proposed is not Saved (owner ruling 25 Sep 2026, issue #1245)', () => {
  it('AC-16: nothing saved reads 0 to confirm, Confirm stays disabled, and the pre-marked pill still reads Change proposed', async () => {
    renderPanel();
    await screen.findByTestId('board-change-icon-pcr-381895-1');

    await waitFor(() =>
      expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
        '0 to confirm',
      ),
    );
    expect(screen.getByTestId('board-confirm')).toHaveTextContent('Confirm (0)');
    expect(screen.getByTestId('board-confirm')).toBeDisabled();

    fireEvent.click(
      await screen.findByRole('button', {
        name: /SRTWCX7405-RL-S-PJ, .* across 1 sales order/,
      }),
    );
    const linesTab = await screen.findByRole('tab', {
      name: /^Contributing lines/,
    });
    fireEvent.mouseDown(linesTab);
    fireEvent.click(linesTab);
    await waitFor(() => {
      expect(
        screen.getByTestId('decision-pill-so-381895|1|SRTWCX7405-RL-S-PJ|2026-08-17'),
      ).toHaveTextContent('Change proposed');
    });
  });

  it('AC-17: "Save all suggested" counts the pre-marked line, and pressing it saves it - pill reads Saved, and it now counts toward Confirm', async () => {
    renderPanel();
    await screen.findByTestId('board-change-icon-pcr-381895-1');

    const saveAll = await screen.findByRole('button', {
      name: /^Save all suggested/,
    });
    expect(saveAll).toHaveTextContent('Save all suggested (1)');

    fireEvent.click(saveAll);

    await waitFor(() =>
      expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
        '1 to confirm',
      ),
    );
    expect(screen.getByTestId('board-confirm')).toHaveTextContent('Confirm (1)');

    fireEvent.click(
      await screen.findByRole('button', {
        name: /SRTWCX7405-RL-S-PJ, .* across 1 sales order/,
      }),
    );
    const linesTab = await screen.findByRole('tab', {
      name: /^Contributing lines/,
    });
    fireEvent.mouseDown(linesTab);
    fireEvent.click(linesTab);
    await waitFor(() => {
      expect(
        screen.getByTestId('decision-pill-so-381895|1|SRTWCX7405-RL-S-PJ|2026-08-17'),
      ).toHaveTextContent('Saved');
    });
  });

  it('AC-18: Confirm posts only the line the planner actually saved, leaving the untouched pre-marked one out', async () => {
    // TWO pre-marked lines on the SAME cell (same item, same bucket, different line_no) - so
    // one dialog opens both, and only ONE of them ever gets its own Save pressed below.
    getPlanningBoard.mockResolvedValue(
      buildBoard(
        [
          demand(),
          demand({ project_line_id: 'pl-381895-9', line_no: 9 }),
        ],
        { today: TODAY, freeStock: {}, granularity: 'week' },
      ),
    );
    const row1 = MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE.orders[0].rows[0];
    const row9 = { ...row1, id: 'pcr-381895-9', project_line_id: 'pl-381895-9', line_no: 9 };
    getPlanningChangeBatch.mockResolvedValue({
      ...MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE,
      orders: [
        {
          ...MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE.orders[0],
          rows: [row1, row9],
        },
      ],
    });
    confirmMany.mockResolvedValue({
      results: [
        { pso_id: 'pso-381895', ok: true, decision_revision: 3, inquiry_rows_created: 1 },
      ],
    });

    renderPanel();
    await screen.findByTestId('board-change-icon-pcr-381895-1');
    await waitFor(() =>
      expect(screen.getByTestId('board-confirm')).toHaveTextContent('Confirm (0)'),
    );

    fireEvent.click(
      await screen.findByRole('button', {
        name: /SRTWCX7405-RL-S-PJ, .* across 1 sales order/,
      }),
    );
    const linesTab = await screen.findByRole('tab', {
      name: /^Contributing lines/,
    });
    fireEvent.mouseDown(linesTab);
    fireEvent.click(linesTab);

    // Save ONLY line 1's pre-mark - line 9 is left exactly as the board pre-marked it.
    fireEvent.click(
      await screen.findByRole('button', { name: 'Save SO381895 line 1 as suggested' }),
    );
    await waitFor(() => {
      expect(
        screen.getByTestId('decision-pill-so-381895|1|SRTWCX7405-RL-S-PJ|2026-08-17'),
      ).toHaveTextContent('Saved');
    });
    expect(
      screen.getByTestId('decision-pill-so-381895|9|SRTWCX7405-RL-S-PJ|2026-08-17'),
    ).toHaveTextContent('Change proposed');

    await pressPreviewThenConfirm();

    await waitFor(() => expect(confirmMany).toHaveBeenCalledTimes(1));
    const [body] = confirmMany.mock.calls[0];
    expect(body.orders[0].lines).toHaveLength(1);
    expect(body.orders[0].lines[0].project_line_id).toBe('pl-381895-1');
  });

  it('AC-19: rejecting a pre-marked line still counts it as rejected and sends it as such', async () => {
    renderPanel();
    await screen.findByTestId('board-change-icon-pcr-381895-1');

    fireEvent.click(
      await screen.findByRole('button', {
        name: /SRTWCX7405-RL-S-PJ, .* across 1 sales order/,
      }),
    );
    const linesTab = await screen.findByRole('tab', {
      name: /^Contributing lines/,
    });
    fireEvent.mouseDown(linesTab);
    fireEvent.click(linesTab);

    fireEvent.click(
      await screen.findByRole('button', { name: 'Reject SO381895 line 1' }),
    );
    const reasonBox = await screen.findByPlaceholderText('In your own words');
    fireEvent.change(reasonBox, { target: { value: 'Wrong site' } });
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));

    await waitFor(() => {
      expect(
        screen.getByTestId('decision-pill-so-381895|1|SRTWCX7405-RL-S-PJ|2026-08-17'),
      ).toHaveTextContent('Rejected');
    });
    await waitFor(() =>
      expect(screen.getByTestId('board-confirm-summary')).toHaveTextContent(
        '1 rejected',
      ),
    );
  });
});

/**
 * R3 (`PLAN-board-draft-on-confirmed-line.md`, review round 3, captain ruling): the client's
 * own uncover rule has to match the server's (AC-B10/AC-B11) - a batch keeps its own lines
 * covered once it has been applied, whatever those lines' rows say, exactly the way an applied
 * batch already refuses a second Confirm above. TEST-FIRST: `uncoverChangedLines` (fed by
 * `changeBatchData`, built off `bySoNumber` in `FulfilmentBoardPanel.tsx`) runs off every
 * loaded batch's rows with no `applied_at` gate at all, so this line is uncovered today
 * whichever way the batch itself has been applied.
 */
describe('a covered line stays covered once the batch has been applied (R3, review round 3)', () => {
  it('AC-F6: the pill still reads Confirmed, and Save stays disabled once Amend is pressed', async () => {
    getPlanningBoard.mockResolvedValue(
      buildBoard(
        [
          demand({
            decision: {
              revision_no: 1,
              confirmed_at: '2026-08-18T02:00:00',
              timely_spo_qty: '0',
              reserve: [{ warehouse_id: 'wh-BRW-IB', location: 'BRW-IB', qty: '25' }],
              borrow: [],
              buy_qty: '0',
            },
          }),
        ],
        { today: TODAY, freeStock: {}, granularity: 'week' },
      ),
    );
    getPlanningChangeBatch.mockResolvedValue({
      ...MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE,
      applied_at: '2026-08-19T10:00:00Z',
      applied_by_name: 'Cyndi Tee',
    });

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    fireEvent.click(
      await screen.findByRole('button', {
        name: /SRTWCX7405-RL-S-PJ, .* across 1 sales order/,
      }),
    );
    const linesTab = await screen.findByRole('tab', {
      name: /^Contributing lines/,
    });
    fireEvent.mouseDown(linesTab);
    fireEvent.click(linesTab);

    const key = 'so-381895|1|SRTWCX7405-RL-S-PJ|2026-08-17';
    await waitFor(() => {
      expect(screen.getByTestId(`decision-pill-${key}`)).toHaveTextContent('Confirmed');
    });

    fireEvent.click(screen.getByText('SO381895'));
    fireEvent.click(await screen.findByRole('button', { name: 'Amend' }));

    expect(screen.getByRole('button', { name: 'Save decision' })).toBeDisabled();
  });

  /**
   * AC-F7: the same case as AC-F6, but the batch resolves AFTER the board's own contributions
   * are already on screen - `usePlanningChangeBatchesByIds` is its own query, so a real page
   * almost always renders the board before the batch (a second, slower fetch) lands. The
   * pre-mark effect must not seed a `Saved` verdict for a line that is COVERED once the batch
   * finally arrives, whatever order the two reads settle in.
   */
  it('AC-F7: the pill still reads Confirmed when the batch resolves AFTER the board contributions are already on screen', async () => {
    getPlanningBoard.mockResolvedValue(
      buildBoard(
        [
          demand({
            decision: {
              revision_no: 1,
              confirmed_at: '2026-08-18T02:00:00',
              timely_spo_qty: '0',
              reserve: [{ warehouse_id: 'wh-BRW-IB', location: 'BRW-IB', qty: '25' }],
              borrow: [],
              buy_qty: '0',
            },
          }),
        ],
        { today: TODAY, freeStock: {}, granularity: 'week' },
      ),
    );
    let resolveBatch: (
      value: typeof MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE,
    ) => void = () => {};
    getPlanningChangeBatch.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveBatch = resolve;
        }),
    );

    renderPanel();
    await screen.findByTestId('fulfilment-board-matrix');

    // The board is on screen with no batch loaded yet; only now does the batch land.
    resolveBatch({
      ...MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE,
      applied_at: '2026-08-19T10:00:00Z',
      applied_by_name: 'Cyndi Tee',
    });

    fireEvent.click(
      await screen.findByRole('button', {
        name: /SRTWCX7405-RL-S-PJ, .* across 1 sales order/,
      }),
    );
    const linesTab = await screen.findByRole('tab', {
      name: /^Contributing lines/,
    });
    fireEvent.mouseDown(linesTab);
    fireEvent.click(linesTab);

    const key = 'so-381895|1|SRTWCX7405-RL-S-PJ|2026-08-17';
    await waitFor(() => {
      expect(screen.getByTestId(`decision-pill-${key}`)).toHaveTextContent('Confirmed');
    });
  });
});
