import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock('@/lib/toast', () => ({
  toast: {
    error: (...a: unknown[]) => toastError(...a),
    success: (...a: unknown[]) => toastSuccess(...a),
  },
}));

vi.mock('../lib/portal-client', () => ({
  fetchSubmissionComments: vi.fn(),
  postSubmissionComment: vi.fn(),
  editSubmissionComment: vi.fn(),
  deleteSubmissionComment: vi.fn(),
  portalAttachmentDownloadUrl: (id: string) => `/dl/${id}`,
}));
vi.mock('../lib/portal-preview', () => ({ portalFetchBytes: vi.fn() }));
vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  __esModule: true,
  default: () => null,
}));

import {
  deleteSubmissionComment,
  editSubmissionComment,
  fetchSubmissionComments,
  postSubmissionComment,
} from '../lib/portal-client';
import { PortalSubmissionComments } from './PortalSubmissionComments';
import type { FormComment } from '@/types/formComments';

const fetchMock = fetchSubmissionComments as ReturnType<typeof vi.fn>;
const postMock = postSubmissionComment as ReturnType<typeof vi.fn>;
const editMock = editSubmissionComment as ReturnType<typeof vi.fn>;
const deleteMock = deleteSubmissionComment as ReturnType<typeof vi.fn>;

const existing: FormComment = {
  id: 'c1',
  body: 'We need the PDF.',
  authorName: 'Alice Staff',
  authorKind: 'staff',
  postedByName: null,
  kind: 'comment' as const,
  parentId: null,
  editedAt: null,
  deleted: false,
  canEdit: true,
  canDelete: true,
  canReply: true,
  createdAt: '2026-10-01T09:00:00',
  attachments: [],
};
const mine: FormComment = {
  id: 'c2',
  body: 'Here you go',
  authorName: 'Dan Dealer',
  authorKind: 'submitter',
  postedByName: null,
  kind: 'comment' as const,
  parentId: null,
  editedAt: null,
  deleted: false,
  canEdit: true,
  canDelete: true,
  canReply: true,
  createdAt: '2026-10-01T10:00:00',
  attachments: [],
};

function mount(readOnly = false) {
  return render(
    <PortalSubmissionComments
      kind="stock_inquiry"
      submissionId="si-1"
      authorName="Dan Dealer"
      readOnly={readOnly}
    />,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('PortalSubmissionComments', () => {
  it('loads the thread for the submission', async () => {
    fetchMock.mockResolvedValue([existing]);
    mount();
    expect(await screen.findByText('We need the PDF.')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('stock_inquiry', 'si-1');
  });

  it('shows the empty state when there are no comments', async () => {
    fetchMock.mockResolvedValue([]);
    mount();
    expect(await screen.findByText('No comments yet')).toBeInTheDocument();
  });

  it('shows an inline error with Retry, not a toast, when the load fails', async () => {
    fetchMock.mockRejectedValueOnce(new Error('Failed to load comments.'));
    mount();
    expect(
      await screen.findByText('Comments could not be loaded'),
    ).toBeInTheDocument();
    expect(screen.queryByText('No comments yet')).not.toBeInTheDocument();
    expect(toastError).not.toHaveBeenCalled();
    fetchMock.mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByText('No comments yet')).toBeInTheDocument();
  });

  it('re-reads the thread after a post so the comment shows, and clears the composer', async () => {
    fetchMock
      .mockResolvedValueOnce([existing])
      .mockResolvedValue([existing, mine]);
    postMock.mockResolvedValue({ comment: mine, delivery: 'queued' });
    mount();
    await screen.findByText('We need the PDF.');
    const box = screen.getByPlaceholderText(
      'Write a comment',
    ) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'Here you go' } });
    fireEvent.click(screen.getByRole('button', { name: 'Comment' }));

    expect(await screen.findByText('Dan Dealer')).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith(
      'stock_inquiry',
      'si-1',
      'Here you go',
      [],
      undefined,
    );
    expect(screen.getByText('Submitter')).toBeInTheDocument();
    expect(toastSuccess).toHaveBeenCalled();
    await waitFor(() => expect(box.value).toBe(''));
  });

  it('keeps the typed text and toasts when the post fails', async () => {
    fetchMock.mockResolvedValue([]);
    postMock.mockRejectedValue(new Error('Server said no'));
    mount();
    await screen.findByText('No comments yet');
    const box = screen.getByPlaceholderText(
      'Write a comment',
    ) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'Do not lose me' } });
    fireEvent.click(screen.getByRole('button', { name: 'Comment' }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('Server said no'),
    );
    expect(box.value).toBe('Do not lose me');
    expect(screen.getByText('No comments yet')).toBeInTheDocument();
  });

  it('hides the composer when read only', async () => {
    fetchMock.mockResolvedValue([existing]);
    mount(true);
    await screen.findByText('We need the PDF.');
    expect(screen.queryByPlaceholderText('Write a comment')).toBeNull();
  });

  it('Reply posts with the parent id', async () => {
    fetchMock.mockResolvedValue([existing]);
    postMock.mockResolvedValue({ comment: mine, delivery: 'queued' });
    mount();
    await screen.findByText('We need the PDF.');
    fireEvent.click(screen.getByRole('button', { name: 'Reply' }));
    fireEvent.change(screen.getByPlaceholderText('Write a reply'), {
      target: { value: 'Answer' },
    });
    const replyButtons = screen.getAllByRole('button', { name: 'Reply' });
    fireEvent.click(replyButtons[replyButtons.length - 1]);
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(
        'stock_inquiry',
        'si-1',
        'Answer',
        [],
        'c1',
      ),
    );
  });

  it('Edit saves through the portal edit call and re-reads the thread', async () => {
    fetchMock.mockResolvedValue([existing]);
    editMock.mockResolvedValue(undefined);
    mount();
    await screen.findByText('We need the PDF.');
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(screen.getByPlaceholderText('Edit comment'), {
      target: { value: 'We need the PDF, please.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(editMock).toHaveBeenCalledWith(
        'stock_inquiry',
        'si-1',
        'c1',
        'We need the PDF, please.',
      ),
    );
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('Delete is a countdown with Cancel: Cancel keeps the comment, the lapse deletes it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      fetchMock.mockResolvedValue([existing]);
      deleteMock.mockResolvedValue(undefined);
      mount();
      await screen.findByText('We need the PDF.');

      fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
      expect(await screen.findByRole('timer')).toHaveTextContent(/Deleting/);
      fireEvent.click(screen.getByRole('button', { name: /Cancel/ }));
      await vi.advanceTimersByTimeAsync(11_000);
      expect(deleteMock).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
      await vi.advanceTimersByTimeAsync(11_000);
      await waitFor(() =>
        expect(deleteMock).toHaveBeenCalledWith('stock_inquiry', 'si-1', 'c1'),
      );
    } finally {
      vi.useRealTimers();
    }
  });
});
