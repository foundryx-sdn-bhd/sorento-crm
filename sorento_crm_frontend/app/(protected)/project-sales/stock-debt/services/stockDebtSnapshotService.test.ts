/**
 * STOCK-DEBT-SNAPSHOT: the wire contract of `stockDebtSnapshotService` (UAC AC-1, AC-9, AC-10,
 * AC-12, AC-13, AC-16). Layering: every list goes through `buildDataGridParams` (page is
 * 1-based, `limit`, `query`), every failure surfaces the SERVER message via `extractApiError`.
 * There is no delete function: Delete is a deferred action (AC-15), not a service call.
 *
 * Param names (`pageIndex`, `pageSize`, `query`, `decision`) follow `stockDebtService`.
 * `createSnapshot` takes the board's camelCase export filters (the object Export sends,
 * minus `split`) and puts them on the wire as the snake_case `StockDebtExportIn` fields.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

import { apiFetch } from '@/lib/api';
import {
  createSnapshot,
  exportSnapshot,
  getSnapshot,
  getSnapshotRowLines,
  listSnapshotRows,
  listSnapshots,
  setSnapshotDecision,
} from './stockDebtSnapshotService';

const mockedFetch = vi.mocked(apiFetch);
const BASE = '/api/v1/project-sales/stock-debt/snapshots';

function ok(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}

function failure(status: number, message: string): Response {
  return {
    ok: false,
    status,
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
function calledInit(): RequestInit {
  return (mockedFetch.mock.calls[0][1] ?? {}) as RequestInit;
}
function sentBody(): Record<string, unknown> {
  return JSON.parse(String(calledInit().body));
}

beforeEach(() => vi.clearAllMocks());

describe('createSnapshot', () => {
  const FILTERS = {
    query: 'SRT',
    onlyDebt: true,
    book: 'project',
    supplierIds: ['sup-guangdong'],
    locations: ['wh-brw'],
    locationsMode: 'include',
    dateFrom: '2023-01-01',
    dateTo: '2027-05-31',
    showAux: true,
  };

  it('POSTs { name, filters } with the export fields in snake_case and no split', async () => {
    mockedFetch.mockResolvedValue(ok({ id: 's-1' }));

    await createSnapshot({ name: 'Stock Debt 15/10/2026', filters: FILTERS } as never);

    expect(calledUrl().pathname).toBe(BASE);
    expect(calledInit().method).toBe('POST');
    const body = sentBody();
    expect(body.name).toBe('Stock Debt 15/10/2026');
    expect(body.filters).toMatchObject({
      query: 'SRT',
      only_debt: true,
      book: 'project',
      supplier_ids: ['sup-guangdong'],
      location: ['wh-brw'],
      date_from: '2023-01-01',
      date_to: '2027-05-31',
      show_aux: true,
    });
    expect(body.filters).not.toHaveProperty('split');
    expect(body.filters).not.toHaveProperty('onlyDebt');
  });

  it('sends exclude_location instead of location in "all except" mode', async () => {
    mockedFetch.mockResolvedValue(ok({ id: 's-1' }));
    await createSnapshot({
      name: 'n',
      filters: { ...FILTERS, locationsMode: 'exclude' },
    } as never);
    const filters = sentBody().filters as Record<string, unknown>;
    expect(filters.exclude_location).toEqual(['wh-brw']);
    expect(filters).not.toHaveProperty('location');
  });

  it('returns the created header', async () => {
    mockedFetch.mockResolvedValue(ok({ id: 's-1', name: 'n', row_count: 550 }));
    await expect(createSnapshot({ name: 'n', filters: FILTERS } as never)).resolves.toMatchObject({
      id: 's-1',
      row_count: 550,
    });
  });

  it('surfaces the server message ("Narrow the filters first") on a 422', async () => {
    mockedFetch.mockResolvedValue(failure(422, 'Narrow the filters first'));
    await expect(createSnapshot({ name: 'n', filters: FILTERS } as never)).rejects.toThrow(
      'Narrow the filters first',
    );
  });
});

describe('listSnapshots', () => {
  it('GETs the list with 1-based page, limit and the name query', async () => {
    mockedFetch.mockResolvedValue(ok({ data: [], pagination: { total: 0, page: 1, limit: 25 } }));

    await listSnapshots({ pageIndex: 1, pageSize: 25, query: 'Stock Debt' } as never);

    const url = calledUrl();
    expect(url.pathname).toBe(BASE);
    expect(url.searchParams.get('page')).toBe('2');
    expect(url.searchParams.get('limit')).toBe('25');
    expect(url.searchParams.get('query')).toBe('Stock Debt');
  });

  it('omits query when empty', async () => {
    mockedFetch.mockResolvedValue(ok({ data: [], pagination: { total: 0, page: 1, limit: 25 } }));
    await listSnapshots({ pageIndex: 0, pageSize: 25, query: '' } as never);
    expect(calledUrl().searchParams.has('query')).toBe(false);
  });

  it('surfaces the server message on failure', async () => {
    mockedFetch.mockResolvedValue(failure(500, 'boom'));
    await expect(listSnapshots({ pageIndex: 0, pageSize: 25, query: '' } as never)).rejects.toThrow('boom');
  });
});

describe('getSnapshot', () => {
  it('GETs one header by id', async () => {
    mockedFetch.mockResolvedValue(ok({ id: 's-1', name: 'n' }));
    await expect(getSnapshot('s-1')).resolves.toMatchObject({ id: 's-1' });
    expect(calledUrl().pathname).toBe(`${BASE}/s-1`);
  });

  it('surfaces the server message on a 404', async () => {
    mockedFetch.mockResolvedValue(failure(404, 'Snapshot not found'));
    await expect(getSnapshot('s-9')).rejects.toThrow('Snapshot not found');
  });
});

describe('listSnapshotRows', () => {
  it('GETs the stored rows with paging, product query and decision', async () => {
    mockedFetch.mockResolvedValue(ok({ data: [], pagination: { total: 0, page: 1, limit: 25 } }));

    await listSnapshotRows('s-1', {
      pageIndex: 0,
      pageSize: 25,
      query: 'SRTWT',
      decision: 'decided',
    } as never);

    const url = calledUrl();
    expect(url.pathname).toBe(`${BASE}/s-1/rows`);
    expect(url.searchParams.get('page')).toBe('1');
    expect(url.searchParams.get('limit')).toBe('25');
    expect(url.searchParams.get('query')).toBe('SRTWT');
    expect(url.searchParams.get('decision')).toBe('decided');
  });

  it('omits decision and query when unset', async () => {
    mockedFetch.mockResolvedValue(ok({ data: [], pagination: { total: 0, page: 1, limit: 25 } }));
    await listSnapshotRows('s-1', { pageIndex: 0, pageSize: 25, query: '', decision: '' } as never);
    const url = calledUrl();
    expect(url.searchParams.has('query')).toBe(false);
    expect(url.searchParams.has('decision')).toBe(false);
  });
});

describe('getSnapshotRowLines', () => {
  it('GETs one bucket when a month is given', async () => {
    mockedFetch.mockResolvedValue(ok({ lines: [] }));
    await getSnapshotRowLines('s-1', 'r-1', '2027-01');
    const url = calledUrl();
    expect(url.pathname).toBe(`${BASE}/s-1/rows/r-1/lines`);
    expect(url.searchParams.get('month')).toBe('2027-01');
  });

  it('GETs every bucket (no month param) when none is given', async () => {
    mockedFetch.mockResolvedValue(ok({ lines: [] }));
    await getSnapshotRowLines('s-1', 'r-1');
    expect(calledUrl().searchParams.has('month')).toBe(false);
  });

  it('addresses the tba bucket', async () => {
    mockedFetch.mockResolvedValue(ok({ lines: [] }));
    await getSnapshotRowLines('s-1', 'r-1', 'tba');
    expect(calledUrl().searchParams.get('month')).toBe('tba');
  });
});

describe('setSnapshotDecision', () => {
  it('PUTs { decision, warehouse_id, note } to the row decision route', async () => {
    mockedFetch.mockResolvedValue(ok({ row_id: 'r-1', decision: 'reserve' }));

    await setSnapshotDecision('s-1', 'r-1', {
      decision: 'reserve',
      warehouse_id: 'wh-brw',
      note: 'Reserve all 223 from BRW',
    });

    expect(calledUrl().pathname).toBe(`${BASE}/s-1/rows/r-1/decision`);
    expect(calledInit().method).toBe('PUT');
    expect(sentBody()).toEqual({
      decision: 'reserve',
      warehouse_id: 'wh-brw',
      note: 'Reserve all 223 from BRW',
    });
  });

  it('sends nulls to clear', async () => {
    mockedFetch.mockResolvedValue(ok({ row_id: 'r-1', decision: null }));
    await setSnapshotDecision('s-1', 'r-1', { decision: null, warehouse_id: null, note: null });
    expect(sentBody()).toEqual({ decision: null, warehouse_id: null, note: null });
  });

  it('surfaces the server message on a 422', async () => {
    mockedFetch.mockResolvedValue(failure(422, 'Pick a site pool'));
    await expect(
      setSnapshotDecision('s-1', 'r-1', { decision: 'reserve', warehouse_id: 'x', note: null }),
    ).rejects.toThrow('Pick a site pool');
  });
});

describe('exportSnapshot', () => {
  it('POSTs { split, show_aux } to the snapshot export route', async () => {
    mockedFetch.mockResolvedValue(ok({ id: 'd-1', status: 'pending', kind: 'stock_debt_snapshot_xlsx' }));

    await exportSnapshot('s-1', { split: 'supplier', show_aux: true });

    expect(calledUrl().pathname).toBe(`${BASE}/s-1/export`);
    expect(calledInit().method).toBe('POST');
    expect(sentBody()).toEqual({ split: 'supplier', show_aux: true });
  });

  it('surfaces the 409 in-flight message', async () => {
    mockedFetch.mockResolvedValue(failure(409, 'An export is already running'));
    await expect(exportSnapshot('s-1', { split: 'none', show_aux: false })).rejects.toThrow(
      'An export is already running',
    );
  });
});
