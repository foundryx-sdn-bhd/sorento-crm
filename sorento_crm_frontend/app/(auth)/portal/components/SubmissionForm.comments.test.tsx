/**
 * A `responded` stock inquiry is read-only on the plain-edit path (portal
 * revision UX): the office is mid-conversation with the salesperson, so the
 * only door back in is Revise. Cancelling a revision navigates back to the
 * portal list, same as the non-revise Cancel - unsaved edits are not kept,
 * but a draft already saved server-side stays saved.
 *
 * Mocking pattern mirrors `SubmissionRevise.test.tsx`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import type {
  PortalContact,
  PortalRevisionEntry,
  PortalRevisionPolicy,
  PortalSubmissionDetail,
} from '../lib/portal-client';
import { portalHomePath } from '../lib/portal-paths';

const push = vi.fn();
const replace = vi.fn();
const router = { push, replace };
vi.mock('next/navigation', () => ({
  useRouter: () => router,
}));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('@/lib/toast', () => ({
  toast: {
    error: (...args: unknown[]) => toastError(...args),
    success: (...args: unknown[]) => toastSuccess(...args),
  },
}));

vi.mock('../lib/portal-client', async (importOriginal) => {
  const original =
    await importOriginal<typeof import('../lib/portal-client')>();
  return {
    ...original,
    fetchMe: vi.fn(),
    fetchSubmission: vi.fn(),
    fetchSubmissionNeighbours: vi.fn(),
    fetchSubmissionComments: vi.fn().mockResolvedValue([]),
    fetchRevisions: vi.fn(),
    reviseSubmission: vi.fn(),
    saveDraft: vi.fn(),
    submitDraft: vi.fn(),
    deleteDraftSubmission: vi.fn(),
    uploadAttachment: vi.fn(),
    deleteAttachment: vi.fn(),
    lookupSet: vi.fn().mockResolvedValue({ options: [], defaultValue: null }),
  };
});

vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  __esModule: true,
  default: () => null,
}));

import {
  fetchMe,
  fetchRevisions,
  fetchSubmission,
  fetchSubmissionComments,
  fetchSubmissionNeighbours,
} from '../lib/portal-client';
import { SubmissionForm } from './SubmissionForm';
import { waitForSectionLoaded } from '@/test-utils';

const CONTACT: PortalContact = {
  contact_id: 'contact-1',
  space_id: 'space-1',
  name: 'CONTACT X Lee',
  phone_number: '60123456789',
  expires_at: '2026-09-01T00:00:00Z',
};

function policy(
  over: Partial<PortalRevisionPolicy> = {},
): PortalRevisionPolicy {
  return {
    enabled: true,
    allowed: true,
    used: 1,
    max: 3,
    remaining: 2,
    blocked_reason: null,
    ...over,
  };
}

function detail(
  over: Partial<PortalSubmissionDetail> = {},
): PortalSubmissionDetail {
  return {
    id: 'si-1',
    kind: 'stock_inquiry',
    title: 'Stock inquiry',
    reference: 'SI-26-0184',
    status: 'responded',
    is_editable: false,
    is_draft: false,
    created_at: '2026-07-20T00:00:00Z',
    revision_no: 1,
    attachments: [],
    revision: policy(),
    product_code: 'ABC-123',
    item_description: 'Panel',
    quantity: '5',
    delivery_date: '',
    project_customer: '',
    project_name: '',
    salesperson_contact_id: 'contact-1',
    salesperson: 'CONTACT X Lee',
    remark: '',
    additional_remark: '',
    ...over,
  } as PortalSubmissionDetail;
}

const ORIGINAL: PortalRevisionEntry = {
  id: 'rev-0',
  version_no: 0,
  revision_no: 0,
  kind: 'original',
  label: 'Original',
  reason: null,
  submitted_at: '2026-07-20T02:00:00',
  submitted_by: 'CONTACT X Lee',
  is_reconstructed: false,
  snapshot: {},
  attachments: [],
  invalidated: null,
  voided_stage_code: null,
  voided_assignee_name: null,
  changes: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  (fetchMe as ReturnType<typeof vi.fn>).mockResolvedValue(CONTACT);
  (fetchSubmissionNeighbours as ReturnType<typeof vi.fn>).mockResolvedValue({
    prev_id: null,
    next_id: null,
    position: 1,
    total: 1,
  });
  (fetchRevisions as ReturnType<typeof vi.fn>).mockResolvedValue([ORIGINAL]);
  window.history.replaceState({}, '', '/portal/stock_inquiry/si-1');
});

async function renderWith(over: Partial<PortalSubmissionDetail> = {}) {
  (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(detail(over));
  render(<SubmissionForm kind="stock_inquiry" submissionId="si-1" />);
  await waitForSectionLoaded();
}

// Round 3 (PLAN-form-comments-09oct.md rule 5): the thread lives in a "Comments (N)" tab next to
// Details / Revisions; Details wears the green "Comment / reply by purchasing" banner again with a
// "Reply in Comments" link that switches to the Comments tab. N counts live comments and replies
// only (not the purchasing reply entry, not deleted placeholders) and shows while Details is
// active. Replaced the round 2 "thread above the form in Details" cases, which contradict this.
const REPLY = {
  id: 'response',
  kind: 'response',
  parentId: null,
  body: 'brand?',
  authorName: 'Pat Purchasing',
  authorKind: 'staff',
  postedByName: null,
  createdAt: '2026-10-02T08:30:00',
  editedAt: null,
  deleted: false,
  canEdit: false,
  canDelete: false,
  canReply: false,
  attachments: [],
};
const mkComment = (id: string, over: Record<string, unknown> = {}) => ({
  ...REPLY,
  id,
  kind: 'comment',
  body: `body ${id}`,
  authorName: 'CONTACT X Lee',
  authorKind: 'submitter',
  ...over,
});

const mockThread = (items: unknown[]) =>
  (fetchSubmissionComments as ReturnType<typeof vi.fn>).mockResolvedValue(
    items,
  );

describe('SubmissionForm - comments tab', () => {
  it('adds a "Comments (N)" tab next to Details and Revisions, counting live comments only', async () => {
    mockThread([
      REPLY,
      mkComment('c1'),
      mkComment('c2', { parentId: 'c1' }),
      mkComment('c3', { deleted: true, body: '' }),
    ]);
    await renderWith({ purchasing_response: 'brand?' });
    expect(
      await screen.findByRole('tab', { name: 'Comments (2)' }),
    ).toBeInTheDocument();
    const names = screen.getAllByRole('tab').map((t) => t.textContent);
    expect(names).toContain('Details');
    expect(names).toContain('Revisions');
    expect(fetchSubmissionComments).toHaveBeenCalledWith('stock_inquiry', 'si-1');
  });

  it('keeps the thread out of Details and shows it, with its composer, in the Comments tab', async () => {
    mockThread([REPLY, mkComment('c1')]);
    await renderWith({ purchasing_response: 'brand?' });
    await screen.findByRole('tab', { name: 'Comments (1)' });
    expect(screen.queryByRole('region', { name: 'Comments' })).toBeNull();

    fireEvent.click(screen.getByRole('tab', { name: 'Comments (1)' }));

    expect(
      await screen.findByRole('region', { name: 'Comments' }),
    ).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Write a comment')).toBeInTheDocument();
  });

  it('starts the thread with the purchasing reply entry, then the comments oldest first', async () => {
    mockThread([REPLY, mkComment('c1')]);
    await renderWith({ purchasing_response: 'brand?' });
    fireEvent.click(await screen.findByRole('tab', { name: 'Comments (1)' }));
    const reply = await screen.findByText('brand?');
    const first = await screen.findByText('body c1');
    expect(
      reply.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('shows the green purchasing banner in Details with a Reply in Comments link that opens the tab', async () => {
    mockThread([REPLY]);
    await renderWith({ purchasing_response: 'brand?' });
    expect(screen.getByText('Comment / reply by purchasing')).toBeInTheDocument();
    expect(screen.getByText('brand?')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Reply in Comments'));

    expect(
      await screen.findByRole('region', { name: 'Comments' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /^Comments/ })).toHaveAttribute(
      'data-state',
      'active',
    );
    expect(screen.queryByText('Comment / reply by purchasing')).toBeNull();
  });

  it('shows no banner and no link in Details before any purchasing reply', async () => {
    mockThread([]);
    await renderWith({ purchasing_response: null });
    await screen.findByRole('tab', { name: 'Comments (0)' });
    expect(screen.queryByText('Comment / reply by purchasing')).toBeNull();
    expect(screen.queryByText('Reply in Comments')).toBeNull();
  });

  it('has no Comments tab and fetches nothing on a draft', async () => {
    await renderWith({ status: 'draft', is_draft: true, is_editable: true });
    await screen.findByText('Draft');
    expect(screen.queryByRole('tab', { name: /^Comments/ })).toBeNull();
    expect(fetchSubmissionComments).not.toHaveBeenCalled();
  });

  it('keeps the thread but hides the composer on a voided inquiry', async () => {
    mockThread([]);
    await renderWith({ status: 'voided' });
    fireEvent.click(await screen.findByRole('tab', { name: /^Comments/ }));
    expect(
      await screen.findByRole('region', { name: 'Comments' }),
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('Write a comment')).toBeNull();
  });
});
