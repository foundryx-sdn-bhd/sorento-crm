/**
 * Phase 2 red tests, `board-simulate-confirm-12oct` (AC-S9, AC-S10): the board's admin-only
 * "Simulate confirm" gear entry and the results it writes into the existing
 * `board-confirm-results` block.
 *
 * Scaffolding mirrors `FulfilmentBoardPanel.test.tsx` (same service mock, same fixtures); the
 * one new moving part is a switchable session so the admin gate (`isSuperadminUser`, reads
 * `roleName`) can be driven per test.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

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
const adoptSalesOrder = vi.fn();
const confirmMany = vi.fn();
const previewConfirmMany = vi.fn();
const simulateConfirm = vi.fn();
const putLineDraft = vi.fn().mockResolvedValue({
  decision: { verdict: 'approved' },
  saved_by: 'Test Planner',
  saved_at: '2026-09-03T00:00:00Z',
});
const deleteLineDraft = vi.fn().mockResolvedValue(undefined);

vi.mock('../../_shared/services/fulfilmentPlanningService', () => ({
  getPlanningBoard: (...args: unknown[]) => getPlanningBoard(...args),
  listFulfilmentPlanning: vi.fn(),
  getReconciliation: vi.fn(),
  rerunReconciliation: vi.fn(),
  adoptSalesOrder: (...args: unknown[]) => adoptSalesOrder(...args),
  getSupply: vi.fn(),
  confirmSupply: vi.fn(),
  confirmMany: (...args: unknown[]) => confirmMany(...args),
  previewConfirmMany: (...args: unknown[]) => previewConfirmMany(...args),
  simulateConfirm: (...args: unknown[]) => simulateConfirm(...args),
  putLineDraft: (...args: unknown[]) => putLineDraft(...args),
  deleteLineDraft: (...args: unknown[]) => deleteLineDraft(...args),
  ConfirmSupplyError: class ConfirmSupplyError extends Error {
    readonly failingLines: unknown[] = [];
  },
}));

vi.mock('../../_shared/services/planningChangeService', () => ({
  listPlanningChangeBatches: vi.fn(),
  getPlanningChangeBatch: vi.fn(),
  updatePlanningChangeRow: vi.fn(),
  applyPlanningChanges: vi.fn(),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), dismiss: vi.fn() },
}));

let sessionRole: string | null = 'Admin';
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { id: 'user-1', name: 'Test Planner', roleName: sessionRole } },
    status: 'authenticated',
  }),
}));

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => false,
}));
vi.mock('../../_shared/hooks/useBoardTransfers', () => ({
  BOARD_TRANSFERS_KEY: 'board-stock-transfers',
  useBoardTransfers: () => ({ data: { data: [] }, isLoading: false, error: undefined }),
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
import type { PlanningBoard } from '../../_shared/types/fulfilmentPlanning.types';

const TODAY = '2026-08-18';

function demand(overrides: Partial<BoardDemandLine> = {}): BoardDemandLine {
  return {
    sales_order_id: 'so-a',
    so_number: 'SO403340',
    customer_name: 'SETIA-WOOD INDUSTRIES SDN BHD (PROJECT)',
    project_label: 'SETIA-WOOD/100U DSTH',
    line_no: 1,
    item_code: 'WESERP10B',
    qty: '100',
    required_date: '2026-09-04',
    fulfilment_location: 'BRW-BB',
    priority: null,
    ...overrides,
  };
}

/** Every contribution carries a saved approved draft, so Confirm (and Simulate) post it. */
function allSaved(board: PlanningBoard): PlanningBoard {
  const apply = <T extends object>(entry: T): T => ({
    ...entry,
    draft: {
      decision: { verdict: 'approved' as const },
      saved_by: 'Test Planner',
      saved_at: '2026-09-08T00:00:00Z',
    },
  });
  return {
    ...board,
    cells: board.cells.map((cell) => ({
      ...cell,
      contributions: cell.contributions.map(apply),
    })),
    contributions: board.contributions.map(apply),
  };
}

function adoptedBoard(): PlanningBoard {
  return allSaved(
    buildBoard(
      [
        demand({ line_no: 1, item_code: 'WESERP10B' }),
        demand({ line_no: 2, item_code: 'TPE-9204' }),
      ],
      { today: TODAY, freeStock: {}, granularity: 'week' },
    ),
  );
}

/** so-a is adopted (two saved lines); so-b (SO000002) has no planning record at all. */
function mixedBoard(): PlanningBoard {
  const board = allSaved(
    buildBoard(
      [
        demand({ line_no: 1, item_code: 'WESERP10B' }),
        demand({
          sales_order_id: 'so-b',
          so_number: 'SO000002',
          line_no: 1,
          item_code: 'ZZT-ITEM-2',
        }),
      ],
      { today: TODAY, freeStock: {}, granularity: 'week' },
    ),
  );
  const unplan = <T extends { sales_order_id: string; project_line_id?: string | null }>(
    entry: T,
  ): T => (entry.sales_order_id === 'so-b' ? { ...entry, project_line_id: null } : entry);
  return {
    ...board,
    orders: board.orders.map((order) =>
      order.sales_order_id === 'so-b' ? { ...order, project_sales_order_id: null } : order,
    ),
    cells: board.cells.map((cell) => ({
      ...cell,
      contributions: cell.contributions.map(unplan),
    })),
    contributions: board.contributions.map(unplan),
  };
}

function renderPanel(soNumbers = ['SO403340']) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FulfilmentBoardPanel soNumbers={soNumbers} batchId={null} onBack={vi.fn()} />
    </QueryClientProvider>,
  );
}

async function openGear() {
  const trigger = await screen.findByRole('button', { name: 'Board actions' });
  fireEvent.keyDown(trigger, { key: 'Enter' });
  await screen.findByRole('menuitem', { name: 'Undo all' });
  return screen.getByRole('menu');
}

async function pressSimulate() {
  await waitFor(() => expect(screen.getByTestId('board-confirm')).toBeEnabled());
  const menu = await openGear();
  fireEvent.click(within(menu).getByRole('menuitem', { name: 'Simulate confirm' }));
}

const SIM_ERROR = 'TypeError: Object of type date is not JSON serializable';

function okSimulated(overrides: Record<string, unknown> = {}) {
  return {
    pso_id: 'pso-so-a',
    ok: true,
    preview: true,
    decision_revision: 3,
    lines_confirmed: [
      { project_line_id: 'pl-so-a-1', line_no: 1, item_code: 'WESERP10B' },
      { project_line_id: 'pl-so-a-2', line_no: 2, item_code: 'TPE-9204' },
    ],
    lines_carried: 0,
    lines_held_back: [],
    lines_fulfilled_skipped: 0,
    inquiry_rows: [
      {
        line_no: 10,
        item_code: 'ITEM',
        verb: 'ORDER',
        qty: '50',
        stock_location: 'LOC',
        is_new: true,
      },
      {
        line_no: 11,
        item_code: 'OLDITEM',
        verb: 'ORDER',
        qty: '7',
        stock_location: 'LOC2',
        is_new: false,
      },
    ],
    transfers: [{ line_no: 10, kind: 'borrow', qty: '100', from_location: 'FROM', to_location: 'TO' }],
    simulated_errors: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionRole = 'Admin';
  getPlanningBoard.mockResolvedValue(adoptedBoard());
});

describe('AC-S9: the gear entry is admin-only', () => {
  it.each(['Admin', 'Super Admin', 'superadmin'])(
    'shows "Simulate confirm" for a %s session',
    async (role) => {
      sessionRole = role;
      renderPanel();
      const menu = await openGear();
      expect(within(menu).getByRole('menuitem', { name: 'Simulate confirm' })).toBeInTheDocument();
    },
  );

  it('does not show "Simulate confirm" for a Planner session', async () => {
    sessionRole = 'Planner';
    renderPanel();
    const menu = await openGear();
    // Guard against passing for the wrong reason: the menu itself did open.
    expect(within(menu).getByRole('menuitem', { name: 'Undo all' })).toBeInTheDocument();
    expect(within(menu).queryByRole('menuitem', { name: 'Simulate confirm' })).toBeNull();
  });
});

describe('AC-S9: selecting it only simulates', () => {
  it('calls simulateConfirm once with the Confirm body, and never adopt, confirmMany or previewConfirmMany', async () => {
    simulateConfirm.mockResolvedValue({ results: [okSimulated()] });
    renderPanel();

    await pressSimulate();

    await waitFor(() => expect(simulateConfirm).toHaveBeenCalledTimes(1));
    const [body] = simulateConfirm.mock.calls[0];
    expect(body.orders).toHaveLength(1);
    expect(body.orders[0].pso_id).toBe('pso-so-a');
    expect(
      body.orders[0].lines
        .map((line: { project_line_id: string }) => line.project_line_id)
        .sort(),
    ).toEqual(['pl-so-a-1', 'pl-so-a-2']);
    // The same body Confirm builds is not a preview request: the server ignores the flag.
    expect(body.preview).toBeFalsy();
    expect(adoptSalesOrder).not.toHaveBeenCalled();
    expect(confirmMany).not.toHaveBeenCalled();
    expect(previewConfirmMany).not.toHaveBeenCalled();
  });

  it('F1: simulates an un-adopted order by core id with placeholder lines, never calling adopt', async () => {
    getPlanningBoard.mockResolvedValue(mixedBoard());
    simulateConfirm.mockResolvedValue({
      results: [
        okSimulated(),
        okSimulated({
          pso_id: 'pso-so-b-rolled-back',
          adopted: true,
          decision_revision: 1,
          lines_confirmed: [
            { project_line_id: 'pl-b-1', line_no: 1, item_code: 'ZZT-ITEM-2' },
          ],
        }),
      ],
    });
    renderPanel(['SO403340', 'SO000002']);

    await pressSimulate();

    await waitFor(() => expect(simulateConfirm).toHaveBeenCalledTimes(1));
    expect(adoptSalesOrder).not.toHaveBeenCalled();
    const [body] = simulateConfirm.mock.calls[0];
    const adoptedEntry = body.orders.find(
      (order: { sales_order_id?: string }) => order.sales_order_id === 'so-b',
    );
    expect(adoptedEntry).toBeDefined();
    expect(adoptedEntry.pso_id).toBeUndefined();
    expect(
      adoptedEntry.lines.map((line: { project_line_id: string }) => line.project_line_id),
    ).toEqual(['pending-adoption:core-so-b-1']);

    const results = await screen.findByTestId('board-confirm-results');
    expect(within(results).queryByText(/was not simulated/)).toBeNull();
    expect(within(results).queryByText(/is not being planned yet/)).toBeNull();
    expect(within(results).getByText(/SO000002:? would confirm as revision 1/)).toBeInTheDocument();
  });
});

describe('AC-S10: the results block', () => {
  it('headlines the simulation and says each ok order would confirm as its revision', async () => {
    simulateConfirm.mockResolvedValue({ results: [okSimulated()] });
    renderPanel();

    await pressSimulate();

    const results = await screen.findByTestId('board-confirm-results');
    expect(
      within(results).getByText('Simulation, nothing was saved: 1 of 1 orders would confirm'),
    ).toBeInTheDocument();
    expect(within(results).getByText(/SO403340:? would confirm as revision 3/)).toBeInTheDocument();
    expect(within(results).queryByText(/orders confirmed/)).toBeNull();
  });

  it('lists the inquiry rows and transfers an ok order would write', async () => {
    simulateConfirm.mockResolvedValue({ results: [okSimulated()] });
    renderPanel();

    await pressSimulate();

    const writes = await screen.findByTestId('board-simulate-writes-pso-so-a');
    expect(within(writes).getByText('line 10 ITEM: ORDER 50 at LOC')).toBeInTheDocument();
    expect(
      within(writes).getByText('line 11 OLDITEM: ORDER 7 at LOC2 (settles an existing row)'),
    ).toBeInTheDocument();
    expect(within(writes).getByText('line 10: borrow 100 FROM -> TO')).toBeInTheDocument();
    expect(within(writes).getByText(/Would write/)).toBeInTheDocument();
  });

  it('names each simulated_errors line as "line N ITEM: <error>" inside the order\'s error list', async () => {
    simulateConfirm.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: false,
          preview: true,
          error: SIM_ERROR,
          simulated_errors: [
            { line_no: 8, item_code: 'SRTSH1040', project_line_id: 'pl-so-a-2', error: SIM_ERROR },
          ],
        },
      ],
    });
    renderPanel();

    await pressSimulate();

    const errors = await screen.findByTestId('board-simulate-errors-pso-so-a');
    expect(within(errors).getByText(`line 8 SRTSH1040: ${SIM_ERROR}`)).toBeInTheDocument();
    const results = screen.getByTestId('board-confirm-results');
    expect(
      within(results).getByText('Simulation, nothing was saved: 0 of 1 orders would confirm'),
    ).toBeInTheDocument();
  });

  it('leaves the board drafts and the Confirm count exactly as they were', async () => {
    simulateConfirm.mockResolvedValue({ results: [okSimulated()] });
    renderPanel();

    await waitFor(() => expect(screen.getByTestId('board-confirm')).toBeEnabled());
    const before = screen.getByTestId('board-confirm').textContent;
    expect(before).toMatch(/^Confirm \(\d+\)$/);

    await pressSimulate();
    await screen.findByTestId('board-confirm-results');

    expect(screen.getByTestId('board-confirm').textContent).toBe(before);
    expect(screen.getByTestId('board-confirm')).toBeEnabled();
    expect(deleteLineDraft).not.toHaveBeenCalled();
    expect(putLineDraft).not.toHaveBeenCalled();
  });
});

describe('fix round: simulated wording and number formatting (R7-R10)', () => {
  it('R7: Cancel SO wording is future tense in a simulation', async () => {
    simulateConfirm.mockResolvedValue({
      results: [
        okSimulated({
          lines_confirmed: [],
          inquiry_rows: [],
          transfers: [],
          lines_cancelled: [
            { project_line_id: 'pl-so-a-1', line_no: 3, item_code: 'WESERP10B' },
          ],
        }),
      ],
    });
    renderPanel();

    await pressSimulate();

    const results = await screen.findByTestId('board-confirm-results');
    expect(within(results).getByText(/would transfer 1 line to Cancel SO/)).toBeInTheDocument();
    expect(
      within(results).getByText('Would transfer to Cancel SO: line 3 WESERP10B'),
    ).toBeInTheDocument();
    expect(within(results).queryByText(/transferred/i)).toBeNull();
  });

  it('R8: a simulated error with no item code reads "line 8: <error>"', async () => {
    simulateConfirm.mockResolvedValue({
      results: [
        {
          pso_id: 'pso-so-a',
          ok: false,
          preview: true,
          error: SIM_ERROR,
          simulated_errors: [
            { line_no: 8, item_code: null, project_line_id: 'pl-so-a-2', error: SIM_ERROR },
          ],
        },
      ],
    });
    renderPanel();

    await pressSimulate();

    const errors = await screen.findByTestId('board-simulate-errors-pso-so-a');
    expect(within(errors).getByText(`line 8: ${SIM_ERROR}`)).toBeInTheDocument();
  });

  it('R9: quantities print without trailing zeros', async () => {
    simulateConfirm.mockResolvedValue({
      results: [
        okSimulated({
          inquiry_rows: [
            {
              line_no: 10,
              item_code: 'ITEM',
              verb: 'ORDER',
              qty: '12.5000',
              stock_location: 'LOC',
              is_new: true,
            },
          ],
          transfers: [
            { line_no: 10, kind: 'pool', qty: '50.0000', from_location: 'BRW', to_location: 'OWN' },
          ],
        }),
      ],
    });
    renderPanel();

    await pressSimulate();

    const writes = await screen.findByTestId('board-simulate-writes-pso-so-a');
    expect(within(writes).getByText('line 10 ITEM: ORDER 12.5 at LOC')).toBeInTheDocument();
    expect(within(writes).getByText('line 10: pool 50 BRW -> OWN')).toBeInTheDocument();
  });

  it('R10: confirmed and carried lines read in the conditional mood', async () => {
    simulateConfirm.mockResolvedValue({
      results: [okSimulated({ lines_carried: 2 })],
    });
    renderPanel();

    await pressSimulate();

    const lines = await screen.findByTestId('board-confirm-lines-pso-so-a');
    expect(
      within(lines).getByText('Would confirm: line 1 WESERP10B · line 2 TPE-9204'),
    ).toBeInTheDocument();
    expect(within(lines).getByText('2 would carry forward unchanged')).toBeInTheDocument();
    expect(within(lines).queryByText(/^Confirmed:/)).toBeNull();
    expect(within(lines).queryByText(/carried forward unchanged$/)).not.toBeNull;
  });

  it('shows a stock-short warning on a simulated ok order', async () => {
    simulateConfirm.mockResolvedValue({
      results: [
        okSimulated({
          stock_short_notices: [
            { line_no: 12, item_code: 'CB2807-DIY', reason: 'only 3 free at BRW, 2 short' },
          ],
        }),
      ],
    });
    renderPanel();

    await pressSimulate();

    const results = await screen.findByTestId('board-confirm-results');
    const notices = within(results).getByTestId('board-confirm-notices');
    expect(notices.textContent).toMatch(/12/);
    expect(notices.textContent).toContain('CB2807-DIY');
    expect(notices.textContent).toContain('only 3 free at BRW, 2 short');
  });

  it('a cancel-only simulated order has no "Would write" list', async () => {
    simulateConfirm.mockResolvedValue({
      results: [
        okSimulated({
          lines_confirmed: [],
          inquiry_rows: [],
          transfers: [],
          lines_cancelled: [{ project_line_id: 'pl-so-a-1', line_no: 10, item_code: 'ZZT-X' }],
        }),
      ],
    });
    renderPanel();

    await pressSimulate();

    const results = await screen.findByTestId('board-confirm-results');
    expect(
      within(results).getByText('Would transfer to Cancel SO: line 10 ZZT-X'),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('board-simulate-writes-pso-so-a')).toBeNull();
    expect(within(results).queryByText(/Would write/)).toBeNull();
  });

  it('already-fulfilled lines read "would be cleared" in a simulation', async () => {
    simulateConfirm.mockResolvedValue({
      results: [okSimulated({ lines_fulfilled_skipped: 2 })],
    });
    renderPanel();

    await pressSimulate();

    const results = await screen.findByTestId('board-confirm-results');
    expect(
      within(results).getByText('2 lines already fulfilled, decision would be cleared'),
    ).toBeInTheDocument();
  });

  it('S3: an ok order with nothing to confirm reads "has nothing to confirm"', async () => {
    simulateConfirm.mockResolvedValue({
      results: [
        okSimulated({
          decision_revision: 4,
          lines_confirmed: [],
          lines_withdrawn: [],
          lines_cancelled: [],
          lines_fulfilled_skipped: 0,
          inquiry_rows: [],
          transfers: [],
        }),
      ],
    });
    renderPanel();

    await pressSimulate();

    const results = await screen.findByTestId('board-confirm-results');
    expect(within(results).getByText(/SO403340:? has nothing to confirm/)).toBeInTheDocument();
    expect(within(results).queryByText(/would confirm as revision/)).toBeNull();
  });
});
