/**
 * S2 - stockDebtService, against the contract in its own header.
 *
 * Pins the `getStockDebtCell` paths, the params the board sends (through
 * `buildDataGridParams`, never a hand-built query string), and that a failure surfaces the
 * SERVER's message - which is what the page's error state renders beside its Retry
 * (AC-S2-12).
 *
 * `getStockDebtList`'s own Phase-1-mock suite that used to live here is retired: Phase 2
 * flipped `USE_STOCK_DEBT_FILTER_MOCKS` to `false` and deleted the mock branch, so
 * `stockDebtService.real.test.ts` is the one pinning the AC-1 to AC-18 wire contract now
 * (PLAN-stock-debt-filters-totals-export-24sep.md).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

import { apiFetch } from '@/lib/api';
import { getStockDebtCell } from './stockDebtService';

const mockedFetch = vi.mocked(apiFetch);

function okResponse(body: unknown): Response {
  return { ok: true, json: async () => body } as Response;
}

function failure(message: string): Response {
  return {
    ok: false,
    status: 500,
    headers: { get: () => 'application/json' },
    json: async () => ({ detail: message }),
    text: async () => '',
    clone() {
      return this;
    },
  } as unknown as Response;
}

function calledUrl(): URL {
  return new URL(mockedFetch.mock.calls[0][0] as string, 'http://localhost');
}

beforeEach(() => vi.clearAllMocks());

describe('getStockDebtCell', () => {
  it('addresses the product and the month key', async () => {
    mockedFetch.mockResolvedValue(okResponse({ demand: [], supply: [] }));

    await getStockDebtCell('p1', '2026-10');

    const url = calledUrl();
    expect(url.pathname).toBe('/api/v1/project-sales/stock-debt/p1/cell');
    expect(url.searchParams.get('month')).toBe('2026-10');
  });

  it('addresses the three buckets that are not months', async () => {
    mockedFetch.mockResolvedValue(okResponse({ demand: [], supply: [] }));

    await getStockDebtCell('p1', 'undated');
    expect(calledUrl().searchParams.get('month')).toBe('undated');

    vi.clearAllMocks();
    mockedFetch.mockResolvedValue(okResponse({ demand: [], supply: [] }));
    await getStockDebtCell('p1', 'unlocated');
    expect(calledUrl().searchParams.get('month')).toBe('unlocated');
  });

  it('carries the due date range and book, so the drill foots with the cell', async () => {
    // R16 retires `group` from the wire entirely - the drill now foots with the cell
    // via the board's due date range and book, never an ownership group.
    mockedFetch.mockResolvedValue(okResponse({ demand: [], supply: [] }));

    await getStockDebtCell('p1', '2026-10', '2026-10-01', '2026-10-31', 'retail');

    const url = calledUrl();
    expect(url.searchParams.get('date_from')).toBe('2026-10-01');
    expect(url.searchParams.get('date_to')).toBe('2026-10-31');
    expect(url.searchParams.get('book')).toBe('retail');
    expect(url.searchParams.has('group')).toBe(false);
  });

  it('omits the group when the board is showing the whole book', async () => {
    mockedFetch.mockResolvedValue(okResponse({ demand: [], supply: [] }));

    await getStockDebtCell('p1', '2026-10', '');

    expect(calledUrl().searchParams.get('group')).toBeNull();
  });

  it('surfaces the server message', async () => {
    mockedFetch.mockResolvedValue(failure('That cell could not be read'));

    await expect(getStockDebtCell('p1', 'tba')).rejects.toThrow(
      'That cell could not be read',
    );
  });
});

describe('exportStockDebt show_aux (STOCKDEBT-AUX-COLS AC-12)', () => {
  const base = {
    query: '', onlyDebt: true, book: 'all' as const, supplierIds: [], dateFrom: '',
    dateTo: '', split: 'none' as const, showAux: true,
  };

  async function sentBody(extra: Record<string, unknown>) {
    mockedFetch.mockResolvedValue(okResponse({ id: 'd1' }));
    const { exportStockDebt } = await import('./stockDebtService');
    await exportStockDebt({ ...base, ...extra } as Parameters<typeof exportStockDebt>[0]);
    return JSON.parse((mockedFetch.mock.calls[0][1] as RequestInit).body as string);
  }

  it('sends show_aux true when the board shows the columns', async () => {
    expect((await sentBody({ showAux: true })).show_aux).toBe(true);
  });

  it('sends show_aux false when the board hides them', async () => {
    expect((await sentBody({ showAux: false })).show_aux).toBe(false);
  });
});

describe('Location (repeatable warehouse id) on the wire (AC-R8)', () => {
  const listBase = {
    pageIndex: 0, pageSize: 25, query: '', onlyDebt: true, book: 'all' as const,
    supplierIds: [], dateFrom: '', dateTo: '',
  };
  const exportBase = {
    query: '', onlyDebt: true, book: 'all' as const, supplierIds: [], dateFrom: '',
    dateTo: '', split: 'none' as const, showAux: false,
  };

  async function exportBody(extra: Record<string, unknown>) {
    mockedFetch.mockResolvedValue(okResponse({ id: 'd1' }));
    const { exportStockDebt } = await import('./stockDebtService');
    await exportStockDebt({ ...exportBase, ...extra } as Parameters<typeof exportStockDebt>[0]);
    return JSON.parse((mockedFetch.mock.calls[0][1] as RequestInit).body as string);
  }

  it('list sends one location per id under Only and never group', async () => {
    mockedFetch.mockResolvedValue(okResponse({ data: [] }));
    const { getStockDebtList } = await import('./stockDebtService');

    await getStockDebtList({ ...listBase, locations: ['w-1', 'w-2'], locationsMode: 'include' });

    const url = calledUrl();
    expect(url.searchParams.getAll('location')).toEqual(['w-1', 'w-2']);
    expect(url.searchParams.has('exclude_location')).toBe(false);
    expect(url.searchParams.has('group')).toBe(false);
    expect(url.searchParams.has('exclude_group')).toBe(false);
  });

  it('list sends exclude_location per id under All except', async () => {
    mockedFetch.mockResolvedValue(okResponse({ data: [] }));
    const { getStockDebtList } = await import('./stockDebtService');

    await getStockDebtList({ ...listBase, locations: ['w-1', 'w-2'], locationsMode: 'exclude' });

    const url = calledUrl();
    expect(url.searchParams.getAll('exclude_location')).toEqual(['w-1', 'w-2']);
    expect(url.searchParams.has('location')).toBe(false);
    expect(url.searchParams.has('exclude_group')).toBe(false);
  });

  it('list sends neither when none is picked', async () => {
    mockedFetch.mockResolvedValue(okResponse({ data: [] }));
    const { getStockDebtList } = await import('./stockDebtService');

    await getStockDebtList({ ...listBase, locations: [], locationsMode: 'include' });

    const url = calledUrl();
    expect(url.searchParams.has('location')).toBe(false);
    expect(url.searchParams.has('exclude_location')).toBe(false);
  });

  it('cell takes locations and the mode as trailing params', async () => {
    mockedFetch.mockResolvedValue(okResponse({ demand: [], supply: [] }));
    await getStockDebtCell('p1', '2026-10', '', '', 'all', false, ['w-1', 'w-2'], 'include');
    let url = calledUrl();
    expect(url.searchParams.getAll('location')).toEqual(['w-1', 'w-2']);
    expect(url.searchParams.has('group')).toBe(false);

    mockedFetch.mockClear();
    mockedFetch.mockResolvedValue(okResponse({ demand: [], supply: [] }));
    await getStockDebtCell('p1', '2026-10', '', '', 'all', false, ['w-1'], 'exclude');
    url = calledUrl();
    expect(url.searchParams.getAll('exclude_location')).toEqual(['w-1']);
    expect(url.searchParams.has('location')).toBe(false);
    expect(url.searchParams.has('exclude_group')).toBe(false);
  });

  it('export body carries location under Only', async () => {
    const body = await exportBody({ locations: ['w-1', 'w-2'], locationsMode: 'include' });
    expect(body.location).toEqual(['w-1', 'w-2']);
    expect(body.exclude_location === undefined || body.exclude_location.length === 0).toBe(true);
    expect(body.group).toBeUndefined();
    expect(body.exclude_group).toBeUndefined();
  });

  it('export body carries exclude_location under All except', async () => {
    const body = await exportBody({ locations: ['w-1'], locationsMode: 'exclude' });
    expect(body.exclude_location).toEqual(['w-1']);
    expect(body.location === undefined || body.location.length === 0).toBe(true);
    expect(body.group).toBeUndefined();
    expect(body.exclude_group).toBeUndefined();
  });
});
