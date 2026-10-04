/**
 * Regression for #482: a create that succeeds but whose post-create attachment
 * flush then fails (quota, extension, 502) must not lose the new row's id. Before
 * the fix, `handleSaveDraft`/`handleSubmit` read only the `submissionId` prop -
 * which never changes mid-session - so a retry after the flush failure called
 * `saveDraft` a SECOND time with no id, creating a duplicate row for the same
 * draft. The fix (ported from PriceTagRequestForm, #483 review round) holds the
 * id a create call answered with in state and prefers it over the prop.
 *
 * Scoped to `kind="stock_inquiry"` - the create-then-flush code path in
 * `handleSaveDraft`/`handleSubmit` is shared verbatim across all four legacy
 * portal kinds, so one kind is enough to prove the fix.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

import type { PortalContact } from '../lib/portal-client';

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

import {
  fetchMe,
  fetchSubmissionNeighbours,
  saveDraft,
  submitDraft,
  uploadAttachment,
} from '../lib/portal-client';
import { SubmissionForm } from './SubmissionForm';

// AttachmentPreviewModal pulls in embla, which needs layout APIs jsdom lacks
// (same reasoning as SubmissionForm.test.tsx) - stubbed out, unused here.
vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  __esModule: true,
  default: () => null,
}));

// Replaced with a minimal stand-in that exposes ONE button wired to
// `onPendingFilesChange`, so the test can put a file into `pendingFiles`
// without simulating a real file input / drop event. Captures the last props
// it was rendered with so a test can assert on `submissionId` (AC: the
// dropzone must be handed the held id, not the stale prop, on retry).
let lastDropzoneProps: { submissionId?: string | null } | null = null;
vi.mock('./AttachmentDropzone', () => ({
  AttachmentDropzone: (props: {
    submissionId?: string | null;
    pendingFiles?: File[];
    onPendingFilesChange?: (files: File[]) => void;
  }) => {
    lastDropzoneProps = props;
    return (
      <button
        type="button"
        data-testid="attach-pending-file"
        onClick={() =>
          props.onPendingFilesChange?.([
            ...(props.pendingFiles ?? []),
            new File(['data'], 'photo.png', { type: 'image/png' }),
          ])
        }
      >
        attach
      </button>
    );
  },
}));

const CONTACT: PortalContact = {
  contact_id: 'contact-1',
  space_id: 'space-1',
  name: 'CONTACT X Lee',
  phone_number: '60123456789',
  expires_at: '2026-08-01T00:00:00Z',
};

beforeEach(() => {
  vi.clearAllMocks();
  lastDropzoneProps = null;
  push.mockClear();
  replace.mockClear();
  (fetchMe as Mock).mockResolvedValue(CONTACT);
  (fetchSubmissionNeighbours as Mock).mockResolvedValue({
    prev_id: null,
    next_id: null,
    position: 1,
    total: 1,
  });
});

/**
 * A CREATE form never renders "Loading..." - `loading` initialises to
 * `Boolean(submissionId)`, which is false with no id - so waiting for that text to go
 * is a no-op and waits for nothing at all.
 *
 * What these tests actually depend on is the SEED: `salesperson_contact_id` is required
 * and is filled from the contact `fetchMe` answers with, in an effect that lands a task
 * after the first paint. Until it does, `handleSubmit` stops at `collectMissingRequired`,
 * closes the confirm dialog and never calls `saveDraft` - which is exactly what happened
 * on CI (5 s spent waiting on a mock that could no longer be called). Save Draft does not
 * validate, which is why only the Submit test failed there.
 *
 * The contact's own name is the label the Salesperson select shows once the seed lands,
 * so waiting for it waits for the exact effect rather than for a clock.
 */
async function waitForSeededForm() {
  await screen.findByText(CONTACT.name as string);
}

describe('SubmissionForm - retry after create-succeeded/flush-failed does not duplicate (#482)', () => {
  it('Save Draft: a retry after the flush fails updates the created row instead of creating a second one', async () => {
    (saveDraft as Mock).mockResolvedValue({
      id: 'new-si-1',
      kind: 'stock_inquiry',
      title: 'Stock inquiry',
      reference: 'SI-2026-0099',
      status: 'new',
      is_editable: true,
      is_draft: true,
      created_at: '2026-09-01T00:00:00Z',
      attachments: [],
    });
    (uploadAttachment as Mock).mockRejectedValue(new Error('quota exceeded'));

    render(<SubmissionForm kind="stock_inquiry" />);
    await waitForSeededForm();

    fireEvent.click(screen.getByTestId('attach-pending-file'));

    fireEvent.click(screen.getByRole('button', { name: 'Save as draft' }));
    await waitFor(() => expect(saveDraft).toHaveBeenCalledTimes(1));
    // First attempt: nothing existed yet, so it CREATES (no id argument).
    expect((saveDraft as Mock).mock.calls[0][3]).toBeUndefined();
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledTimes(1));
    // The flush failed, so the draft never navigated away - the retry click
    // below is reachable.
    await waitFor(() => expect(replace).not.toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: 'Save as draft' }));
    await waitFor(() => expect(saveDraft).toHaveBeenCalledTimes(2));
    // Retry: the id the first create answered with must be reused (UPDATE),
    // never a second CREATE for the same draft.
    expect((saveDraft as Mock).mock.calls[1][3]).toBe('new-si-1');

    // The dropzone must also be handed the held id, not the stale (still
    // undefined) `submissionId` prop, so a file attached now uploads to the
    // right row instead of buffering forever.
    expect(lastDropzoneProps?.submissionId).toBe('new-si-1');
  });

  it('Submit: a retry after the flush fails updates the created row instead of creating a second one', async () => {
    // Two DIFFERENT ids: if the bug were still present, the retry's create
    // call would resolve the SECOND one, and the assertions below would catch
    // it landing anywhere (a second saveDraft call, or a flush aimed at it).
    (saveDraft as Mock)
      .mockResolvedValueOnce({
        id: 'new-si-2',
        kind: 'stock_inquiry',
        title: 'Stock inquiry',
        reference: 'SI-2026-0100',
        status: 'new',
        is_editable: true,
        is_draft: true,
        created_at: '2026-09-01T00:00:00Z',
        attachments: [],
      })
      .mockResolvedValueOnce({
        id: 'DUPLICATE-si-2b',
        kind: 'stock_inquiry',
        title: 'Stock inquiry',
        reference: 'SI-2026-0101',
        status: 'new',
        is_editable: true,
        is_draft: true,
        created_at: '2026-09-01T00:00:01Z',
        attachments: [],
      });
    (uploadAttachment as Mock).mockRejectedValue(new Error('502 Bad Gateway'));

    render(<SubmissionForm kind="stock_inquiry" />);
    await waitForSeededForm();

    fireEvent.click(screen.getByTestId('attach-pending-file'));

    // The trigger button and the AlertDialogAction confirm button share the
    // same accessible name ("Submit stock inquiry"), so `findAllByRole` can
    // resolve on WHICHEVER one is queryable first - a race that flaked ~40%
    // of CI runs (#597): the trigger re-click was a no-op, `handleSubmit`
    // never ran, and the assertions below timed out at zero. Scoping to the
    // dialog's own role removes the ambiguity outright.
    const openAndConfirm = async () => {
      fireEvent.click(
        screen.getByRole('button', { name: 'Submit stock inquiry' }),
      );
      await screen.findByRole('alertdialog');
      // Re-queried at the click, never the handle `findByRole` returned: the
      // dialog is mounted through Radix's Presence, so the node that handle
      // points at can already have been replaced, and a click on a node that
      // is no longer in the tree does nothing at all - which is how this
      // landed on CI three times, as `saveDraft` called 0 times.
      fireEvent.click(
        within(screen.getByRole('alertdialog')).getByRole('button', {
          name: 'Submit stock inquiry',
        }),
      );
    };

    await openAndConfirm();
    await waitFor(() => expect(saveDraft).toHaveBeenCalledTimes(1));
    expect((saveDraft as Mock).mock.calls[0][3]).toBeUndefined();
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledTimes(1));
    // The flush failed, so submitDraft was never reached, and the form never
    // navigated away - the retry click below is reachable.
    expect(submitDraft).not.toHaveBeenCalled();
    await waitFor(() => expect(replace).not.toHaveBeenCalled());

    await openAndConfirm();
    // The retry flushes against the id the first create already answered
    // with - proven by uploadAttachment's second call using it - and never
    // calls saveDraft a second time: with the id already held, there is
    // nothing left to create.
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledTimes(2));
    expect((uploadAttachment as Mock).mock.calls[1][1]).toBe('new-si-2');
    await waitFor(() => expect(saveDraft).toHaveBeenCalledTimes(1));
  });
});
