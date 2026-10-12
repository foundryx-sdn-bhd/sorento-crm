/**
 * stockDebtService - the REAL (Phase 2) backend contract, from the PHASE-2 BACKEND
 * CONTRACT header of `stockDebtService.ts` (PLAN-stock-debt-filters-totals-export-24sep.md).
 *
 * Owner's hand-test round (R14-R19, 24 Sep 2026) replaces the single `cutoff` with a
 * `date_from`/`date_to` range (R14) and the single `supplier_id` with a repeatable
 * `supplier_ids` (R15), and drops `group` from the wire entirely (R16 - the Ownership
 * group filter leaves the screen, so the FE service never sends it again). The OLD
 * cutoff/supplier_id assertions are REPLACED here, not kept alongside the new ones - the
 * old wire shape no longer exists.
 *
 * The exact JS param names (`dateFrom`/`dateTo`/`supplierIds`) are not fixed by the
 * contract header, only the WIRE names are (`date_from`, `date_to`, `supplier_ids`) -
 * assumed here consistent with the existing camelCase convention (`onlyDebt`,
 * `supplierId`).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

import { apiFetch } from '@/lib/api';
import {
  exportStockDebt,
  getStockDebtCell,
  getStockDebtList,
} from './stockDebtService';
import type { StockDebtExportParams, StockDebtListParams } from './stockDebtService';

const mockedFetch = vi.mocked(apiFetch);

const LIST_PARAMS: StockDebtListParams = {
  pageIndex: 0,
  pageSize: 25,
  query: 'SRTWB242',
  onlyDebt: true,
  book: 'retail',
  supplierIds: ['sup-guangdong', 'sup-foshan'],
  dateFrom: '2026-11-01',
  dateTo: '2026-11-30',
} as unknown as StockDebtListParams;

const EXPORT_PARAMS: StockDebtExportParams = {
  query: 'SRTWB242',
  onlyDebt: true,
  book: 'retail',
  supplierIds: ['sup-guangdong', 'sup-foshan'],
  dateFrom: '2026-11-01',
  dateTo: '2026-11-30',
  split: 'supplier',
} as unknown as StockDebtExportParams;

const EMPTY_ENVELOPE = {
  data: [], pagination: { total: 0, page: 1, limit: 25 }, months: [],
  tba_month: '2029-01', groups: [],
  totals: { months: {}, tba: 0, total: 0 },
  suppliers: [], sheet_counts: { supplier: 0, category: 0, supplier_category: 0 },
};

function ok(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    json: async () => body,
  } as unknown as Response;
}

function calledUrl(callIndex = 0): URL {
  const call = mockedFetch.mock.calls[callIndex];
  return new URL(call[0] as string, 'http://localhost');
}

beforeEach(() => vi.clearAllMocks());

describe('getStockDebtList - real backend, due date range + multi supplier (R14/R15/R16)', () => {
  it('sends date_from, date_to and repeated supplier_ids, never cutoff/supplier_id/group', async () => {
    mockedFetch.mockResolvedValue(ok(EMPTY_ENVELOPE));
    await getStockDebtList(LIST_PARAMS);

    const url = calledUrl();
    expect(url.pathname).toBe('/api/v1/project-sales/stock-debt');
    expect(url.searchParams.get('query')).toBe('SRTWB242');
    expect(url.searchParams.get('only_debt')).toBe('true');
    expect(url.searchParams.get('book')).toBe('retail');
    expect(url.searchParams.get('date_from')).toBe('2026-11-01');
    expect(url.searchParams.get('date_to')).toBe('2026-11-30');
    expect(url.searchParams.getAll('supplier_ids')).toEqual([
      'sup-guangdong', 'sup-foshan',
    ]);

    // R14/R16: the retired params must never reach the wire at all.
    expect(url.searchParams.has('cutoff')).toBe(false);
    expect(url.searchParams.has('supplier_id')).toBe(false);
    expect(url.searchParams.has('group')).toBe(false);
  });

  it('omits book from the query when it is "all" (the default, per the header)', async () => {
    mockedFetch.mockResolvedValue(ok(EMPTY_ENVELOPE));
    await getStockDebtList({ ...LIST_PARAMS, book: 'all' });
    const url = calledUrl();
    expect(url.searchParams.get('book')).toBeFalsy();
  });

  it('surfaces the server error message on a non-ok response', async () => {
    mockedFetch.mockResolvedValue({
      ok: false,
      status: 500,
      headers: { get: () => 'application/json' },
      json: async () => ({ detail: 'stock debt boom' }),
      text: async () => JSON.stringify({ detail: 'stock debt boom' }),
      clone() {
        return this;
      },
    } as unknown as Response);
    await expect(getStockDebtList(LIST_PARAMS)).rejects.toThrow(/stock debt boom/);
  });
});

describe('getStockDebtCell - real backend, due date range (AC-11/R14)', () => {
  it('passes date_from/date_to and book, never cutoff or group', async () => {
    mockedFetch.mockResolvedValue(ok({ demand: [], supply: [] }));

    await (
      getStockDebtCell as unknown as (
        productId: string,
        month: string,
        dateFrom?: string,
        dateTo?: string,
        book?: string,
      ) => Promise<unknown>
    )('prod-1', '2026-11', '2026-11-01', '2026-11-30', 'retail');

    const url = calledUrl();
    expect(url.pathname).toBe('/api/v1/project-sales/stock-debt/prod-1/cell');
    expect(url.searchParams.get('month')).toBe('2026-11');
    expect(url.searchParams.get('date_from')).toBe('2026-11-01');
    expect(url.searchParams.get('date_to')).toBe('2026-11-30');
    expect(url.searchParams.get('book')).toBe('retail');
    expect(url.searchParams.has('cutoff')).toBe(false);
    expect(url.searchParams.has('group')).toBe(false);
  });
});

describe('exportStockDebt - real backend, due date range + multi supplier (R14/R15/R16)', () => {
  it('POSTs date_from, date_to and supplier_ids: [] to /stock-debt/export, never cutoff/supplier_id/group', async () => {
    const download = {
      id: 'dl-1', kind: 'stock_debt_xlsx', status: 'pending',
      filename: 'stock-debt-24092026.xlsx', created_at: '2026-09-24T00:00:00Z', ready_at: null,
    };
    mockedFetch.mockResolvedValue(ok(download));

    const result = await exportStockDebt(EXPORT_PARAMS);

    expect(mockedFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockedFetch.mock.calls[0];
    expect(url).toBe('/api/v1/project-sales/stock-debt/export');
    expect((init as RequestInit).method).toBe('POST');
    const body = JSON.parse((init as RequestInit).body as string);
    expect(body).toMatchObject({
      query: 'SRTWB242',
      only_debt: true,
      book: 'retail',
      supplier_ids: ['sup-guangdong', 'sup-foshan'],
      date_from: '2026-11-01',
      date_to: '2026-11-30',
      split: 'supplier',
    });
    expect(body.cutoff).toBeUndefined();
    expect(body.supplier_id).toBeUndefined();
    expect(body.group).toBeUndefined();
    expect(result).toEqual(download);
  });

  it('surfaces the server error message on a non-ok response, and no row lingers', async () => {
    mockedFetch.mockResolvedValue({
      ok: false,
      status: 422,
      headers: { get: () => 'application/json' },
      json: async () => ({ detail: 'Narrow the plan first' }),
      text: async () => JSON.stringify({ detail: 'Narrow the plan first' }),
      clone() {
        return this;
      },
    } as unknown as Response);
    await expect(exportStockDebt(EXPORT_PARAMS)).rejects.toThrow(/Narrow the plan first/);
  });
});

describe('STOCK-DEBT-PROJECTION - the projected flag on the wire', () => {
  it('list: sends projected=true only while the switch is on', async () => {
    mockedFetch.mockResolvedValue(ok(EMPTY_ENVELOPE));
    await getStockDebtList({ ...LIST_PARAMS, projected: true });
    expect(calledUrl(0).searchParams.get('projected')).toBe('true');

    await getStockDebtList({ ...LIST_PARAMS, projected: false });
    expect(calledUrl(1).searchParams.has('projected')).toBe(false);
  });

  it('cell: sends projected=true only when asked', async () => {
    mockedFetch.mockResolvedValue(ok({ demand: [], supply: [] }));
    await getStockDebtCell('p1', '2026-12', '', undefined, 'all', true);
    expect(calledUrl(0).searchParams.get('projected')).toBe('true');

    await getStockDebtCell('p1', '2026-12');
    expect(calledUrl(1).searchParams.has('projected')).toBe(false);
  });

  it('export: carries projected in the body, false by default', async () => {
    mockedFetch.mockResolvedValue(ok({ id: 'dl-1' }));
    await exportStockDebt({ ...EXPORT_PARAMS, projected: true });
    await exportStockDebt(EXPORT_PARAMS);
    const body = (i: number) =>
      JSON.parse((mockedFetch.mock.calls[i][1] as RequestInit).body as string);
    expect(body(0).projected).toBe(true);
    expect(body(1).projected).toBe(false);
  });
});
