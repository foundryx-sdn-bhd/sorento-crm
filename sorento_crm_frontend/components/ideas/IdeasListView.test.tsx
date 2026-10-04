/**
 * IDEATION-IN-CRM Phase 2 red tests for the Ideas list: AC-C-03, C-05, C-06 still stand;
 * AC-K-01..K-08 (list rework, owner hand test #1) supersede AC-C-01, C-02 and C-04.
 *
 * Real DataGrid, real hooks; the service is the seam. The column-preference hook is stubbed as
 * "loaded" (the pattern every list test here uses) and records the listing key it was given, which
 * is how AC-C-02 (per-user persistence under `ideation.board.view`) is pinned.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const push = vi.fn();
const replace = vi.hoisted(() => vi.fn());
const urlState = vi.hoisted(() => ({ search: '' }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace, prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/ideas',
  useSearchParams: () => new URLSearchParams(urlState.search),
}));

const prefsCalls = vi.hoisted(() => [] as Array<{ listingKey?: string | null }>);
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: (args: { listingKey?: string | null }) => {
    prefsCalls.push({ listingKey: args.listingKey });
    return { resetToDefaults: vi.fn(), isLoading: false };
  },
}));

// Container reads the app-wide SettingsProvider; the page chrome is not under test.
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

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

// Manage is a separate slug from view; the test decides which the viewer holds.
const held = new Set<string>();
vi.mock('@/hooks/usePermissions', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, useHasPermission: (slug: string) => held.has(slug) };
});
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { name: 'Pat Staff' } }, status: 'authenticated' }),
}));

const svc = vi.hoisted(() => ({
  listIdeas: vi.fn(),
  voteIdea: vi.fn(),
  createIdea: vi.fn(),
  getIdea: vi.fn(),
  getBoard: vi.fn(),
  getMergedChildren: vi.fn(),
  listComments: vi.fn(),
  updateIdea: vi.fn(),
  moveIdeaToStatus: vi.fn(),
  restoreIdea: vi.fn(),
  reorderIdeas: vi.fn(),
  mergeIdeas: vi.fn(),
  unmergeIdea: vi.fn(),
  promoteIdea: vi.fn(),
  promoteIdeas: vi.fn(),
  uploadAttachment: vi.fn(),
  addComment: vi.fn(),
  editComment: vi.fn(),
  IDEA_STATUS_FILTER_OPTIONS: [
    { value: 'new', label: 'New' },
    { value: 'archived', label: 'Archived' },
  ],
}));
vi.mock('@/services/ideasService', () => svc);

const pending = vi.hoisted(() => ({
  createPendingAction: vi.fn(),
  cancelPendingAction: vi.fn(),
  getCurrentPendingAction: vi.fn(),
}));
vi.mock('@/services/pendingActionService', () => pending);

import { formatDate } from '@/lib/helpers';
import { IdeasListView } from './IdeasListView';
import { IdeasScopeToggle } from './IdeasScopeToggle';

function idea(over: Record<string, unknown> = {}) {
  return {
    id: 'idea-1',
    productName: 'Sorento CRM',
    status: 'new',
    statusId: 'st-new',
    statusLabel: 'New',
    statusColor: 'info',
    statusIsArchived: false,
    transitions: [],
    advanceTransitionId: null,
    title: 'Faster quotes',
    problem: 'Quotes take too long',
    proposedSolution: null,
    impact: null,
    department: null,
    rawText: '',
    source: 'manual',
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

function renderList(props: Record<string, unknown> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <IdeasListView {...props} />
    </QueryClientProvider>,
  );
}

const MANAGE = 'ideation.ideas.manage';
const VIEW = 'ideation.board.view';

beforeEach(() => {
  vi.clearAllMocks();
  urlState.search = '';
  prefsCalls.length = 0;
  held.clear();
  held.add(VIEW);
  pending.getCurrentPendingAction.mockResolvedValue({ pending: null, last_outcome: null });
  pending.createPendingAction.mockImplementation(async (a: { actionKey: string; entityId: string }) => ({
    id: `pa-${a.entityId}`,
    action_key: a.actionKey,
    entity_type: 'idea',
    entity_id: a.entityId,
    commit_at: new Date(Date.now() + 5000).toISOString().replace(/\.\d+Z$/, ''),
    window_seconds: 5,
  }));
  svc.listIdeas.mockResolvedValue([idea(), idea({ id: 'idea-2', title: 'Merged child', mergedIntoId: 'idea-1', ideaNumber: 'IDEA-0043' })]);
});

describe('AC-K-04 formatting (kept from AC-C-01)', () => {
  it('renders the capture date as dd/MM/yyyy', async () => {
    renderList();
    await screen.findByText('Faster quotes');
    expect(screen.getAllByText(formatDate('2026-07-21T09:05:00Z')).length).toBeGreaterThan(0);
    expect(formatDate('2026-07-21T09:05:00Z')).toMatch(/^\d{2}\/\d{2}\/\d{4}$/);
  });

  it('uses the fixed resizable layout (columns carry explicit widths)', async () => {
    const { container } = renderList();
    await screen.findByText('Faster quotes');
    const table = container.querySelector('table') as HTMLTableElement;
    expect(table.className).toMatch(/table-fixed/);
    const widths = Array.from(table.querySelectorAll('th')).map((th) => th.style.width);
    expect(widths.every((w) => /px$/.test(w))).toBe(true);
  });

  it('persists columns per user under ideation.board.view (AC-K-04)', async () => {
    renderList();
    await screen.findByText('Faster quotes');
    expect(prefsCalls.length).toBeGreaterThan(0);
    expect(prefsCalls.every((c) => c.listingKey === 'ideation.board.view')).toBe(true);
  });
});

describe('AC-C-03 vote box', () => {
  it('a click votes through the service and does NOT open the row', async () => {
    svc.voteIdea.mockResolvedValue(idea({ upvotes: 4, myVote: 'up' }));
    renderList();
    await screen.findByText('Faster quotes');
    const [firstVote] = screen.getAllByRole('button', { name: /upvote/i });
    fireEvent.click(firstVote);
    await waitFor(() => expect(svc.voteIdea).toHaveBeenCalledWith('idea-1'));
    expect(push).not.toHaveBeenCalled();
  });

  it('a merged child has a disabled vote box', async () => {
    renderList();
    await screen.findByText('Merged child');
    const row = screen.getByText('Merged child').closest('tr') as HTMLElement;
    expect(within(row).getByRole('button', { name: /upvote/i })).toBeDisabled();
  });

  it('shows the filled state when the viewer has voted', async () => {
    svc.listIdeas.mockResolvedValue([idea({ myVote: 'up' })]);
    renderList();
    await screen.findByText('Faster quotes');
    expect(screen.getByRole('button', { name: /remove your upvote/i })).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('AC-C-05 Capture idea', () => {
  it('the header CTA opens a modal with the five fields and no Product picker', async () => {
    renderList();
    await screen.findByText('Faster quotes');
    fireEvent.click(screen.getByRole('button', { name: /Capture idea/ }));
    const dialog = await screen.findByRole('dialog');
    for (const label of ['Problem statement', 'Proposed solution', 'Impact', 'Department', 'Attachments']) {
      expect(within(dialog).getByLabelText(label)).toBeInTheDocument();
    }
    expect(within(dialog).queryByLabelText(/Product/)).toBeNull();
  });

  it('saving without a problem statement shows "Problem statement is required" and calls nothing', async () => {
    renderList();
    await screen.findByText('Faster quotes');
    fireEvent.click(screen.getByRole('button', { name: /Capture idea/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Capture' }));
    expect(await within(dialog).findByText('Problem statement is required')).toBeInTheDocument();
    expect(svc.createIdea).not.toHaveBeenCalled();
  });

  it('saving hands the fields and the dropped files to the service, then the list refreshes', async () => {
    svc.createIdea.mockResolvedValue(idea({ id: 'idea-9', title: 'Brand new' }));
    renderList();
    await screen.findByText('Faster quotes');
    fireEvent.click(screen.getByRole('button', { name: /Capture idea/ }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Problem statement'), { target: { value: 'Brand new problem' } });
    const file = new File(['x'], 'shot.png', { type: 'image/png' });
    fireEvent.change(within(dialog).getByLabelText('Attachments'), { target: { files: [file] } });
    const before = svc.listIdeas.mock.calls.length;
    fireEvent.click(within(dialog).getByRole('button', { name: 'Capture' }));
    await waitFor(() => expect(svc.createIdea).toHaveBeenCalled());
    // react-query hands the mutation function a second (context) argument; only the first is ours.
    expect(svc.createIdea.mock.calls[0][0]).toMatchObject({ problem: 'Brand new problem', files: [file] });
    await waitFor(() => expect(svc.listIdeas.mock.calls.length).toBeGreaterThan(before));
  });
});

describe('AC-C-06 empty and error states', () => {
  it('empty: a heading and a hint, with no button of its own', async () => {
    svc.listIdeas.mockResolvedValue([]);
    renderList();
    expect(await screen.findByText('No ideas yet')).toBeInTheDocument();
    expect(screen.getByText(/Captured ideas appear here/)).toBeInTheDocument();
    const empty = screen.getByText('No ideas yet').parentElement as HTMLElement;
    expect(within(empty).queryByRole('button')).toBeNull();
  });

  it('gateway error: an error state with a Retry that asks again', async () => {
    svc.listIdeas.mockRejectedValueOnce(new Error("The Ideas workspace isn't reachable right now."));
    renderList();
    expect(await screen.findByText("The Ideas workspace isn't reachable right now.")).toBeInTheDocument();
    svc.listIdeas.mockResolvedValue([idea()]);
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    expect(await screen.findByText('Faster quotes')).toBeInTheDocument();
  });
});

describe('AC-C-06 error OR empty, never both', () => {
  it('a gateway error shows the error state and not the empty-state heading', async () => {
    svc.listIdeas.mockRejectedValue(new Error("The Ideas workspace isn't reachable right now."));
    renderList();
    expect(await screen.findByText("The Ideas workspace isn't reachable right now.")).toBeInTheDocument();
    expect(screen.queryByText('No ideas yet')).toBeNull();
  });
});

describe('AC-C-06 empty state when filters are active (AC-K-08)', () => {
  it('says "No ideas match these filters." when a search matches nothing, not the empty heading', async () => {
    renderList();
    await screen.findByText('Faster quotes');
    fireEvent.change(screen.getByPlaceholderText('Search ideas...'), { target: { value: 'zzzz-no-match' } });
    expect(await screen.findByText('No ideas match these filters.')).toBeInTheDocument();
    expect(screen.queryByText('No ideas yet')).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
// Section K helpers
// ---------------------------------------------------------------------------------------------

function rowTitles(): string[] {
  return screen
    .getAllByRole('row')
    .slice(1)
    .map((r) => within(r).queryByRole('link')?.textContent ?? '')
    .filter(Boolean);
}

function rowOf(title: string): HTMLElement {
  return screen.getByText(title).closest('tr') as HTMLElement;
}

/** Tick the row checkbox for each title. */
function selectRows(...titles: string[]) {
  for (const t of titles) fireEvent.click(within(rowOf(t)).getByRole('checkbox'));
}

async function openActions() {
  fireEvent.pointerDown(screen.getByRole('button', { name: /^Actions$/ }), { button: 0, ctrlKey: false });
  await screen.findByRole('menu');
}

function menuItem(name: RegExp | string) {
  return screen.findByRole('menuitem', { name });
}

const T_TRIAGED = { id: 'tr-1', label: 'Triage', toStatusId: 'st-triaged', toStatusLabel: 'Triaged' };

function twoManageable() {
  return [
    idea({ id: 'a', title: 'Alpha idea', ideaNumber: 'IDEA-1', transitions: [T_TRIAGED], advanceTransitionId: 'tr-1' }),
    idea({ id: 'b', title: 'Bravo idea', ideaNumber: 'IDEA-2', transitions: [T_TRIAGED], advanceTransitionId: 'tr-1' }),
  ];
}

describe('AC-K-01 data loading and client-side search', () => {
  it('loads every idea once with status "all" and does not refetch when searching', async () => {
    renderList();
    await screen.findByText('Faster quotes');
    expect(svc.listIdeas).toHaveBeenCalledTimes(1);
    expect(svc.listIdeas.mock.calls[0][0]).toMatchObject({ status: 'all' });
    expect(svc.listIdeas.mock.calls[0][0].query || '').toBe('');
    fireEvent.change(screen.getByPlaceholderText('Search ideas...'), { target: { value: 'quotes' } });
    await new Promise((r) => setTimeout(r, 600));
    expect(svc.listIdeas).toHaveBeenCalledTimes(1);
  });

  it('search matches idea title, submitter and product', async () => {
    svc.listIdeas.mockResolvedValue([
      idea({ id: 'a', title: 'Alpha idea', problem: 'p1', submitterName: 'Jane Lim', productName: 'Sorento CRM' }),
      idea({ id: 'b', title: 'Bravo idea', problem: 'p2', submitterName: 'Omar Said', productName: 'Sorento CRM' }),
      idea({ id: 'c', title: 'Charlie idea', problem: 'p3', submitterName: 'Jane Lim', productName: 'Warehouse App' }),
    ]);
    renderList();
    await screen.findByText('Alpha idea');
    const box = screen.getByPlaceholderText('Search ideas...');
    fireEvent.change(box, { target: { value: 'bravo' } });
    await waitFor(() => expect(rowTitles()).toEqual(['Bravo idea']));
    fireEvent.change(box, { target: { value: 'omar' } });
    await waitFor(() => expect(rowTitles()).toEqual(['Bravo idea']));
    fireEvent.change(box, { target: { value: 'warehouse' } });
    await waitFor(() => expect(rowTitles()).toEqual(['Charlie idea']));
    fireEvent.change(box, { target: { value: 'p2' } });
    await waitFor(() => expect(rowTitles()).toEqual(['Bravo idea']));
  });

  it('paginates client side: 10 per page, with 10 / 25 / 50 / 100 offered', async () => {
    svc.listIdeas.mockResolvedValue(
      Array.from({ length: 12 }, (_, i) =>
        idea({ id: `i${i}`, title: `Idea number ${String(i).padStart(2, '0')}`, upvotes: 100 - i }),
      ),
    );
    renderList();
    await screen.findByText('Idea number 00');
    expect(rowTitles()).toHaveLength(10);
    expect(screen.getByText('1 - 10 of 12')).toBeInTheDocument();
    const sizePicker = screen.getAllByRole('combobox').find((c) => c.textContent?.trim() === '10') as HTMLElement;
    expect(sizePicker).toBeTruthy();
    fireEvent.click(sizePicker);
    for (const size of ['10', '25', '50', '100']) {
      expect(await screen.findByRole('option', { name: size })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole('option', { name: '25' }));
    await waitFor(() => expect(rowTitles()).toHaveLength(12));
  });
});

describe('AC-K-02 toolbar order, status picker, Active | Archived', () => {
  it('left cluster order is search, info icon, Status picker, Active | Archived toggle', async () => {
    renderList();
    await screen.findByText('Faster quotes');
    const search = screen.getByPlaceholderText('Search ideas...');
    const info = screen.getByRole('button', { name: 'What can I search?' });
    const status = screen.getByRole('combobox', { name: 'Status' });
    const active = screen.getByRole('radio', { name: 'Active' });
    const archived = screen.getByRole('radio', { name: 'Archived' });
    const before = (x: HTMLElement, y: HTMLElement) =>
      Boolean(x.compareDocumentPosition(y) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(before(search, info)).toBe(true);
    expect(before(info, status)).toBe(true);
    expect(before(status, active)).toBe(true);
    expect(before(active, archived)).toBe(true);
    expect(status).toHaveTextContent('All statuses');
    expect(active).toHaveAttribute('aria-checked', 'true');
  });

  it('the info icon hover card says "You can search by" Idea, Submitter, Product', async () => {
    renderList();
    await screen.findByText('Faster quotes');
    const info = screen.getByRole('button', { name: 'What can I search?' });
    fireEvent.focus(info);
    fireEvent.pointerEnter(info);
    expect(await screen.findByText('You can search by')).toBeInTheDocument();
    const card = screen.getByText('You can search by').parentElement as HTMLElement;
    for (const word of ['Idea', 'Submitter', 'Product']) {
      expect(within(card).getByText(word)).toBeInTheDocument();
    }
  });

  it('Active and Archived split on statusIsArchived, and the toggle clears the selection', async () => {
    svc.listIdeas.mockResolvedValue([
      idea({ id: 'a', title: 'Alpha idea' }),
      idea({ id: 'z', title: 'Zulu idea', statusLabel: 'Closed', statusIsArchived: true }),
    ]);
    renderList();
    await screen.findByText('Alpha idea');
    expect(screen.queryByText('Zulu idea')).toBeNull();
    selectRows('Alpha idea');
    expect(screen.getByText('1 selected')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'Archived' }));
    expect(await screen.findByText('Zulu idea')).toBeInTheDocument();
    expect(screen.queryByText('Alpha idea')).toBeNull();
    expect(screen.queryByText(/\d+ selected/)).toBeNull();
  });

  it('the Status picker lists the statuses in the data and filters to one', async () => {
    svc.listIdeas.mockResolvedValue([
      idea({ id: 'a', title: 'Alpha idea', statusLabel: 'New' }),
      idea({ id: 'b', title: 'Bravo idea', statusLabel: 'Triaged' }),
    ]);
    renderList();
    await screen.findByText('Alpha idea');
    fireEvent.click(screen.getByRole('combobox', { name: 'Status' }));
    expect(await screen.findByRole('option', { name: 'New' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('option', { name: 'Triaged' }));
    await waitFor(() => expect(rowTitles()).toEqual(['Bravo idea']));
    // Client side: the filter never goes back to the service.
    expect(svc.listIdeas).toHaveBeenCalledTimes(1);
  });
});

describe('AC-K-07 toolbarScopeSlot', () => {
  it('renders the slot right after the Active | Archived toggle', async () => {
    renderList({ toolbarScopeSlot: <span data-testid="scope-slot">scope</span> });
    await screen.findByText('Faster quotes');
    const slot = screen.getByTestId('scope-slot');
    const archived = screen.getByRole('radio', { name: 'Archived' });
    expect(Boolean(archived.compareDocumentPosition(slot) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
    expect(slot.closest('[data-slot="data-grid-list-toolbar"]')).toBeTruthy();
  });

  it('renders nothing extra by default', async () => {
    renderList();
    await screen.findByText('Faster quotes');
    expect(screen.queryByTestId('scope-slot')).toBeNull();
  });
});

describe('AC-K-03 toolbar right cluster', () => {
  it('has Filters, Export, Columns and a primary Capture idea inside the toolbar, not the page header', async () => {
    renderList();
    await screen.findByText('Faster quotes');
    const toolbar = document.querySelector('[data-slot="data-grid-list-toolbar"]') as HTMLElement;
    expect(toolbar).toBeTruthy();
    for (const name of [/Filters/, /Export/, /Columns/, /Capture idea/]) {
      expect(within(toolbar).getByRole('button', { name })).toBeInTheDocument();
    }
    expect(screen.getByRole('radio', { name: 'List' }) || screen.getByRole('link', { name: /List/ })).toBeTruthy();
  });

  it('Filters by Channel and by Submitter', async () => {
    svc.listIdeas.mockResolvedValue([
      idea({ id: 'a', title: 'Alpha idea', source: 'whatsapp', submitterName: 'Jane Lim' }),
      idea({ id: 'b', title: 'Bravo idea', source: 'manual', submitterName: 'Omar Said' }),
      idea({ id: 'c', title: 'Charlie idea', source: 'manual', submitterName: 'Jane Lim' }),
    ]);
    renderList();
    await screen.findByText('Alpha idea');
    fireEvent.pointerDown(screen.getByRole('button', { name: /Filters/ }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole('combobox', { name: 'Channel' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Manual' }));
    await waitFor(() => expect(rowTitles().sort()).toEqual(['Bravo idea', 'Charlie idea']));
    fireEvent.click(screen.getByRole('combobox', { name: 'Submitter' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Jane Lim' }));
    await waitFor(() => expect(rowTitles()).toEqual(['Charlie idea']));
    expect(svc.listIdeas).toHaveBeenCalledTimes(1);
  });
});

describe('AC-K-04 columns, chips and sorting', () => {
  it('columns in order: select, Votes, Idea, Submitter, Channel, Product, Status, Submitted; no "No." column', async () => {
    renderList();
    await screen.findByText('Faster quotes');
    const ths = Array.from(document.querySelectorAll('table th'));
    expect(within(ths[0] as HTMLElement).getByRole('checkbox')).toBeInTheDocument();
    const titles = ths.map((th) => th.textContent?.trim() ?? '').filter(Boolean);
    expect(titles).toEqual(['Votes', 'Idea', 'Submitter', 'Channel', 'Product', 'Status', 'Submitted']);
    expect(screen.queryByText('IDEA-0042')).toBeNull();
  });

  it('Product is visible by default', async () => {
    renderList();
    await screen.findByText('Faster quotes');
    expect(screen.getAllByText('Sorento CRM').length).toBeGreaterThan(0);
  });

  it('shows an "N merged" chip on a survivor and a "Merged" chip on a merged child', async () => {
    svc.listIdeas.mockResolvedValue([
      idea({ id: 'a', title: 'Alpha idea', mergedCount: 2 }),
      idea({ id: 'b', title: 'Bravo idea', mergedIntoId: 'a' }),
    ]);
    renderList();
    await screen.findByText('Alpha idea');
    expect(within(rowOf('Alpha idea')).getByText('2 merged')).toBeInTheDocument();
    expect(within(rowOf('Bravo idea')).getByText('Merged')).toBeInTheDocument();
    expect(within(rowOf('Bravo idea')).queryByText(/\d+ merged/)).toBeNull();
  });

  const sortable = () => {
    svc.listIdeas.mockResolvedValue([
      idea({ id: 'a', title: 'Alpha idea', upvotes: 1, createdAt: '2026-07-01T09:00:00Z' }),
      idea({ id: 'b', title: 'Bravo idea', upvotes: 5, createdAt: '2026-07-02T09:00:00Z' }),
      idea({ id: 'c', title: 'Charlie idea', upvotes: 5, createdAt: '2026-07-03T09:00:00Z' }),
    ]);
  };

  it('default order is votes descending, then newest first', async () => {
    sortable();
    renderList();
    await screen.findByText('Alpha idea');
    expect(rowTitles()).toEqual(['Charlie idea', 'Bravo idea', 'Alpha idea']);
  });

  it('the Idea header sorts asc, desc, then clears', async () => {
    sortable();
    renderList();
    await screen.findByText('Alpha idea');
    const header = screen.getByRole('button', { name: 'Idea' });
    fireEvent.click(header);
    expect(rowTitles()).toEqual(['Alpha idea', 'Bravo idea', 'Charlie idea']);
    fireEvent.click(header);
    expect(rowTitles()).toEqual(['Charlie idea', 'Bravo idea', 'Alpha idea']);
  });

  it('the Submitted header sorts by date, oldest first on the first click', async () => {
    sortable();
    renderList();
    await screen.findByText('Alpha idea');
    fireEvent.click(screen.getByRole('button', { name: 'Submitted' }));
    expect(rowTitles()).toEqual(['Alpha idea', 'Bravo idea', 'Charlie idea']);
  });

  it('the Votes header sorts by the vote count', async () => {
    sortable();
    renderList();
    await screen.findByText('Alpha idea');
    fireEvent.click(screen.getByRole('button', { name: 'Votes' }));
    const first = rowTitles()[0];
    expect(first).toBe('Alpha idea');
  });
});

describe('AC-K-05 bulk strip and Actions menu (manage holder)', () => {
  beforeEach(() => held.add(MANAGE));

  it('selecting rows shows "N selected", Actions, Export and Clear; Clear unselects', async () => {
    svc.listIdeas.mockResolvedValue(twoManageable());
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Alpha idea', 'Bravo idea');
    expect(screen.getByText('2 selected')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Actions$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Export/ })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: /Clear/ }));
    expect(screen.queryByText(/\d+ selected/)).toBeNull();
  });

  it('header checkbox selects every row on the page', async () => {
    svc.listIdeas.mockResolvedValue(twoManageable());
    renderList();
    await screen.findByText('Alpha idea');
    fireEvent.click(screen.getByRole('checkbox', { name: /Select all rows on this page/ }));
    expect(screen.getByText('2 selected')).toBeInTheDocument();
  });

  it('Promote to BR: one call with every selected id and the FIRST selected idea title, then a success toast', async () => {
    svc.listIdeas.mockResolvedValue(twoManageable());
    svc.promoteIdeas.mockResolvedValue({ id: 'br-1' });
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Bravo idea', 'Alpha idea');
    await openActions();
    fireEvent.click(await menuItem(/Promote to BR/));
    await waitFor(() => expect(svc.promoteIdeas).toHaveBeenCalledTimes(1));
    // Selection order decides "first": Bravo was ticked first.
    expect(svc.promoteIdeas.mock.calls[0][0]).toEqual({ ideaIds: expect.arrayContaining(['a', 'b']), title: 'Bravo idea' });
    expect(svc.promoteIdeas.mock.calls[0][0].ideaIds).toHaveLength(2);
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
  });

  it('Promote to BR is disabled when the rows span more than one product', async () => {
    const [a, b] = twoManageable();
    svc.listIdeas.mockResolvedValue([a, { ...b, productName: 'Warehouse App' }]);
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Alpha idea', 'Bravo idea');
    await openActions();
    expect(await menuItem(/Promote to BR/)).toHaveAttribute('aria-disabled', 'true');
  });

  it('Merge needs two or more rows; one row hides it', async () => {
    svc.listIdeas.mockResolvedValue(twoManageable());
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Alpha idea');
    await openActions();
    await menuItem(/Promote to BR/);
    expect(screen.queryByRole('menuitem', { name: /^Merge/ })).toBeNull();
  });

  it('Merge opens a dialog limited to the selected ideas and posts { survivorId, ideaIds }', async () => {
    svc.listIdeas.mockResolvedValue([...twoManageable(), idea({ id: 'c', title: 'Charlie idea', ideaNumber: 'IDEA-3' })]);
    svc.mergeIdeas.mockResolvedValue(idea());
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Alpha idea', 'Bravo idea');
    await openActions();
    fireEvent.click(await menuItem(/^Merge/));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('combobox'));
    expect(await screen.findByRole('option', { name: /Alpha idea/ })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /Bravo idea/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /Charlie idea/ })).toBeNull();
    fireEvent.click(screen.getByRole('option', { name: /Bravo idea/ }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Merge' }));
    await waitFor(() => expect(svc.mergeIdeas).toHaveBeenCalled());
    const input = svc.mergeIdeas.mock.calls[0][0];
    expect(input.survivorId).toBe('b');
    expect([...input.ideaIds].sort()).toEqual(['a', 'b']);
  });

  it('Unmerge shows only when every selected row has merged children, one call per row', async () => {
    svc.listIdeas.mockResolvedValue([
      idea({ id: 'a', title: 'Alpha idea', mergedCount: 1 }),
      idea({ id: 'b', title: 'Bravo idea', mergedCount: 2 }),
      idea({ id: 'c', title: 'Charlie idea', mergedCount: 0 }),
    ]);
    svc.unmergeIdea.mockResolvedValue(undefined);
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Alpha idea', 'Charlie idea');
    await openActions();
    await menuItem(/Delete/);
    expect(screen.queryByRole('menuitem', { name: /Unmerge/ })).toBeNull();
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });
    fireEvent.click(within(rowOf('Charlie idea')).getByRole('checkbox'));
    selectRows('Bravo idea');
    await openActions();
    fireEvent.click(await menuItem(/Unmerge/));
    await waitFor(() => expect(svc.unmergeIdea).toHaveBeenCalledTimes(2));
    expect(svc.unmergeIdea.mock.calls.map((c) => c[0]).sort()).toEqual(['a', 'b']);
  });

  it('"Move to Triaged" when every row advances to the same label; one status call per row; selection clears and list refetches', async () => {
    svc.listIdeas.mockResolvedValue(twoManageable());
    svc.moveIdeaToStatus.mockResolvedValue(idea({ statusLabel: 'Triaged' }));
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Alpha idea', 'Bravo idea');
    await openActions();
    const before = svc.listIdeas.mock.calls.length;
    fireEvent.click(await menuItem('Move to Triaged'));
    await waitFor(() => expect(svc.moveIdeaToStatus).toHaveBeenCalledTimes(2));
    expect(svc.moveIdeaToStatus.mock.calls.map((c) => [c[0], c[1]]).sort()).toEqual([
      ['a', 'st-triaged'],
      ['b', 'st-triaged'],
    ]);
    await waitFor(() => expect(screen.queryByText(/\d+ selected/)).toBeNull());
    await waitFor(() => expect(svc.listIdeas.mock.calls.length).toBeGreaterThan(before));
  });

  it('"Advance to next stage" when the next moves differ', async () => {
    const [a, b] = twoManageable();
    svc.listIdeas.mockResolvedValue([
      a,
      { ...b, transitions: [{ id: 'tr-2', label: 'Plan', toStatusId: 'st-planned', toStatusLabel: 'Planned' }], advanceTransitionId: 'tr-2' },
    ]);
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Alpha idea', 'Bravo idea');
    await openActions();
    expect(await menuItem('Advance to next stage')).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: /Move to/ })).toBeNull();
  });

  it('the move item is disabled when any row has no advance transition', async () => {
    const [a, b] = twoManageable();
    svc.listIdeas.mockResolvedValue([a, { ...b, transitions: [], advanceTransitionId: null }]);
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Alpha idea', 'Bravo idea');
    await openActions();
    expect(await menuItem(/Move to|Advance to next stage/)).toHaveAttribute('aria-disabled', 'true');
  });

  it('a partial failure toasts how many failed', async () => {
    svc.listIdeas.mockResolvedValue(twoManageable());
    svc.moveIdeaToStatus.mockImplementation(async (id: string) => {
      if (id === 'b') throw new Error('boom');
      return idea({ statusLabel: 'Triaged' });
    });
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Alpha idea', 'Bravo idea');
    await openActions();
    fireEvent.click(await menuItem('Move to Triaged'));
    await waitFor(() => expect(svc.moveIdeaToStatus).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(toastError.mock.calls.some((c) => /1/.test(String(c[0])) && /fail/i.test(String(c[0])))).toBe(true),
    );
  });

  it('Archive starts one deferred idea.archive per row, with no confirm and no dialog', async () => {
    svc.listIdeas.mockResolvedValue(twoManageable());
    const confirm = vi.spyOn(window, 'confirm');
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Alpha idea', 'Bravo idea');
    await openActions();
    fireEvent.click(await menuItem(/^Archive$/));
    await waitFor(() => expect(pending.createPendingAction).toHaveBeenCalledTimes(2));
    const calls = pending.createPendingAction.mock.calls.map((c) => c[0]);
    expect(calls.every((c) => c.actionKey === 'idea.archive' && c.entityType === 'idea')).toBe(true);
    expect(calls.map((c) => c.entityId).sort()).toEqual(['a', 'b']);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(confirm).not.toHaveBeenCalled();
    expect(svc.updateIdea).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByText(/\d+ selected/)).toBeNull());
  });

  it('Delete starts one deferred idea.delete per row, with no confirm and no dialog', async () => {
    svc.listIdeas.mockResolvedValue(twoManageable());
    const confirm = vi.spyOn(window, 'confirm');
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Alpha idea', 'Bravo idea');
    await openActions();
    fireEvent.click(await menuItem(/^Delete$/));
    await waitFor(() => expect(pending.createPendingAction).toHaveBeenCalledTimes(2));
    const calls = pending.createPendingAction.mock.calls.map((c) => c[0]);
    expect(calls.every((c) => c.actionKey === 'idea.delete' && c.entityType === 'idea')).toBe(true);
    expect(calls.map((c) => c.entityId).sort()).toEqual(['a', 'b']);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(confirm).not.toHaveBeenCalled();
  });

  it('Restore (archived view): every row archived with an outgoing transition, one status call per row, no Archive', async () => {
    const t = { id: 'tr-r', label: 'Reopen', toStatusId: 'st-new', toStatusLabel: 'New' };
    svc.listIdeas.mockResolvedValue([
      idea({ id: 'x', title: 'Xray idea', statusIsArchived: true, statusLabel: 'Archived', transitions: [t], advanceTransitionId: 'tr-r' }),
      idea({ id: 'y', title: 'Yankee idea', statusIsArchived: true, statusLabel: 'Archived', transitions: [t], advanceTransitionId: 'tr-r' }),
    ]);
    svc.restoreIdea.mockResolvedValue(idea());
    renderList();
    await screen.findByText('Faster quotes').catch(() => undefined);
    fireEvent.click(await screen.findByRole('radio', { name: 'Archived' }));
    await screen.findByText('Xray idea');
    selectRows('Xray idea', 'Yankee idea');
    await openActions();
    expect(screen.queryByRole('menuitem', { name: /^Archive$/ })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: /Promote to BR/ })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: /^Merge/ })).toBeNull();
    fireEvent.click(await menuItem(/Restore/));
    await waitFor(() => expect(svc.restoreIdea).toHaveBeenCalledTimes(2));
    expect(svc.restoreIdea.mock.calls.map((c) => [c[0], c[1]]).sort()).toEqual([
      ['x', 'st-new'],
      ['y', 'st-new'],
    ]);
  });

  it('Restore is NOT offered for an archived-flag idea with no outgoing transition (AC-K-05)', async () => {
    svc.listIdeas.mockResolvedValue([
      idea({ id: 'x', title: 'Xray idea', statusIsArchived: true, statusLabel: 'Closed', transitions: [], advanceTransitionId: null }),
    ]);
    renderList();
    await screen.findByRole('radio', { name: 'Archived' });
    fireEvent.click(screen.getByRole('radio', { name: 'Archived' }));
    await screen.findByText('Xray idea');
    selectRows('Xray idea');
    await openActions();
    await menuItem(/Delete/);
    expect(screen.queryByRole('menuitem', { name: /Restore/ })).toBeNull();
  });

  it('one archived row WITHOUT a transition among several hides Restore for the whole selection', async () => {
    const t = { id: 'tr-r', label: 'Reopen', toStatusId: 'st-new', toStatusLabel: 'New' };
    svc.listIdeas.mockResolvedValue([
      idea({ id: 'x', title: 'Xray idea', statusIsArchived: true, transitions: [t], advanceTransitionId: 'tr-r' }),
      idea({ id: 'y', title: 'Yankee idea', statusIsArchived: true, transitions: [], advanceTransitionId: null }),
    ]);
    renderList();
    await screen.findByRole('radio', { name: 'Archived' });
    fireEvent.click(screen.getByRole('radio', { name: 'Archived' }));
    await screen.findByText('Xray idea');
    selectRows('Xray idea', 'Yankee idea');
    await openActions();
    await menuItem(/Delete/);
    expect(screen.queryByRole('menuitem', { name: /Restore/ })).toBeNull();
  });

  it('a move toasts one success sentence for the batch ("2 ideas moved.")', async () => {
    svc.listIdeas.mockResolvedValue(twoManageable());
    svc.moveIdeaToStatus.mockResolvedValue(idea({ statusLabel: 'Triaged' }));
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Alpha idea', 'Bravo idea');
    await openActions();
    fireEvent.click(await menuItem('Move to Triaged'));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith('2 ideas moved.'));
  });

  it('shows a row "..." menu per row for a manage holder', async () => {
    svc.listIdeas.mockResolvedValue(twoManageable());
    renderList();
    await screen.findByText('Alpha idea');
    expect(within(rowOf('Alpha idea')).getByRole('button', { name: 'Row actions' })).toBeInTheDocument();
  });
});

describe('AC-K-06 view-only user', () => {
  it('sees the select column and Export but no Actions dropdown and no row actions', async () => {
    svc.listIdeas.mockResolvedValue(twoManageable());
    renderList();
    await screen.findByText('Alpha idea');
    selectRows('Alpha idea');
    expect(screen.getByText('1 selected')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Export/ })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /^Actions$/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Row actions' })).toBeNull();
  });

  it('a row holds no controls beyond voting (AC-B-02 kept)', async () => {
    renderList();
    await screen.findByText('Faster quotes');
    const row = screen.getByText('Faster quotes').closest('tr') as HTMLElement;
    const names = within(row)
      .getAllByRole('button')
      .map((b) => b.getAttribute('aria-label') || b.textContent || '');
    expect(names.every((n) => /vote/i.test(n))).toBe(true);
  });
});

describe('IDEATION-CAPTURE My ideas / All ideas toggle', () => {
  // The page fills the AC-K-07 slot with the toggle; the URL (`?view=mine`) is the only state.
  const selected = (el: HTMLElement) =>
    el.getAttribute('aria-checked') === 'true' || el.getAttribute('data-state') === 'on';

  function renderScoped() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // A NEW element each time: re-rendering the same element reference lets React bail out, so
    // the component would never read the changed search params (what a real navigation does).
    const tree = () => (
      <QueryClientProvider client={client}>
        <IdeasListView toolbarScopeSlot={<IdeasScopeToggle />} />
      </QueryClientProvider>
    );
    const utils = render(tree());
    return { ...utils, navigate: () => utils.rerender(tree()) };
  }

  it('shows a two-option toggle in the slot, All ideas selected, and queries without mine', async () => {
    renderScoped();
    await screen.findByText('Faster quotes');
    const mine = screen.getByRole('radio', { name: 'My ideas' });
    const all = screen.getByRole('radio', { name: 'All ideas' });
    expect(selected(all)).toBe(true);
    expect(selected(mine)).toBe(false);
    expect(mine.closest('[data-slot="data-grid-list-toolbar"]')).toBeTruthy();
    expect(svc.listIdeas.mock.calls[0][0].mine).toBeFalsy();
  });

  it('clicking My ideas puts view=mine in the URL, selects My ideas and re-queries with mine true', async () => {
    push.mockImplementation((url: string) => {
      urlState.search = String(url).split('?')[1] ?? '';
    });
    const { navigate } = renderScoped();
    await screen.findByText('Faster quotes');
    fireEvent.click(screen.getByRole('radio', { name: 'My ideas' }));
    // push, not replace: browser Back returns to All ideas.
    expect(push).toHaveBeenLastCalledWith(expect.stringContaining('view=mine'));
    expect(replace).not.toHaveBeenCalled();
    navigate();
    await waitFor(() => expect(selected(screen.getByRole('radio', { name: 'My ideas' }))).toBe(true));
    await waitFor(() =>
      expect(svc.listIdeas).toHaveBeenLastCalledWith(expect.objectContaining({ mine: true })),
    );
  });

  it('clicking All ideas drops view from the URL', async () => {
    urlState.search = 'view=mine';
    renderScoped();
    await screen.findByText('Faster quotes');
    fireEvent.click(screen.getByRole('radio', { name: 'All ideas' }));
    expect(push).toHaveBeenLastCalledWith(expect.not.stringContaining('view='));
  });

  it('with ?view=mine in the URL, My ideas is selected on first render and the first query has mine true', async () => {
    urlState.search = 'view=mine';
    renderScoped();
    await screen.findByText('Faster quotes');
    expect(selected(screen.getByRole('radio', { name: 'My ideas' }))).toBe(true);
    expect(selected(screen.getByRole('radio', { name: 'All ideas' }))).toBe(false);
    expect(svc.listIdeas.mock.calls[0][0].mine).toBe(true);
  });

  it('follows the URL: "" to view=mine after mount (back/forward) selects My ideas and queries mine', async () => {
    const { navigate } = renderScoped();
    await screen.findByText('Faster quotes');
    expect(selected(screen.getByRole('radio', { name: 'All ideas' }))).toBe(true);
    urlState.search = 'view=mine';
    navigate();
    await waitFor(() => expect(selected(screen.getByRole('radio', { name: 'My ideas' }))).toBe(true));
    await waitFor(() =>
      expect(svc.listIdeas).toHaveBeenLastCalledWith(expect.objectContaining({ mine: true })),
    );
  });

  it('follows the URL back: view=mine to "" selects All ideas and queries without mine', async () => {
    urlState.search = 'view=mine';
    const { navigate } = renderScoped();
    await screen.findByText('Faster quotes');
    expect(selected(screen.getByRole('radio', { name: 'My ideas' }))).toBe(true);
    urlState.search = '';
    navigate();
    await waitFor(() => expect(selected(screen.getByRole('radio', { name: 'All ideas' }))).toBe(true));
    await waitFor(() => expect(svc.listIdeas.mock.calls.at(-1)?.[0].mine).toBeFalsy());
  });
});
