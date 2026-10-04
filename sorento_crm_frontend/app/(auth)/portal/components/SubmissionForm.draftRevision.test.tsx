/**
 * Revision drafts: save-and-resume for an in-progress revision.
 *
 * Mocking pattern mirrors `SubmissionForm.revisingBanner.test.tsx`.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import type {
  PortalContact,
  PortalRevisionDraft,
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
  discardRevisionDraft,
  fetchMe,
  fetchRevisions,
  fetchSubmission,
  fetchSubmissionNeighbours,
  saveRevisionDraft,
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

function draft(over: Partial<PortalRevisionDraft> = {}): PortalRevisionDraft {
  return {
    fields: { item_description: 'Drafted description', quantity: '9' },
    reason: 'Thinking about the quantity',
    base_revision_no: 1,
    updated_at: '2026-08-20T03:00:00Z',
    stale: false,
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

/** Radix opens on pointerdown, which jsdom does not synthesize from a click. */
async function clickRevise() {
  const gear = await screen.findByRole('button', {
    name: 'Submission actions',
  });
  fireEvent.pointerDown(gear, { button: 0, pointerId: 1 });
  fireEvent.pointerUp(gear, { button: 0, pointerId: 1 });
  fireEvent.click(gear);
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Revise' }));
}

describe('SubmissionForm - resuming a saved revision draft', () => {
  it('auto-opens revise mode with the draft fields and reason prefilled, and shows the Draft saved line', async () => {
    await renderWith({ revision_draft: draft() });

    const banner = await screen.findByTestId('revising-banner');
    expect(banner).toHaveTextContent('Revising - revision 2 (not sent yet)');
    expect(banner).toHaveTextContent(/Draft saved/);

    const description = document.getElementById('item_description') as
      | HTMLInputElement
      | HTMLTextAreaElement;
    expect(description.value).toBe('Drafted description');

    const reasonBox = document.getElementById(
      'revision_reason',
    ) as HTMLTextAreaElement;
    expect(reasonBox.value).toBe('Thinking about the quantity');
  });

  it('does not auto-open revise mode when there is no draft', async () => {
    await renderWith({ revision_draft: null });

    expect(screen.queryByTestId('revising-banner')).toBeNull();
    expect(await screen.findByText('Responded')).toBeInTheDocument();
  });
});

describe('SubmissionForm - Save as draft', () => {
  it('calls saveRevisionDraft with the current fields, the reason and the base revision number', async () => {
    (saveRevisionDraft as ReturnType<typeof vi.fn>).mockResolvedValue(draft());
    await renderWith();
    await clickRevise();

    fireEvent.change(
      document.getElementById('revision_reason') as HTMLElement,
      {
        target: { value: 'Wrong quantity' },
      },
    );
    fireEvent.change(
      document.getElementById('item_description') as HTMLElement,
      {
        target: { value: 'Updated description' },
      },
    );

    fireEvent.click(screen.getByRole('button', { name: 'Save as draft' }));

    await waitFor(() => expect(saveRevisionDraft).toHaveBeenCalledTimes(1));
    const [calledKind, calledId, payload] = (
      saveRevisionDraft as ReturnType<typeof vi.fn>
    ).mock.calls[0];
    expect(calledKind).toBe('stock_inquiry');
    expect(calledId).toBe('si-1');
    expect(payload.reason).toBe('Wrong quantity');
    expect(payload.baseRevisionNo).toBe(1); // detail.revision.used
    expect(payload.fields.item_description).toBe('Updated description');

    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith('Draft saved.'),
    );
  });
});

describe('SubmissionForm - a stale draft', () => {
  it('does not auto-open revise mode and renders the stale-draft banner instead', async () => {
    await renderWith({ revision_draft: draft({ stale: true }) });

    expect(screen.queryByTestId('revising-banner')).toBeNull();
    expect(await screen.findByTestId('stale-draft-banner')).toBeInTheDocument();
  });
});

describe('SubmissionForm - discarding a draft', () => {
  it('opens the confirm dialog and, on confirm, calls discardRevisionDraft', async () => {
    (discardRevisionDraft as ReturnType<typeof vi.fn>).mockResolvedValue(
      undefined,
    );
    await renderWith({ revision_draft: draft({ stale: true }) });

    fireEvent.click(
      await screen.findByRole('button', { name: 'Discard revision' }),
    );

    expect(
      await screen.findByText('Discard this draft revision?'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));

    await waitFor(() => expect(discardRevisionDraft).toHaveBeenCalledTimes(1));
    expect(discardRevisionDraft).toHaveBeenCalledWith('stock_inquiry', 'si-1');
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith('Draft discarded.'),
    );
  });

  it('also offers Discard revision from within revise mode when a draft exists', async () => {
    (discardRevisionDraft as ReturnType<typeof vi.fn>).mockResolvedValue(
      undefined,
    );
    await renderWith({ revision_draft: draft() });

    // The draft auto-opened revise mode; the footer's Discard revision button
    // is the one INSIDE the revise-mode action row, not the stale banner's.
    fireEvent.click(screen.getByRole('button', { name: /Discard revision/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));

    await waitFor(() => expect(discardRevisionDraft).toHaveBeenCalledTimes(1));
  });
});
