/**
 * Round 3 (PLAN-form-comments-09oct.md, rule 4): the comment thread leaves Details and gets its
 * own "Comments (N)" tab, right after Details, on every form kind.
 *
 * API assumed (the coder satisfies it):
 *  - `FormDetailWithSLATabs` and `FormDetailTabsWithRevisions` take an optional prop
 *    `commentsKind?: FormCommentKind` ('stock_inquiry' | 'complaint' | 'purchase_request' |
 *    'sponsorship_form'). When set, the shell PREPENDS a first extra tab labelled
 *    `Comments (N)` whose content is `<FormComments kind={commentsKind} entityId={sourceEntityId} />`.
 *  - N = comments from `listFormComments` that are not deleted placeholders and not the
 *    `kind: 'response'` entry. The count comes from `useFormCommentsQuery` (same query key as the
 *    thread), so it shows while the Details tab is active.
 *  - The pages pass it: stock-inquiries / purchase-requests / sponsorship-forms `[id]/page.tsx`
 *    via FormDetailTabsWithRevisions, complaints `[id]/page.tsx` via FormDetailWithSLATabs.
 *  - StockInquiryDetail, ComplaintDetail and PurchaseRequestDetail stop rendering `<FormComments`.
 */
import React from 'react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { listFormComments } = vi.hoisted(() => ({ listFormComments: vi.fn() }));
vi.mock('@/services/formCommentsService', () => ({
  listFormComments: (...a: unknown[]) => listFormComments(...a),
  addFormComment: vi.fn(),
  editFormComment: vi.fn(),
}));
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));
vi.mock('next-auth/react', async () => {
  const { createContext } = await import('react');
  return {
    SessionContext: createContext({
      data: { user: { name: 'Pat Buyer' } },
      status: 'authenticated',
    }),
  };
});
vi.mock('./FormSLATrackingTab', () => ({
  __esModule: true,
  default: () => <div data-testid="form-sla-tracking-tab" />,
}));
vi.mock('./FormRevisionsTab', () => ({
  __esModule: true,
  default: () => <div data-testid="form-revisions-tab" />,
}));
vi.mock('./useRevisionEnabledMap', () => ({
  useRevisionEnabledMap: () => ({
    data: { stock_inquiry: true, purchase_request: true, sponsorship_form: true },
  }),
}));
vi.mock(
  '@/app/(protected)/procurement-management/stock-inquiries/hooks/useStockInquiries',
  () => ({ useStockInquiry: () => ({ data: undefined }) }),
);
vi.mock(
  '@/app/(protected)/procurement-management/purchase-requests/hooks/usePurchaseRequests',
  () => ({ usePurchaseRequest: () => ({ data: undefined }) }),
);

import FormDetailWithSLATabs from './FormDetailWithSLATabs';
import FormDetailTabsWithRevisions from './FormDetailTabsWithRevisions';

const base = {
  kind: 'comment' as const,
  parentId: null,
  authorName: 'Pat Buyer',
  authorKind: 'staff' as const,
  postedByName: null,
  editedAt: null,
  deleted: false,
  canEdit: true,
  canDelete: true,
  canReply: true,
  createdAt: '2026-10-09T01:00:00',
  attachments: [],
};
const THREAD = [
  { ...base, id: 'response', kind: 'response' as const, body: 'purchasing reply' },
  { ...base, id: 'c1', body: 'first comment' },
  { ...base, id: 'c2', body: 'second', parentId: 'c1', authorKind: 'submitter' as const },
  { ...base, id: 'c3', body: '', deleted: true },
];

function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

const tabNames = () => screen.getAllByRole('tab').map((t) => t.textContent);

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  listFormComments.mockResolvedValue(THREAD);
});

describe('Comments tab on the complaint shell (FormDetailWithSLATabs)', () => {
  const mount = () =>
    wrap(
      <FormDetailWithSLATabs
        sourceEntityType="complaint"
        sourceEntityId="c-1"
        // @ts-expect-error assumed new prop, see header
        commentsKind="complaint"
        extraTabs={[{ value: 'fulfilment', label: 'Fulfilment DOs', content: <div>dos</div> }]}
      >
        <div>Details content</div>
      </FormDetailWithSLATabs>,
    );

  it('puts "Comments (N)" right after Details, ahead of the other extra tabs and SLA Tracking', async () => {
    mount();
    expect(await screen.findByRole('tab', { name: 'Comments (2)' })).toBeInTheDocument();
    expect(tabNames()).toEqual(['Details', 'Comments (2)', 'Fulfilment DOs', 'SLA Tracking']);
  });

  it('counts live comments and replies only: not the purchasing reply, not deleted placeholders', async () => {
    mount();
    expect(await screen.findByRole('tab', { name: 'Comments (2)' })).toBeInTheDocument();
    expect(listFormComments).toHaveBeenCalledWith('complaint', 'c-1');
  });

  it('shows the thread only after selecting the tab, never inside Details', async () => {
    mount();
    await screen.findByRole('tab', { name: 'Comments (2)' });
    expect(screen.getByText('Details content')).toBeInTheDocument();
    expect(screen.queryByText('first comment')).toBeNull();

    await userEvent.click(screen.getByRole('tab', { name: 'Comments (2)' }));

    expect(await screen.findByText('first comment')).toBeInTheDocument();
    expect(screen.queryByText('Details content')).toBeNull();
  });

  it('reads "Comments (0)" for an empty thread', async () => {
    listFormComments.mockResolvedValue([]);
    mount();
    expect(await screen.findByRole('tab', { name: 'Comments (0)' })).toBeInTheDocument();
  });
});

describe.each([
  ['stock_inquiry', 'stock_inquiry'],
  ['purchase_request', 'purchase_request'],
  ['sponsorship_form', 'sponsorship_form'],
] as const)('Comments tab on the %s shell (FormDetailTabsWithRevisions)', (kind, revisionsKind) => {
  it('sits right after Details and before Revisions and SLA Tracking', async () => {
    wrap(
      <FormDetailTabsWithRevisions
        sourceEntityType={kind}
        sourceEntityId="f-1"
        revisionsKind={revisionsKind}
        // @ts-expect-error assumed new prop, see header
        commentsKind={kind}
      >
        <div>Details content</div>
      </FormDetailTabsWithRevisions>,
    );
    expect(await screen.findByRole('tab', { name: 'Comments (2)' })).toBeInTheDocument();
    expect(tabNames()).toEqual(['Details', 'Comments (2)', 'Revisions', 'SLA Tracking']);
    expect(listFormComments).toHaveBeenCalledWith(kind, 'f-1');

    await userEvent.click(screen.getByRole('tab', { name: 'Comments (2)' }));
    expect(await screen.findByText('first comment')).toBeInTheDocument();
  });
});

describe('composition: pages opt in, detail components give the thread up', () => {
  const root = process.cwd();
  const read = (p: string) => readFileSync(join(root, p), 'utf8');

  it.each([
    ['app/(protected)/procurement-management/stock-inquiries/[id]/page.tsx', 'stock_inquiry'],
    ['app/(protected)/procurement-management/purchase-requests/[id]/page.tsx', 'purchase_request'],
    ['app/(protected)/procurement-management/sponsorship-forms/[id]/page.tsx', 'sponsorship_form'],
    ['app/(protected)/complaint-management/complaints/[id]/page.tsx', 'complaint'],
  ])('%s passes commentsKind', (file, kind) => {
    expect(new RegExp(`commentsKind=["{']+${kind}`).test(read(file))).toBe(true);
  });

  it.each([
    'app/(protected)/procurement-management/stock-inquiries/components/StockInquiryDetail.tsx',
    'app/(protected)/complaint-management/complaints/components/ComplaintDetail.tsx',
    'app/(protected)/procurement-management/purchase-requests/components/PurchaseRequestDetail.tsx',
  ])('%s no longer renders the thread inside Details', (file) => {
    expect(read(file).includes('<FormComments')).toBe(false);
  });

  it('keeps the form-table "Comment / reply by purchasing" row in Details', () => {
    expect(
      read('app/(protected)/procurement-management/stock-inquiries/components/StockInquiryDetail.tsx').includes(
        'Comment / reply by purchasing',
      ),
    ).toBe(true);
  });
});
