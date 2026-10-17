/** S9: a typed negative portal number is reported on the field, never as "Missing ... required". */
import React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

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
} from '../lib/portal-client';
import { SubmissionForm } from './SubmissionForm';

vi.mock('@/lib/toast', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));
vi.mock('@/components/common/AttachmentPreviewModal', () => ({ __esModule: true, default: () => null }));

import { toast } from '@/lib/toast';

const CONTACT: PortalContact = {
  contact_id: 'contact-1',
  space_id: 'space-1',
  name: 'CONTACT X Lee',
  phone_number: '60123456789',
  expires_at: '2026-08-01T00:00:00Z',
};

beforeEach(() => {
  vi.clearAllMocks();
  (fetchMe as Mock).mockResolvedValue(CONTACT);
  (fetchSubmissionNeighbours as Mock).mockResolvedValue({ prev_id: null, next_id: null, position: 1, total: 1 });
  (saveDraft as Mock).mockResolvedValue({ id: 'new-1' });
  (submitDraft as Mock).mockResolvedValue({ id: 'new-1' });
});

describe('SubmissionForm number widget refusal (S9)', () => {
  it('a typed -5 Quantity gets the number sentence on the field and is not listed as a missing field', async () => {
    render(<SubmissionForm kind="stock_inquiry" />);
    await screen.findByText(CONTACT.name as string);
    fireEvent.change(screen.getAllByRole('spinbutton')[0], { target: { value: '-5' } });
    const qty = screen.getAllByRole('spinbutton')[0];
    expect(qty).toHaveValue(-5);
    fireEvent.click(screen.getByRole('button', { name: 'Submit stock inquiry' }));
    await screen.findByRole('alertdialog');
    fireEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Submit stock inquiry' }),
    );
    expect(await screen.findByText('Enter a number of at least 0.')).toBeInTheDocument();
    expect(qty).toHaveAttribute('aria-invalid', 'true');
    const messages = (toast.error as Mock).mock.calls.map((c) => String(c[0]));
    expect(messages.some((m) => /Missing .*Quantity/.test(m))).toBe(false);
    expect(saveDraft).not.toHaveBeenCalled();
  });
});
