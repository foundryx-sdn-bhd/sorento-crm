/** NUMBER-FIELDS round 3 (R1-R3): bad browser input on a portal number box is never saved. */
import React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

import type { PortalContact } from '../lib/portal-client';
import { typeBadInput } from '@/test-utils/badInput';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
const toasts = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), info: vi.fn() }));
vi.mock('@/lib/toast', () => ({ toast: toasts }));
vi.mock('@/components/common/AttachmentPreviewModal', () => ({ __esModule: true, default: () => null }));

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

import { fetchMe, fetchSubmissionNeighbours, saveDraft, submitDraft } from '../lib/portal-client';
import { SubmissionForm } from './SubmissionForm';

const CONTACT: PortalContact = {
  contact_id: 'contact-1',
  space_id: 'space-1',
  name: 'CONTACT X Lee',
  phone_number: '60123456789',
  expires_at: '2026-08-01T00:00:00Z',
};

const SENTENCE = /Enter a number of at least 0/;

beforeEach(() => {
  vi.clearAllMocks();
  (fetchMe as Mock).mockResolvedValue(CONTACT);
  (fetchSubmissionNeighbours as Mock).mockResolvedValue({ prev_id: null, next_id: null, position: 1, total: 1 });
  (saveDraft as Mock).mockResolvedValue({ id: 'new-1' });
  (submitDraft as Mock).mockResolvedValue({ id: 'new-1' });
});

async function sponsorshipWithLine() {
  render(<SubmissionForm kind="sponsorship_form" />);
  await screen.findByText(CONTACT.name as string);
  fireEvent.click(screen.getByRole('button', { name: /Add item/i }));
  const table = screen.getByRole('table');
  // Row order: Quantity, Unit price, Total.
  const boxes = within(table).getAllByRole('spinbutton');
  fireEvent.change(boxes[0], { target: { value: '2' } });
  return boxes;
}

describe('SubmissionForm bad number input (R1-R3)', () => {
  it('R1: Save Draft with bad input in Unit price sends nothing and shows the sentence', async () => {
    const boxes = await sponsorshipWithLine();
    typeBadInput(boxes[1]);
    fireEvent.click(screen.getByRole('button', { name: /Save as draft/i }));
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    expect(saveDraft).not.toHaveBeenCalled();
  });

  it('R2: Save Draft with bad input in line Total sends nothing and shows the sentence', async () => {
    const boxes = await sponsorshipWithLine();
    typeBadInput(boxes[2]);
    fireEvent.click(screen.getByRole('button', { name: /Save as draft/i }));
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    expect(saveDraft).not.toHaveBeenCalled();
  });

  it('R2: Submit with bad input in line Total sends nothing and shows the sentence', async () => {
    const boxes = await sponsorshipWithLine();
    fireEvent.change(boxes[1], { target: { value: '10' } });
    typeBadInput(boxes[2]);
    fireEvent.click(screen.getAllByRole('button', { name: /Submit sponsorship form/i })[0]);
    const dialog = screen.queryByRole('alertdialog');
    if (dialog) {
      fireEvent.click(within(dialog).getByRole('button', { name: /Submit sponsorship form/i }));
    }
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    await waitFor(() => expect(submitDraft).not.toHaveBeenCalled());
    expect(saveDraft).not.toHaveBeenCalled();
  });

  it('R3: Save Draft with bad input in a header number field sends nothing and shows the sentence', async () => {
    render(<SubmissionForm kind="stock_inquiry" />);
    await screen.findByText(CONTACT.name as string);
    typeBadInput(screen.getAllByRole('spinbutton')[0]);
    fireEvent.click(screen.getByRole('button', { name: /Save as draft/i }));
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    expect(saveDraft).not.toHaveBeenCalled();
  });
});
