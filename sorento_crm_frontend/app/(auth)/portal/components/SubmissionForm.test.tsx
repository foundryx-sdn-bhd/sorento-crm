/**
 * SubmissionForm - staff attachment banner (UAC-response-attachments.md group E)
 * and portal record navigation (UAC-response-attachments.md group G / D8 mobile).
 *
 * Scoped to `kind="stock_inquiry"` (simplest field set: no lookup-select /
 * do-multi-filter widgets), which is enough to exercise the banner + chevron
 * logic that is shared verbatim across all four submission kinds.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

import type {
  PortalAttachment,
  PortalContact,
  PortalSubmissionDetail,
  PortalSubmissionNeighbours,
} from '../lib/portal-client';

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

import { fetchMe, fetchSubmission, fetchSubmissionNeighbours } from '../lib/portal-client';
import { SubmissionForm } from './SubmissionForm';
import { waitForSectionLoaded } from '@/test-utils';

// Captures every render of the shared preview modal so the banner test can
// assert on the exact `items` it was handed (staff uploads only - never the
// contact's own files), without the real carousel engine.
const previewCalls: Array<{ open: boolean; items: { id: string; name: string }[] }> = [];
vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  __esModule: true,
  default: (props: { open: boolean; items: { id: string; name: string }[] }) => {
    previewCalls.push({ open: props.open, items: props.items });
    if (!props.open) return null;
    return (
      <div data-testid="staff-preview-modal">
        {props.items.map((it) => (
          <span key={it.id}>{it.name}</span>
        ))}
      </div>
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

function contactFile(over: Partial<PortalAttachment> = {}): PortalAttachment {
  return {
    link_id: 'c-link',
    attachment_id: 'c-att',
    filename: 'my-upload.jpg',
    size: 1024,
    url: 'https://cdn.example.com/my-upload.jpg',
    uploader_kind: 'contact',
    can_unlink: true,
    ...over,
  };
}

function staffFile(over: Partial<PortalAttachment> = {}): PortalAttachment {
  return {
    link_id: `s-link-${Math.random()}`,
    attachment_id: `s-att-${Math.random()}`,
    filename: 'purchasing-photo.jpg',
    size: 2048,
    url: 'https://cdn.example.com/purchasing-photo.jpg',
    uploader_kind: 'user',
    can_unlink: false,
    ...over,
  };
}

function detail(over: Partial<PortalSubmissionDetail> = {}): PortalSubmissionDetail {
  return {
    id: 'si-1',
    kind: 'stock_inquiry',
    title: 'Stock inquiry',
    reference: 'SI-2026-0001',
    status: 'responded',
    is_editable: true,
    is_draft: false,
    created_at: '2026-07-20T00:00:00Z',
    purchasing_response: 'We have 20 units in stock.',
    attachments: [],
    product_code: 'ABC-123',
    item_description: '',
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

beforeEach(() => {
  vi.clearAllMocks();
  previewCalls.length = 0;
  push.mockClear();
  replace.mockClear();
  (fetchMe as ReturnType<typeof vi.fn>).mockResolvedValue(CONTACT);
  (fetchSubmissionNeighbours as ReturnType<typeof vi.fn>).mockResolvedValue({
    prev_id: null,
    next_id: null,
    position: 1,
    total: 1,
  } satisfies PortalSubmissionNeighbours);
});

describe('SubmissionForm - staff attachment banner', () => {
  it('renders no attachment-count block or button when there are zero staff attachments', async () => {
    (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(
      detail({ attachments: [contactFile()] }),
    );
    render(<SubmissionForm kind="stock_inquiry" submissionId="si-1" />);
    await waitForSectionLoaded();

    expect(screen.getByText('Comment / reply by purchasing')).toBeInTheDocument();
    expect(screen.queryByText(/attachment.*from purchasing/i)).toBeNull();
    // Disambiguate from the per-row "View attachment" preview button that the
    // (contact's own) attachments dropzone always renders - the banner button
    // is the one with a "(N)" count suffix.
    expect(screen.queryByRole('button', { name: /View attachments? \(/i })).toBeNull();
  });

  it('shows a singular "1 attachment from purchasing" + button for one staff file', async () => {
    (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(
      detail({ attachments: [contactFile(), staffFile({ link_id: 's-1' })] }),
    );
    render(<SubmissionForm kind="stock_inquiry" submissionId="si-1" />);
    await waitForSectionLoaded();

    expect(screen.getByText(/1 attachment from purchasing/)).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'View attachment (1)' }),
    ).toBeInTheDocument();
  });

  it('shows a plural count for many staff files, and the button label carries the count', async () => {
    (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(
      detail({
        attachments: [
          contactFile(),
          staffFile({ link_id: 's-1' }),
          staffFile({ link_id: 's-2' }),
          staffFile({ link_id: 's-3' }),
        ],
      }),
    );
    render(<SubmissionForm kind="stock_inquiry" submissionId="si-1" />);
    await waitForSectionLoaded();

    expect(screen.getByText(/3 attachments from purchasing/)).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'View attachments (3)' }),
    ).toBeInTheDocument();
  });

  it('opens the preview scoped to ONLY uploader_kind === "user" items, not the contact’s own uploads', async () => {
    (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(
      detail({
        attachments: [
          contactFile({ link_id: 'contact-file', filename: 'contact-only.jpg' }),
          staffFile({ link_id: 's-1', filename: 'staff-1.jpg' }),
          staffFile({ link_id: 's-2', filename: 'staff-2.jpg' }),
        ],
      }),
    );
    render(<SubmissionForm kind="stock_inquiry" submissionId="si-1" />);
    await waitForSectionLoaded();

    fireEvent.click(screen.getByRole('button', { name: 'View attachments (2)' }));

    const modal = within(screen.getByTestId('staff-preview-modal'));
    expect(modal.getByText('staff-1.jpg')).toBeInTheDocument();
    expect(modal.getByText('staff-2.jpg')).toBeInTheDocument();
    expect(modal.queryByText('contact-only.jpg')).toBeNull();

    // The prop the (mocked) modal actually received - never the contact's own file.
    const lastOpenCall = previewCalls.filter((c) => c.open).at(-1);
    expect(lastOpenCall?.items.map((i) => i.name).sort()).toEqual([
      'staff-1.jpg',
      'staff-2.jpg',
    ]);
  });
});

describe('SubmissionForm - portal record navigation', () => {
  it('hides the chevron on the side with no neighbour and shows "position / total"', async () => {
    (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(detail());
    (fetchSubmissionNeighbours as ReturnType<typeof vi.fn>).mockResolvedValue({
      prev_id: null,
      next_id: 'si-2',
      position: 2,
      total: 5,
    });
    render(<SubmissionForm kind="stock_inquiry" submissionId="si-1" />);
    await waitForSectionLoaded();
    await waitFor(() => expect(screen.getByText('2 / 5')).toBeInTheDocument());

    expect(screen.queryByLabelText('Previous submission')).toBeNull();
    expect(screen.getByLabelText('Next submission')).toBeInTheDocument();
  });

  it('shows the previous chevron and hides next when there is no next neighbour', async () => {
    (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(detail());
    (fetchSubmissionNeighbours as ReturnType<typeof vi.fn>).mockResolvedValue({
      prev_id: 'si-0',
      next_id: null,
      position: 5,
      total: 5,
    });
    render(<SubmissionForm kind="stock_inquiry" submissionId="si-1" />);
    await waitForSectionLoaded();
    await waitFor(() => expect(screen.getByLabelText('Previous submission')).toBeInTheDocument());
    expect(screen.queryByLabelText('Next submission')).toBeNull();
  });

  it('ArrowRight navigates to the next neighbour when nothing editable has focus', async () => {
    (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(detail());
    (fetchSubmissionNeighbours as ReturnType<typeof vi.fn>).mockResolvedValue({
      prev_id: 'si-0',
      next_id: 'si-2',
      position: 2,
      total: 5,
    });
    render(<SubmissionForm kind="stock_inquiry" submissionId="si-1" />);
    await waitForSectionLoaded();
    await waitFor(() => expect(screen.getByLabelText('Next submission')).toBeInTheDocument());

    document.body.focus();
    fireEvent.keyDown(window, { key: 'ArrowRight' });

    expect(push).toHaveBeenCalledWith(expect.stringContaining('si-2'));
  });

  it('ignores ArrowLeft/ArrowRight while a text field has focus', async () => {
    (fetchSubmission as ReturnType<typeof vi.fn>).mockResolvedValue(detail());
    (fetchSubmissionNeighbours as ReturnType<typeof vi.fn>).mockResolvedValue({
      prev_id: 'si-0',
      next_id: 'si-2',
      position: 2,
      total: 5,
    });
    render(<SubmissionForm kind="stock_inquiry" submissionId="si-1" />);
    await waitForSectionLoaded();
    await waitFor(() => expect(screen.getByLabelText('Next submission')).toBeInTheDocument());

    const additionalRemark = document.getElementById('additional_remark') as HTMLTextAreaElement;
    expect(additionalRemark).not.toBeNull();
    additionalRemark.focus();
    fireEvent.keyDown(additionalRemark, { key: 'ArrowRight' });

    expect(push).not.toHaveBeenCalled();
  });
});

/**
 * AC-F4: the registered-project picker belongs to flagged contacts only. An unflagged
 * contact keeps the free-text project title and never sees the field - the promise the
 * flag's own hint in the contact dialog makes.
 */
describe('SubmissionForm - sponsorship registered-project field', () => {
  it('hides the picker for a contact without requires_registered_project', async () => {
    (fetchMe as ReturnType<typeof vi.fn>).mockResolvedValue(CONTACT);
    render(<SubmissionForm kind="sponsorship_form" />);
    // A CREATE form never renders "Loading...", so `waitForLoaded` waits for nothing
    // here - and the picker is absent before the contact arrives too, so the assertion
    // below would pass on a form that had simply not read the flag yet. The name the
    // contact seeds into Requested by is what says it has.
    await screen.findByText(CONTACT.name as string);

    expect(screen.getByText('Project title')).toBeInTheDocument();
    expect(screen.queryByText('Registered project')).toBeNull();
  });

  it('shows the picker, marked required, for a flagged contact', async () => {
    (fetchMe as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...CONTACT,
      requires_registered_project: true,
    } satisfies PortalContact);
    render(<SubmissionForm kind="sponsorship_form" />);

    const label = await screen.findByText('Registered project');
    expect(label).toBeInTheDocument();
    expect(label.textContent).toContain('*');
  });
});
