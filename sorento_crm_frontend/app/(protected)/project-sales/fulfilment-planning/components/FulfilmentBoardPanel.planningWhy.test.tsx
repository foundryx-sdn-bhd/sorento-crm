/**
 * Phase 2 red tests, PLANNING-WHY (AC-W12 gear entry, AC-W13 refusal table).
 *
 * Contract (documentation/plans/scm/planning-why-12oct-acceptance-criteria.md):
 * - AC-W12: an admin's board gear menu shows "History" (under "Simulate confirm") and it opens
 *   a right Sheet titled "History"; a non-admin sees no History item.
 * - AC-W13: an admin's refused order renders the `failing_lines` that carry `reason_code` as a
 *   table Line | Product | Change | reason Badge | action ("Go to line" for not_decided and
 *   line_cancelled, "Reload" for changed_again and already_confirmed) plus a "History" link;
 *   lines without `reason_code` render as today (`failingLineText`); a non-admin sees today's
 *   block.
 *
 * Scaffolding is `FulfilmentBoardPanel.simulate.test.tsx`'s (switchable session role).
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
const getPlanningHistory = vi.fn().mockResolvedValue({ events: [] });
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
  getPlanningHistory: (...args: unknown[]) => getPlanningHistory(...args),
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

function adoptedBoard(): PlanningBoard {
  const board = buildBoard(
    [demand({ line_no: 1, item_code: 'WESERP10B' }), demand({ line_no: 2, item_code: 'TPE-9204' })],
    { today: TODAY, freeStock: {}, granularity: 'week' },
  );
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
    cells: board.cells.map((cell) => ({ ...cell, contributions: cell.contributions.map(apply) })),
    contributions: board.contributions.map(apply),
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

const ERROR = 'Nothing on this planning change could be confirmed.';

function refusal(lines: Record<string, unknown>[]) {
  return {
    results: [{ pso_id: 'pso-so-a', ok: false, preview: true, error: ERROR, failing_lines: lines }],
  };
}

const NOT_DECIDED = {
  line_no: 592,
  so_line_no: 592,
  item_code: 'SRTWC8840-SC',
  reason_code: 'not_decided',
  reason: 'Not decided',
  change: 'Qty 25 -> 22',
};
const CHANGED_AGAIN = {
  line_no: 1056,
  so_line_no: 1056,
  item_code: 'SRTWC8318-RL-N',
  reason_code: 'changed_again',
  reason: 'Changed again',
  change: 'Date 09/11/2026 -> 16/11/2026',
};
const PLAIN = { line_no: 7, so_line_no: 7, item_code: 'TPE-9204', reason: 'only 3 free' };

async function pressConfirm() {
  await waitFor(() => expect(screen.getByTestId('board-confirm')).toBeEnabled());
  fireEvent.click(screen.getByTestId('board-confirm'));
  return screen.findByTestId('board-confirm-results');
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionRole = 'Admin';
  getPlanningBoard.mockResolvedValue(adoptedBoard());
  getPlanningHistory.mockResolvedValue({ events: [] });
});

describe('AC-W13: the admin refusal table', () => {
  it('renders each failing line that carries a reason_code as a table row', async () => {
    previewConfirmMany.mockResolvedValue(refusal([NOT_DECIDED, CHANGED_AGAIN]));
    renderPanel();

    const results = await pressConfirm();

    const table = within(results).getByRole('table');
    const headers = within(table).getAllByRole('columnheader').map((cell) => cell.textContent);
    expect(headers.slice(0, 4)).toEqual(['Line', 'Product', 'Change', 'Reason']);
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText('592')).toBeInTheDocument();
    expect(within(rows[0]).getByText('SRTWC8840-SC')).toBeInTheDocument();
    expect(within(rows[0]).getByText('Qty 25 -> 22')).toBeInTheDocument();
    expect(within(rows[0]).getByText('Not decided')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Changed again')).toBeInTheDocument();
  });

  it('offers Go to line for a not-decided line and Reload for a changed-again one', async () => {
    previewConfirmMany.mockResolvedValue(refusal([NOT_DECIDED, CHANGED_AGAIN]));
    renderPanel();

    const results = await pressConfirm();

    const rows = within(within(results).getByRole('table')).getAllByRole('row').slice(1);
    expect(within(rows[0]).getByRole('button', { name: 'Go to line' })).toBeInTheDocument();
    expect(within(rows[0]).queryByRole('button', { name: 'Reload' })).toBeNull();
    expect(within(rows[1]).getByRole('button', { name: 'Reload' })).toBeInTheDocument();
    expect(within(rows[1]).queryByRole('button', { name: 'Go to line' })).toBeNull();
  });

  it('Reload reads the board again', async () => {
    previewConfirmMany.mockResolvedValue(refusal([CHANGED_AGAIN]));
    renderPanel();
    const results = await pressConfirm();
    const before = getPlanningBoard.mock.calls.length;

    fireEvent.click(within(results).getByRole('button', { name: 'Reload' }));

    await waitFor(() => expect(getPlanningBoard.mock.calls.length).toBeGreaterThan(before));
  });

  it('a History link in the block opens the History sheet on that order', async () => {
    previewConfirmMany.mockResolvedValue(refusal([NOT_DECIDED]));
    renderPanel();
    const results = await pressConfirm();

    fireEvent.click(within(results).getByRole('button', { name: 'History' }));

    expect(await screen.findByRole('dialog', { name: 'History' })).toBeInTheDocument();
    await waitFor(() => expect(getPlanningHistory).toHaveBeenCalled());
    expect(getPlanningHistory.mock.calls[0][0]).toContain('pso-so-a');
  });

  it('a line without a reason_code still reads as today, beside the table', async () => {
    previewConfirmMany.mockResolvedValue(refusal([NOT_DECIDED, PLAIN]));
    renderPanel();

    const results = await pressConfirm();

    const rows = within(within(results).getByRole('table')).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(1);
    expect(within(results).getByText('Line 7, TPE-9204: only 3 free')).toBeInTheDocument();
  });

  it('a non-admin sees today\'s block and no table', async () => {
    sessionRole = 'Planner';
    previewConfirmMany.mockResolvedValue(refusal([PLAIN]));
    renderPanel();

    const results = await pressConfirm();

    expect(within(results).queryByRole('table')).toBeNull();
    expect(within(results).queryByRole('button', { name: 'History' })).toBeNull();
    expect(within(results).getByText('Line 7, TPE-9204: only 3 free')).toBeInTheDocument();
  });
});

describe('AC-W12: the gear entry', () => {
  it('shows History under Simulate confirm for an admin', async () => {
    renderPanel();
    const menu = await openGear();

    const items = within(menu).getAllByRole('menuitem').map((item) => item.textContent?.trim());
    expect(items).toContain('History');
    expect(items.indexOf('History')).toBe(items.indexOf('Simulate confirm') + 1);
  });

  it('does not show History to a Planner', async () => {
    sessionRole = 'Planner';
    renderPanel();
    const menu = await openGear();

    expect(within(menu).getByRole('menuitem', { name: 'Undo all' })).toBeInTheDocument();
    expect(within(menu).queryByRole('menuitem', { name: 'History' })).toBeNull();
  });

  it('opens a Sheet titled History that reads the history of the board\'s orders', async () => {
    renderPanel();
    const menu = await openGear();

    fireEvent.click(within(menu).getByRole('menuitem', { name: 'History' }));

    expect(await screen.findByRole('dialog', { name: 'History' })).toBeInTheDocument();
    await waitFor(() => expect(getPlanningHistory).toHaveBeenCalledTimes(1));
    expect(getPlanningHistory.mock.calls[0][0]).toEqual(['pso-so-a']);
  });
});
