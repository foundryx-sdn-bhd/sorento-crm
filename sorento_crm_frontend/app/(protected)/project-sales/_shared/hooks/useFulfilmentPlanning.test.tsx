/**
 * Stage 1B and 1C - the list, reconciliation, supply and confirmation hooks.
 *
 * What is worth pinning here is the wiring, not the rendering: the reconciliation and the
 * supply are both off until there is an order to ask about and its sheet is open, a rerun
 * invalidates every surface that carries the review state (the planning worklist, the
 * reconciliation itself, the project's SO list, and the SO detail header), and a
 * confirmation invalidates those plus the supply and the ORDER INQUIRY - the confirmed Buy
 * residual is what purchasing is handed, so a stale inquiry list is the one place the
 * decision would look like it had not happened.
 */
import React, { type ReactNode } from 'react';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { render, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  BoardCell,
  BoardContribution,
  BoardSource,
  ConfirmSupplyBody,
  PlanningBoard,
  ReconciliationSummary,
  SupplyProposal,
} from '../types/fulfilmentPlanning.types';

const listFulfilmentPlanning = vi.fn();
const getReconciliation = vi.fn();
const rerunReconciliation = vi.fn();
const getSupply = vi.fn();
const confirmSupply = vi.fn();
const getStockDetail = vi.fn();
const confirmMany = vi.fn();
const putLineDraft = vi.fn();
const deleteLineDraft = vi.fn();

vi.mock('../services/fulfilmentPlanningService', () => ({
  listFulfilmentPlanning: (...args: unknown[]) => listFulfilmentPlanning(...args),
  getReconciliation: (...args: unknown[]) => getReconciliation(...args),
  rerunReconciliation: (...args: unknown[]) => rerunReconciliation(...args),
  getSupply: (...args: unknown[]) => getSupply(...args),
  confirmSupply: (...args: unknown[]) => confirmSupply(...args),
  getStockDetail: (...args: unknown[]) => getStockDetail(...args),
  confirmMany: (...args: unknown[]) => confirmMany(...args),
  putLineDraft: (...args: unknown[]) => putLineDraft(...args),
  deleteLineDraft: (...args: unknown[]) => deleteLineDraft(...args),
}));

const toastSuccess = vi.fn();
const toastWarning = vi.fn();
const toastError = vi.fn();
vi.mock('@/lib/toast', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    warning: (...args: unknown[]) => toastWarning(...args),
    error: (...args: unknown[]) => toastError(...args),
  },
}));

import {
  FULFILMENT_PLANNING_KEY,
  PILE_QUEUE_KEY,
  PLANNING_BOARD_KEY,
  RECONCILIATION_KEY,
  STOCK_DETAIL_KEY,
  SUPPLY_KEY,
  patchContributionDraft,
  useConfirmManyMutation,
  useFulfilmentPlanning,
  useLineDraftMutation,
  useReconciliation,
  useReconciliationMutations,
  useStockDetail,
  useSupply,
} from './useFulfilmentPlanning';
import { BOARD_TRANSFERS_KEY } from './useBoardTransfers';
import { PLANNING_CHANGE_BATCH_KEY } from './usePlanningChanges';
import {
  ORDER_INQUIRY_ROWS_KEY,
  ORDER_INQUIRY_SUMMARY_KEY,
  ORDER_INQUIRY_WORKLIST_KEY,
  ORDER_INQUIRY_WORKLIST_SUMMARY_KEY,
  ORDER_INQUIRY_HEADER_KEY,
  ORDER_INQUIRY_HEADER_LINES_KEY,
} from './useOrderInquiry';
import { SALES_ORDERS_KEY, SALES_ORDER_KEY } from './useProjectSalesOrders';

function summary(overrides: Partial<ReconciliationSummary> = {}): ReconciliationSummary {
  return {
    project_sales_order_id: 'pso-1',
    provisional_ref: 'PSO-000123',
    autocount_doc_no: 'SO376201',
    project_id: 'proj-1',
    project_code: 'PRJ-0041',
    project_name: 'Tuju Residences',
    customer_name: 'Buimaco Sdn Bhd (Project)',
    po_number: 'HQ/26/01/121',
    area_group: 'TOWER',
    status: 'published',
    review_state: 'needs_cs_review',
    header: { outcome: 'linked', core_so_number: 'SO376201', reason: 'Linked.' },
    lines: [],
    exceptions: [],
    lines_total: 0,
    lines_linked: 0,
    ...overrides,
  };
}

function supplyProposal(overrides: Partial<SupplyProposal> = {}): SupplyProposal {
  return {
    project_sales_order_id: 'pso-1',
    provisional_ref: 'PSO-000123',
    autocount_doc_no: 'SO376201',
    project_id: 'proj-1',
    project_code: 'PRJ-0041',
    project_name: 'Tuju Residences',
    status: 'published',
    review_state: 'needs_cs_review',
    lines: [],
    ...overrides,
  };
}

/** A minimal contribution - just enough for `patchContributionDraft` and the draft mutations. */
function contribution(overrides: Partial<BoardContribution> = {}): BoardContribution {
  return {
    key: 'so-a|22|SRTWB7518|2026-06-29',
    sales_order_id: 'so-a',
    so_number: 'SO397450',
    line_no: 22,
    item_code: 'SRTWB7518',
    qty: '10',
    unplannable: false,
    rank_score: 0,
    rank_factors: [],
    sources: [],
    contested: false,
    ...overrides,
  };
}

/** A minimal cell carrying its own copy of a contribution, the same way the server does. */
function cell(overrides: Partial<BoardCell> = {}): BoardCell {
  return {
    item_code: 'SRTWB7518',
    bucket_key: '2026-06-29',
    total_qty: '10',
    locations: [],
    contributions: [contribution()],
    unplannable_count: 0,
    contested_count: 0,
    ...overrides,
  };
}

/** A minimal board - just the two arrays `patchContributionDraft` walks. */
function board(overrides: Partial<PlanningBoard> = {}): PlanningBoard {
  return {
    granularity: 'week',
    policy: { id: 'default', label: 'Default', weights: [] } as unknown as PlanningBoard['policy'],
    as_of: '2026-09-03',
    line_count: 1,
    past_line_count: 0,
    unplannable_line_count: 0,
    contested_line_count: 0,
    dateBuckets: [],
    productRows: [],
    cells: [cell()],
    contributions: [contribution()],
    orders: [],
    ...overrides,
  };
}

const CONFIRM_BODY: ConfirmSupplyBody = {
  lines: [
    {
      project_line_id: 'pl-1',
      timely_spo_qty: '0',
      reserve: [],
      borrow: [],
      buy_qty: '600',
      buy_reason: null,
    },
  ],
};

let client: QueryClient;
let invalidated: unknown[][];

function wrapper() {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  Wrapper.displayName = 'QueryWrapper';
  return Wrapper;
}

beforeEach(() => {
  vi.clearAllMocks();
  invalidated = [];
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  const original = client.invalidateQueries.bind(client);
  client.invalidateQueries = ((filters?: { queryKey?: unknown[] }) => {
    if (filters?.queryKey) invalidated.push(filters.queryKey);
    return original(filters as never);
  }) as typeof client.invalidateQueries;
});

describe('useFulfilmentPlanning', () => {
  it('lists with the params it is given', async () => {
    listFulfilmentPlanning.mockResolvedValue({ data: [], total: 0, page: 1, limit: 25 });

    const { result } = renderHook(
      () => useFulfilmentPlanning({ page: 1, limit: 25, review_state: 'needs_cs_review' }),
      { wrapper: wrapper() },
    );

    await waitFor(() => expect(listFulfilmentPlanning).toHaveBeenCalledTimes(1));
    expect(listFulfilmentPlanning).toHaveBeenCalledWith({
      page: 1,
      limit: 25,
      review_state: 'needs_cs_review',
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });
});

describe('useReconciliation', () => {
  it('reads the reconciliation once an id is given', async () => {
    getReconciliation.mockResolvedValue(summary());

    const { result } = renderHook(() => useReconciliation('pso-1'), { wrapper: wrapper() });

    await waitFor(() => expect(getReconciliation).toHaveBeenCalledWith('pso-1'));
    await waitFor(() => expect(result.current.data?.review_state).toBe('needs_cs_review'));
  });

  it('asks nothing while there is no order to open', () => {
    renderHook(() => useReconciliation(undefined), { wrapper: wrapper() });

    expect(getReconciliation).not.toHaveBeenCalled();
  });

  it('asks nothing while the sheet is closed, even with an id in hand', () => {
    renderHook(() => useReconciliation('pso-1', false), { wrapper: wrapper() });

    expect(getReconciliation).not.toHaveBeenCalled();
  });
});

describe('useSupply', () => {
  it('reads the composition once an id is given', async () => {
    getSupply.mockResolvedValue(supplyProposal());

    const { result } = renderHook(() => useSupply('pso-1'), { wrapper: wrapper() });

    await waitFor(() => expect(getSupply).toHaveBeenCalledWith('pso-1'));
    await waitFor(() => expect(result.current.data?.provisional_ref).toBe('PSO-000123'));
  });

  it('asks nothing while there is no order to open', () => {
    renderHook(() => useSupply(undefined), { wrapper: wrapper() });

    expect(getSupply).not.toHaveBeenCalled();
  });

  it('asks nothing while the sheet is closed: it reads live stock per line', () => {
    renderHook(() => useSupply('pso-1', false), { wrapper: wrapper() });

    expect(getSupply).not.toHaveBeenCalled();
  });

  it('refetches the pill sources when the composition disagrees about the review state', async () => {
    // An out-of-band change flips the decision to challenged; only the supply read
    // notices, so a cached "Confirmed" reconciliation must be refetched, not trusted.
    // Both hooks render, as they do in the open sheet, so the reconciliation data is
    // held by an observer rather than collected by gcTime: 0.
    getReconciliation.mockResolvedValue(summary({ review_state: 'confirmed' }));
    getSupply.mockResolvedValue(supplyProposal({ review_state: 'needs_cs_review' }));

    renderHook(
      () => {
        useReconciliation('pso-1');
        return useSupply('pso-1');
      },
      { wrapper: wrapper() },
    );

    await waitFor(() =>
      expect(invalidated).toEqual(
        expect.arrayContaining([[RECONCILIATION_KEY, 'pso-1'], [FULFILMENT_PLANNING_KEY]]),
      ),
    );
  });

  it('leaves the caches alone when both sources agree', async () => {
    getReconciliation.mockResolvedValue(summary({ review_state: 'needs_cs_review' }));
    getSupply.mockResolvedValue(supplyProposal({ review_state: 'needs_cs_review' }));

    const { result } = renderHook(
      () => {
        useReconciliation('pso-1');
        return useSupply('pso-1');
      },
      { wrapper: wrapper() },
    );

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(invalidated).toEqual([]);
  });
});

describe('useReconciliationMutations', () => {
  async function rerunOn(psoId: string) {
    let mutations: ReturnType<typeof useReconciliationMutations> | null = null;
    function Harness({ onReady }: { onReady: (api: typeof mutations) => void }) {
      const api = useReconciliationMutations();
      React.useEffect(() => {
        onReady(api);
      }, [api, onReady]);
      return null;
    }
    render(
      <QueryClientProvider client={client}>
        <Harness onReady={(value) => (mutations = value)} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(mutations).not.toBeNull());
    await mutations!.rerun.mutateAsync(psoId);
    return mutations!;
  }

  it('invalidates the planning list, the reconciliation, and both sales-order surfaces', async () => {
    rerunReconciliation.mockResolvedValue(summary({ exceptions: [] }));

    await rerunOn('pso-1');

    const flattened = invalidated.map((key) => JSON.stringify(key));
    expect(flattened.some((key) => key.includes(FULFILMENT_PLANNING_KEY))).toBe(true);
    expect(flattened.some((key) => key.includes(RECONCILIATION_KEY))).toBe(true);
    expect(flattened.some((key) => key.includes(SALES_ORDERS_KEY))).toBe(true);
    expect(flattened.some((key) => key.includes(SALES_ORDER_KEY))).toBe(true);
  });

  it('celebrates a clean rerun', async () => {
    rerunReconciliation.mockResolvedValue(summary({ exceptions: [] }));

    await rerunOn('pso-1');

    expect(toastSuccess).toHaveBeenCalledWith(
      'Reconciled. This sales order is ready for CS review.',
    );
    expect(toastWarning).not.toHaveBeenCalled();
  });

  it('names how many exceptions are still open, pluralized', async () => {
    rerunReconciliation.mockResolvedValue(
      summary({
        exceptions: [
          { kind: 'missing', line_no: 2, item_code: 'SRT501-CP', message: 'No AutoCount line.' },
          { kind: 'surplus', item_code: 'SRT770-BK', message: 'Spare on AutoCount.' },
        ],
      }),
    );

    await rerunOn('pso-1');

    expect(toastWarning).toHaveBeenCalledWith('2 exceptions still to clear on this sales order.');
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('reports a failed rerun instead of a silent no-op', async () => {
    rerunReconciliation.mockRejectedValue(new Error('That order was rebuilt'));

    await expect(rerunOn('pso-1')).rejects.toThrow('That order was rebuilt');

    expect(toastError).toHaveBeenCalledWith('That order was rebuilt');
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(toastWarning).not.toHaveBeenCalled();
  });

  // ------------------------------------------------------- the confirmation
  async function confirmOn(psoId: string, body: ConfirmSupplyBody = CONFIRM_BODY) {
    let mutations: ReturnType<typeof useReconciliationMutations> | null = null;
    function Harness({ onReady }: { onReady: (api: typeof mutations) => void }) {
      const api = useReconciliationMutations();
      React.useEffect(() => {
        onReady(api);
      }, [api, onReady]);
      return null;
    }
    render(
      <QueryClientProvider client={client}>
        <Harness onReady={(value) => (mutations = value)} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(mutations).not.toBeNull());
    await mutations!.confirm.mutateAsync({ psoId, body });
    return mutations!;
  }

  it('sends the whole order in one call', async () => {
    confirmSupply.mockResolvedValue({
      revision_no: 1,
      confirmed_at: '2026-08-18T02:00:00',
      review_state: 'confirmed',
      inquiry_rows_created: 1,
      exceptions: [],
    });

    await confirmOn('pso-1');

    expect(confirmSupply).toHaveBeenCalledTimes(1);
    expect(confirmSupply).toHaveBeenCalledWith('pso-1', CONFIRM_BODY);
  });

  it('invalidates every key family the decision shows up in, order inquiry included', async () => {
    confirmSupply.mockResolvedValue({
      revision_no: 1,
      confirmed_at: '2026-08-18T02:00:00',
      review_state: 'confirmed',
      inquiry_rows_created: 1,
      exceptions: [],
    });

    await confirmOn('pso-1');

    const flattened = invalidated.map((key) => JSON.stringify(key));
    for (const key of [
      FULFILMENT_PLANNING_KEY,
      RECONCILIATION_KEY,
      SUPPLY_KEY,
      SALES_ORDERS_KEY,
      SALES_ORDER_KEY,
      ORDER_INQUIRY_ROWS_KEY,
      ORDER_INQUIRY_SUMMARY_KEY,
      ORDER_INQUIRY_WORKLIST_KEY,
      ORDER_INQUIRY_WORKLIST_SUMMARY_KEY,
      PILE_QUEUE_KEY,
      STOCK_DETAIL_KEY,
      // The board's own transfers panel (D6): the movements this confirmation raised are on
      // screen beside it, and a panel that fills only after a reload is a panel nobody uses.
      BOARD_TRANSFERS_KEY,
    ]) {
      expect(flattened.some((entry) => entry.includes(key))).toBe(true);
    }
  });

  it('names the revision and how many purchase rows were handed over', async () => {
    confirmSupply.mockResolvedValue({
      revision_no: 3,
      confirmed_at: '2026-08-18T02:00:00',
      review_state: 'confirmed',
      inquiry_rows_created: 2,
      exceptions: [],
    });

    await confirmOn('pso-1');

    expect(toastSuccess).toHaveBeenCalledWith(
      'Confirmed as revision 3. 2 purchase rows handed over.',
    );
  });

  /**
   * PLAN-scm-cs-planning-uat.md section E: the transfer write is best-effort on the
   * server, so a failure cannot fail a promise already made - but a movement nobody was
   * told about is a movement nobody makes.
   */
  it('says nothing about transfers when they were all written', async () => {
    confirmSupply.mockResolvedValue({
      revision_no: 4,
      confirmed_at: '2026-08-18T02:00:00',
      review_state: 'confirmed',
      inquiry_rows_created: 0,
      exceptions: [],
      transfers_written: 3,
      transfers_failed: 0,
    });

    await confirmOn('pso-1');

    expect(toastWarning).not.toHaveBeenCalled();
  });

  it('says how many movements went unwritten, rather than swallowing the failure', async () => {
    confirmSupply.mockResolvedValue({
      revision_no: 4,
      confirmed_at: '2026-08-18T02:00:00',
      review_state: 'confirmed',
      inquiry_rows_created: 0,
      exceptions: [],
      transfers_written: 0,
      transfers_failed: 2,
    });

    await confirmOn('pso-1');

    expect(toastWarning).toHaveBeenCalledWith('Transfers not written: 2');
  });

  it('says "1 purchase row" rather than "1 purchase rows"', async () => {
    confirmSupply.mockResolvedValue({
      revision_no: 1,
      confirmed_at: '2026-08-18T02:00:00',
      review_state: 'confirmed',
      inquiry_rows_created: 1,
      exceptions: [],
    });

    await confirmOn('pso-1');

    expect(toastSuccess).toHaveBeenCalledWith(
      'Confirmed as revision 1. 1 purchase row handed over.',
    );
  });

  it('reports a refusal in one short sentence: the failing lines belong on the sheet', async () => {
    confirmSupply.mockRejectedValue(new Error('This sales order could not be confirmed'));

    await expect(confirmOn('pso-1')).rejects.toThrow(
      'This sales order could not be confirmed',
    );

    expect(toastError).toHaveBeenCalledWith('This sales order could not be confirmed');
    expect(toastSuccess).not.toHaveBeenCalled();
    // Nothing is invalidated by a refusal: nothing was written.
    expect(invalidated).toEqual([]);
  });
});

/**
 * The board's ONE Confirm (R11/D6).
 *
 * Two things are pinned: the transfers panel on the same screen is invalidated, so the
 * movements the press raised appear on the press; and the hook says NOTHING on success -
 * the board's own "N lines confirmed - T transfers proposed - I inquiry rows" is the
 * sentence, and a second toast counting orders beside it answered a question nobody asked.
 */
describe('useConfirmManyMutation', () => {
  async function confirmAll() {
    let mutation: ReturnType<typeof useConfirmManyMutation> | null = null;
    function Harness({ onReady }: { onReady: (api: typeof mutation) => void }) {
      const api = useConfirmManyMutation();
      React.useEffect(() => {
        onReady(api);
      }, [api, onReady]);
      return null;
    }
    render(
      <QueryClientProvider client={client}>
        <Harness onReady={(value) => (mutation = value)} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(mutation).not.toBeNull());
    await mutation!.mutateAsync({ orders: [{ pso_id: 'pso-1', lines: [] }] });
    return mutation!;
  }

  it('invalidates the board transfers panel, the batch and the transfers page', async () => {
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-1', ok: true, decision_revision: 2, transfers_written: 1 }],
    });

    await confirmAll();

    const flattened = invalidated.map((key) => JSON.stringify(key));
    for (const key of [
      PLANNING_BOARD_KEY,
      BOARD_TRANSFERS_KEY,
      PLANNING_CHANGE_BATCH_KEY,
      STOCK_DETAIL_KEY,
    ]) {
      expect(flattened.some((entry) => entry.includes(key))).toBe(true);
    }
  });

  it('says nothing on success: the board states what the press produced', async () => {
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-1', ok: true, decision_revision: 2 }],
    });

    await confirmAll();

    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('still names a partial refusal, which the board sentence does not carry', async () => {
    confirmMany.mockResolvedValue({
      results: [
        { pso_id: 'pso-1', ok: true, decision_revision: 2 },
        { pso_id: 'pso-2', ok: false, error: 'refused' },
      ],
    });

    await confirmAll();

    expect(toastWarning).toHaveBeenCalledWith(
      'Confirmed 1 order; 1 refused - see the results below.',
    );
  });

  it('AC-F3 (R3, `PLAN-board-draft-on-confirmed-line.md`): invalidates the planning board and the fulfilment-planning list on a lost or failed confirm', async () => {
    confirmMany.mockRejectedValue(new Error('network lost'));

    await expect(confirmAll()).rejects.toThrow('network lost');

    const flattened = invalidated.map((key) => JSON.stringify(key));
    for (const key of [PLANNING_BOARD_KEY, FULFILMENT_PLANNING_KEY]) {
      expect(flattened.some((entry) => entry.includes(key))).toBe(true);
    }
  });
});

describe('useStockDetail', () => {
  it('reads the detail by ids under the exported key, so a confirmation can invalidate it', async () => {
    getStockDetail.mockResolvedValue({ product_id: 'prod-1', warehouse_id: 'wh-1' });

    const { result } = renderHook(() => useStockDetail('prod-1', 'wh-1', ['line-a']), {
      wrapper: wrapper(),
    });

    await waitFor(() =>
      expect(getStockDetail).toHaveBeenCalledWith('prod-1', 'wh-1', ['line-a'], undefined),
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    // The asking lines are part of the key: a different asker is a different answer.
    expect(client.getQueryData([STOCK_DETAIL_KEY, 'prod-1', 'wh-1', 'line-a'])).toEqual({
      product_id: 'prod-1',
      warehouse_id: 'wh-1',
    });
  });

  it('reads a whole ownership GROUP under a key of its own', async () => {
    // The group is the pile the ladder's first step draws, and it is a different answer from
    // any one of its bins - so it may not share a cache entry with one.
    getStockDetail.mockResolvedValue({ product_id: 'prod-1', group: 'IB' });

    const { result } = renderHook(() => useStockDetail('prod-1', null, ['line-a'], 'IB'), {
      wrapper: wrapper(),
    });

    await waitFor(() =>
      expect(getStockDetail).toHaveBeenCalledWith('prod-1', null, ['line-a'], 'IB'),
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(client.getQueryData([STOCK_DETAIL_KEY, 'prod-1', 'IB', 'line-a'])).toEqual({
      product_id: 'prod-1',
      group: 'IB',
    });
  });
});

/**
 * Save decision / Undo (S4, R-F, D16). What the hook owes: the service call with the key and
 * the suggestion the save was taken against, a PATCH of the cached board rather than an
 * invalidation (a draft is never `active`, so no count anywhere else moves, and re-running the
 * whole board query for it was the cause of the flicker the captain called "very choppy" 3
 * September 2026), no success toast (the panel's own `decide()` says "Line N saved - K to
 * confirm" off the draft it just wrote), and the message on a refusal.
 */
describe('useLineDraftMutation', () => {
  async function drafts() {
    let mutation: ReturnType<typeof useLineDraftMutation> | null = null;
    function Harness({ onReady }: { onReady: (api: typeof mutation) => void }) {
      const api = useLineDraftMutation();
      React.useEffect(() => {
        onReady(api);
      }, [api, onReady]);
      return null;
    }
    render(
      <QueryClientProvider client={client}>
        <Harness onReady={(value) => (mutation = value)} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(mutation).not.toBeNull());
    return mutation!;
  }

  const KEY = 'so-a|22|SRTWB7518|2026-06-29';

  it('saves the key and the decision, and carries no proposal (S1, code review round 3)', async () => {
    putLineDraft.mockResolvedValue({
      decision: { verdict: 'amended' },
      saved_by: 'Eling',
      saved_at: '2026-09-03T01:00:00',
      stale: false,
    });

    const api = await drafts();
    const saved = await api.save(KEY, { verdict: 'amended' });

    // No saver: the server reads that off the caller's own JWT. No proposal either: the
    // server snapshots the line's own facts at save time, never the proposal.
    expect(putLineDraft).toHaveBeenCalledWith(KEY, { verdict: 'amended' });
    expect(saved.saved_by).toBe('Eling');
  });

  /**
   * D12 (#573): the caller (`FulfilmentBoardPanel.decide()`) may now pass the
   * contribution's own `sources`, which the service carries as `proposed` so the Sales
   * Order page's Suggested column reads it back on a saved line. Distinct from the S1
   * ruling above: staleness is still judged on the line's own facts only.
   */
  it('passes a given proposal through to the service (D12)', async () => {
    putLineDraft.mockResolvedValue({
      decision: { verdict: 'approved' },
      saved_by: 'Eling',
      saved_at: '2026-09-03T01:00:00',
      stale: false,
    });
    const proposed: BoardSource[] = [
      { kind: 'reserve', qty: '3', location: 'BRW', reason: 'Reserve from BRW' },
    ];

    const api = await drafts();
    await api.save(KEY, { verdict: 'approved' }, proposed);

    expect(putLineDraft).toHaveBeenCalledWith(KEY, { verdict: 'approved' }, proposed);
  });

  it('patches the cached board in place on save, and invalidates nothing (D16)', async () => {
    const saved = {
      decision: { verdict: 'approved' as const },
      saved_by: 'Eling',
      saved_at: '2026-09-03T01:00:00',
      stale: false,
    };
    putLineDraft.mockResolvedValue(saved);

    // The default `gcTime: 0` (`beforeEach` above) evicts a query the moment it has no
    // observer, which this seeded one never gets - only `useLineDraftMutation`'s own
    // mutations run here, nothing subscribes to the board itself. Pinned so the seeded
    // entry survives long enough for the assertions below to read it back.
    client.setQueryDefaults([PLANNING_BOARD_KEY], { gcTime: Infinity });
    const otherContribution = contribution({ key: 'so-b|1|ITEM|2026-06-30' });
    client.setQueryData([PLANNING_BOARD_KEY, 'so-a'], board({ contributions: [contribution(), otherContribution] }));

    const api = await drafts();
    await api.save(KEY, { verdict: 'approved' });

    // No refetch of the board query at all - that IS the fix (the captain, 3 Sep: "very
    // choppy"). Only the cache write above did anything to it.
    expect(invalidated).toEqual([]);
    expect(toastSuccess).not.toHaveBeenCalled();

    const patched = client.getQueryData<PlanningBoard>([PLANNING_BOARD_KEY, 'so-a'])!;
    expect(patched.contributions[0].draft).toEqual(saved);
    // The cell's own copy of the same line agrees - Confirm-all and the grid each read a
    // different one of the two arrays.
    expect(patched.cells[0].contributions[0].draft).toEqual(saved);
    // An UNRELATED contribution is untouched, and stays the SAME OBJECT: the seeding effect
    // in `FulfilmentBoardPanel` reads `contribution.draft` off every one of them on every
    // render, and a new reference there would re-render a card this write never touched.
    expect(patched.contributions[1]).toBe(otherContribution);
  });

  /**
   * REWORKED (owner ruling 23 Sep 2026, `PLAN-board-reject-on-confirmed-line.md`, hand-test
   * feedback: "we should confirm the rejection"): a reject on a COVERED line used to reach
   * `uncover_lines` on the server at save time (S3, fix round 3), which needed the wider
   * invalidation this test used to pin. It is a STAGED draft now, same as any other verdict
   * - nothing about the active confirmation moves until Confirm actually withdraws it - so a
   * reject save behaves identically whether the line is covered or not: the plain cache
   * patch is the whole story, same as `useConfirmManyMutation`'s own D16 note above.
   */
  it('a rejected save on a COVERED line stays the plain cache patch - nothing invalidated (D16)', async () => {
    const saved = {
      decision: { verdict: 'rejected' as const, reason: 'wrong site' },
      saved_by: 'Eling',
      saved_at: '2026-09-03T01:00:00',
      stale: false,
    };
    putLineDraft.mockResolvedValue(saved);

    client.setQueryDefaults([PLANNING_BOARD_KEY], { gcTime: Infinity });
    client.setQueryData(
      [PLANNING_BOARD_KEY, 'so-a'],
      board({ contributions: [contribution({ covered: true })] }),
    );

    const api = await drafts();
    await api.save(KEY, { verdict: 'rejected', reason: 'wrong site' });

    expect(invalidated).toEqual([]);
    // The cache patch still ran - Rejected reads back immediately.
    const patched = client.getQueryData<PlanningBoard>([PLANNING_BOARD_KEY, 'so-a'])!;
    expect(patched.contributions[0].draft).toEqual(saved);
  });

  it('a rejected save on an UNCOVERED line is the same plain cache patch', async () => {
    const saved = {
      decision: { verdict: 'rejected' as const, reason: 'wrong site' },
      saved_by: 'Eling',
      saved_at: '2026-09-03T01:00:00',
      stale: false,
    };
    putLineDraft.mockResolvedValue(saved);

    client.setQueryDefaults([PLANNING_BOARD_KEY], { gcTime: Infinity });
    client.setQueryData(
      [PLANNING_BOARD_KEY, 'so-a'],
      board({ contributions: [contribution({ covered: false })] }),
    );

    const api = await drafts();
    await api.save(KEY, { verdict: 'rejected', reason: 'wrong site' });

    expect(invalidated).toEqual([]);
    const patched = client.getQueryData<PlanningBoard>([PLANNING_BOARD_KEY, 'so-a'])!;
    expect(patched.contributions[0].draft).toEqual(saved);
  });

  it('undoes by key: patches the draft to null, and invalidates nothing (D16)', async () => {
    deleteLineDraft.mockResolvedValue(undefined);

    client.setQueryDefaults([PLANNING_BOARD_KEY], { gcTime: Infinity });
    const savedDraft = {
      decision: { verdict: 'approved' as const },
      saved_by: 'Eling',
      saved_at: '2026-09-03T01:00:00',
    };
    client.setQueryData(
      [PLANNING_BOARD_KEY, 'so-a'],
      board({ contributions: [contribution({ draft: savedDraft })] }),
    );

    const api = await drafts();
    await api.remove(KEY);

    expect(deleteLineDraft).toHaveBeenCalledWith(KEY);
    expect(invalidated).toEqual([]);

    const patched = client.getQueryData<PlanningBoard>([PLANNING_BOARD_KEY, 'so-a'])!;
    expect(patched.contributions[0].draft).toBeNull();
  });

  it('names the refusal, and lets the caller put the row back', async () => {
    putLineDraft.mockRejectedValue(new Error('Backend said no'));

    const api = await drafts();
    await expect(api.save(KEY, { verdict: 'approved' })).rejects.toThrow(
      'Backend said no',
    );

    expect(toastError).toHaveBeenCalledWith('Backend said no');
  });

  it('names a refused Undo too', async () => {
    deleteLineDraft.mockRejectedValue(new Error('Backend said no'));

    const api = await drafts();
    await expect(api.remove(KEY)).rejects.toThrow('Backend said no');

    expect(toastError).toHaveBeenCalledWith('Backend said no');
  });
});

/**
 * The pure updater itself (D16), on its own: what `useLineDraftMutation` above hands to
 * `setQueriesData`.
 */
describe('patchContributionDraft', () => {
  const KEY = 'so-a|22|SRTWB7518|2026-06-29';

  it('sets the draft on the matching contribution in BOTH arrays', () => {
    const source = board();
    const draft = {
      decision: { verdict: 'amended' as const },
      saved_by: 'Eling',
      saved_at: '2026-09-03T01:00:00',
    };

    const patched = patchContributionDraft(source, KEY, draft);

    expect(patched.contributions[0].draft).toEqual(draft);
    expect(patched.cells[0].contributions[0].draft).toEqual(draft);
  });

  it('clears the draft to null on Undo, without touching anything else on the row', () => {
    const source = board({
      contributions: [
        contribution({ draft: { decision: { verdict: 'approved' }, saved_by: 'Eling', saved_at: 'x' } }),
      ],
    });

    const patched = patchContributionDraft(source, KEY, null);

    expect(patched.contributions[0].draft).toBeNull();
    expect(patched.contributions[0].qty).toBe('10');
  });

  it('leaves every unrelated contribution at the SAME OBJECT reference', () => {
    const untouched = contribution({ key: 'so-b|1|ITEM|2026-06-30' });
    const source = board({ contributions: [contribution(), untouched] });

    const patched = patchContributionDraft(source, KEY, null);

    expect(patched.contributions[1]).toBe(untouched);
  });

  it('is a no-op (but still a fresh board object) when the key matches nothing', () => {
    const source = board();

    const patched = patchContributionDraft(source, 'no-such-key', null);

    expect(patched.contributions[0]).toBe(source.contributions[0]);
    expect(patched.cells[0]).not.toBe(source.cells[0]);
  });
});

/**
 * Owner hand test, 9 Oct 2026 (BORROW-LOCATION-ONLY): right after Confirm, the board's OI
 * link said "the line you clicked is not here" and the OI page still showed the borrow's old
 * donor line; a second click a while later showed the right one. The server had already
 * committed every row before Confirm returned. Two caches were stale: the OI page's own
 * header and Lines queries (cached from an earlier visit, fresh for the app's 30s default and
 * never invalidated by Confirm), and the board itself, whose refetch Confirm fired without
 * waiting for, so its OI link still named the row this Confirm had just cancelled.
 */
describe('a Confirm hands back only once the OI page and the board are fresh', () => {
  function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((r) => {
      resolve = r;
    });
    return { promise, resolve };
  }

  /** An OI page visited before the Confirm: its cached header and Lines name the old row. */
  function seedVisitedOi() {
    client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 }, mutations: { retry: false } },
    });
    client.setQueryData([ORDER_INQUIRY_HEADER_KEY, 'oi-1'], { id: 'oi-1' });
    client.setQueryData([ORDER_INQUIRY_HEADER_LINES_KEY, 'oi-1'], { rows: [{ id: 'donor-row' }] });
  }

  /** A mounted board whose refetch after the Confirm is held until the test lets it land. */
  function mountBoard(refetch: Promise<unknown>) {
    const boardFn = vi.fn().mockResolvedValueOnce({ cells: [] }).mockReturnValueOnce(refetch);
    function Board() {
      useQuery({ queryKey: [PLANNING_BOARD_KEY, 'b'], queryFn: boardFn });
      return null;
    }
    return { Board, boardFn };
  }

  async function harness<T>(useApi: () => T, Board: () => null) {
    let api: T | null = null;
    function Harness({ onReady }: { onReady: (value: T) => void }) {
      const value = useApi();
      React.useEffect(() => {
        onReady(value);
      }, [value, onReady]);
      return null;
    }
    render(
      <QueryClientProvider client={client}>
        <Board />
        <Harness onReady={(value) => (api = value)} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(api).not.toBeNull());
    return () => api as T;
  }

  it('per-order Confirm: drops the visited OI page and waits for the board refetch', async () => {
    seedVisitedOi();
    confirmSupply.mockResolvedValue({
      revision_no: 2,
      confirmed_at: '2026-10-09T01:20:00',
      review_state: 'confirmed',
      inquiry_rows_created: 1,
      exceptions: [],
    });
    const refetch = deferred<unknown>();
    const { Board, boardFn } = mountBoard(refetch.promise);
    const api = await harness(useReconciliationMutations, Board);
    await waitFor(() => expect(boardFn).toHaveBeenCalledTimes(1));

    let settled = false;
    const done = api()
      .confirm.mutateAsync({ psoId: 'pso-1', body: CONFIRM_BODY })
      .then(() => {
        settled = true;
      });
    await waitFor(() => expect(boardFn).toHaveBeenCalledTimes(2));
    expect(settled).toBe(false);
    refetch.resolve({ cells: [] });
    await done;

    expect(settled).toBe(true);
    expect(client.getQueryData([ORDER_INQUIRY_HEADER_KEY, 'oi-1'])).toBeUndefined();
    expect(client.getQueryData([ORDER_INQUIRY_HEADER_LINES_KEY, 'oi-1'])).toBeUndefined();
  });

  it('per-order Confirm with the board not on screen: drops the cached board too', async () => {
    // Reviewer, hand-test round: from the per-order sheet the board query is inactive, so an
    // invalidation only marks it stale and the next visit paints the cached board, old OI
    // link and all, while it refetches. Dropped, the board loads instead of lying.
    seedVisitedOi();
    client.setQueryData([PLANNING_BOARD_KEY, 'b'], { cells: [] });
    client.setQueryData([FULFILMENT_PLANNING_KEY, 'list'], { items: [] });
    confirmSupply.mockResolvedValue({
      revision_no: 2,
      confirmed_at: '2026-10-09T01:20:00',
      review_state: 'confirmed',
      inquiry_rows_created: 1,
      exceptions: [],
    });
    const api = await harness(useReconciliationMutations, () => null);

    await api().confirm.mutateAsync({ psoId: 'pso-1', body: CONFIRM_BODY });

    expect(client.getQueryData([PLANNING_BOARD_KEY, 'b'])).toBeUndefined();
    expect(client.getQueryData([FULFILMENT_PLANNING_KEY, 'list'])).toBeUndefined();
  });

  it('board Confirm: drops the visited OI page and waits for the board refetch', async () => {
    seedVisitedOi();
    confirmMany.mockResolvedValue({
      results: [{ pso_id: 'pso-1', ok: true, decision_revision: 2 }],
    });
    const refetch = deferred<unknown>();
    const { Board, boardFn } = mountBoard(refetch.promise);
    const api = await harness(useConfirmManyMutation, Board);
    await waitFor(() => expect(boardFn).toHaveBeenCalledTimes(1));

    let settled = false;
    const done = api()
      .mutateAsync({ orders: [{ pso_id: 'pso-1', lines: [] }] })
      .then(() => {
        settled = true;
      });
    await waitFor(() => expect(boardFn).toHaveBeenCalledTimes(2));
    expect(settled).toBe(false);
    refetch.resolve({ cells: [] });
    await done;

    expect(settled).toBe(true);
    expect(client.getQueryData([ORDER_INQUIRY_HEADER_KEY, 'oi-1'])).toBeUndefined();
    expect(client.getQueryData([ORDER_INQUIRY_HEADER_LINES_KEY, 'oi-1'])).toBeUndefined();
  });
});
