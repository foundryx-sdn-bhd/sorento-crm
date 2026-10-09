import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { listFormComments, addFormComment, toast } = vi.hoisted(() => ({
  listFormComments: vi.fn(),
  addFormComment: vi.fn(),
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));
vi.mock('@/services/formCommentsService', () => ({
  listFormComments: (...a: unknown[]) => listFormComments(...a),
  addFormComment: (...a: unknown[]) => addFormComment(...a),
}));

vi.mock('@/lib/toast', () => ({ toast }));

vi.mock('next-auth/react', async () => {
  const { createContext } = await import('react');
  return {
    SessionContext: createContext({
      data: { user: { name: 'Pat Buyer' } },
      status: 'authenticated',
    }),
  };
});

import { FormComments } from './FormComments';

function renderIt() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FormComments kind="stock_inquiry" entityId="si-1" />
    </QueryClientProvider>,
  );
}

const comment = {
  id: 'c1',
  body: 'Which brand?',
  authorName: 'Pat Buyer',
  authorKind: 'staff' as const,
  postedByName: null,
  kind: 'comment' as const,
  parentId: null,
  editedAt: null,
  deleted: false,
  canEdit: true,
  canDelete: true,
  canReply: true,
  createdAt: '2026-10-09T01:00:00',
  attachments: [],
};

describe('FormComments (staff)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the signed-in user initials on the composer avatar', async () => {
    listFormComments.mockResolvedValueOnce([]);
    renderIt();
    await screen.findByText('No comments yet');
    expect(screen.getByText('PB')).toBeInTheDocument();
  });

  it('shows an error state with Retry when the thread fails to load', async () => {
    listFormComments.mockRejectedValueOnce(new Error('boom'));
    renderIt();
    expect(
      await screen.findByText('Comments could not be loaded'),
    ).toBeInTheDocument();
    expect(screen.queryByText('No comments yet')).not.toBeInTheDocument();
    listFormComments.mockResolvedValueOnce([]);
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByText('No comments yet')).toBeInTheDocument();
  });

  it.each([
    ['queued', 'success', 'Comment posted'],
    ['no_whatsapp', 'warning', /not notified/],
    ['failed', 'warning', /could not be sent/],
  ] as const)('delivery %s toasts %s', async (delivery, channel, message) => {
    listFormComments.mockResolvedValue([]);
    addFormComment.mockResolvedValueOnce({ comment, delivery });
    renderIt();
    await screen.findByText('No comments yet');
    await userEvent.type(
      screen.getByLabelText('Write a comment'),
      'Which brand?',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Comment' }));
    await waitFor(() =>
      expect(toast[channel]).toHaveBeenCalledWith(
        typeof message === 'string' ? message : expect.stringMatching(message),
      ),
    );
    expect(addFormComment).toHaveBeenCalledWith(
      'stock_inquiry',
      'si-1',
      'Which brand?',
      [],
      undefined,
    );
  });
});
