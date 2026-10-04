/**
 * Import job detail page - the delivery-orders APPLY job (lane DO-APPLY-PROGRESS, AC-6 of
 * documentation/plans/autocount/do-apply-progress-acceptance-criteria.md).
 *
 * Reported live: a 6,487-document apply sat on Total 0 / Processed 0 for its whole run, then
 * finished with nothing on the page saying what it did. The backend now publishes progress;
 * this pins the page side: the Total reads the polled total while the job runs (the job query
 * itself is only refetched on focus), the job is re-read once the poll says it finished, and
 * a finished DO apply lists the apply's own counts in Results.
 *
 * Same harness as `page.deliveryOrdersPull.test.tsx`.
 */
import React, { Suspense } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => '/system-management/import-jobs/job-apply',
}));

vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const getImportJob = vi.fn();
const getImportJobs = vi.fn();
const getImportJobSourceUrl = vi.fn();
const getImportJobStatus = vi.fn();
vi.mock('../services/importJobService', () => ({
  getImportJob: (...a: unknown[]) => getImportJob(...a),
  getImportJobs: (...a: unknown[]) => getImportJobs(...a),
  getImportJobSourceUrl: (...a: unknown[]) => getImportJobSourceUrl(...a),
  getImportJobStatus: (...a: unknown[]) => getImportJobStatus(...a),
}));

vi.mock('../hooks/useImportJobs', async () => {
  const actual = await vi.importActual<typeof import('../hooks/useImportJobs')>('../hooks/useImportJobs');
  return {
    ...actual,
    useCancelImportJob: () => ({ mutate: vi.fn(), isPending: false }),
  };
});

vi.mock('../components/OutcomeBreakdownCard', () => ({
  OutcomeBreakdownCard: () => <div data-testid="outcome-breakdown" />,
}));
vi.mock('../components/ImportJobRowsCard', () => ({
  ImportJobRowsCard: () => <div data-testid="rows-card" />,
}));
vi.mock('../components/PlanningChangeOutcomeCard', () => ({
  PlanningChangeOutcomeCard: () => <div data-testid="planning-change" />,
}));

const usePull = vi.fn();
vi.mock('../autocount-pull/hooks/useAutocountPull', () => ({
  usePull: (...a: unknown[]) => usePull(...a),
}));
vi.mock('../autocount-pull/components/AutocountPullReview', () => ({
  AutocountPullReview: () => <div data-testid="autocount-pull-review" />,
}));

import ImportJobDetailPage from './page';

const PARAMS = Promise.resolve({ id: 'job-apply' });

const FINAL_COUNTS = {
  total: 6487,
  created: 6301,
  updated: 17,
  adopted: 142,
  unchanged: 11,
  failed: 7,
  retryable: 9,
  lines_deleted: 23,
  with_warnings: 0,
};

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return (
    <QueryClientProvider client={client}>
      <Suspense fallback={null}>{ui}</Suspense>
    </QueryClientProvider>
  );
}

function applyJob(overrides: Record<string, unknown> = {}) {
  return {
    id: 'job-apply',
    job_id: 'job-apply',
    job_type: 'autocount_delivery_orders_apply',
    status: 'finished',
    user_id: 'me',
    total_rows: 6487,
    processed_rows: 6487,
    successful_rows: 6460,
    failed_rows: 16,
    skipped_rows: 11,
    result: null,
    error: null,
    created_at: new Date('2026-10-01T00:00:00Z'),
    job_metadata: {
      autocount_apply: {
        entity: 'delivery_orders',
        snapshot_id: 'snap-1',
        pull_job_id: 'pull-1',
        counts: FINAL_COUNTS,
      },
    },
    ...overrides,
  };
}

function resultsCard(): HTMLElement {
  const title = screen.getByText('Results');
  const card = title.closest('[data-slot="card"]') ?? title.parentElement?.parentElement;
  if (!card) throw new Error('Results card not found');
  return card as HTMLElement;
}

function valueOf(card: HTMLElement, label: string): string {
  const labelEl = within(card).getByText(label);
  return labelEl.nextElementSibling?.textContent ?? '';
}

beforeEach(async () => {
  cleanup();
  getImportJob.mockReset();
  getImportJobs.mockReset();
  getImportJobSourceUrl.mockReset();
  getImportJobStatus.mockReset();
  usePull.mockReset();
  usePull.mockReturnValue({ data: undefined });
  getImportJobs.mockResolvedValue({ data: [], pagination: { total: 0, page: 1 }, empty: true });
  await PARAMS;
});

describe('AC-6: a finished DO apply lists its own counts in Results', () => {
  it('shows Created / Adopted / Updated / Unchanged / Failed / Retryable / Lines deleted', async () => {
    getImportJob.mockResolvedValue(applyJob());
    getImportJobStatus.mockResolvedValue({ job_id: 'job-apply', status: 'finished', progress: null });

    await act(async () => {
      render(wrap(<ImportJobDetailPage params={PARAMS} />));
    });

    await screen.findByText('Results');
    const card = resultsCard();
    expect(valueOf(card, 'Created')).toBe('6301');
    expect(valueOf(card, 'Adopted')).toBe('142');
    expect(valueOf(card, 'Updated')).toBe('17');
    expect(valueOf(card, 'Unchanged')).toBe('11');
    expect(valueOf(card, 'Failed')).toBe('7');
    expect(valueOf(card, 'Retryable')).toBe('9');
    expect(valueOf(card, 'Lines deleted')).toBe('23');
  });

  it('keeps the generic Results for any other job type', async () => {
    getImportJob.mockResolvedValue(
      applyJob({ job_type: 'stock_import', job_metadata: null, successful_rows: 5, failed_rows: 1 }),
    );
    getImportJobStatus.mockResolvedValue({ job_id: 'job-apply', status: 'finished', progress: null });

    await act(async () => {
      render(wrap(<ImportJobDetailPage params={PARAMS} />));
    });

    await screen.findByText('Results');
    const card = resultsCard();
    expect(valueOf(card, 'Successful')).toBe('5');
    expect(within(card).queryByText('Adopted')).not.toBeInTheDocument();
    expect(within(card).queryByText('Lines deleted')).not.toBeInTheDocument();
  });
});

describe('a running apply shows the polled progress, then the final counts', () => {
  it('reads Total from the poll while the job query still says 0', async () => {
    getImportJob.mockResolvedValue(
      applyJob({
        status: 'started',
        total_rows: 0,
        processed_rows: 0,
        successful_rows: 0,
        failed_rows: 0,
        skipped_rows: 0,
        job_metadata: { autocount_apply: { entity: 'delivery_orders' } },
      }),
    );
    getImportJobStatus.mockResolvedValue({
      job_id: 'job-apply',
      status: 'started',
      progress: { total: 6487, processed: 1200, successful: 1190, failed: 4, skipped: 6, percentage: 18 },
    });

    await act(async () => {
      render(wrap(<ImportJobDetailPage params={PARAMS} />));
    });

    expect(await screen.findByText('1200 / 6487')).toBeInTheDocument();
    expect(screen.getByText('Total Rows').nextElementSibling?.textContent).toBe('6487');
  });

  it('re-reads the job once the poll reports it finished, so the counts appear', async () => {
    getImportJob
      .mockResolvedValueOnce(
        applyJob({ status: 'started', job_metadata: { autocount_apply: { entity: 'delivery_orders' } } }),
      )
      .mockResolvedValue(applyJob());
    getImportJobStatus.mockResolvedValue({
      job_id: 'job-apply',
      status: 'finished',
      progress: { total: 6487, processed: 6487, successful: 6460, failed: 16, skipped: 11, percentage: 100 },
    });

    await act(async () => {
      render(wrap(<ImportJobDetailPage params={PARAMS} />));
    });

    await waitFor(() => expect(getImportJob).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(valueOf(resultsCard(), 'Lines deleted')).toBe('23'));
  });

  it('does not re-read a job the page already loaded as finished', async () => {
    getImportJob.mockResolvedValue(applyJob());
    getImportJobStatus.mockResolvedValue({
      job_id: 'job-apply',
      status: 'finished',
      progress: { total: 6487, processed: 6487, successful: 6460, failed: 16, skipped: 11, percentage: 100 },
    });

    await act(async () => {
      render(wrap(<ImportJobDetailPage params={PARAMS} />));
    });

    await screen.findByText('Results');
    await waitFor(() => expect(getImportJobStatus).toHaveBeenCalled());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(getImportJob).toHaveBeenCalledTimes(1);
  });
});
