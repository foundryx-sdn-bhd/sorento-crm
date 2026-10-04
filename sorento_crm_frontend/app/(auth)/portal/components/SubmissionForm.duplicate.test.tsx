/**
 * PLAN-portal-price-tag-journey-r8, D-D1 (AC-D2, AC-D4).
 *
 * Duplicate on a legacy kind (stock inquiry, sponsorship, purchase request,
 * complaint) opens `/<type>/new?from=<id>` in NEW mode: `SubmissionForm`
 * reads `?from=` off `window.location` (not the mocked `useSearchParams`,
 * which this file leaves untouched) and runs `applySourceToForm` - the same
 * prefill block a revision-draft resume uses. Scoped to `kind="stock_inquiry"`
 * (`SubmissionForm.test.tsx` / `.duplicateCreate.test.tsx`'s own scoping
 * reasoning: the simplest field set, no lookup-select / do-multi-filter
 * widgets, and the code path under test is shared verbatim across all four
 * legacy kinds).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import type { PortalContact, PortalSubmissionDetail } from '../lib/portal-client';

const push = vi.fn();
const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace }),
}));

vi.mock('../lib/portal-client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../lib/portal-client')>();
  return {
    ...original,
    fetchMe: vi.fn(),
    fetchSubmission: vi.fn(),
    fetchSubmissionNeighbours: vi.fn(),
    saveDraft: vi.fn(),
    submitDraft: vi.fn(),
    deleteDraftSubmission: vi.fn(),
    uploadAttachment: vi.fn(),
    deleteAttachment: vi.fn(),
    lookupSet: vi.fn().mockResolvedValue({ options: [], defaultValue: null }),
  };
});

const toasts = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), info: vi.fn() }));
vi.mock('@/lib/toast', () => ({ toast: toasts }));

import { fetchMe, fetchSubmission, saveDraft } from '../lib/portal-client';
import { SubmissionForm } from './SubmissionForm';

vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  __esModule: true,
  default: () => null,
}));

const CONTACT: PortalContact = {
  contact_id: 'contact-1',
  space_id: 'space-1',
  name: 'CONTACT X Lee',
  phone_number: '60123456789',
  expires_at: '2026-08-01T00:00:00Z',
};

const SOURCE: PortalSubmissionDetail = {
  id: 'si-source-1',
  kind: 'stock_inquiry',
  title: 'Stock inquiry',
  document_number: 'SI-26-0100',
  reference: 'SI-26-0100',
  status: 'answered',
  is_editable: false,
  is_draft: false,
  created_at: '2026-07-01T00:00:00Z',
  product_code: 'ZZT-PROD-9',
  item_description: 'ZZT Kitchen sink, matte black',
  quantity: '3',
  project_customer: 'ZZT Dealer Sdn Bhd',
  project_name: 'ZZT Renovation Project',
  remark: 'Copy this note too',
  attachments: [
    {
      link_id: 'l-1',
      attachment_id: 'a-1',
      filename: 'source-photo.jpg',
      size: 1024,
      url: 'https://cdn.example.com/source-photo.jpg',
      uploader_kind: 'contact',
      can_unlink: true,
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  window.history.pushState({}, '', '/stock_inquiry/new');
  (fetchMe as Mock).mockResolvedValue(CONTACT);
});

// This grid layout has no `<label for>` association at all (a plain `<div>`
// caption sits beside the input's `<div>`, per the print-styled
// `PRODUCT INQUIRY FORM` layout) - so a field is read by value, or by id,
// never by `getByLabelText`.
function projectNameField(): HTMLTextAreaElement {
  return document.getElementById('project_name') as HTMLTextAreaElement;
}

describe('SubmissionForm - Duplicate prefill from ?from= (AC-D2)', () => {
  it('copies every field from the source, leaves attachments empty, and saves nothing until Save/Submit', async () => {
    window.history.pushState({}, '', '/stock_inquiry/new?from=si-source-1');
    (fetchSubmission as Mock).mockResolvedValue(SOURCE);

    render(<SubmissionForm kind="stock_inquiry" />);
    await screen.findByDisplayValue('ZZT Kitchen sink, matte black');

    expect(fetchSubmission).toHaveBeenCalledWith('stock_inquiry', 'si-source-1');
    expect(screen.getByDisplayValue('ZZT Renovation Project')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Copy this note too')).toBeInTheDocument();

    // Attachments stay empty (D-D1: duplicating attachments is out of scope,
    // trigger named in the plan) - the source's file is not carried over.
    expect(screen.queryByText('source-photo.jpg')).toBeNull();

    // Nothing is saved until Save draft / Submit is tapped.
    expect(saveDraft).not.toHaveBeenCalled();
  });
});

describe('SubmissionForm - a source the contact cannot copy (AC-D4)', () => {
  it('shows "Could not copy that submission." and opens an empty form', async () => {
    window.history.pushState({}, '', '/stock_inquiry/new?from=not-mine-1');
    (fetchSubmission as Mock).mockRejectedValue(new Error('Forbidden'));

    render(<SubmissionForm kind="stock_inquiry" />);
    await waitFor(() =>
      expect(toasts.error).toHaveBeenCalledWith('Could not copy that submission.'),
    );

    // Empty form: the field the source would have carried is blank, not
    // populated with anything left over from the failed fetch.
    await waitFor(() => expect(projectNameField()).toBeTruthy());
    expect(projectNameField().value).toBe('');
  });
});
