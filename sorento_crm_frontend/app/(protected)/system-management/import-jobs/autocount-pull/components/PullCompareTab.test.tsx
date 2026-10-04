/**
 * PullCompareTab - Phase 3 fix round red tests (captain's fix-round brief, V-1).
 *
 * `useComparePull` is mocked at the hook module (component -> hook, per layering) so the
 * mutation resolves synchronously with whatever fixture a test hands it, in the REAL backend
 * response shape (`app/services/autocount_pull_compare.py` + the route's own `summary`):
 * `{ summary: { filename, compared_at, total, matched, different, only_in_excel, only_in_pull,
 * qty_total_excel, qty_total_pull }, differences: [{ item_code, field, excel, pull }],
 * only_in_excel: [...codes], only_in_pull: [...codes] }`.
 *
 * `lib/excel-utils` is mocked too so the differences-download assertions read the call the
 * component made rather than a real generated file.
 *
 * Two real defects this file pins, both already present in `PullCompareTab.tsx`:
 *  - `summaryHeadline`'s `ok` check is `matched === total && differences.length === 0` only -
 *    it never looks at `only_in_excel` / `only_in_pull`, so a batch missing rows entirely (no
 *    per-field difference, just an absence) still gets called "100% match" - V-1b.
 *  - the differences grid reads `row.original.your_excel` / `row.original.autocount_pull`,
 *    keys the real backend has never sent (it sends `excel` / `pull`) - so every cell in those
 *    two columns is blank in production - V-1c/V-1d.
 *  - `handleDownloadDifferences` names the exported file `autocount-pull-${jobId}-differences.
 *    xlsx` - a UUID in a filename the user sees, the cursor rule's own "no UUIDs in the FE UI" -
 *    V-1d.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const useComparePull = vi.fn();
vi.mock('../hooks/useAutocountPull', () => ({
  useComparePull: (...a: unknown[]) => useComparePull(...a),
  // DO-COMPARE-SIM: the tab also reads the saved compare mapping; these tests do not care.
  useCompareMappings: () => ({ data: undefined, isLoading: false }),
  useSaveCompareMapping: () => ({ mutate: vi.fn(), isPending: false }),
}));

const generateExcelFile = vi.fn();
const parseExcelFile = vi.fn();
vi.mock('@/lib/excel-utils', () => ({
  generateExcelFile: (...a: unknown[]) => generateExcelFile(...a),
  parseExcelFile: (...a: unknown[]) => parseExcelFile(...a),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

// DataGrid fetches column preferences under `useListingColumnPreferences`; unmocked, jsdom
// never answers it, so the grid stays on its loading skeleton forever and no row renders
// (`project_datagrid_jsdom_rows_mockable.md` - it is not a jsdom limitation, just this fetch).
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

import { PullCompareTab } from './PullCompareTab';

const JOB_ID = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';

function xlsxFile(name = 'check.xlsx') {
  return new File(['x'], name, {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
}

/** The hidden input inside the shared FileDropzone. */
function fileInput(): HTMLInputElement {
  return screen.getByLabelText('Excel file to compare') as HTMLInputElement;
}

async function dropFile(rows: Record<string, unknown>[] = [{ 'Item Code': 'X' }]) {
  parseExcelFile.mockResolvedValueOnce(rows);
  fireEvent.change(fileInput(), { target: { files: [xlsxFile()] } });
  await waitFor(() => expect(parseExcelFile).toHaveBeenCalled());
}

/** The differences DataGrid needs a QueryClient (`useListingColumnPreferences`) even
 *  though this tab does not itself use react-query for that grid's data. */
function renderTab(entity: 'products' | 'stock_balances' | 'delivery_orders' = 'products') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    React.createElement(
      QueryClientProvider,
      { client },
      React.createElement(PullCompareTab, { jobId: JOB_ID, entity }),
    ),
  );
}

function mutateReturning(result: unknown) {
  return {
    mutate: (
      _vars: unknown,
      opts?: { onSuccess?: (data: unknown) => void },
    ) => {
      opts?.onSuccess?.(result);
    },
    isPending: false,
  };
}

beforeEach(() => {
  useComparePull.mockReset();
  generateExcelFile.mockReset();
  parseExcelFile.mockReset();
});

describe('PullCompareTab - headline (captain ruling, Phase 3 fix round)', () => {
  it('V-1a: all matched and both only_in counts 0 -> "100% match", the counts, and the file name', async () => {
    useComparePull.mockReturnValue(
      mutateReturning({
        summary: {
          filename: 'check.xlsx', compared_at: '2026-09-20T00:00:00Z',
          total: 10, matched: 10, different: 0, only_in_excel: 0, only_in_pull: 0,
        },
        differences: [], only_in_excel: [], only_in_pull: [],
      }),
    );

    renderTab();
    await dropFile();

    expect(screen.getByText(/100% match/i)).toBeInTheDocument();
    // The FileDropzone's own picked-file chip ALSO renders the bare filename, so pin the
    // whole composed sentence (unique to the headline) rather than the filename alone.
    expect(screen.getByText(/10 of 10 items agree with check\.xlsx/)).toBeInTheDocument();
  });

  it('V-1b: matched == total but only_in_pull > 0 -> NOT a 100% match headline', async () => {
    useComparePull.mockReturnValue(
      mutateReturning({
        summary: {
          filename: 'partial.xlsx', compared_at: '2026-09-20T00:00:00Z',
          total: 9, matched: 9, different: 0, only_in_excel: 0, only_in_pull: 1,
        },
        differences: [], only_in_excel: [], only_in_pull: [`${JOB_ID}-ONLY-PULL`],
      }),
    );

    renderTab();
    await dropFile();

    expect(screen.queryByText(/100% match/i)).not.toBeInTheDocument();
  });
});

describe('PullCompareTab - differences grid (captain ruling, Phase 3 fix round, V-1c)', () => {
  it('shows the excel and pull VALUES for a differing field', async () => {
    useComparePull.mockReturnValue(
      mutateReturning({
        summary: {
          filename: 'diff.xlsx', compared_at: '2026-09-20T00:00:00Z',
          total: 1, matched: 0, different: 1, only_in_excel: 0, only_in_pull: 0,
        },
        differences: [
          { item_code: 'SRT-1', field: 'price', excel: '10.00', pull: '12.00' },
        ],
        only_in_excel: [], only_in_pull: [],
      }),
    );

    renderTab();
    await dropFile();

    expect(await screen.findByText('10.00')).toBeInTheDocument();
    expect(screen.getByText('12.00')).toBeInTheDocument();
  });
});

describe('PullCompareTab - differences export (captain ruling, Phase 3 fix round, V-1d)', () => {
  it('exports rows carrying the excel/pull values, filename without the job id', async () => {
    useComparePull.mockReturnValue(
      mutateReturning({
        summary: {
          filename: 'diff.xlsx', compared_at: '2026-09-20T00:00:00Z',
          total: 1, matched: 0, different: 1, only_in_excel: 0, only_in_pull: 0,
        },
        differences: [
          { item_code: 'SRT-1', field: 'price', excel: '10.00', pull: '12.00' },
        ],
        only_in_excel: [], only_in_pull: [],
      }),
    );

    renderTab();
    await dropFile();
    await screen.findByText('10.00');

    fireEvent.click(screen.getByRole('button', { name: /Download differences/i }));

    expect(generateExcelFile).toHaveBeenCalledTimes(1);
    const [rows, cols, filename] = generateExcelFile.mock.calls[0];

    expect(filename).not.toContain(JOB_ID);

    const colKeys: string[] = (cols as Array<{ key: string }>).map((c) => c.key);
    // The columns must read keys the REAL backend row actually carries (`excel`/`pull`),
    // not `your_excel`/`autocount_pull` - otherwise every exported cell is blank.
    expect(colKeys).toContain('excel');
    expect(colKeys).toContain('pull');
    expect((rows as Array<Record<string, unknown>>)[0].excel).toBe('10.00');
    expect((rows as Array<Record<string, unknown>>)[0].pull).toBe('12.00');
  });
});

describe('PullCompareTab - booleans and human field labels (CT-1, CT-5)', () => {
  it('an is_active difference shows Active / Inactive in both cells and their titles, never true/false', async () => {
    useComparePull.mockReturnValue(
      mutateReturning({
        summary: {
          filename: 'active.xlsx', compared_at: '2026-09-22T00:00:00Z',
          total: 1, matched: 0, different: 1, only_in_excel: 0, only_in_pull: 0,
        },
        differences: [
          { item_code: 'SRT-1', field: 'is_active', excel: true, pull: false },
        ],
        only_in_excel: [], only_in_pull: [],
      }),
    );

    renderTab();
    await dropFile();

    // The Difference column reads "Is Active" (captain ruling N7) - it never collides with
    // the Active/Inactive VALUE cells next to it.
    expect(await screen.findByText('Is Active')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByText('Inactive')).toBeInTheDocument();
    expect(screen.getByTitle('Active')).toBeInTheDocument();
    expect(screen.getByTitle('Inactive')).toBeInTheDocument();
    expect(screen.queryByText('true')).not.toBeInTheDocument();
    expect(screen.queryByText('false')).not.toBeInTheDocument();
  });

  it('the Difference column shows human labels for every known field', async () => {
    useComparePull.mockReturnValue(
      mutateReturning({
        summary: {
          filename: 'fields.xlsx', compared_at: '2026-09-22T00:00:00Z',
          total: 1, matched: 0, different: 1, only_in_excel: 0, only_in_pull: 0,
        },
        differences: [
          { item_code: 'SRT-1', field: 'item_group', excel: 'A', pull: 'B' },
        ],
        only_in_excel: [], only_in_pull: [],
      }),
    );

    renderTab();
    await dropFile();

    expect(await screen.findByText('Item Group')).toBeInTheDocument();
    expect(screen.queryByText('item_group')).not.toBeInTheDocument();
  });
});

describe('PullCompareTab - headline names both counts (CT-2)', () => {
  it('N items differ (M differences) when an item differs on more than one field', async () => {
    useComparePull.mockReturnValue(
      mutateReturning({
        summary: {
          filename: 'multi.xlsx', compared_at: '2026-09-22T00:00:00Z',
          total: 2, matched: 0, different: 2, only_in_excel: 0, only_in_pull: 0,
        },
        differences: [
          { item_code: 'SRT-1', field: 'price', excel: '1', pull: '2' },
          { item_code: 'SRT-1', field: 'item_group', excel: 'A', pull: 'B' },
          { item_code: 'SRT-2', field: 'price', excel: '3', pull: '4' },
        ],
        only_in_excel: [], only_in_pull: [],
      }),
    );

    renderTab();
    await dropFile();

    expect(await screen.findByText(/2 items differ \(3 differences\)/)).toBeInTheDocument();
  });

  it('just "1 item differs" (singular) when a single item differs on exactly one field', async () => {
    useComparePull.mockReturnValue(
      mutateReturning({
        summary: {
          filename: 'single.xlsx', compared_at: '2026-09-22T00:00:00Z',
          total: 1, matched: 0, different: 1, only_in_excel: 0, only_in_pull: 0,
        },
        differences: [
          { item_code: 'SRT-1', field: 'price', excel: '1', pull: '2' },
        ],
        only_in_excel: [], only_in_pull: [],
      }),
    );

    renderTab();
    await dropFile();

    expect(await screen.findByText(/^1 item differs\.?$/)).toBeInTheDocument();
    expect(screen.queryByText(/differences\)/)).not.toBeInTheDocument();
  });

  it('just "N items differ" (plural) when every differing item differs on exactly one field', async () => {
    useComparePull.mockReturnValue(
      mutateReturning({
        summary: {
          filename: 'plural.xlsx', compared_at: '2026-09-22T00:00:00Z',
          total: 2, matched: 0, different: 2, only_in_excel: 0, only_in_pull: 0,
        },
        differences: [
          { item_code: 'SRT-1', field: 'price', excel: '1', pull: '2' },
          { item_code: 'SRT-2', field: 'price', excel: '3', pull: '4' },
        ],
        only_in_excel: [], only_in_pull: [],
      }),
    );

    renderTab();
    await dropFile();

    expect(await screen.findByText(/^2 items differ\.?$/)).toBeInTheDocument();
    expect(screen.queryByText(/differences\)/)).not.toBeInTheDocument();
  });

  it('review S3: drops the "items differ" clause entirely when there are no per-field differences, keeping just the only-in clauses', async () => {
    useComparePull.mockReturnValue(
      mutateReturning({
        summary: {
          filename: 'onlyin-only.xlsx', compared_at: '2026-09-22T00:00:00Z',
          total: 0, matched: 0, different: 0, only_in_excel: 1, only_in_pull: 0,
        },
        differences: [],
        only_in_excel: ['SRT-9'],
        only_in_pull: [],
      }),
    );

    renderTab();
    await dropFile();

    expect(await screen.findByText(/^1 only in your Excel\.$/)).toBeInTheDocument();
    expect(screen.queryByText(/item.*differ/)).not.toBeInTheDocument();
  });
});

describe('PullCompareTab - only-in rows in the grid (CT-3, CT-4)', () => {
  it('lists only_in_excel and only_in_pull codes as grid rows with the right labels, and recordCount covers all of them', async () => {
    useComparePull.mockReturnValue(
      mutateReturning({
        summary: {
          filename: 'onlyin.xlsx', compared_at: '2026-09-22T00:00:00Z',
          total: 1, matched: 0, different: 1, only_in_excel: 1, only_in_pull: 1,
        },
        differences: [
          { item_code: 'SRT-1', field: 'price', excel: '1', pull: '2' },
        ],
        only_in_excel: ['SRT-2'],
        only_in_pull: ['SRT-3'],
      }),
    );

    renderTab();
    await dropFile();

    expect(await screen.findByText('SRT-2')).toBeInTheDocument();
    expect(screen.getByText('Only in your Excel')).toBeInTheDocument();
    expect(screen.getByText('SRT-3')).toBeInTheDocument();
    expect(screen.getByText('Only in AutoCount')).toBeInTheDocument();
    // Present/Missing appear twice each (once per only-in row's excel/pull cell).
    expect(screen.getAllByText('Present')).toHaveLength(2);
    expect(screen.getAllByText('Missing')).toHaveLength(2);
  });

  it('splits a stock code|location only-in label into Item Code and Location columns', async () => {
    useComparePull.mockReturnValue(
      mutateReturning({
        summary: {
          filename: 'stock-onlyin.xlsx', compared_at: '2026-09-22T00:00:00Z',
          total: 0, matched: 0, different: 0, only_in_excel: 1, only_in_pull: 0,
        },
        differences: [],
        only_in_excel: ['SRT-9|MAIN-WH'],
        only_in_pull: [],
      }),
    );

    renderTab('stock_balances');
    await dropFile();

    expect(await screen.findByText('SRT-9')).toBeInTheDocument();
    expect(screen.getByText('MAIN-WH')).toBeInTheDocument();
  });

  it('the download carries the same combined + formatted rows the grid shows', async () => {
    useComparePull.mockReturnValue(
      mutateReturning({
        summary: {
          filename: 'download.xlsx', compared_at: '2026-09-22T00:00:00Z',
          total: 1, matched: 0, different: 1, only_in_excel: 1, only_in_pull: 0,
        },
        differences: [
          { item_code: 'SRT-1', field: 'is_active', excel: true, pull: false },
        ],
        only_in_excel: ['SRT-2'],
        only_in_pull: [],
      }),
    );

    renderTab();
    await dropFile();
    await screen.findByText('SRT-2');

    fireEvent.click(screen.getByRole('button', { name: /Download differences/i }));

    expect(generateExcelFile).toHaveBeenCalledTimes(1);
    const [rows] = generateExcelFile.mock.calls[0];
    const typedRows = rows as Array<Record<string, unknown>>;
    expect(typedRows).toHaveLength(2);
    expect(typedRows[0]).toMatchObject({ item_code: 'SRT-1', field: 'Is Active', excel: 'Active', pull: 'Inactive' });
    expect(typedRows[1]).toMatchObject({
      item_code: 'SRT-2', field: 'Only in your Excel', excel: 'Present', pull: 'Missing',
    });
  });
});

describe('PullCompareTab - delivery orders, two files (AC-DP-42, owner decision 30 Sep)', () => {
  function doResult(source: 'lines' | 'headers', overrides: Record<string, unknown> = {}) {
    return {
      summary: {
        filename: source === 'lines' ? 'Order Listing 2026-09.xlsm and Order Tracking 2026-09.xlsm' : 'Order Tracking 2026-09.xlsm',
        compared_at: source === 'lines' ? '2026-09-30T00:01:00Z' : '2026-09-30T00:00:00Z',
        total: 2, matched: 1, different: 1, only_in_excel: 1, only_in_pull: 0,
      },
      source,
      window: { fromDay: '2026-09-01', toDay: '2026-09-30' },
      ignored_outside_window: 61,
      rows_in_window: 2214,
      differences: source === 'lines'
        ? [{ item_code: 'ZZAC-P1', doc_no: 'ZZDO-0001', location: 'ZZAC-WH1', field: 'qty', excel: 11, pull: 10 }]
        : [{ item_code: '', doc_no: 'ZZDO-0131', location: '', field: 'cancel', excel: true, pull: false }],
      only_in_excel: source === 'lines' ? ['ZZDO-0009|ZZAC-P2|ZZAC-WH1'] : ['ZZDO-0142'],
      only_in_pull: [],
      ...overrides,
    };
  }

  it('shows two dropzones, the window line, and one grid with Doc No, Location and Source columns', async () => {
    const posted: unknown[] = [];
    useComparePull.mockReturnValue({
      mutate: (vars: { source?: string }, opts?: { onSuccess?: (data: unknown) => void }) => {
        posted.push(vars);
        opts?.onSuccess?.(doResult(vars.source === 'headers' ? 'headers' : 'lines'));
      },
      isPending: false,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      React.createElement(
        QueryClientProvider,
        { client },
        React.createElement(PullCompareTab, {
          jobId: JOB_ID, entity: 'delivery_orders', window: { fromDay: '2026-09-01', toDay: '2026-09-30' },
        }),
      ),
    );

    expect(screen.getByLabelText('Order Listing sheet to compare')).toBeInTheDocument();
    expect(screen.getByLabelText('Order Tracking sheet to compare')).toBeInTheDocument();
    expect(screen.queryByLabelText('Excel file to compare')).not.toBeInTheDocument();
    expect(screen.getByText(/Compared inside the pulled window only, 01\/09\/2026 to 30\/09\/2026/)).toBeInTheDocument();

    parseExcelFile.mockResolvedValueOnce([{ 'Doc No': 'ZZDO-0001', 'Item Code': 'ZZAC-P1', Location: 'ZZAC-WH1', Qty: 11 }]);
    fireEvent.change(screen.getByLabelText('Order Listing sheet to compare'), { target: { files: [xlsxFile('Order Listing 2026-09.xlsm')] } });
    await waitFor(() => expect(parseExcelFile).toHaveBeenCalledTimes(1));
    parseExcelFile.mockResolvedValueOnce([{ 'Doc. No.': 'ZZDO-0131', Cancel: 'Y' }]);
    fireEvent.change(screen.getByLabelText('Order Tracking sheet to compare'), { target: { files: [xlsxFile('Order Tracking 2026-09.xlsm')] } });
    await waitFor(() => expect(parseExcelFile).toHaveBeenCalledTimes(2));

    expect(posted.map((v) => (v as { source?: string }).source)).toEqual(['lines', 'headers']);
    expect(screen.getAllByText(/2,214 lines in the window, 61 outside it ignored/).length).toBe(1);

    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    expect(headers.indexOf('Doc No')).toBeGreaterThanOrEqual(0);
    expect(headers.indexOf('Doc No')).toBeLessThan(headers.indexOf('Item Code'));
    expect(headers).toContain('Location');
    expect(headers).toContain('Source');
    expect(screen.getByText('ZZDO-0001')).toBeInTheDocument();
    expect(screen.getByText('ZZDO-0131')).toBeInTheDocument();
    expect(screen.getByText('ZZDO-0142')).toBeInTheDocument();
    expect(screen.getAllByText('Lines').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Headers').length).toBeGreaterThan(0);
    expect(screen.getByText('Cancel')).toBeInTheDocument();
    expect(screen.getByText('Yes')).toBeInTheDocument();
    // One headline over both files, the advisory sentence included (owner Q4, advisory default).
    expect(screen.getByText(/Confirm applies the AutoCount pull as it is/)).toBeInTheDocument();
  });
});
