/**
 * Stage 1B - the fulfilment planning / reconciliation service (contract section 3).
 *
 * What is pinned here is the wire: the three documented URLs and their methods, the query
 * params the worklist sends, envelope normalisation both ways round, and `extractApiError`
 * on every failure. `@/lib/api` and `@/lib/api-client` are replaced through
 * `vi.resetModules` + `vi.doMock` + a dynamic `import()`, because the module reads them at
 * load time.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { BoardSource } from '../types/fulfilmentPlanning.types';

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
});

describe('fulfilmentPlanningService', () => {
  const apiFetch = vi.fn();

  function ok(body: unknown) {
    return { ok: true, json: async () => body } as Response;
  }

  function fail() {
    return { ok: false, headers: { get: () => 'application/json' }, json: async () => ({}) } as unknown as Response;
  }

  async function loadRealService() {
    vi.doMock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));
    vi.doMock('@/lib/api-client', async (importOriginal) => {
      const actual = await importOriginal<typeof import('@/lib/api-client')>();
      return { ...actual, extractApiError: vi.fn(async () => 'Backend said no') };
    });
    return import('./fulfilmentPlanningService');
  }

  it('lists via the documented GET url, carrying page, limit, query and filters', async () => {
    apiFetch.mockResolvedValue(ok({ data: [], pagination: { total: 0, page: 2, limit: 10 } }));
    const { listFulfilmentPlanning } = await loadRealService();

    await listFulfilmentPlanning({
      page: 2,
      limit: 10,
      query: 'buimaco',
      review_state: 'needs_cs_review',
      project_id: 'proj-1',
    });

    const [url] = apiFetch.mock.calls[0];
    expect(url).toContain('/api/v1/project-sales/fulfilment-planning?');
    expect(url).toContain('page=2');
    expect(url).toContain('limit=10');
    expect(url).toContain('query=buimaco');
    expect(url).toContain('review_state=needs_cs_review');
    expect(url).toContain('project_id=proj-1');
  });

  it('normalises the standard {data, pagination} envelope', async () => {
    apiFetch.mockResolvedValue(
      ok({
        data: [{ id: 'r1' }],
        pagination: { total: 7, page: 2, limit: 10 },
      }),
    );
    const { listFulfilmentPlanning } = await loadRealService();

    const result = await listFulfilmentPlanning({ page: 2, limit: 10 });

    expect(result).toEqual({ data: [{ id: 'r1' }], total: 7, page: 2, limit: 10 });
  });

  it('falls back to a flat {data, total, page, limit} envelope', async () => {
    apiFetch.mockResolvedValue(ok({ data: [{ id: 'r1' }], total: 1, page: 1, limit: 25 }));
    const { listFulfilmentPlanning } = await loadRealService();

    const result = await listFulfilmentPlanning();

    expect(result).toEqual({ data: [{ id: 'r1' }], total: 1, page: 1, limit: 25 });
  });

  it('defaults an envelope with no total to the row count', async () => {
    apiFetch.mockResolvedValue(ok({ data: [{ id: 'r1' }, { id: 'r2' }] }));
    const { listFulfilmentPlanning } = await loadRealService();

    const result = await listFulfilmentPlanning();

    expect(result.total).toBe(2);
    expect(result.data).toHaveLength(2);
  });

  it('surfaces the backend message when the list fails', async () => {
    apiFetch.mockResolvedValue(fail());
    const { listFulfilmentPlanning } = await loadRealService();

    await expect(listFulfilmentPlanning()).rejects.toThrow('Backend said no');
  });

  it('reads one reconciliation via the documented GET url', async () => {
    apiFetch.mockResolvedValue(ok({ project_sales_order_id: 'pso-1', review_state: 'needs_cs_review' }));
    const { getReconciliation } = await loadRealService();

    await getReconciliation('pso-1');

    expect(apiFetch).toHaveBeenCalledWith('/api/v1/project-sales/sales-orders/pso-1/reconciliation');
  });

  it('surfaces the backend message when the reconciliation read fails', async () => {
    apiFetch.mockResolvedValue(fail());
    const { getReconciliation } = await loadRealService();

    await expect(getReconciliation('pso-1')).rejects.toThrow('Backend said no');
  });

  it('reruns via a POST to the documented url', async () => {
    apiFetch.mockResolvedValue(ok({ project_sales_order_id: 'pso-1', review_state: 'needs_cs_review' }));
    const { rerunReconciliation } = await loadRealService();

    await rerunReconciliation('pso-1');

    const [url, init] = apiFetch.mock.calls[0];
    expect(url).toBe('/api/v1/project-sales/sales-orders/pso-1/reconcile');
    expect(init).toEqual({ method: 'POST' });
  });

  it('surfaces the backend message when the rerun fails', async () => {
    apiFetch.mockResolvedValue(fail());
    const { rerunReconciliation } = await loadRealService();

    await expect(rerunReconciliation('pso-1')).rejects.toThrow('Backend said no');
  });

  // ------------------------------------------------ Stage 1C: supply and confirm
  it('reads the composition via the documented GET url', async () => {
    apiFetch.mockResolvedValue(ok({ project_sales_order_id: 'pso-1', lines: [] }));
    const { getSupply } = await loadRealService();

    await getSupply('pso-1');

    expect(apiFetch).toHaveBeenCalledWith('/api/v1/project-sales/sales-orders/pso-1/supply');
  });

  it('surfaces the backend message when the composition read fails', async () => {
    apiFetch.mockResolvedValue(fail());
    const { getSupply } = await loadRealService();

    await expect(getSupply('pso-1')).rejects.toThrow('Backend said no');
  });

  it('confirms with one POST carrying the whole order, under the documented keys', async () => {
    apiFetch.mockResolvedValue(
      ok({
        revision_no: 1,
        confirmed_at: '2026-08-18T02:00:00',
        review_state: 'confirmed',
        inquiry_rows_created: 1,
        exceptions: [],
      }),
    );
    const { confirmSupply } = await loadRealService();

    const body = {
      lines: [
        {
          project_line_id: 'pl-1',
          timely_spo_qty: '100',
          reserve: [{ warehouse_id: 'wh-brw', qty: '200' }],
          borrow: [
            {
              source: 'other_project' as const,
              warehouse_id: 'wh-jb',
              donor_project_id: 'proj-2',
              qty: '40',
              reason: 'Their hand-over is in December.',
            },
          ],
          buy_qty: '260',
          buy_reason: null,
        },
      ],
    };
    const result = await confirmSupply('pso-1', body);

    const [url, init] = apiFetch.mock.calls[0];
    expect(url).toBe('/api/v1/project-sales/sales-orders/pso-1/confirm');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(init.body)).toEqual(body);
    expect(result.revision_no).toBe(1);
  });

  it('carries the failing lines off a refusal, with the message from the shared extractor', async () => {
    // The refusal body is the shared AppException envelope plus `failing_lines`. The
    // extractor answers with a string, so the list is read off a clone of the same
    // response - losing it would lose exactly the part CS acts on.
    const failing = [
      { line_no: 1, item_code: 'CB6633', reason: 'Only 25 units are still free at BRW-BB.' },
      { line_no: 2, item_code: 'CB2201', reason: 'The open quantity changed to 80.' },
    ];
    const refusal = {
      ok: false,
      headers: { get: () => 'application/json' },
      clone: () => ({
        json: async () => ({
          message: 'This sales order could not be confirmed',
          code: 'supply_confirm_refused',
          failing_lines: failing,
        }),
      }),
      json: async () => ({ message: 'This sales order could not be confirmed' }),
    } as unknown as Response;
    apiFetch.mockResolvedValue(refusal);
    const { confirmSupply, ConfirmSupplyError } = await loadRealService();

    const error = await confirmSupply('pso-1', { lines: [] }).catch((caught) => caught);

    expect(error).toBeInstanceOf(ConfirmSupplyError);
    expect(error.message).toBe('Backend said no');
    expect(error.failingLines).toEqual(failing);
  });

  it('still refuses with a message when the body names no line at all', async () => {
    const refusal = {
      ok: false,
      headers: { get: () => 'text/html' },
      clone: () => ({
        json: async () => {
          throw new Error('not json');
        },
      }),
      json: async () => ({}),
    } as unknown as Response;
    apiFetch.mockResolvedValue(refusal);
    const { confirmSupply } = await loadRealService();

    const error = await confirmSupply('pso-1', { lines: [] }).catch((caught) => caught);

    expect(error.message).toBe('Backend said no');
    expect(error.failingLines).toEqual([]);
  });
});

/**
 * Phase 2: off the mock and onto the real endpoints.
 *
 * These are written BEFORE the wiring and run red first, because the copy defects earlier in
 * this feature shipped with green tests written against the wrong wording. A test that is
 * authored after the code it checks tends to describe the code rather than the contract.
 */
describe('fulfilmentPlanningService, wired to the real backend', () => {
  const apiFetch = vi.fn();

  function ok(body: unknown) {
    return { ok: true, json: async () => body } as Response;
  }

  async function loadService() {
    vi.doMock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));
    vi.doMock('@/lib/api-client', async (importOriginal) => {
      const actual = await importOriginal<typeof import('@/lib/api-client')>();
      return { ...actual, extractApiError: vi.fn(async () => 'Backend said no') };
    });
    return import('./fulfilmentPlanningService');
  }

  /** The URL of the last apiFetch call, as a URL object so params can be read by name. */
  function lastUrl(): URL {
    const raw = String(apiFetch.mock.calls[apiFetch.mock.calls.length - 1][0]);
    return new URL(raw, 'http://test.local');
  }

  it('serves NO fixtures: there is no mock switch left to turn on', async () => {
    const service = await loadService();
    expect(service).not.toHaveProperty('FULFILMENT_MOCK');
  });

  it('reaches the network for the worklist even when the old mock flag is set', async () => {
    vi.stubEnv('NEXT_PUBLIC_FULFILMENT_MOCK', '1');
    apiFetch.mockResolvedValue(ok({ data: [], pagination: { total: 0, page: 1, limit: 25 } }));
    const { listFulfilmentPlanning } = await loadService();

    await listFulfilmentPlanning({});

    expect(apiFetch).toHaveBeenCalled();
    vi.unstubAllEnvs();
  });

  it('adopts through the documented POST, carrying the sales order id as a body', async () => {
    apiFetch.mockResolvedValue(
      ok({
        project_sales_order_id: 'pso-1',
        so_number: 'SO345418',
        review_state: 'needs_cs_review',
        already_adopted: false,
      }),
    );
    const { adoptSalesOrder } = await loadService();

    const result = await adoptSalesOrder('6ef019e2-1405-4e2e-821c-b550882963d4');

    const [url, init] = apiFetch.mock.calls[0];
    expect(url).toBe('/api/v1/project-sales/fulfilment-planning/adopt');
    expect(init).toMatchObject({ method: 'POST' });
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      sales_order_id: '6ef019e2-1405-4e2e-821c-b550882963d4',
    });
    expect(result.already_adopted).toBe(false);
  });

  it('asks the board route for sales-order NUMBERS and the granularity', async () => {
    apiFetch.mockResolvedValue(ok({ cells: [] }));
    const { getPlanningBoard } = await loadService();

    await getPlanningBoard(['SO391698', 'SO324265'], 'month');

    const url = lastUrl();
    expect(url.pathname).toBe('/api/v1/project-sales/fulfilment-planning/board');
    expect(url.searchParams.get('orders')).toBe('SO391698,SO324265');
    expect(url.searchParams.get('granularity')).toBe('month');
  });

  it('omits preview_policy entirely when the live policy is wanted', async () => {
    apiFetch.mockResolvedValue(ok({ cells: [] }));
    const { getPlanningBoard } = await loadService();

    await getPlanningBoard(['SO391698'], 'week');

    expect(lastUrl().searchParams.has('preview_policy')).toBe(false);
  });

  it('asks for the preview policy by flag', async () => {
    apiFetch.mockResolvedValue(ok({ cells: [] }));
    const { getPlanningBoard } = await loadService();

    await getPlanningBoard(['SO391698'], 'week', true);

    expect(lastUrl().searchParams.get('preview_policy')).toBe('1');
  });

  /** Deviation 6: the route takes a policy NAME as well as 1/true. */
  it('asks for a named policy when one is given, rather than the bare flag', async () => {
    apiFetch.mockResolvedValue(ok({ cells: [] }));
    const { getPlanningBoard } = await loadService();

    await getPlanningBoard(['SO391698'], 'week', 'Fulfilment board preview');

    expect(lastUrl().searchParams.get('preview_policy')).toBe('Fulfilment board preview');
  });

  /** Deviation 7: the route accepts day_window and as_of; the day view needs the first. */
  it('sends day_window when the day view is scrolled, and never otherwise', async () => {
    apiFetch.mockResolvedValue(ok({ cells: [] }));
    const { getPlanningBoard } = await loadService();

    await getPlanningBoard(['SO391698'], 'day', false, { dayWindow: '2026-09-01' });
    expect(lastUrl().searchParams.get('day_window')).toBe('2026-09-01');

    await getPlanningBoard(['SO391698'], 'week');
    expect(lastUrl().searchParams.has('day_window')).toBe(false);
  });

  it('pins the board to a date when one is asked for, so a run is reproducible', async () => {
    apiFetch.mockResolvedValue(ok({ cells: [] }));
    const { getPlanningBoard } = await loadService();

    await getPlanningBoard(['SO391698'], 'week', false, { asOf: '2026-08-18' });

    expect(lastUrl().searchParams.get('as_of')).toBe('2026-08-18');
  });

  it('reports a board failure through the shared extractor', async () => {
    apiFetch.mockResolvedValue({
      ok: false,
      headers: { get: () => 'application/json' },
      json: async () => ({}),
    } as unknown as Response);
    const { getPlanningBoard } = await loadService();

    await expect(getPlanningBoard(['SO391698'])).rejects.toThrow('Backend said no');
  });
});

/**
 * S4 saved decisions (R-F), Phase 2: the two draft endpoints are real, and the Phase 1
 * overlay (`lib/fulfilmentS4Mock.ts`) is gone. What is pinned here is the wire - the URL
 * with the contribution key ENCODED into it, the body, and the one status Undo forgives.
 */
describe('fulfilmentPlanningService: saved decisions (S4)', () => {
  const apiFetch = vi.fn();

  async function loadService() {
    vi.doMock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));
    vi.doMock('@/lib/api-client', async (importOriginal) => {
      const actual = await importOriginal<typeof import('@/lib/api-client')>();
      return { ...actual, extractApiError: vi.fn(async () => 'Backend said no') };
    });
    return import('./fulfilmentPlanningService');
  }

  const KEY = 'a1b2|3|B2155-NL-BLUE|2026-09-07';
  const ENCODED =
    '/api/v1/project-sales/fulfilment-planning/lines/' +
    'a1b2%7C3%7CB2155-NL-BLUE%7C2026-09-07/draft';

  it('saves through the documented PUT, with the key encoded into the path', async () => {
    apiFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        decision: { verdict: 'amended' },
        saved_by: 'Eling',
        saved_at: '2026-09-03T01:00:00',
        stale: false,
      }),
    } as Response);
    const { putLineDraft } = await loadService();

    const saved = await putLineDraft(KEY, { verdict: 'amended', buy_qty: '4' });

    const [url, init] = apiFetch.mock.calls[0];
    // The key embeds `|`, which a raw path segment may not carry.
    expect(url).toBe(ENCODED);
    expect(init).toMatchObject({ method: 'PUT' });
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      decision: { verdict: 'amended', buy_qty: '4' },
    });
    expect(saved.saved_by).toBe('Eling');
    expect(saved.stale).toBe(false);
  });

  it('carries no `proposed` (S1, code review round 3): the server snapshots the line\'s own facts, never the proposal', async () => {
    apiFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ decision: {}, saved_by: 'Eling', saved_at: '', stale: false }),
    } as Response);
    const { putLineDraft } = await loadService();

    await putLineDraft(KEY, { verdict: 'approved' });

    expect(
      Object.keys(
        JSON.parse((apiFetch.mock.calls[0][1] as RequestInit).body as string),
      ),
    ).toEqual(['decision']);
  });

  it('carries `proposed` when given (D12, #573): the draft keeps the engine\'s suggestion at save time', async () => {
    apiFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ decision: {}, saved_by: 'Eling', saved_at: '', stale: false }),
    } as Response);
    const { putLineDraft } = await loadService();
    const proposed: BoardSource[] = [
      { kind: 'reserve', qty: '3', location: 'BRW', reason: 'Reserve from BRW' },
    ];

    await putLineDraft(KEY, { verdict: 'approved' }, proposed);

    expect(JSON.parse((apiFetch.mock.calls[0][1] as RequestInit).body as string)).toEqual({
      decision: { verdict: 'approved' },
      proposed,
    });
  });

  it('reports a refused save through the shared extractor', async () => {
    apiFetch.mockResolvedValue({
      ok: false,
      status: 422,
      headers: { get: () => 'application/json' },
      json: async () => ({}),
    } as unknown as Response);
    const { putLineDraft } = await loadService();

    await expect(putLineDraft(KEY, { verdict: 'approved' })).rejects.toThrow(
      'Backend said no',
    );
  });

  it('undoes through the documented DELETE on the same encoded path', async () => {
    apiFetch.mockResolvedValue({ ok: true, status: 204 } as Response);
    const { deleteLineDraft } = await loadService();

    await deleteLineDraft(KEY);

    expect(apiFetch.mock.calls[0][0]).toBe(ENCODED);
    expect(apiFetch.mock.calls[0][1]).toMatchObject({ method: 'DELETE' });
  });

  it('treats a 404 on Undo as the line already being clear', async () => {
    apiFetch.mockResolvedValue({
      ok: false,
      status: 404,
      headers: { get: () => 'application/json' },
      json: async () => ({}),
    } as unknown as Response);
    const { deleteLineDraft } = await loadService();

    await expect(deleteLineDraft(KEY)).resolves.toBeUndefined();
  });

  it('still reports any OTHER refusal on Undo', async () => {
    apiFetch.mockResolvedValue({
      ok: false,
      status: 403,
      headers: { get: () => 'application/json' },
      json: async () => ({}),
    } as unknown as Response);
    const { deleteLineDraft } = await loadService();

    await expect(deleteLineDraft(KEY)).rejects.toThrow('Backend said no');
  });
});

/**
 * FULFIL-CONFIRM-SCOPE v2 (P3): Preview posts the SAME body Confirm would post, with
 * `preview: true`, to the same route, and hands back the server's per-order answer untouched.
 */
describe('previewConfirmMany', () => {
  const apiFetch = vi.fn();

  async function load() {
    vi.doMock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));
    vi.doMock('@/lib/api-client', async (importOriginal) => {
      const actual = await importOriginal<typeof import('@/lib/api-client')>();
      return { ...actual, extractApiError: vi.fn(async () => 'Backend said no') };
    });
    return import('./fulfilmentPlanningService');
  }

  const body = {
    orders: [{ pso_id: 'pso-1', lines: [] as never[], batch_id: null }],
    batch_id: null,
  };

  it('posts the confirm body plus preview: true to confirm-all and returns the parsed result', async () => {
    const answer = {
      results: [
        {
          pso_id: 'pso-1',
          ok: true,
          preview: true,
          decision_revision: 2,
          lines_confirmed: [],
          lines_carried: 1,
          lines_held_back: [],
          lines_fulfilled_skipped: 0,
          inquiry_rows: [
            {
              line_no: 6,
              item_code: 'SRTWT6808',
              verb: 'ORDER',
              qty: '239',
              delivery_date: '2026-09-01',
              stock_location: 'BRW-IB',
              note: null,
            },
          ],
          transfers: [],
        },
      ],
    };
    apiFetch.mockResolvedValue({ ok: true, json: async () => answer } as Response);
    const { previewConfirmMany } = await load();

    const result = await previewConfirmMany(body);

    const [url, init] = apiFetch.mock.calls[0];
    expect(url).toBe('/api/v1/project-sales/fulfilment-planning/confirm-all');
    expect(init.method).toBe('POST');
    expect(init.headers).toMatchObject({ 'Content-Type': 'application/json' });
    expect(JSON.parse(init.body)).toEqual({ ...body, preview: true });
    expect(result).toEqual(answer);
  });

  it('does not mutate the caller body and reports a refusal through extractApiError', async () => {
    apiFetch.mockResolvedValue({
      ok: false,
      headers: { get: () => 'application/json' },
      json: async () => ({}),
    } as unknown as Response);
    const { previewConfirmMany } = await load();
    const before = JSON.stringify(body);

    await expect(previewConfirmMany(body)).rejects.toThrow('Backend said no');
    expect(JSON.stringify(body)).toBe(before);
  });
});
