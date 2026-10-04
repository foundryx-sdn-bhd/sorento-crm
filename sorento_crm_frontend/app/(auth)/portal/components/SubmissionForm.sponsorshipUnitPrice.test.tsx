/**
 * #1227: unit price is mandatory on every sponsorship form line, on portal
 * submission. Same validation shape `PriceTagRequestForm` already uses (see
 * `PriceTagRequestForm.validation.test.tsx`): a client-side check before
 * submit that names the offending line, and a server 422 naming
 * `line:<index>`, surfaced as "Line N: <message>".
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

import type { PortalContact } from '../lib/portal-client';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

const toasts = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), info: vi.fn() }));
vi.mock('@/lib/toast', () => ({ toast: toasts }));

vi.mock('../lib/portal-client', async (importOriginal) => {
  const original = await importOriginal<typeof import('../lib/portal-client')>();
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

import {
  fetchMe,
  fetchRevisions,
  fetchSubmission,
  fetchSubmissionNeighbours,
  reviseSubmission,
  saveDraft,
  submitDraft,
} from '../lib/portal-client';
import { SubmissionForm } from './SubmissionForm';
import { waitForSectionLoaded } from '@/test-utils';

const asMock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

const CONTACT: PortalContact = {
  contact_id: 'contact-1',
  space_id: 'space-1',
  name: 'CONTACT X Lee',
  phone_number: '60123456789',
  expires_at: '2026-08-01T00:00:00Z',
};

beforeEach(() => {
  vi.clearAllMocks();
  asMock(fetchMe).mockResolvedValue(CONTACT);
  asMock(fetchSubmissionNeighbours).mockResolvedValue({
    prev_id: null,
    next_id: null,
    position: 1,
    total: 1,
  });
  asMock(saveDraft).mockResolvedValue({ id: 'new-1' });
  asMock(submitDraft).mockResolvedValue({ id: 'new-1' });
});

/** Adds one item row and fills its Quantity + (optionally) Unit price cell -
 *  scoped to the products table so the page's OTHER number input ("Total
 *  project value") is never touched by mistake. */
async function addLine({ unitPrice }: { unitPrice?: string } = {}) {
  fireEvent.click(screen.getByRole('button', { name: /Add item/i }));
  const table = screen.getByRole('table');
  const spinbuttons = within(table).getAllByRole('spinbutton');
  // Row order: Quantity, Unit price, Total.
  fireEvent.change(spinbuttons[0], { target: { value: '2' } });
  if (unitPrice !== undefined) {
    fireEvent.change(spinbuttons[1], { target: { value: unitPrice } });
  }
}

function openSubmitConfirm() {
  const submitButtons = screen.getAllByRole('button', {
    name: /Submit sponsorship form/i,
  });
  // The first is the page action that opens the confirm dialog.
  fireEvent.click(submitButtons[0]);
}

function confirmSubmit() {
  const dialog = screen.getByRole('alertdialog');
  fireEvent.click(
    within(dialog).getByRole('button', { name: /Submit sponsorship form/i }),
  );
}

describe('SubmissionForm - sponsorship unit price required on submit (#1227)', () => {
  it('blocks submit client-side and names the line when unit price is missing', async () => {
    render(<SubmissionForm kind="sponsorship_form" />);
    await screen.findByText(CONTACT.name as string);

    await addLine();
    openSubmitConfirm();
    confirmSubmit();

    await waitFor(() =>
      expect(toasts.error).toHaveBeenCalledWith('Line 1: Unit price is required.'),
    );
    expect(await screen.findByText('Unit price is required.')).toBeInTheDocument();
    expect(saveDraft).not.toHaveBeenCalled();
    expect(submitDraft).not.toHaveBeenCalled();
  });

  it('blocks submit client-side for a blank unit price the same as a missing one', async () => {
    render(<SubmissionForm kind="sponsorship_form" />);
    await screen.findByText(CONTACT.name as string);

    await addLine({ unitPrice: '   ' });
    openSubmitConfirm();
    confirmSubmit();

    await waitFor(() =>
      expect(toasts.error).toHaveBeenCalledWith('Line 1: Unit price is required.'),
    );
    expect(submitDraft).not.toHaveBeenCalled();
  });

  it('blocks submit client-side for a negative unit price', async () => {
    render(<SubmissionForm kind="sponsorship_form" />);
    await screen.findByText(CONTACT.name as string);

    await addLine({ unitPrice: '-5' });
    openSubmitConfirm();
    confirmSubmit();

    await waitFor(() =>
      expect(toasts.error).toHaveBeenCalledWith('Line 1: Unit price is required.'),
    );
    expect(submitDraft).not.toHaveBeenCalled();
  });

  it('submits once every line has a unit price', async () => {
    render(<SubmissionForm kind="sponsorship_form" />);
    await screen.findByText(CONTACT.name as string);

    await addLine({ unitPrice: '10.00' });
    openSubmitConfirm();
    confirmSubmit();

    await waitFor(() => expect(submitDraft).toHaveBeenCalled());
    expect(toasts.error).not.toHaveBeenCalled();
  });

  it('submits when the unit price is exactly 0 (owner ruling 25 Sep 2026: 0 is accepted)', async () => {
    render(<SubmissionForm kind="sponsorship_form" />);
    await screen.findByText(CONTACT.name as string);

    await addLine({ unitPrice: '0' });
    openSubmitConfirm();
    confirmSubmit();

    await waitFor(() => expect(submitDraft).toHaveBeenCalled());
    expect(toasts.error).not.toHaveBeenCalled();
  });

  it('surfaces a server 422 naming line:<index> as "Line N: <message>" (mirrors lineErrorToast)', async () => {
    asMock(submitDraft).mockRejectedValue(
      Object.assign(new Error('Unit price is required.'), {
        code: 'SPONSORSHIP_UNIT_PRICE_REQUIRED',
        fields: ['line:0'],
      }),
    );
    render(<SubmissionForm kind="sponsorship_form" />);
    await screen.findByText(CONTACT.name as string);

    // A valid unit price clears the CLIENT check, so this exercises the server
    // refusal path specifically (e.g. a race with another edit).
    await addLine({ unitPrice: '10.00' });
    openSubmitConfirm();
    confirmSubmit();

    await waitFor(() =>
      expect(toasts.error).toHaveBeenCalledWith('Line 1: Unit price is required.'),
    );
    expect(await screen.findByText('Unit price is required.')).toBeInTheDocument();
  });
});

describe('SubmissionForm - Save as draft is never blocked by unit price (#1232 blocking 5, AC-P4)', () => {
  /**
   * AC-P4: "A draft save with an incomplete line is never blocked - only
   * submit is gated." `handleSaveDraft` deliberately never calls
   * `findMissingSponsorshipUnitPrice` - this is the FE guard for that (the
   * reviewer's kill test 3 on the client: disabling the client gate on
   * `handleSaveDraft` left every prior portal vitest test green, so the
   * absence of THIS test was itself the gap).
   */
  it('saves a draft with a price-less sponsorship line: saveDraft is called and no error toast fires', async () => {
    render(<SubmissionForm kind="sponsorship_form" />);
    await screen.findByText(CONTACT.name as string);

    await addLine();
    fireEvent.click(screen.getByRole('button', { name: /Save as draft/i }));

    await waitFor(() => expect(saveDraft).toHaveBeenCalled());
    expect(toasts.error).not.toHaveBeenCalled();
    expect(submitDraft).not.toHaveBeenCalled();
  });
});

describe('SubmissionForm - sponsorship unit price required on revise (#1232 blocking 4)', () => {
  /**
   * A revise is a second submission path back into the approval flow
   * (`handleRevise` sends `cleanedProducts` the same way `handleSubmit` does),
   * so `openReviseConfirm` must run the same `findMissingSponsorshipUnitPrice`
   * check `handleSubmit` does - reusing `collectMissingRequired`'s own
   * reasoning ("Shared by submit and revise so the two can never disagree").
   */
  async function renderRevisableSponsorshipForm() {
    asMock(fetchSubmission).mockResolvedValue({
      id: 'sf-1',
      kind: 'sponsorship_form',
      title: 'Sponsorship form',
      reference: 'SF-26-0001',
      status: 'submitted',
      is_editable: false,
      is_draft: false,
      created_at: '2026-07-20T00:00:00Z',
      attachments: [],
      revision: {
        enabled: true,
        allowed: true,
        used: 0,
        max: 3,
        remaining: 3,
        blocked_reason: null,
      },
      project_title: 'Community Fun Run',
      purpose: 'Sponsorship',
      requested_by_contact_id: 'contact-1',
      requested_by: 'CONTACT X Lee',
      products: [{ item_code: 'ITEM-A', quantity: '2', unit_price: '10' }],
    });
    asMock(fetchSubmissionNeighbours).mockResolvedValue({
      prev_id: null,
      next_id: null,
      position: 1,
      total: 1,
    });
    asMock(fetchRevisions).mockResolvedValue([]);
    render(<SubmissionForm kind="sponsorship_form" submissionId="sf-1" />);
    await waitForSectionLoaded();
    await waitFor(() =>
      expect(asMock(fetchSubmission).mock.calls.length).toBeGreaterThanOrEqual(2),
    );
  }

  async function enterReviseMode() {
    const gear = await screen.findByRole('button', { name: 'Submission actions' });
    fireEvent.pointerDown(gear, { button: 0, pointerId: 1 });
    fireEvent.pointerUp(gear, { button: 0, pointerId: 1 });
    fireEvent.click(gear);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Revise' }));
    fireEvent.change(screen.getByLabelText(/what changed, and why\?/i), {
      target: { value: 'Corrected the price' },
    });
  }

  it('blocks Send revision client-side and names the line when a price is cleared', async () => {
    await renderRevisableSponsorshipForm();
    await enterReviseMode();

    const table = screen.getByRole('table');
    const spinbuttons = within(table).getAllByRole('spinbutton');
    // Row order: Quantity, Unit price, Total - clear the seeded price.
    fireEvent.change(spinbuttons[1], { target: { value: '' } });

    fireEvent.click(screen.getByRole('button', { name: 'Send revision' }));

    await waitFor(() =>
      expect(toasts.error).toHaveBeenCalledWith('Line 1: Unit price is required.'),
    );
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(reviseSubmission).not.toHaveBeenCalled();
  });

  it('opens the confirm dialog once every line still has a unit price', async () => {
    await renderRevisableSponsorshipForm();
    await enterReviseMode();

    fireEvent.click(screen.getByRole('button', { name: 'Send revision' }));

    expect(await screen.findByRole('alertdialog')).toBeInTheDocument();
    expect(toasts.error).not.toHaveBeenCalled();
  });
});
