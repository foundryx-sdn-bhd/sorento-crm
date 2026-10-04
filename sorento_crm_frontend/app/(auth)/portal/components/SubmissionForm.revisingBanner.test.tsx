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

async function renderResponded(over: Partial<PortalSubmissionDetail> = {}) {
  (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(detail(over));
  render(<SubmissionForm kind="stock_inquiry" submissionId="si-1" />);
  await waitForSectionLoaded();
  // The form reloads once more when /me resolves (contact-derived defaults).
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

describe('SubmissionForm - a responded stock inquiry is read-only', () => {
  it('disables the form fields and shows no Save-as-draft button', async () => {
    await renderResponded();

    const productCode = document.getElementById('product_code');
    expect(productCode).toHaveAttribute('disabled');

    expect(screen.queryByRole('button', { name: 'Save as draft' })).toBeNull();
  });

  it('renders no Submit button at all - the only door back in is Revise', async () => {
    await renderResponded();

    // Wait for the detail to resolve (the badge proves it), then assert absence.
    expect(await screen.findByText('Responded')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Submit' })).toBeNull();
  });

  it('shows the "Responded" status badge, not an editable draft badge', async () => {
    await renderResponded();

    expect(await screen.findByText('Responded')).toBeInTheDocument();
  });
});

describe('SubmissionForm - starting a revision from a responded inquiry', () => {
  it('shows the revising banner naming the next revision number, and the badge keeps showing the real status', async () => {
    await renderResponded();
    await clickRevise();

    const banner = await screen.findByTestId('revising-banner');
    expect(banner).toHaveTextContent('Revising - revision 2 (not sent yet)');

    // The pill shows the real status throughout - revising does not override it
    // to "Draft" (the "nothing sent yet" message lives in the banner instead).
    expect(screen.getByText('Responded')).toBeInTheDocument();
    expect(screen.queryByText('Draft')).toBeNull();
  });

  it('keeps Sales person disabled in revise mode, with no explanatory prose', async () => {
    await renderResponded();
    await clickRevise();

    const salesperson = document.getElementById('salesperson_contact_id');
    expect(salesperson).toHaveAttribute('disabled');
    // The disabled control is the whole message: the UI does not explain itself
    // (cursor rule - no feature explanations inside the UI).
    expect(screen.queryByText(/Locked during a revision/)).toBeNull();
  });

  it('Cancel navigates back to the portal list, same as the non-revise Cancel', async () => {
    await renderResponded();
    await clickRevise();

    expect(await screen.findByTestId('revising-banner')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(replace).toHaveBeenCalledWith(
      portalHomePath({ type: 'stock_inquiry' }),
    );
  });
});
