/**
 * PLAN-portal-price-tag-journey-r8, Round 3 (R3-5, AC-R8).
 *
 * The legacy kinds' view page (stock inquiry, purchase request, sponsorship
 * form, complaint) carries exactly ONE gear, holding Duplicate and Revise
 * (when the policy allows it); the standalone `ReviseAction variant="menu"`
 * gear that used to sit below the tabs is gone, and the revision status text
 * (or the blocked reason) sits beside the form number instead.
 *
 * Mocking pattern mirrors `SubmissionForm.draftRevision.test.tsx`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import type {
  PortalContact,
  PortalRevisionEntry,
  PortalRevisionPolicy,
  PortalSubmissionDetail,
} from '../lib/portal-client';

const push = vi.fn();
const replace = vi.fn();
const router = { push, replace };
vi.mock('next/navigation', () => ({
  useRouter: () => router,
}));

vi.mock('@/lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

vi.mock('../lib/portal-client', async (importOriginal) => {
  const original =
    await importOriginal<typeof import('../lib/portal-client')>();
  return {
    ...original,
    fetchMe: vi.fn(),
    fetchSubmission: vi.fn(),
    fetchSubmissionNeighbours: vi.fn(),
    fetchRevisions: vi.fn(),
    reviseSubmission: vi.fn(),
    saveDraft: vi.fn(),
    submitDraft: vi.fn(),
    deleteDraftSubmission: vi.fn(),
    uploadAttachment: vi.fn(),
    deleteAttachment: vi.fn(),
    lookupSet: vi.fn().mockResolvedValue({ options: [], defaultValue: null }),
    saveRevisionDraft: vi.fn(),
    discardRevisionDraft: vi.fn(),
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

function policy(over: Partial<PortalRevisionPolicy> = {}): PortalRevisionPolicy {
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

function detail(over: Partial<PortalSubmissionDetail> = {}): PortalSubmissionDetail {
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
    revision_draft: null,
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
  await waitFor(() =>
    expect(
      (fetchSubmission as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBeGreaterThanOrEqual(2),
  );
}

describe('SubmissionForm - exactly one gear, holding Duplicate and Revise (R3-5, AC-R8)', () => {
  it('renders exactly one actions gear, not two', async () => {
    await renderWith();

    // R3-5: exactly one gear, labelled "Submission actions" (aa3ff8e21
    // settled on the generic label - draftRevision/revisingBanner's own
    // suites already pin it, and a kind-specific label was this test's OWN
    // mistake, not the implementation's).
    expect(
      screen.getAllByRole('button', { name: 'Submission actions' }),
    ).toHaveLength(1);
  });

  it('the revision status sits in the SAME line as the form number, not a second gear row', async () => {
    await renderWith();

    const reference = await screen.findByText('SI-26-0184');
    const line = reference.closest('span');
    // Same containing line as the reference - not a separate row of its own
    // the way the old below-the-tabs gear rendered it.
    expect(line?.textContent).toContain('SI-26-0184');
    expect(line?.textContent).toMatch(/revisions left/);
  });
});
