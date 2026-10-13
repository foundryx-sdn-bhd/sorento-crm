/**
 * IDEA-CRM-SIDE Phase 2 red tests for the merge grid modal (AC-D-01..D-04).
 *
 * Contract: `<IdeaMergeModal ideas={Idea[]} open onOpenChange />`. `ideas` is the initial
 * selection (column order). One column per idea, one row per field; a checkbox per cell named
 * "<Field> from <ideaNumber>"; the survivor is the radio named "Keep <ideaNumber>" (default: the
 * first idea). Submit ("Merge") calls `mergeIdeas({survivorId, ideaIds, fields?})`, ideaIds in
 * column order, `fields` only for rows whose ticks differ from "survivor only".
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/ideas/idea-1',
  useSearchParams: () => new URLSearchParams(),
}));

const toastError = vi.fn();
vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: (...a: unknown[]) => toastError(...a), dismiss: vi.fn() },
}));

const svc = vi.hoisted(() => ({
  listIdeas: vi.fn(),
  mergeIdeas: vi.fn(),
  getIdea: vi.fn(),
  getBoard: vi.fn(),
  getMergedChildren: vi.fn(),
  getSimilarIdeas: vi.fn(),
  createIdea: vi.fn(),
  updateIdea: vi.fn(),
  voteIdea: vi.fn(),
  moveIdeaToStatus: vi.fn(),
  restoreIdea: vi.fn(),
  reorderIdeas: vi.fn(),
  unmergeIdea: vi.fn(),
  promoteIdea: vi.fn(),
  promoteIdeas: vi.fn(),
  uploadAttachment: vi.fn(),
  fetchAttachment: vi.fn(),
  listComments: vi.fn(),
  addComment: vi.fn(),
  editComment: vi.fn(),
  IDEA_STATUS_FILTER_OPTIONS: [],
}));
vi.mock('@/services/ideasService', () => svc);

import { IdeaMergeModal } from './IdeaMergeModal';

function makeIdea(over: Record<string, unknown> = {}) {
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
    desiredOutcome: 'Same hour quotes',
    useCases: ['Dealer asks', 'Rush order'],
    currentWorkaround: 'Spreadsheet',
    rawText: 'original words one',
    source: 'manual',
    submitterName: 'Jane Lim',
    upvotes: 3,
    myVote: null,
    priority: 1,
    rank: 1,
    attachments: [],
    createdAt: '2026-07-21T09:05:00Z',
    ideaNumber: 'IDEA-0001',
    mergedIntoId: null,
    mergedInto: null,
    mergedCount: 0,
    ...over,
  };
}

const ONE = makeIdea();
const TWO = makeIdea({
  id: 'idea-2',
  ideaNumber: 'IDEA-0002',
  title: 'Quote templates',
  problem: 'Writing quotes by hand',
  desiredOutcome: 'One click quotes',
  useCases: ['Template reuse'],
  currentWorkaround: 'Word documents',
  rawText: 'original words two',
});
const THREE = makeIdea({ id: 'idea-3', ideaNumber: 'IDEA-0003', title: 'Quote macros', problem: 'Macros' });

const FIELDS: Array<[string, string]> = [
  ['Title', 'title'],
  ['Business problem', 'problem'],
  ['Desired outcome', 'desiredOutcome'],
  ['Use cases', 'useCases'],
  ['Current workaround', 'currentWorkaround'],
  ['Original message', 'rawText'],
];

function renderModal(ideas = [ONE, TWO], onOpenChange = vi.fn()) {
  svc.listIdeas.mockResolvedValue([ONE, TWO, THREE]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={client}>
      <IdeaMergeModal ideas={ideas as never} open onOpenChange={onOpenChange} />
    </QueryClientProvider>,
  );
  return { ...utils, onOpenChange };
}

const cell = (field: string, number: string) =>
  screen.getByRole('checkbox', { name: `${field} from ${number}` }) as HTMLInputElement;
const ticked = (el: HTMLElement) =>
  el.getAttribute('aria-checked') === 'true' || (el as HTMLInputElement).checked === true;

async function submit() {
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Merge' }));
}

beforeEach(() => {
  vi.clearAllMocks();
  svc.mergeIdeas.mockResolvedValue(ONE);
});

describe('AC-D-01 grid', () => {
  it('has one checkbox per idea and field, named "<Field> from <ideaNumber>"', async () => {
    renderModal();
    await screen.findByRole('dialog');
    for (const [label] of FIELDS) {
      for (const number of ['IDEA-0001', 'IDEA-0002']) {
        expect(cell(label, number)).toBeInTheDocument();
      }
    }
    expect(screen.getAllByRole('checkbox')).toHaveLength(FIELDS.length * 2);
  });

  it('has a column per selected idea: three ideas give three radios and 18 cells', async () => {
    renderModal([ONE, TWO, THREE]);
    await screen.findByRole('dialog');
    expect(screen.getAllByRole('radio')).toHaveLength(3);
    expect(screen.getAllByRole('checkbox')).toHaveLength(FIELDS.length * 3);
  });

  it('shows each idea value in its column, use cases one per line', async () => {
    renderModal();
    const dialog = await screen.findByRole('dialog');
    for (const text of [
      'Faster quotes',
      'Quote templates',
      'Quotes take too long',
      'Writing quotes by hand',
      'Same hour quotes',
      'One click quotes',
      'Spreadsheet',
      'Word documents',
      'original words one',
      'original words two',
      'Dealer asks',
      'Rush order',
      'Template reuse',
    ]) {
      expect(within(dialog).getAllByText(text).length).toBeGreaterThan(0);
    }
    for (const [label] of FIELDS) {
      expect(within(dialog).getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it('survivor radio defaults to the first idea', async () => {
    renderModal();
    await screen.findByRole('dialog');
    expect(screen.getByRole('radio', { name: 'Keep IDEA-0001' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Keep IDEA-0002' })).not.toBeChecked();
  });

  it('never prints a record id', async () => {
    renderModal();
    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).not.toMatch(/idea-1\b|idea-2\b/);
  });
});

describe('AC-D-02 default submit', () => {
  it("ticks the survivor's cells and none of the others", async () => {
    renderModal();
    await screen.findByRole('dialog');
    for (const [label] of FIELDS) {
      expect(ticked(cell(label, 'IDEA-0001'))).toBe(true);
      expect(ticked(cell(label, 'IDEA-0002'))).toBe(false);
    }
  });

  it('submits { survivorId, ideaIds } with no fields', async () => {
    const { onOpenChange } = renderModal();
    await submit();
    await waitFor(() => expect(svc.mergeIdeas).toHaveBeenCalledTimes(1));
    const input = svc.mergeIdeas.mock.calls[0][0];
    expect(input.survivorId).toBe('idea-1');
    expect(input.ideaIds).toEqual(['idea-1', 'idea-2']);
    expect(input).not.toHaveProperty('fields');
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it('keeping the second idea, untouched ticks follow it: survivorId is idea-2, still no fields', async () => {
    renderModal();
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('radio', { name: 'Keep IDEA-0002' }));
    await submit();
    await waitFor(() => expect(svc.mergeIdeas).toHaveBeenCalled());
    const input = svc.mergeIdeas.mock.calls[0][0];
    expect(input.survivorId).toBe('idea-2');
    expect(input.ideaIds).toEqual(['idea-1', 'idea-2']);
    expect(input).not.toHaveProperty('fields');
  });
});

describe('AC-D-03 picking cells', () => {
  it("swapping one row to the other idea sends fields for that row only", async () => {
    renderModal();
    await screen.findByRole('dialog');
    fireEvent.click(cell('Business problem', 'IDEA-0001'));
    fireEvent.click(cell('Business problem', 'IDEA-0002'));
    await submit();
    await waitFor(() => expect(svc.mergeIdeas).toHaveBeenCalled());
    const input = svc.mergeIdeas.mock.calls[0][0];
    expect(input.survivorId).toBe('idea-1');
    expect(input.fields).toEqual({ problem: ['idea-2'] });
  });

  it('ticking an extra cell combines the values, ids in column order', async () => {
    renderModal();
    await screen.findByRole('dialog');
    fireEvent.click(cell('Use cases', 'IDEA-0002'));
    await submit();
    await waitFor(() => expect(svc.mergeIdeas).toHaveBeenCalled());
    expect(svc.mergeIdeas.mock.calls[0][0].fields).toEqual({ useCases: ['idea-1', 'idea-2'] });
  });

  it('column order, not click order, decides the ids', async () => {
    renderModal([ONE, TWO, THREE]);
    await screen.findByRole('dialog');
    fireEvent.click(cell('Title', 'IDEA-0003'));
    fireEvent.click(cell('Title', 'IDEA-0002'));
    await submit();
    await waitFor(() => expect(svc.mergeIdeas).toHaveBeenCalled());
    expect(svc.mergeIdeas.mock.calls[0][0].fields).toEqual({ title: ['idea-1', 'idea-2', 'idea-3'] });
  });

  it('several changed rows each get a key, unchanged rows get none', async () => {
    renderModal();
    await screen.findByRole('dialog');
    fireEvent.click(cell('Title', 'IDEA-0001'));
    fireEvent.click(cell('Title', 'IDEA-0002'));
    fireEvent.click(cell('Current workaround', 'IDEA-0002'));
    await submit();
    await waitFor(() => expect(svc.mergeIdeas).toHaveBeenCalled());
    expect(svc.mergeIdeas.mock.calls[0][0].fields).toEqual({
      title: ['idea-2'],
      currentWorkaround: ['idea-1', 'idea-2'],
    });
  });

  it('ticking a cell and unticking it again sends no fields', async () => {
    renderModal();
    await screen.findByRole('dialog');
    fireEvent.click(cell('Desired outcome', 'IDEA-0002'));
    fireEvent.click(cell('Desired outcome', 'IDEA-0002'));
    await submit();
    await waitFor(() => expect(svc.mergeIdeas).toHaveBeenCalled());
    expect(svc.mergeIdeas.mock.calls[0][0]).not.toHaveProperty('fields');
  });
});

describe('AC-D-04 a row with no tick', () => {
  it('blocks submit with "Pick at least one value for <Field>" and calls nothing', async () => {
    renderModal();
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(cell('Business problem', 'IDEA-0001'));
    await submit();
    expect(await within(dialog).findByText('Pick at least one value for Business problem')).toBeInTheDocument();
    expect(svc.mergeIdeas).not.toHaveBeenCalled();
  });

  it('ticking a cell in that row clears the block', async () => {
    renderModal();
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(cell('Use cases', 'IDEA-0001'));
    await submit();
    await within(dialog).findByText('Pick at least one value for Use cases');
    fireEvent.click(cell('Use cases', 'IDEA-0002'));
    await submit();
    await waitFor(() => expect(svc.mergeIdeas).toHaveBeenCalled());
    expect(svc.mergeIdeas.mock.calls[0][0].fields).toEqual({ useCases: ['idea-2'] });
  });
});
