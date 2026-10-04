/**
 * IDEATION-IN-CRM Phase 2 red tests for staff comments (AC-E-01..E-04, E-06).
 *
 * The component reads the FE comment shape: `deleted`, plus `canEdit` / `canDelete` straight from
 * ss (no ownership guess in the browser). The service maps ss `isDeleted` to `deleted`
 * (see `services/ideasService.test.ts`). Delete is a server-deferred countdown, never a dialog.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { name: 'Pat Staff' } }, status: 'authenticated' }),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() },
}));
vi.mock('@/components/common/deferredToast', () => ({
  deferredToast: vi.fn(() => 'toast-1'),
  dismissDeferredToast: vi.fn(),
}));

const svc = vi.hoisted(() => ({
  listComments: vi.fn(),
  addComment: vi.fn(),
  editComment: vi.fn(),
  // The rest of the service surface `hooks/useIdeas` imports; unused here.
  deleteComment: vi.fn(),
  getIdea: vi.fn(),
  listIdeas: vi.fn(),
  getBoard: vi.fn(),
  getMergedChildren: vi.fn(),
  createIdea: vi.fn(),
  updateIdea: vi.fn(),
  voteIdea: vi.fn(),
  moveIdeaToStatus: vi.fn(),
  restoreIdea: vi.fn(),
  reorderIdeas: vi.fn(),
  mergeIdeas: vi.fn(),
  unmergeIdea: vi.fn(),
  promoteIdea: vi.fn(),
  uploadAttachment: vi.fn(),
}));
vi.mock('@/services/ideasService', () => svc);

const pending = vi.hoisted(() => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn(),
}));
vi.mock('@/services/pendingActionService', () => pending);

import { formatDateTime } from '@/lib/helpers';
import { IdeaComments } from './IdeaComments';

function serverTime(offsetMs: number): string {
  return new Date(Date.now() + offsetMs).toISOString().replace(/\.\d+Z$/, '');
}

interface C {
  id: string;
  parentId?: string | null;
  authorName?: string;
  body?: string;
  createdAt?: string;
  editedAt?: string | null;
  deleted?: boolean;
  canEdit?: boolean;
  canDelete?: boolean;
}

/** Both the FE and the raw ss spellings, so the fixture is valid whichever the component reads. */
function comment(c: C) {
  const deleted = c.deleted ?? false;
  return {
    id: c.id,
    ideaId: 'idea-1',
    parentId: c.parentId ?? null,
    authorName: c.authorName ?? 'Alex Staff',
    authorKind: 'embed',
    authorIsMe: !!c.canEdit,
    isMine: !!c.canEdit,
    body: deleted ? '' : (c.body ?? 'A comment'),
    createdAt: c.createdAt ?? '2026-10-01T09:00:00Z',
    editedAt: c.editedAt ?? null,
    deleted,
    isDeleted: deleted,
    canEdit: c.canEdit ?? false,
    canDelete: c.canDelete ?? false,
  };
}

function renderComments(list: C[], props: { frozen?: boolean } = {}) {
  svc.listComments.mockResolvedValue(list.map(comment));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const componentProps = { ideaId: 'idea-1', frozen: props.frozen ?? false, canDeleteAny: false } as {
    ideaId: string;
    frozen: boolean;
  };
  return render(
    <QueryClientProvider client={client}>
      <IdeaComments {...componentProps} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  pending.getCurrentPendingAction.mockResolvedValue({ pending: null, last_outcome: null });
  pending.cancelPendingAction.mockResolvedValue({ id: 'pa-1', status: 'cancelled' });
  svc.addComment.mockResolvedValue(comment({ id: 'new' }));
});

describe('AC-E-01 thread layout', () => {
  it('renders oldest first with one reply level nested under its parent', async () => {
    renderComments([
      { id: 'c1', body: 'First comment', createdAt: '2026-10-01T09:00:00Z' },
      { id: 'c2', body: 'Second comment', createdAt: '2026-10-01T10:00:00Z' },
      { id: 'r1', parentId: 'c1', body: 'Reply to first', createdAt: '2026-10-01T11:00:00Z' },
    ]);
    await screen.findByText('First comment');
    const order = ['First comment', 'Reply to first', 'Second comment'].map((text) => screen.getByText(text));
    for (let i = 0; i < order.length - 1; i += 1) {
      expect(order[i].compareDocumentPosition(order[i + 1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it('shows the author display name, dd/MM/yyyy h:mm AM/PM time and an "edited" tag only when edited', async () => {
    renderComments([
      { id: 'c1', authorName: 'Alex Staff', body: 'Plain one', createdAt: '2026-10-01T09:00:00Z' },
      {
        id: 'c2',
        authorName: 'Sam Wong',
        body: 'Edited one',
        createdAt: '2026-10-01T10:00:00Z',
        editedAt: '2026-10-01T10:30:00Z',
      },
    ]);
    await screen.findByText('Plain one');
    expect(screen.getByText('Alex Staff')).toBeInTheDocument();
    expect(screen.getByText('Sam Wong')).toBeInTheDocument();
    expect(screen.getByText(formatDateTime('2026-10-01T09:00:00Z'))).toBeInTheDocument();
    expect(screen.getAllByText('edited')).toHaveLength(1);
  });
});

describe('AC-E-02 posting and replying', () => {
  it('posts a top-level comment through the service with no parent', async () => {
    renderComments([]);
    fireEvent.change(await screen.findByPlaceholderText('Write a comment'), { target: { value: 'Hello team' } });
    fireEvent.click(screen.getByRole('button', { name: 'Comment' }));
    await waitFor(() => expect(svc.addComment).toHaveBeenCalled());
    const [ideaId, input] = svc.addComment.mock.calls[0];
    expect(ideaId).toBe('idea-1');
    expect(input.body).toBe('Hello team');
    expect(input.parentId ?? null).toBeNull();
  });

  it('a reply to a top-level comment posts that comment as the parent', async () => {
    renderComments([{ id: 'c1', body: 'Root comment' }]);
    await screen.findByText('Root comment');
    fireEvent.click(screen.getByRole('button', { name: /Reply/ }));
    const box = await screen.findByPlaceholderText('Write a reply');
    fireEvent.change(box, { target: { value: 'Replying' } });
    // The composer's own submit, not the row's "Reply" action.
    fireEvent.click(within(box.parentElement as HTMLElement).getByRole('button', { name: 'Reply' }));
    await waitFor(() => expect(svc.addComment).toHaveBeenCalledWith('idea-1', expect.objectContaining({ body: 'Replying', parentId: 'c1' })));
  });

  it('a reply to a reply posts the TOP-LEVEL parent id, never the reply id', async () => {
    renderComments([
      { id: 'c1', body: 'Root comment' },
      { id: 'r1', parentId: 'c1', body: 'First reply' },
    ]);
    await screen.findByText('First reply');
    const replyRow = screen.getByText('First reply').closest('div.flex.items-start') as HTMLElement;
    fireEvent.click(within(replyRow).getByRole('button', { name: /Reply/ }));
    const box = await screen.findByPlaceholderText('Write a reply');
    fireEvent.change(box, { target: { value: 'Nested answer' } });
    fireEvent.click(within(box.parentElement as HTMLElement).getByRole('button', { name: 'Reply' }));
    await waitFor(() => expect(svc.addComment).toHaveBeenCalled());
    const [, input] = svc.addComment.mock.calls[0];
    expect(input.parentId).toBe('c1');
  });
});

describe('AC-E-03 Edit / Delete follow the ss flags', () => {
  it('shows neither when ss says neither', async () => {
    renderComments([{ id: 'c1', body: 'Not mine', canEdit: false, canDelete: false }]);
    await screen.findByText('Not mine');
    expect(screen.queryByRole('button', { name: /Edit/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Delete/ })).toBeNull();
  });

  it('shows Edit only when canEdit, Delete only when canDelete', async () => {
    renderComments([
      { id: 'c1', body: 'Edit only', canEdit: true, canDelete: false },
      { id: 'c2', body: 'Delete only', canEdit: false, canDelete: true },
    ]);
    await screen.findByText('Edit only');
    const first = screen.getByText('Edit only').closest('div.flex.items-start') as HTMLElement;
    const second = screen.getByText('Delete only').closest('div.flex.items-start') as HTMLElement;
    expect(within(first).getByRole('button', { name: /Edit/ })).toBeInTheDocument();
    expect(within(first).queryByRole('button', { name: /Delete/ })).toBeNull();
    expect(within(second).queryByRole('button', { name: /Edit/ })).toBeNull();
    expect(within(second).getByRole('button', { name: /Delete/ })).toBeInTheDocument();
  });

  it('a manage holder does not get Delete on a comment ss did not allow (no client-side override)', async () => {
    const client = new QueryClient();
    svc.listComments.mockResolvedValue([comment({ id: 'c1', body: 'Someone elses', canDelete: false })]);
    const props = { ideaId: 'idea-1', frozen: false, canDeleteAny: true } as { ideaId: string; frozen: boolean };
    render(
      <QueryClientProvider client={client}>
        <IdeaComments {...props} />
      </QueryClientProvider>,
    );
    await screen.findByText('Someone elses');
    expect(screen.queryByRole('button', { name: /Delete/ })).toBeNull();
  });

  it('Delete becomes a 10 s server-deferred countdown with Cancel, no dialog, nothing deleted yet', async () => {
    pending.createPendingAction.mockResolvedValue({
      id: 'pa-9',
      action_key: 'idea_comment.delete',
      entity_type: 'idea_comment',
      entity_id: 'c1',
      commit_at: serverTime(10_000),
      window_seconds: 10,
    });
    // The server answers `current` with what is parked (Reviewer 4: items no longer poll from mount).
    pending.getCurrentPendingAction.mockResolvedValue({
      pending: {
        id: 'pa-9',
        action_key: 'idea_comment.delete',
        entity_type: 'idea_comment',
        entity_id: 'c1',
        commit_at: serverTime(10_000),
        window_seconds: 10,
      },
      last_outcome: null,
    });
    const confirm = vi.spyOn(window, 'confirm');
    renderComments([{ id: 'c1', body: 'Mine to delete', canEdit: true, canDelete: true }]);
    await screen.findByText('Mine to delete');
    fireEvent.click(screen.getByRole('button', { name: /Delete/ }));
    await waitFor(() =>
      expect(pending.createPendingAction).toHaveBeenCalledWith(
        expect.objectContaining({ entityId: 'c1', actionKey: 'idea_comment.delete',
        entityType: 'idea_comment',
        payload: expect.objectContaining({ idea_id: 'idea-1' }) }),
      ),
    );
    const countdown = await screen.findByTestId('deferred-countdown');
    expect(within(countdown).getByRole('timer')).toHaveTextContent(/Deleting in (9|10)s/);
    expect(within(countdown).getByRole('button', { name: /Cancel/ })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(confirm).not.toHaveBeenCalled();
    expect(svc.deleteComment).not.toHaveBeenCalled();
  });

  it('Edit opens the composer prefilled and Save sends the edit', async () => {
    svc.editComment.mockResolvedValue(comment({ id: 'c1' }));
    renderComments([{ id: 'c1', body: 'Original words', canEdit: true }]);
    await screen.findByText('Original words');
    fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
    const box = await screen.findByPlaceholderText('Edit comment');
    expect(box).toHaveValue('Original words');
    fireEvent.change(box, { target: { value: 'Better words' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(svc.editComment).toHaveBeenCalledWith('idea-1', 'c1', 'Better words'));
  });
});

describe('AC-E-04 deleted comments', () => {
  it('a deleted comment WITH replies shows "Comment deleted" and keeps its replies', async () => {
    renderComments([
      { id: 'c1', deleted: true },
      { id: 'r1', parentId: 'c1', body: 'Reply that survives' },
    ]);
    expect(await screen.findByText('Comment deleted')).toBeInTheDocument();
    expect(screen.getByText('Reply that survives')).toBeInTheDocument();
  });

  it('a deleted comment WITHOUT replies is not shown at all', async () => {
    renderComments([
      { id: 'c1', deleted: true },
      { id: 'c2', body: 'Still here' },
    ]);
    await screen.findByText('Still here');
    expect(screen.queryByText('Comment deleted')).toBeNull();
  });
});

describe('AC-E-06 plain text and empty bodies', () => {
  it('renders markup in a body as literal text, never as HTML', async () => {
    const hostile = '<b>bold</b><img src=x onerror=alert(1)>';
    const { container } = renderComments([{ id: 'c1', body: hostile }]);
    expect(await screen.findByText(hostile)).toBeInTheDocument();
    expect(container.querySelector('b')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
  });

  it('refuses an empty or whitespace-only body before calling the service', async () => {
    renderComments([]);
    const box = await screen.findByPlaceholderText('Write a comment');
    const submit = screen.getByRole('button', { name: 'Comment' });
    expect(submit).toBeDisabled();
    fireEvent.change(box, { target: { value: '   ' } });
    expect(submit).toBeDisabled();
    fireEvent.click(submit);
    expect(svc.addComment).not.toHaveBeenCalled();
  });
});

describe('states', () => {
  it('empty: heading and hint, no button other than the composer', async () => {
    renderComments([]);
    expect(await screen.findByText('No comments yet')).toBeInTheDocument();
  });

  it('a merged (frozen) idea has no composer and no Reply', async () => {
    renderComments([{ id: 'c1', body: 'Frozen thread', canEdit: true, canDelete: true }], { frozen: true });
    await screen.findByText('Frozen thread');
    expect(screen.queryByPlaceholderText('Write a comment')).toBeNull();
    expect(screen.queryByRole('button', { name: /Reply/ })).toBeNull();
  });
});
