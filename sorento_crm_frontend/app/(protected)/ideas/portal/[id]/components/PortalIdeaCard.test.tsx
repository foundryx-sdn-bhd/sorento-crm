/**
 * IDEA-CRM-SIDE Phase 2 red tests for the portal card page `PortalIdeaCard` (AC-E-06..E-08).
 *
 * Contract: `<PortalIdeaCard id="idea-1" />`; reads through `@/services/ideasPortalService`;
 * manager = `useHasPermission('ideation.ideas.manage')`; comment delete is the deferred pending
 * action `idea_comment.delete` (never a dialog). Composer placeholders and button names mirror
 * `IdeaComments`: "Write a comment" / "Write a reply", buttons "Comment" / "Reply".
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/ideas/portal/idea-1',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { name: 'Pat Staff' } }, status: 'authenticated' }),
}));

const held = new Set<string>();
vi.mock('@/hooks/usePermissions', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, useHasPermission: (slug: string) => held.has(slug) };
});

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() } }));
vi.mock('@/components/common/deferredToast', () => ({
  deferredToast: vi.fn(() => 'toast-1'),
  dismissDeferredToast: vi.fn(),
}));
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const svc = vi.hoisted(() => ({
  getRoadmap: vi.fn(),
  listFeedback: vi.fn(),
  getChangelog: vi.fn(),
  getPortalIdea: vi.fn(),
  getVoters: vi.fn(),
  getActivity: vi.fn(),
  listPortalComments: vi.fn(),
  togglePortalVote: vi.fn(),
  postPortalComment: vi.fn(),
  likePortalComment: vi.fn(),
  setPortalHidden: vi.fn(),
  setPortalNote: vi.fn(),
}));
vi.mock('@/services/ideasPortalService', () => svc);

const pending = vi.hoisted(() => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn(),
}));
vi.mock('@/services/pendingActionService', () => pending);

import { PortalIdeaCard } from './PortalIdeaCard';

const MANAGE = 'ideation.ideas.manage';
const VIEW = 'ideation.board.view';

function serverTime(offsetMs: number): string {
  return new Date(Date.now() + offsetMs).toISOString().replace(/\.\d+Z$/, '');
}

function portalIdea(over: Record<string, unknown> = {}) {
  return {
    id: 'idea-1',
    ideaNumber: 'IDEA-0042',
    title: 'Faster quotes',
    statusLabel: 'Planned',
    statusColor: 'blue',
    upvotes: 3,
    myVote: false,
    commentCount: 1,
    problem: 'Quotes take too long to write',
    desiredOutcome: 'Same hour quotes',
    useCases: ['Dealer asks', 'Rush order'],
    currentWorkaround: 'Spreadsheet',
    submitterFirstName: 'Jane',
    timeline: [
      { key: 'new', label: 'Received', color: 'gray', state: 'done' },
      { key: 'planned', label: 'Planned', color: 'blue', state: 'current' },
      { key: 'shipped', label: 'Shipped', color: 'green', state: 'upcoming' },
    ],
    portalHidden: false,
    note: 'Existing note',
    ...over,
  };
}

function comment(over: Record<string, unknown> = {}) {
  return {
    id: 'c1',
    parentId: null,
    authorName: 'Alex Staff',
    authorRole: 'Sales',
    body: 'A comment',
    createdAt: '2026-10-01T09:00:00Z',
    editedAt: null,
    deleted: false,
    isDeleted: false,
    canEdit: false,
    canDelete: false,
    likeCount: 0,
    likedByMe: false,
    ...over,
  };
}

function renderCard(idea = portalIdea(), comments = [comment()]) {
  svc.getPortalIdea.mockResolvedValue(idea);
  svc.listPortalComments.mockResolvedValue(comments);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PortalIdeaCard id="idea-1" />
    </QueryClientProvider>,
  );
}

async function loaded() {
  await screen.findByText('Quotes take too long to write');
}

beforeEach(() => {
  vi.clearAllMocks();
  held.clear();
  held.add(VIEW);
  svc.getVoters.mockResolvedValue([
    { name: 'Jane Lim', role: 'Sales', isMe: false },
    { name: 'Pat Staff', role: 'Ops', isMe: true },
  ]);
  svc.getActivity.mockResolvedValue([
    { id: 'a1', kind: 'status', label: 'Moved to Planned', at: '2026-10-02T09:00:00Z', actorName: 'Sam Wong' },
  ]);
  svc.togglePortalVote.mockResolvedValue({ myVote: true, upvotes: 4 });
  svc.postPortalComment.mockResolvedValue(comment({ id: 'new' }));
  svc.likePortalComment.mockResolvedValue({ likeCount: 1, likedByMe: true });
  svc.setPortalHidden.mockResolvedValue({ portalHidden: true });
  svc.setPortalNote.mockResolvedValue({ note: 'x' });
  pending.getCurrentPendingAction.mockResolvedValue({ pending: null, last_outcome: null });
  pending.cancelPendingAction.mockResolvedValue({ id: 'pa-1', status: 'cancelled' });
});

describe('AC-E-06 the card', () => {
  it('shows the number, title and status in the header', async () => {
    renderCard();
    await loaded();
    expect(screen.getByText('IDEA-0042')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Faster quotes/ })).toBeInTheDocument();
    expect(svc.getPortalIdea).toHaveBeenCalledWith('idea-1');
    expect(screen.getAllByText('Planned').length).toBeGreaterThan(0);
  });

  it('shows the four fields with their values, use cases one per line', async () => {
    renderCard();
    await loaded();
    for (const label of ['Business problem', 'Desired outcome', 'Use cases', 'Current workaround']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    for (const value of ['Same hour quotes', 'Dealer asks', 'Rush order', 'Spreadsheet']) {
      expect(screen.getByText(value)).toBeInTheDocument();
    }
  });

  it('shows the timeline, voters, activity and comments', async () => {
    renderCard();
    await loaded();
    expect(screen.getByText('Received')).toBeInTheDocument();
    expect(screen.getByText('Shipped')).toBeInTheDocument();
    expect(await screen.findByText('Jane Lim')).toBeInTheDocument();
    expect(await screen.findByText('Moved to Planned')).toBeInTheDocument();
    expect(await screen.findByText('A comment')).toBeInTheDocument();
    expect(screen.getByText('Alex Staff')).toBeInTheDocument();
  });

  it('never prints a record id', async () => {
    renderCard();
    await loaded();
    expect(document.body.textContent).not.toMatch(/\bidea-1\b/);
  });

  it('the vote box shows the count and toggling calls togglePortalVote', async () => {
    renderCard();
    await loaded();
    const vote = screen.getByRole('button', { name: /^Upvote, 3 votes/ });
    fireEvent.click(vote);
    await waitFor(() => expect(svc.togglePortalVote).toHaveBeenCalledWith('idea-1'));
  });

  it('an idea already voted shows the filled state', async () => {
    renderCard(portalIdea({ myVote: true }));
    await loaded();
    expect(screen.getByRole('button', { name: /Remove your upvote/ })).toHaveAttribute('aria-pressed', 'true');
  });

  it('posts a top-level comment', async () => {
    renderCard();
    await loaded();
    fireEvent.change(await screen.findByPlaceholderText('Write a comment'), { target: { value: 'Hello team' } });
    fireEvent.click(screen.getByRole('button', { name: 'Comment' }));
    await waitFor(() => expect(svc.postPortalComment).toHaveBeenCalled());
    const [id, input] = svc.postPortalComment.mock.calls[0];
    expect(id).toBe('idea-1');
    expect(input.body).toBe('Hello team');
    expect(input.parentId ?? null).toBeNull();
  });

  it('replies to a comment with that comment as the parent', async () => {
    renderCard();
    await screen.findByText('A comment');
    fireEvent.click(screen.getByRole('button', { name: /Reply/ }));
    const box = await screen.findByPlaceholderText('Write a reply');
    fireEvent.change(box, { target: { value: 'Replying' } });
    fireEvent.click(within(box.parentElement as HTMLElement).getByRole('button', { name: 'Reply' }));
    await waitFor(() =>
      expect(svc.postPortalComment).toHaveBeenCalledWith(
        'idea-1',
        expect.objectContaining({ body: 'Replying', parentId: 'c1' }),
      ),
    );
  });

  it('refuses an empty comment before calling the service', async () => {
    renderCard();
    await loaded();
    const box = await screen.findByPlaceholderText('Write a comment');
    fireEvent.change(box, { target: { value: '   ' } });
    expect(screen.getByRole('button', { name: 'Comment' })).toBeDisabled();
    expect(svc.postPortalComment).not.toHaveBeenCalled();
  });

  it('likes a comment through the service', async () => {
    renderCard(portalIdea(), [comment({ likeCount: 2 })]);
    await screen.findByText('A comment');
    fireEvent.click(screen.getByRole('button', { name: /like/i }));
    await waitFor(() => expect(svc.likePortalComment).toHaveBeenCalledWith('idea-1', 'c1'));
  });

  it('shows the like count and the liked state', async () => {
    renderCard(portalIdea(), [comment({ likeCount: 2, likedByMe: true })]);
    await screen.findByText('A comment');
    const like = screen.getByRole('button', { name: /like/i });
    expect(like).toHaveTextContent('2');
    expect(like).toHaveAttribute('aria-pressed', 'true');
  });

  it('renders markup in a comment as literal text', async () => {
    const hostile = '<b>bold</b><img src=x onerror=alert(1)>';
    const { container } = renderCard(portalIdea(), [comment({ body: hostile })]);
    expect(await screen.findByText(hostile)).toBeInTheDocument();
    expect(container.querySelector('b')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
  });

  it('a load error shows a message with Retry, not an empty card', async () => {
    svc.getPortalIdea.mockRejectedValue(new Error("The Ideas workspace isn't reachable right now."));
    svc.listPortalComments.mockResolvedValue([]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <PortalIdeaCard id="idea-1" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/isn't reachable right now/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});

describe('AC-E-08 comment delete is the deferred countdown', () => {
  it('Delete parks idea_comment.delete with a 10 s countdown, no dialog, no confirm', async () => {
    const parked = {
      id: 'pa-9',
      action_key: 'idea_comment.delete',
      entity_type: 'idea_comment',
      entity_id: 'c1',
      commit_at: serverTime(10_000),
      window_seconds: 10,
    };
    pending.createPendingAction.mockResolvedValue(parked);
    pending.getCurrentPendingAction.mockResolvedValue({ pending: parked, last_outcome: null });
    const confirm = vi.spyOn(window, 'confirm');
    renderCard(portalIdea(), [comment({ canDelete: true })]);
    await screen.findByText('A comment');
    fireEvent.click(screen.getByRole('button', { name: /Delete/ }));
    await waitFor(() =>
      expect(pending.createPendingAction).toHaveBeenCalledWith(
        expect.objectContaining({
          actionKey: 'idea_comment.delete',
          entityType: 'idea_comment',
          entityId: 'c1',
          payload: expect.objectContaining({ idea_id: 'idea-1' }),
        }),
      ),
    );
    const countdown = await screen.findByTestId('deferred-countdown');
    expect(within(countdown).getByRole('timer')).toHaveTextContent(/Deleting in (9|10)s/);
    expect(within(countdown).getByRole('button', { name: /Cancel/ })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(confirm).not.toHaveBeenCalled();
  });

  it('no Delete button when ss did not allow it', async () => {
    renderCard(portalIdea(), [comment({ canDelete: false })]);
    await screen.findByText('A comment');
    expect(screen.queryByRole('button', { name: /Delete/ })).toBeNull();
  });
});

describe('AC-E-07 manager controls', () => {
  it('a non-manager sees neither the Hide from portal switch nor the changelog note editor', async () => {
    renderCard();
    await loaded();
    expect(screen.queryByRole('switch', { name: /Hide from portal/ })).toBeNull();
    expect(screen.queryByLabelText('Changelog note')).toBeNull();
    expect(screen.queryByText('Hide from portal')).toBeNull();
  });

  it('a manager sees the switch (off) and toggling it calls setPortalHidden(id, true)', async () => {
    held.add(MANAGE);
    renderCard();
    await loaded();
    const toggle = await screen.findByRole('switch', { name: /Hide from portal/ });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(svc.setPortalHidden).toHaveBeenCalledWith('idea-1', true));
  });

  it('a hidden card shows the switch on, and toggling sends false', async () => {
    held.add(MANAGE);
    renderCard(portalIdea({ portalHidden: true }));
    await loaded();
    const toggle = await screen.findByRole('switch', { name: /Hide from portal/ });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(svc.setPortalHidden).toHaveBeenCalledWith('idea-1', false));
  });

  it('a manager edits the changelog note in place and Save sends it', async () => {
    held.add(MANAGE);
    renderCard();
    await loaded();
    const note = await screen.findByLabelText('Changelog note');
    expect(note).toHaveValue('Existing note');
    fireEvent.change(note, { target: { value: 'Shipped in v2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(svc.setPortalNote).toHaveBeenCalledWith('idea-1', 'Shipped in v2'));
  });

  it('a note over 500 characters is refused before calling the service', async () => {
    held.add(MANAGE);
    renderCard();
    await loaded();
    fireEvent.change(await screen.findByLabelText('Changelog note'), { target: { value: 'x'.repeat(501) } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(svc.setPortalNote).not.toHaveBeenCalled();
  });
});
