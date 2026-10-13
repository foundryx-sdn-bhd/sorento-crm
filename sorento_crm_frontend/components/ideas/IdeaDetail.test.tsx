/**
 * IDEATION-IN-CRM Phase 2 red tests for the idea page: header states (AC-D-02), permission
 * gating (AC-B-01, AC-B-02), the "..." menu with one-click Promote and the countdowns
 * (AC-D-04), the metadata strip and no-UUID rule (AC-D-01, AC-D-06).
 *
 * Seams: `@/services/ideasService` (the page's only door to the backend), the pending-actions
 * service (Archive/Delete are server-deferred, never a dialog) and `useHasPermission`. The real
 * hooks, DetailActions, VoteBox and IdeaComments render.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/ideas/idea-1',
  useSearchParams: () => new URLSearchParams(),
}));

// An admin-looking session: the Phase 1 fallback granted manage to this user. It must not.
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { name: 'Pat Admin', roleName: 'Admin' } }, status: 'authenticated' }),
}));

const held = new Set<string>();
vi.mock('@/hooks/usePermissions', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, useHasPermission: (slug: string) => held.has(slug) };
});

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('@/lib/toast', () => ({
  toast: {
    success: (...a: unknown[]) => toastSuccess(...a),
    error: (...a: unknown[]) => toastError(...a),
    dismiss: vi.fn(),
  },
}));

vi.mock('@/components/common/deferredToast', () => ({
  deferredToast: vi.fn(() => 'toast-1'),
  dismissDeferredToast: vi.fn(),
}));

const svc = vi.hoisted(() => ({
  getIdea: vi.fn(),
  listIdeas: vi.fn(),
  getMergedChildren: vi.fn(),
  listComments: vi.fn(),
  voteIdea: vi.fn(),
  updateIdea: vi.fn(),
  moveIdeaToStatus: vi.fn(),
  restoreIdea: vi.fn(),
  unmergeIdea: vi.fn(),
  mergeIdeas: vi.fn(),
  promoteIdea: vi.fn(),
  uploadAttachment: vi.fn(),
  fetchAttachment: vi.fn(),
  addComment: vi.fn(),
  editComment: vi.fn(),
  createIdea: vi.fn(),
  getBoard: vi.fn(),
  reorderIdeas: vi.fn(),
  getSimilarIdeas: vi.fn(),
  // The real helper's contract (AC-B-03), so a component that imports it still works under this mock.
  normalizeUseCases: (v: unknown) =>
    (Array.isArray(v) ? v : typeof v === 'string' ? v.split(/\r?\n/) : [])
      .map((x: unknown) => String(x).trim())
      .filter(Boolean),
  IDEA_STATUS_FILTER_OPTIONS: [{ value: 'archived', label: 'Archived' }],
}));
vi.mock('@/services/ideasService', () => svc);

const pending = vi.hoisted(() => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn(),
}));
vi.mock('@/services/pendingActionService', () => pending);

import { formatDateTime } from '@/lib/helpers';
import { IdeaDetail, IdeaDetailHeader } from './IdeaDetail';

const MANAGE = 'ideation.ideas.manage';
const VIEW = 'ideation.board.view';

function serverTime(offsetMs: number): string {
  return new Date(Date.now() + offsetMs).toISOString().replace(/\.\d+Z$/, '');
}

function makeIdea(over: Record<string, unknown> = {}) {
  return {
    id: 'idea-1',
    productName: 'Sorento CRM',
    status: 'new',
    statusId: 'st-new',
    statusLabel: 'New',
    statusColor: 'info',
    statusIsArchived: false,
    transitions: [{ id: 'tr-1', label: 'Triage', toStatusId: 'st-triaged', toStatusLabel: 'Triaged' }],
    advanceTransitionId: 'tr-1',
    title: 'Faster quotes',
    problem: 'Quotes take too long to write',
    proposedSolution: 'Templates',
    impact: 'Saves a day a week',
    department: 'Sales',
    rawText: 'original words',
    source: 'whatsapp',
    submitterName: 'Jane Lim',
    upvotes: 3,
    myVote: null,
    priority: 1,
    rank: 1,
    attachments: [],
    createdAt: '2026-07-21T09:05:00Z',
    ideaNumber: 'IDEA-0042',
    mergedIntoId: null,
    mergedInto: null,
    mergedCount: 0,
    ...over,
  };
}

function renderDetail(idea: Record<string, unknown>) {
  svc.getIdea.mockResolvedValue(idea);
  svc.listIdeas.mockResolvedValue([idea]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <IdeaDetail id={String(idea.id)} />
    </QueryClientProvider>,
  );
}

async function loaded() {
  await screen.findByRole('heading', { name: /Faster quotes/ });
}

beforeEach(() => {
  vi.clearAllMocks();
  held.clear();
  held.add(VIEW);
  svc.getMergedChildren.mockResolvedValue([]);
  svc.listComments.mockResolvedValue([]);
  svc.getSimilarIdeas.mockResolvedValue({ available: true, ideas: [] });
  pending.getCurrentPendingAction.mockResolvedValue({ pending: null, last_outcome: null });
  pending.cancelPendingAction.mockResolvedValue({ id: 'pa-1', status: 'cancelled' });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('AC-D-02 header states for a manage holder', () => {
  beforeEach(() => held.add(MANAGE));

  it('state A: primary is "Move to <advance target label>" with Edit beside it; clicking moves', async () => {
    renderDetail(makeIdea());
    await loaded();
    const move = screen.getByRole('button', { name: /Move to Triaged/ });
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
    svc.moveIdeaToStatus.mockResolvedValue(makeIdea({ statusLabel: 'Triaged' }));
    fireEvent.click(move);
    await waitFor(() => expect(svc.moveIdeaToStatus).toHaveBeenCalledWith('idea-1', 'st-triaged'));
  });

  it('state B: no next move makes Edit the only primary action', async () => {
    renderDetail(makeIdea({ transitions: [], advanceTransitionId: null }));
    await loaded();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Move to/ })).toBeNull();
  });

  it('state C: archived shows Restore (primary) and Edit, no Move', async () => {
    renderDetail(makeIdea({ statusIsArchived: true, statusLabel: 'Archived' }));
    await loaded();
    const restore = screen.getByRole('button', { name: /Restore/ });
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Move to/ })).toBeNull();
    svc.restoreIdea.mockResolvedValue(makeIdea());
    fireEvent.click(restore);
    // AC-D-02: it posts the idea's own outgoing transition target, never a hardcoded key.
    await waitFor(() => expect(svc.restoreIdea).toHaveBeenCalledWith('idea-1', 'st-triaged'));
  });

  it('AC-D-02: an archived-flagged idea with NO outgoing transition (closed, duplicate) shows no Restore; Edit is primary', async () => {
    renderDetail(makeIdea({ statusIsArchived: true, statusLabel: 'Closed', transitions: [], advanceTransitionId: null }));
    await loaded();
    expect(screen.queryByRole('button', { name: /Restore/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
  });

  it('state D: a merged child shows Unmerge, no Edit, a disabled vote box and no comment composer', async () => {
    renderDetail(
      makeIdea({ mergedIntoId: 'idea-9', mergedInto: { id: 'idea-9', ideaNumber: 'IDEA-0009', title: 'Survivor' } }),
    );
    await loaded();
    const unmerge = screen.getByRole('button', { name: /Unmerge/ });
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Move to/ })).toBeNull();
    expect(screen.getByRole('button', { name: /upvote/i })).toBeDisabled();
    expect(screen.queryByPlaceholderText('Write a comment')).toBeNull();
    svc.unmergeIdea.mockResolvedValue(makeIdea());
    fireEvent.click(unmerge);
    await waitFor(() => expect(svc.unmergeIdea).toHaveBeenCalledWith('idea-1'));
  });

  it('Edit swaps values for inputs in place and Save sends the edited problem', async () => {
    renderDetail(makeIdea());
    await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const problem = await screen.findByLabelText('Business problem');
    expect(problem).toHaveValue('Quotes take too long to write');
    fireEvent.change(problem, { target: { value: 'Quotes take far too long' } });
    svc.updateIdea.mockResolvedValue(makeIdea());
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(svc.updateIdea).toHaveBeenCalledWith(
        'idea-1',
        expect.objectContaining({ problem: 'Quotes take far too long' }),
      ),
    );
  });
});

describe('IDEA-CRM-SIDE field labels', () => {
  it('shows Business problem and hides Proposed solution, Impact and Department in view and edit', async () => {
    renderDetail(makeIdea({ isMine: true }));
    await loaded();
    expect(screen.getByText('Business problem')).toBeInTheDocument();
    for (const label of ['Problem statement', 'Proposed solution', 'Impact', 'Department']) {
      expect(screen.queryByText(label)).toBeNull();
    }
    expect(screen.queryByText('Templates')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await screen.findByLabelText('Business problem');
    for (const label of ['Proposed solution', 'Impact', 'Department']) {
      expect(screen.queryByLabelText(label)).toBeNull();
    }
  });

  it('Save leaves the hidden legacy fields untouched (never sends them)', async () => {
    renderDetail(makeIdea({ isMine: true }));
    await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(await screen.findByLabelText('Business problem'), { target: { value: 'Edited' } });
    svc.updateIdea.mockResolvedValue(makeIdea({ isMine: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(svc.updateIdea).toHaveBeenCalled());
    const body = svc.updateIdea.mock.calls[0][1];
    for (const key of ['proposedSolution', 'impact', 'department']) {
      expect(body).not.toHaveProperty(key);
    }
  });
});

const FIELD_LABELS = ['Title', 'Business problem', 'Desired outcome', 'Use cases', 'Current workaround', 'Original message'];

function richIdea(over: Record<string, unknown> = {}) {
  return makeIdea({
    desiredOutcome: 'Same hour quotes',
    useCases: ['Dealer asks', 'Rush order'],
    currentWorkaround: 'Spreadsheet',
    isMine: true,
    ...over,
  });
}

describe('IDEA-CRM-SIDE AC-B-02 fields in view and edit', () => {
  it('view shows the six fields with their values, in order', async () => {
    renderDetail(richIdea());
    await loaded();
    const labels = FIELD_LABELS.map((l) => screen.getByText(l, { selector: ':not(h1, h2, h3, option)' }));
    for (let i = 1; i < labels.length; i++) {
      expect(labels[i - 1].compareDocumentPosition(labels[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    for (const value of ['Quotes take too long to write', 'Same hour quotes', 'Spreadsheet', 'original words']) {
      expect(screen.getByText(value)).toBeInTheDocument();
    }
  });

  it('use cases are shown one line each', async () => {
    renderDetail(richIdea());
    await loaded();
    expect(screen.getByText('Dealer asks')).toBeInTheDocument();
    expect(screen.getByText('Rush order')).toBeInTheDocument();
  });

  it('AC-B-03: a useCases string (ss #123 shape) is shown as one line per use case', async () => {
    renderDetail(richIdea({ useCases: 'Dealer asks\n\n Rush order ' }));
    await loaded();
    expect(screen.getByText('Dealer asks')).toBeInTheDocument();
    expect(screen.getByText('Rush order')).toBeInTheDocument();
  });

  it('edit swaps the same six fields to inputs in place, prefilled', async () => {
    renderDetail(richIdea());
    await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await screen.findByLabelText('Business problem');
    const inputs = FIELD_LABELS.map((l) => screen.getByLabelText(l));
    for (let i = 1; i < inputs.length; i++) {
      expect(inputs[i - 1].compareDocumentPosition(inputs[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(screen.getByLabelText('Title')).toHaveValue('Faster quotes');
    expect(screen.getByLabelText('Desired outcome')).toHaveValue('Same hour quotes');
    expect(screen.getByLabelText('Use cases')).toHaveValue('Dealer asks\nRush order');
    expect(screen.getByLabelText('Current workaround')).toHaveValue('Spreadsheet');
    expect(screen.getByLabelText('Original message')).toHaveValue('original words');
  });

  it('Save sends exactly title, problem, desiredOutcome, useCases (list), currentWorkaround, rawText', async () => {
    renderDetail(richIdea());
    await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(await screen.findByLabelText('Use cases'), { target: { value: 'x\n\n y ' } });
    fireEvent.change(screen.getByLabelText('Desired outcome'), { target: { value: 'Faster' } });
    svc.updateIdea.mockResolvedValue(richIdea());
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(svc.updateIdea).toHaveBeenCalled());
    const [id, body] = svc.updateIdea.mock.calls[0];
    expect(id).toBe('idea-1');
    expect(Object.keys(body).sort()).toEqual(
      ['currentWorkaround', 'desiredOutcome', 'problem', 'rawText', 'title', 'useCases'],
    );
    expect(body.useCases).toEqual(['x', 'y']);
    expect(body.desiredOutcome).toBe('Faster');
    expect(body.title).toBe('Faster quotes');
  });
});

describe('IDEA-CRM-SIDE AC-C Similar ideas tab', () => {
  const similar = [
    makeIdea({ id: 'idea-2', ideaNumber: 'IDEA-0043', title: 'Quote templates', statusLabel: 'Triaged', upvotes: 7 }),
    makeIdea({ id: 'idea-3', ideaNumber: 'IDEA-0044', title: 'Quote macros', statusLabel: 'Planned', upvotes: 2 }),
  ];

  async function openSimilar() {
    renderDetail(makeIdea());
    await loaded();
    fireEvent.click(screen.getByRole('tab', { name: /Similar ideas/ }));
  }

  it('AC-C-01: the tab exists beside Details and Attachments', async () => {
    renderDetail(makeIdea());
    await loaded();
    for (const name of ['Details', 'Similar ideas', 'Attachments']) {
      expect(screen.getByRole('tab', { name: new RegExp(name) })).toBeInTheDocument();
    }
  });

  it('AC-C-01: lists number, linked title, status and upvotes in the order ss returns', async () => {
    svc.getSimilarIdeas.mockResolvedValue({ available: true, ideas: similar });
    await openSimilar();
    expect(await screen.findByText('IDEA-0043')).toBeInTheDocument();
    expect(svc.getSimilarIdeas).toHaveBeenCalledWith('idea-1');
    const link = screen.getByRole('link', { name: 'Quote templates' });
    expect(link).toHaveAttribute('href', '/ideas/idea-2');
    expect(screen.getByRole('link', { name: 'Quote macros' })).toHaveAttribute('href', '/ideas/idea-3');
    expect(screen.getByText('Triaged')).toBeInTheDocument();
    expect(screen.getByText('Planned')).toBeInTheDocument();
    expect(screen.getByText('7')).toBeInTheDocument();
    const a = screen.getByText('IDEA-0043');
    const b = screen.getByText('IDEA-0044');
    expect(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('AC-C-02: none found shows the heading "No similar ideas" and no button', async () => {
    svc.getSimilarIdeas.mockResolvedValue({ available: true, ideas: [] });
    await openSimilar();
    expect(await screen.findByRole('heading', { name: 'No similar ideas' })).toBeInTheDocument();
    expect(within(screen.getByRole('tabpanel')).queryByRole('button')).toBeNull();
  });

  it('AC-C-03: available=false shows the unavailable text and Retry refetches', async () => {
    svc.getSimilarIdeas.mockResolvedValueOnce({ available: false, ideas: [] });
    await openSimilar();
    expect(await screen.findByText('Similar ideas are unavailable right now')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'No similar ideas' })).toBeNull();
    svc.getSimilarIdeas.mockResolvedValueOnce({ available: true, ideas: similar });
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('IDEA-0043')).toBeInTheDocument();
    expect(svc.getSimilarIdeas).toHaveBeenCalledTimes(2);
  });

  it('AC-C-03: a request error also shows the unavailable text with Retry', async () => {
    svc.getSimilarIdeas.mockRejectedValueOnce(new Error('boom'));
    await openSimilar();
    expect(await screen.findByText('Similar ideas are unavailable right now')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('AC-C-04: a manager sees Merge on each row, and it opens the merge modal with both ideas', async () => {
    held.add(MANAGE);
    svc.getSimilarIdeas.mockResolvedValue({ available: true, ideas: similar });
    svc.listIdeas.mockResolvedValue([makeIdea(), ...similar]);
    await openSimilar();
    await screen.findByText('IDEA-0043');
    const panel = screen.getByRole('tabpanel');
    const merges = within(panel).getAllByRole('button', { name: /^Merge/ });
    expect(merges).toHaveLength(2);
    fireEvent.click(merges[0]);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('radio', { name: 'Keep IDEA-0042' })).toBeInTheDocument();
    expect(within(dialog).getByRole('radio', { name: 'Keep IDEA-0043' })).toBeInTheDocument();
    expect(within(dialog).queryByRole('radio', { name: 'Keep IDEA-0044' })).toBeNull();
  });

  it('AC-C-04: a viewer without manage sees no Merge button', async () => {
    svc.getSimilarIdeas.mockResolvedValue({ available: true, ideas: similar });
    await openSimilar();
    await screen.findByText('IDEA-0043');
    expect(within(screen.getByRole('tabpanel')).queryByRole('button', { name: /^Merge/ })).toBeNull();
  });
});

describe('AC-B-01 / AC-B-02 without manage', () => {
  it('shows the vote box and comments only: no Edit, no Move, no "..." menu (admin role is not enough)', async () => {
    renderDetail(makeIdea());
    await loaded();
    expect(screen.getByRole('button', { name: /upvote/i })).toBeEnabled();
    expect(screen.getByPlaceholderText('Write a comment')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Move to/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Idea options/ })).toBeNull();
  });

  it('the vote box still votes', async () => {
    renderDetail(makeIdea());
    await loaded();
    svc.voteIdea.mockResolvedValue(makeIdea({ upvotes: 4, myVote: 'up' }));
    fireEvent.click(screen.getByRole('button', { name: /upvote/i }));
    await waitFor(() => expect(svc.voteIdea).toHaveBeenCalledWith('idea-1'));
  });
});

describe('Q3 own-idea edit without manage', () => {
  it('a viewer without manage sees Edit on their own idea (isMine true), edits in place and Save calls updateIdea', async () => {
    renderDetail(makeIdea({ isMine: true }));
    await loaded();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const problem = await screen.findByLabelText('Business problem');
    fireEvent.change(problem, { target: { value: 'Quotes take far too long' } });
    svc.updateIdea.mockResolvedValue(makeIdea({ isMine: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(svc.updateIdea).toHaveBeenCalledWith(
        'idea-1',
        expect.objectContaining({ problem: 'Quotes take far too long' }),
      ),
    );
  });

  it.each([
    ['isMine false', { isMine: false }],
    ['isMine absent', {}],
  ])('no Edit button without manage when %s', async (_label, over) => {
    renderDetail(makeIdea(over));
    await loaded();
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
  });

  it('isMine does not unlock manage actions: no Move, Restore, Unmerge or "..." menu for a non-manage owner', async () => {
    renderDetail(makeIdea({ isMine: true }));
    await loaded();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Move to/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Idea options/ })).toBeNull();
    for (const label of ['Archive', 'Delete', 'Merge', 'Promote']) {
      expect(screen.queryByRole('button', { name: new RegExp(label) })).toBeNull();
      expect(screen.queryByRole('menuitem', { name: new RegExp(label) })).toBeNull();
    }
  });

  it('an archived own idea shows Edit but no Restore for a non-manage owner', async () => {
    renderDetail(makeIdea({ isMine: true, statusIsArchived: true, statusLabel: 'Archived' }));
    await loaded();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Restore/ })).toBeNull();
  });
});

describe('AC-D-04 the "..." menu', () => {
  beforeEach(() => held.add(MANAGE));

  async function openMenu() {
    renderDetail(makeIdea());
    await loaded();
    const trigger = screen.getByRole('button', { name: /Idea options/ });
    // Radix opens on pointerdown; once open it hides the trigger from the a11y tree.
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
  }

  it('lists Promote to BR, Merge into another idea, Archive and Delete', async () => {
    await openMenu();
    for (const label of ['Promote to BR', 'Merge into another idea', 'Archive', 'Delete']) {
      expect(await screen.findByRole('menuitem', { name: new RegExp(label) })).toBeInTheDocument();
    }
  });

  it('Promote is ONE click: posts {title: the idea title} at once, toasts success, opens no dialog', async () => {
    svc.promoteIdea.mockResolvedValue({ id: 'br-1', title: 'Quote templates' });
    await openMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Promote to BR/ }));
    await waitFor(() =>
      expect(svc.promoteIdea).toHaveBeenCalledWith('idea-1', expect.objectContaining({ title: 'Faster quotes' })),
    );
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith(expect.stringContaining('Quote templates')));
  });

  it("an ss 403 on promote shows ss's own message as an error toast", async () => {
    const message = 'This user has no Business Requirements access in the Ideas workspace.';
    svc.promoteIdea.mockRejectedValue(new Error(message));
    await openMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Promote to BR/ }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith(message));
  });

  it('Archive parks the server-deferred action idea.archive and shows a 5 s countdown with Cancel, no dialog', async () => {
    pending.createPendingAction.mockResolvedValue({
      id: 'pa-1',
      action_key: 'idea.archive',
      entity_type: 'idea',
      entity_id: 'idea-1',
      commit_at: serverTime(5000),
      window_seconds: 5,
    });
    const confirm = vi.spyOn(window, 'confirm');
    await openMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Archive/ }));
    await waitFor(() =>
      expect(pending.createPendingAction).toHaveBeenCalledWith(
        expect.objectContaining({ actionKey: 'idea.archive', entityType: 'idea', entityId: 'idea-1' }),
      ),
    );
    const countdown = await screen.findByTestId('deferred-countdown');
    expect(within(countdown).getByRole('timer')).toHaveTextContent(/Archiving in [45]s/);
    expect(within(countdown).getByRole('button', { name: /Cancel/ })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(confirm).not.toHaveBeenCalled();
    // The browser never applies it itself: that is the server's job when the window lapses.
    expect(svc.updateIdea).not.toHaveBeenCalled();
  });

  it('Delete parks idea.delete with a 10 s countdown and no dialog', async () => {
    pending.createPendingAction.mockResolvedValue({
      id: 'pa-2',
      action_key: 'idea.delete',
      entity_type: 'idea',
      entity_id: 'idea-1',
      commit_at: serverTime(10_000),
      window_seconds: 10,
    });
    const confirm = vi.spyOn(window, 'confirm');
    await openMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Delete/ }));
    await waitFor(() =>
      expect(pending.createPendingAction).toHaveBeenCalledWith(
        expect.objectContaining({ actionKey: 'idea.delete', entityType: 'idea', entityId: 'idea-1' }),
      ),
    );
    const countdown = await screen.findByTestId('deferred-countdown');
    expect(within(countdown).getByRole('timer')).toHaveTextContent(/Deleting in (9|10)s/);
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(confirm).not.toHaveBeenCalled();
  });

  it('Cancel withdraws the parked action on the server', async () => {
    pending.createPendingAction.mockResolvedValue({
      id: 'pa-2',
      action_key: 'idea.delete',
      entity_type: 'idea',
      entity_id: 'idea-1',
      commit_at: serverTime(10_000),
      window_seconds: 10,
    });
    await openMenu();
    fireEvent.click(await screen.findByRole('menuitem', { name: /Delete/ }));
    const countdown = await screen.findByTestId('deferred-countdown');
    fireEvent.click(within(countdown).getByRole('button', { name: /Cancel/ }));
    await waitFor(() => expect(pending.cancelPendingAction).toHaveBeenCalledWith('pa-2'));
  });
});

describe('AC-D-01 record card and AC-D-06', () => {
  it('shows the idea number as the header title, the full title, tenant status label and the metadata strip', async () => {
    const idea = makeIdea();
    svc.getIdea.mockResolvedValue(idea);
    svc.listIdeas.mockResolvedValue([idea]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <IdeaDetailHeader id="idea-1" />
        <IdeaDetail id="idea-1" />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole('heading', { name: 'IDEA-0042' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Faster quotes/ })).toBeInTheDocument();
    expect(screen.getByText('New')).toBeInTheDocument();
    for (const value of ['Jane Lim', 'WhatsApp', 'Sorento CRM', formatDateTime('2026-07-21T09:05:00Z')]) {
      expect(screen.getByText(new RegExp(value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))).toBeInTheDocument();
    }
    // dd/MM/yyyy, h:mm AM/PM
    expect(document.body.textContent).toMatch(/\d{2}\/\d{2}\/\d{4}, \d{1,2}:\d{2} (AM|PM)/);
  });

  it('has the tabs Details and Attachments and NO Business requirements tab (ss returns no BR links, AC-D-05)', async () => {
    renderDetail(makeIdea());
    await loaded();
    for (const name of ['Details', 'Attachments']) {
      expect(screen.getByRole('tab', { name: new RegExp(name) })).toBeInTheDocument();
    }
    expect(screen.queryByRole('tab', { name: /Business Requirements/ })).toBeNull();
  });

  it('never prints the record id anywhere in the UI', async () => {
    held.add(MANAGE);
    renderDetail(makeIdea({ id: '7c1d0b7e-0000-4000-8000-00000000a001' }));
    await loaded();
    expect(document.body.textContent).not.toContain('7c1d0b7e-0000-4000-8000-00000000a001');
  });

  it('AC-D-06: any error other than a 404 is an error state with Retry, not "not found"', async () => {
    svc.getIdea.mockRejectedValue(
      Object.assign(new Error("The Ideas workspace isn't reachable right now."), { status: 502 }),
    );
    svc.listIdeas.mockResolvedValue([]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <IdeaDetail id="idea-1" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("The Ideas workspace isn't reachable right now.")).toBeInTheDocument();
    expect(screen.queryByText('Idea not found')).toBeNull();
    svc.getIdea.mockResolvedValue(makeIdea());
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    expect(await screen.findByRole('heading', { name: /Faster quotes/ })).toBeInTheDocument();
  });

  it('an unknown id renders the not-found state', async () => {
    svc.getIdea.mockRejectedValue(Object.assign(new Error('Idea not found.'), { status: 404 }));
    svc.listIdeas.mockResolvedValue([]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <IdeaDetail id="missing" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Idea not found')).toBeInTheDocument();
  });
});

describe('AC-D-05 attachments', () => {
  const att = (over: Record<string, unknown>) => ({
    id: 'att-1',
    kind: 'file',
    name: 'brief.pdf',
    sizeBytes: 10,
    durationSec: null,
    hasContent: true,
    url: '',
    ...over,
  });

  async function openAttachments(attachments: unknown[]) {
    renderDetail(makeIdea({ attachments }));
    await loaded();
    fireEvent.click(screen.getByRole('tab', { name: /Attachments/ }));
  }

  it('a view-only viewer on someone else\'s idea (isMine false) sees no upload control', async () => {
    renderDetail(makeIdea({ isMine: false }));
    await loaded();
    fireEvent.click(screen.getByRole('tab', { name: /Attachments/ }));
    await screen.findByText('No attachments');
    expect(screen.queryByLabelText('Upload attachments')).toBeNull();
  });

  it('a view-only viewer sees no upload control when isMine is absent', async () => {
    renderDetail(makeIdea());
    await loaded();
    fireEvent.click(screen.getByRole('tab', { name: /Attachments/ }));
    await screen.findByText('No attachments');
    expect(screen.queryByLabelText('Upload attachments')).toBeNull();
  });

  it('a view-only viewer on their own idea (isMine true) gets the upload control', async () => {
    renderDetail(makeIdea({ isMine: true }));
    await loaded();
    fireEvent.click(screen.getByRole('tab', { name: /Attachments/ }));
    expect(await screen.findByLabelText('Upload attachments')).toBeInTheDocument();
  });

  it('a manage holder always gets the upload control', async () => {
    held.add(MANAGE);
    renderDetail(makeIdea({ isMine: false }));
    await loaded();
    fireEvent.click(screen.getByRole('tab', { name: /Attachments/ }));
    expect(await screen.findByLabelText('Upload attachments')).toBeInTheDocument();
  });

  it('an isMine merged child (non-manage) has no Edit and no upload control', async () => {
    renderDetail(
      makeIdea({
        isMine: true,
        mergedIntoId: 'idea-9',
        mergedInto: { id: 'idea-9', ideaNumber: 'IDEA-0009', title: 'Survivor' },
      }),
    );
    await loaded();
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: /Attachments/ }));
    await screen.findByText('No attachments');
    expect(screen.queryByLabelText('Upload attachments')).toBeNull();
  });

  it('a download saves through an anchor with the download attribute and never window.open of a blob', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    const createObjectURL = vi.fn(() => 'blob:fake');
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe('brief.pdf');
      expect(this.href).toContain('blob:fake');
    });
    svc.fetchAttachment.mockResolvedValue(new Blob(['x']));
    await openAttachments([att({})]);
    fireEvent.click(await screen.findByRole('button', { name: 'brief.pdf' }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(svc.fetchAttachment).toHaveBeenCalledWith('idea-1', 'att-1');
    expect(open).not.toHaveBeenCalled();
    click.mockRestore();
    open.mockRestore();
  });

  it('a link attachment opens only for http/https; a javascript: or data: link is plain text', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    await openAttachments([
      att({ id: 'a1', name: 'web.png', hasContent: false, url: 'https://cdn.test/web.png' }),
      att({ id: 'a2', name: 'evil.txt', hasContent: false, url: 'javascript:alert(1)' }),
      att({ id: 'a3', name: 'data.txt', hasContent: false, url: 'data:text/html,<b>x</b>' }),
    ]);
    fireEvent.click(await screen.findByRole('button', { name: 'web.png' }));
    expect(open).toHaveBeenCalledWith('https://cdn.test/web.png', '_blank', 'noopener,noreferrer');
    expect(screen.queryByRole('button', { name: 'evil.txt' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'data.txt' })).toBeNull();
    expect(screen.getByText('evil.txt')).toBeInTheDocument();
    open.mockRestore();
  });
});
