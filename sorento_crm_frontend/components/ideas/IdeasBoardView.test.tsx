/**
 * AC-F-02 (reviewer 6): the board checks the card's own transitions BEFORE calling ss. A move
 * with no allowed transition is refused with the stated message, nothing is sent, and the card
 * snaps back. Also the board's error state (Retry, no skeleton under it).
 *
 * Dragging itself is dnd-kit's job; the Kanban shell is replaced by one that hands the board's
 * `onMove` to the test, so the board's own decision is what is exercised.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const kanban = vi.hoisted(() => ({ onMove: null as null | ((e: unknown) => void) }));
vi.mock('@/components/ui/kanban', () => {
  const Pass = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return {
    Kanban: ({ children, onMove }: { children: React.ReactNode; onMove: (e: unknown) => void }) => {
      kanban.onMove = onMove;
      return <div>{children}</div>;
    },
    KanbanBoard: Pass,
    KanbanColumn: Pass,
    KanbanColumnContent: Pass,
    KanbanItem: Pass,
    KanbanItemHandle: Pass,
    KanbanOverlay: () => null,
  };
});

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/ideas/board',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
const toastError = vi.fn();
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: (...a: unknown[]) => toastError(...a), dismiss: vi.fn() },
}));
const held = new Set<string>(['ideation.board.view', 'ideation.ideas.manage']);
vi.mock('@/hooks/usePermissions', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  useHasPermission: (slug: string) => held.has(slug),
}));

const svc = vi.hoisted(() => ({
  getBoard: vi.fn(),
  voteIdea: vi.fn(),
  moveIdeaToStatus: vi.fn(),
  reorderIdeas: vi.fn(),
  // The rest of the service surface `hooks/useIdeas` imports; unused here.
  createIdea: vi.fn(),
  getIdea: vi.fn(),
  listIdeas: vi.fn(),
  getMergedChildren: vi.fn(),
  listComments: vi.fn(),
  updateIdea: vi.fn(),
  restoreIdea: vi.fn(),
  mergeIdeas: vi.fn(),
  unmergeIdea: vi.fn(),
  promoteIdea: vi.fn(),
  uploadAttachment: vi.fn(),
  addComment: vi.fn(),
  editComment: vi.fn(),
  IDEA_STATUS_FILTER_OPTIONS: [],
}));
vi.mock('@/services/ideasService', () => svc);

import { IdeasBoardView, NOT_ALLOWED_MESSAGE } from './IdeasBoardView';

function idea(id: string, transitions: Array<{ toStatusId: string }>) {
  return {
    id,
    title: `Idea ${id}`,
    problem: `Problem ${id}`,
    ideaNumber: `IDEA-${id}`,
    submitterName: 'Jane',
    upvotes: 0,
    myVote: null,
    transitions: transitions.map((t, n) => ({ id: `t${n}`, label: 'x', toStatusLabel: 'x', ...t })),
  };
}

const BOARD = {
  columns: [
    { statusId: 's-new', key: 'new', title: 'New', color: 'blue', ideas: [idea('a', [{ toStatusId: 's-triaged' }])] },
    { statusId: 's-triaged', key: 'triaged', title: 'Triaged', color: 'indigo', ideas: [] },
    { statusId: 's-delivered', key: 'delivered', title: 'Delivered', color: 'green', ideas: [] },
  ],
};

function move(from: string, to: string, overIndex = 0) {
  act(() =>
    kanban.onMove?.({
      event: { active: { id: 'a' } },
      activeContainer: from,
      activeIndex: 0,
      overContainer: to,
      overIndex,
    }),
  );
}

function renderBoard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <IdeasBoardView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  svc.getBoard.mockResolvedValue(BOARD);
});

describe('AC-F-02 board moves follow the card transitions', () => {
  it('a move to a lane the card has no transition to is refused: message, no ss call, card back', async () => {
    renderBoard();
    await screen.findByText('Idea a');
    move('s-new', 's-delivered');
    expect(toastError).toHaveBeenCalledWith(NOT_ALLOWED_MESSAGE);
    expect(NOT_ALLOWED_MESSAGE).toBe('This idea cannot move to that status.');
    expect(svc.moveIdeaToStatus).not.toHaveBeenCalled();
    expect(svc.reorderIdeas).not.toHaveBeenCalled();
    // the card is still in its own lane
    expect(screen.getByText('Idea a')).toBeInTheDocument();
  });

  it('a move along an allowed transition calls ss with that lane', async () => {
    svc.moveIdeaToStatus.mockResolvedValue({ statusLabel: 'Triaged' });
    renderBoard();
    await screen.findByText('Idea a');
    move('s-new', 's-triaged');
    await waitFor(() => expect(svc.moveIdeaToStatus).toHaveBeenCalledWith('a', 's-triaged'));
    expect(toastError).not.toHaveBeenCalled();
  });
});

describe('board error state', () => {
  it('shows the message with Retry and no skeleton under it', async () => {
    svc.getBoard.mockRejectedValueOnce(new Error("The Ideas workspace isn't reachable right now."));
    const { container } = renderBoard();
    expect(await screen.findByText("The Ideas workspace isn't reachable right now.")).toBeInTheDocument();
    expect(container.querySelector('[data-slot="skeleton"]')).toBeNull();
    svc.getBoard.mockResolvedValue(BOARD);
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    expect(await screen.findByText('Idea a')).toBeInTheDocument();
  });
});
