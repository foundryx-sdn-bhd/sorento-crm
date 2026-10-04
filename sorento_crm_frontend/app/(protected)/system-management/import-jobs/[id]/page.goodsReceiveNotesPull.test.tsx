/**
 * Import job detail page - the goods-receive-notes pull job type (lane GRN-PULL-CRM, AC-GP-60).
 * The DO test's harness (`page.deliveryOrdersPull.test.tsx`): a job whose `job_type` is
 * `autocount_grn_pull` renders `AutocountPullReview`, none of the ordinary result cards, the
 * label "AutoCount GRN Pull", and a Back button to the Goods Receipt Notes list.
 */
import React, { Suspense } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => '/system-management/import-jobs/job-grn',
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
  AutocountPullReview: ({ jobId }: { jobId: string }) => (
    <div data-testid="autocount-pull-review" data-job-id={jobId} />
  ),
}));

import ImportJobDetailPage from './page';

const PARAMS = Promise.resolve({ id: 'job-grn' });

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return (
    <QueryClientProvider client={client}>
      <Suspense fallback={null}>{ui}</Suspense>
    </QueryClientProvider>
  );
}

function grnPullJob(overrides: Record<string, unknown> = {}) {
  return {
    id: 'job-grn',
    job_id: 'job-grn',
    job_type: 'autocount_grn_pull',
    status: 'finished',
    user_id: 'me',
    total_rows: 2,
    processed_rows: 2,
    successful_rows: 2,
    failed_rows: 0,
    skipped_rows: 0,
    result: null,
    error: null,
    created_at: new Date('2026-09-30T00:00:00Z'),
    ...overrides,
  };
}

beforeEach(async () => {
  cleanup();
  getImportJob.mockReset();
  getImportJobs.mockReset();
  getImportJobSourceUrl.mockReset();
  getImportJobStatus.mockReset();
  usePull.mockReset();
  getImportJobs.mockResolvedValue({ data: [], pagination: { total: 0, page: 1 }, empty: true });
  await PARAMS;
});

describe('AC-GP-60: autocount_grn_pull is a pull job', () => {
  it('renders AutocountPullReview, the GRN label, and none of the ordinary result cards', async () => {
    getImportJob.mockResolvedValue(grnPullJob());
    usePull.mockReturnValue({
      data: { job_id: 'job-grn', entity: 'goods_receive_notes', phase: 'review' },
    });

    await act(async () => {
      render(wrap(<ImportJobDetailPage params={PARAMS} />));
    });

    expect(await screen.findByTestId('autocount-pull-review')).toHaveAttribute('data-job-id', 'job-grn');
    expect(screen.getAllByText('AutoCount GRN Pull').length).toBeGreaterThan(0);
    expect(screen.queryByText('Results')).not.toBeInTheDocument();
    expect(screen.queryByTestId('outcome-breakdown')).not.toBeInTheDocument();
    expect(screen.queryByTestId('rows-card')).not.toBeInTheDocument();
  });

  it('reads "Back to Goods Receipt Notes" and links to /procurement-management/grn', async () => {
    getImportJob.mockResolvedValue(grnPullJob());
    usePull.mockReturnValue({
      data: { job_id: 'job-grn', entity: 'goods_receive_notes', phase: 'review' },
    });

    await act(async () => {
      render(wrap(<ImportJobDetailPage params={PARAMS} />));
    });

    await screen.findByTestId('autocount-pull-review');
    const link = screen.getByRole('link', { name: /Back to Goods Receipt Notes/ });
    expect(link).toHaveAttribute('href', '/procurement-management/grn');
  });
});
