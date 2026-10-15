/**
 * T2/T3 - loading the client's sheet from the series page.
 *
 * Two claims are worth pinning here, and both are things a green "it worked" toast destroys.
 *
 * 1. **The unmatched list is on screen.** An import that silently dropped `CWC1009-RL` looks
 *    exactly like one that succeeded. Their own sheet quotes base codes the catalogue stocks
 *    only as suffixed variants, so a real load misses about a third of its rows - measured:
 *    49 of 141. If those codes are not rendered, the screen is lying about what it did.
 * 2. **Replace asks first.** Replace removes every product the new list leaves out. That is
 *    destructive and goes through an AlertDialog, never straight off the button.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toast } from '@/lib/toast';
import type {
  ProjectSeries,
  SeriesProductImportResult,
} from '../../../_shared/types/project.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), custom: vi.fn() },
}));

const importSeriesProducts = vi.fn();
const uploadSeriesProducts = vi.fn();
const getImportJobStatus = vi.fn();

vi.mock('../../../_shared/services/projectService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../_shared/services/projectService')
  >();
  return {
    ...actual,
    importSeriesProducts: (...args: unknown[]) => importSeriesProducts(...args),
    uploadSeriesProducts: (...args: unknown[]) => uploadSeriesProducts(...args),
    getImportJobStatus: (...args: unknown[]) => getImportJobStatus(...args),
  };
});

import { SeriesSheetLoader, splitCodes } from './SeriesSheetLoader';

function series(overrides: Partial<ProjectSeries> = {}): ProjectSeries {
  return {
    id: 's1',
    name: 'Sanitaryware template',
    is_active: true,
    category_ids: [],
    category_names: [],
    covered_category_count: 0,
    product_count: 0,
    product_codes: [],
    quotation_count: 0,
    ...overrides,
  };
}

function result(overrides: Partial<SeriesProductImportResult> = {}): SeriesProductImportResult {
  return {
    series_id: 's1',
    series_name: 'Sanitaryware template',
    mode: 'append',
    submitted: 153,
    unique_codes: 141,
    matched_codes: 92,
    added: 92,
    already_present: 0,
    removed: 0,
    product_count: 92,
    unmatched_codes: [],
    ...overrides,
  };
}

function renderLoader(row: ProjectSeries = series()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SeriesSheetLoader series={row} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('SeriesSheetLoader', () => {
  it('cannot be loaded with nothing to load', () => {
    renderLoader();
    expect(screen.getByRole('button', { name: 'Load' })).toBeDisabled();
  });

  it('loads pasted codes and reports what it did', async () => {
    importSeriesProducts.mockResolvedValue(result({ submitted: 2, unique_codes: 2, added: 2 }));
    renderLoader();

    fireEvent.change(screen.getByPlaceholderText(/paste product codes/i), {
      target: { value: 'CWC7601-S-RL\nCWC1009-RL' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));

    await waitFor(() => expect(importSeriesProducts).toHaveBeenCalledTimes(1));
    expect(importSeriesProducts).toHaveBeenCalledWith('s1', {
      codes: ['CWC7601-S-RL', 'CWC1009-RL'],
      mode: 'append',
    });
    expect(await screen.findByText('2 products added')).toBeInTheDocument();
  });

  it('shows every code the catalogue does not carry', async () => {
    // The shape of the client's real load: 92 of 141 matched, 49 did not.
    importSeriesProducts.mockResolvedValue(
      result({ unmatched_codes: ['CWC1009-RL', 'SRTWC8036', 'CWB 248'] }),
    );
    renderLoader();

    fireEvent.change(screen.getByPlaceholderText(/paste product codes/i), {
      target: { value: 'CWC1009-RL' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));

    expect(await screen.findByText('3 codes are not in the catalogue')).toBeInTheDocument();
    // Verbatim, so the admin can find each one in their own spreadsheet.
    expect(screen.getByText('CWC1009-RL')).toBeInTheDocument();
    expect(screen.getByText('SRTWC8036')).toBeInTheDocument();
    expect(screen.getByText('CWB 248')).toBeInTheDocument();
  });

  it('says so plainly when nothing missed', async () => {
    importSeriesProducts.mockResolvedValue(result({ unmatched_codes: [] }));
    renderLoader();

    fireEvent.change(screen.getByPlaceholderText(/paste product codes/i), {
      target: { value: 'CWC7601-S-RL' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));

    await screen.findByText('92 products added');
    expect(screen.queryByText(/not in the catalogue/i)).not.toBeInTheDocument();
  });

  it('asks before replacing a series that already names products', async () => {
    importSeriesProducts.mockResolvedValue(result({ mode: 'replace' }));
    renderLoader(series({ product_count: 92 }));

    fireEvent.change(screen.getByPlaceholderText(/paste product codes/i), {
      target: { value: 'CWC7601-S-RL' },
    });
    fireEvent.click(screen.getByRole('radio', { name: /replace the series/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));

    // Nothing has been written yet - the confirmation is the gate, not a courtesy.
    expect(importSeriesProducts).not.toHaveBeenCalled();
    expect(
      await screen.findByText(/replace the products in this series/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/92 products/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
    await waitFor(() => expect(importSeriesProducts).toHaveBeenCalledTimes(1));
  });

  it('replaces an EMPTY series without asking, because nothing can be lost', async () => {
    importSeriesProducts.mockResolvedValue(result({ mode: 'replace' }));
    renderLoader(series({ product_count: 0 }));

    fireEvent.change(screen.getByPlaceholderText(/paste product codes/i), {
      target: { value: 'CWC7601-S-RL' },
    });
    fireEvent.click(screen.getByRole('radio', { name: /replace the series/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));

    await waitFor(() => expect(importSeriesProducts).toHaveBeenCalledTimes(1));
  });
});

/**
 * The upload is QUEUED, so the screen has three states where it used to have two.
 *
 * The failure this guards against is the honest-looking one: the upload resolves in
 * milliseconds because the server has only accepted the bytes, and a loader that treated
 * that as success would flash "0 products added" over a sheet that was still being read.
 */
describe('SeriesSheetLoader, uploading a file', () => {
  function drop(fileName = 'products.xlsx') {
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['zzt'], fileName, {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    fireEvent.change(input, { target: { files: [file] } });
    return file;
  }

  it('says it is still reading, and reports NOTHING, while the job runs', async () => {
    uploadSeriesProducts.mockResolvedValue({
      job_id: 'job-1',
      series_id: 's1',
      mode: 'append',
    });
    getImportJobStatus.mockResolvedValue({
      job_id: 'job-1',
      status: 'started',
      progress: { total: 141, processed: 40, successful: 30, failed: 0, skipped: 10, percentage: 28 },
      result: null,
      error: null,
    });
    renderLoader();

    drop();
    fireEvent.click(await screen.findByRole('button', { name: 'Load' }));

    // The count is the thing the admin can check against their own spreadsheet. It appears
    // on the first poll, not on the upload, which is exactly the distinction under test.
    expect(await screen.findByText(/reading the sheet - 40 of 141 codes/i)).toBeInTheDocument();
    // No report yet. A zero here would be a lie about a sheet nobody has finished reading.
    expect(screen.queryByText(/products added/i)).not.toBeInTheDocument();
  });

  it('renders the finished report in the SAME shape a paste produces', async () => {
    uploadSeriesProducts.mockResolvedValue({
      job_id: 'job-2',
      series_id: 's1',
      mode: 'append',
    });
    getImportJobStatus.mockResolvedValue({
      job_id: 'job-2',
      status: 'completed',
      progress: { total: 141, processed: 141, successful: 92, failed: 0, skipped: 49, percentage: 100 },
      result: result({ added: 92, unmatched_codes: ['CWC1009-RL'] }),
      error: null,
    });
    renderLoader();

    drop();
    fireEvent.click(await screen.findByRole('button', { name: 'Load' }));

    expect(await screen.findByText('92 products added')).toBeInTheDocument();
    // The half of the answer that only the import can tell them, still on screen.
    expect(screen.getByText('CWC1009-RL')).toBeInTheDocument();
    expect(screen.queryByText(/reading the sheet/i)).not.toBeInTheDocument();
  });

  it('surfaces the worker’s own reason when the job dies', async () => {
    // A queued job that fails has nobody watching a request to throw at, so the poll is the
    // only place the reason can reach the person who uploaded.
    uploadSeriesProducts.mockResolvedValue({
      job_id: 'job-3',
      series_id: 's1',
      mode: 'append',
    });
    getImportJobStatus.mockResolvedValue({
      job_id: 'job-3',
      status: 'failed',
      progress: null,
      result: null,
      error: 'No product codes were found in what you sent.',
    });
    renderLoader();

    drop();
    fireEvent.click(await screen.findByRole('button', { name: 'Load' }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('No product codes were found in what you sent.'),
    );
    expect(screen.queryByText(/products added/i)).not.toBeInTheDocument();
  });
});

describe('splitCodes', () => {
  it('splits on every plausible separator at once', () => {
    // Out of Excel a paste is newline-separated; out of an email, comma-separated. Asking
    // which would be asking the user to know something about our parser.
    expect(splitCodes('A\nB, C;D\tE')).toEqual(['A', 'B', 'C', 'D', 'E']);
    expect(splitCodes('  A  \n\n  B  ')).toEqual(['A', 'B']);
    expect(splitCodes('')).toEqual([]);
  });
});

describe('SeriesSheetLoader resolved sets and families (D4)', () => {
  async function load(overrides: Record<string, unknown>) {
    importSeriesProducts.mockResolvedValue(result(overrides as Partial<SeriesProductImportResult>));
    renderLoader();
    fireEvent.change(screen.getByPlaceholderText(/paste product codes/i), {
      target: { value: 'SRTWC286' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Load' }));
    await waitFor(() => expect(importSeriesProducts).toHaveBeenCalledTimes(1));
  }

  it('shows each resolved code, whether it was a set or a family, and every member', async () => {
    await load({
      resolved: [
        { code: 'SRTWC286', via: 'family', members: ['SRTWC286-SH', 'SRTWC286-SH-150'] },
        { code: 'CWC1009-RL', via: 'set', members: ['CWC1009-SC', 'CWCX1009-RL', 'CWCY1009'] },
      ],
    });

    expect(await screen.findByText('SRTWC286')).toBeInTheDocument();
    expect(screen.getByText('CWC1009-RL')).toBeInTheDocument();
    expect(screen.getAllByText(/family/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/\bset\b/i).length).toBeGreaterThan(0);
    for (const member of [
      'SRTWC286-SH',
      'SRTWC286-SH-150',
      'CWC1009-SC',
      'CWCX1009-RL',
      'CWCY1009',
    ]) {
      expect(screen.getByText(member)).toBeInTheDocument();
    }
  });

  it('renders no resolved block when resolved is empty or absent', async () => {
    await load({ resolved: [] });
    expect(await screen.findByText(/products? added/i)).toBeInTheDocument();
    expect(screen.queryByText(/family/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\bset\b/i)).not.toBeInTheDocument();
  });
});
