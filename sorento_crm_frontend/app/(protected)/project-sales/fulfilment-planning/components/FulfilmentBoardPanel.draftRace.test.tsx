/**
 * A saved server draft always wins over the board's own batch pre-mark (hotfix, SO422492,
 * lane HOTFIX-BOARD-DRAFT-RACE).
 *
 * Opened from the Sales Order page's Plan button, the planning-change batch is often already in
 * the react-query cache, so it is loaded on the SAME render the board's contributions arrive. The
 * pre-mark effect then ran before the server-draft seed and filled every key the batch names
 * with `{ verdict: 'approved', preMarked: true }`, and the seed's `!next[key]` guard skipped the
 * saved draft behind it: Saved and Confirm dropped those lines, and "Save all suggested" offered
 * to overwrite them with the engine's suggestion. A cold refresh (batch fetched after the board)
 * showed the right numbers, which is the second spec here.
 *
 * Scaffolding mirrors `FulfilmentBoardPanel.autobatch.test.tsx`.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
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
  getPlanningChangeBatch: (...args: unknown[]) => getPlanningChangeBatch(...args),
  updatePlanningChangeRow: vi.fn(),
  applyPlanningChanges: vi.fn(),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { user: { id: 'user-1', name: 'Test Planner' } },
    status: 'authenticated',
  }),
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

import { FulfilmentBoardPanel } from './FulfilmentBoardPanel';
import {
  buildBoard,
  type BoardDemandLine,
} from '../../_shared/lib/__testsupport__/boardFixture';
import { MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE } from '../../_shared/__mocks__/planningChanges';
import { PLANNING_CHANGE_BATCH_KEY } from '../../_shared/hooks/usePlanningChanges';

const TODAY = '2026-08-18';
const BATCH_A = MOCK_PLANNING_CHANGE_BATCH_SO_CHANGE; // SO381895, names pl-381895-1

/** The line BATCH_A names, so the pre-mark effect targets it. */
function demandNamed(): BoardDemandLine {
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
  } as BoardDemandLine;
}

/** A second line on the same order that BATCH_A does not name. */
function demandUnnamed(): BoardDemandLine {
  return {
    sales_order_id: 'so-381895',
    so_number: 'SO381895',
    customer_name: 'YOTU BUILDER',
    project_sales_order_id: 'pso-381895',
    project_line_id: 'pl-381895-2',
    line_no: 2,
    item_code: 'SRTWCX-OTHER-ITEM',
    qty: '10',
    required_date: '2026-08-25',
    fulfilment_location: 'BRW-IB',
    priority: null,
  } as BoardDemandLine;
}

/**
 * Both lines carry a SERVER-PERSISTED draft (the shape `so_supply_decision_drafts` serves), on
 * `cells[].contributions` and the top-level `contributions` alike, and the order names BATCH_A
 * as its pending planning change.
 */
function boardWithSavedDrafts() {
  const board = buildBoard([demandNamed(), demandUnnamed()], {
    today: TODAY,
    freeStock: {},
    granularity: 'week',
  });
  const saved = {
    decision: { verdict: 'approved' as const },
    saved_by: 'Test Planner',
    saved_at: '2026-09-08T00:00:00Z',
  };
  const apply = <T extends object>(entry: T): T => ({ ...entry, draft: saved });
  return {
    ...board,
    orders: board.orders.map((order) => ({ ...order, pending_change_batch_id: BATCH_A.id })),
    cells: board.cells.map((cell) => ({
      ...cell,
      contributions: cell.contributions.map(apply),
    })),
    contributions: board.contributions.map(apply),
  } as ReturnType<typeof buildBoard>;
}

function renderPanel(client: QueryClient) {
  return render(
    <QueryClientProvider client={client}>
      <FulfilmentBoardPanel soNumbers={['SO381895']} batchId={null} onBack={vi.fn()} />
    </QueryClientProvider>,
  );
}

async function expectSavedDraftsWin() {
  await screen.findByTestId('fulfilment-board-matrix');
  await waitFor(() =>
    expect(screen.getByTestId('board-confirm')).toHaveTextContent('Confirm (2)'),
  );
  expect(screen.getByTestId('board-scope-toggle')).toHaveTextContent('Saved (2)');
  expect(screen.getByRole('button', { name: /^Save all suggested/ })).toHaveTextContent(
    'Save all suggested (0)',
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getPlanningBoard.mockResolvedValue(boardWithSavedDrafts());
  getPlanningChangeBatch.mockResolvedValue(BATCH_A);
});

describe('a saved server draft always wins over the batch pre-mark', () => {
  it('warm cache (Plan button): the batch is cached before the board loads', async () => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: Infinity },
        mutations: { retry: false },
      },
    });
    client.setQueryData([PLANNING_CHANGE_BATCH_KEY, BATCH_A.id], BATCH_A);

    renderPanel(client);

    await expectSavedDraftsWin();
  });

  it('cold load (refresh): the batch arrives after the board', async () => {
    let releaseBatch: (batch: typeof BATCH_A) => void = () => {};
    getPlanningChangeBatch.mockImplementation(
      () => new Promise((resolve) => (releaseBatch = resolve)),
    );
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
        mutations: { retry: false },
      },
    });

    renderPanel(client);
    await screen.findByTestId('fulfilment-board-matrix');
    await waitFor(() => expect(getPlanningChangeBatch).toHaveBeenCalledWith(BATCH_A.id));
    releaseBatch(BATCH_A);

    await expectSavedDraftsWin();
  });
});
