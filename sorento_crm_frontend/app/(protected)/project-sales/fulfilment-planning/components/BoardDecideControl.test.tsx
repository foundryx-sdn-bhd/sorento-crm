/**
 * S3 (D1): the Decide strip's own control - the button, its menu, and the lightbox dialog for
 * the two Borrow items. `FulfilmentBoardListView.test.tsx` covers the strip's placement and the
 * selection-column widening (AC-1 to AC-3); this file is the control's own menu, dialog and
 * save behaviour (AC-4, AC-11, AC-12, AC-16, AC-38, AC-55).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toast } from '@/lib/toast';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import { BoardDecideControl } from './BoardDecideControl';
import type { BoardContribution, BoardDraft } from '../../_shared/types/fulfilmentPlanning.types';

vi.mock('@/lib/toast', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));

// The real SearchableSelect drives a cmdk popover - out of scope here (AC-36 pins it as
// the primitive used). This mock is what a Decide dialog test needs: the option LIST (so
// AC-11's own naming can be pinned) and a plain change to pick one.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
  }: {
    value: string;
    onChange: (next: string) => void;
    options: SearchableSelectOption[];
  }) => (
    <select
      aria-label="donor-or-location"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">-</option>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

function row(overrides: Partial<BoardContribution> = {}): BoardContribution {
  return {
    key: 'so-1:line-10',
    sales_order_id: 'so-1',
    so_number: 'SO397450',
    line_no: 10,
    item_code: 'B2155-NL-BLUE',
    qty: '40',
    unplannable: false,
    cancelled: false,
    rank_score: 1,
    rank_factors: [],
    sources: [{ kind: 'buy', qty: '40', reason: 'Nothing free at any location.' }],
    contested: false,
    covered: false,
    locations: [],
    borrow_candidates: [],
    ...overrides,
  };
}

function openMenu() {
  fireEvent.keyDown(screen.getByTestId('board-decide-button'), { key: 'Enter' });
}

function renderControl(overrides: {
  contributions?: BoardContribution[];
  selectedKeys?: string[];
  draft?: BoardDraft;
  onSave?: (
    entries: { key: string; decision: unknown }[],
  ) => Promise<{ savedKeys: string[]; failed: { key: string; why: string }[] }>;
} = {}) {
  const contributions = overrides.contributions ?? [row()];
  const onSave =
    overrides.onSave ??
    vi.fn(async (entries: { key: string }[]) => ({
      savedKeys: entries.map((entry) => entry.key),
      failed: [],
    }));
  const onSaved = vi.fn();
  const onClear = vi.fn();
  const utils = render(
    <BoardDecideControl
      contributions={contributions}
      selectedKeys={overrides.selectedKeys ?? contributions.map((c) => c.key)}
      draft={overrides.draft ?? {}}
      onSave={onSave as never}
      onSaved={onSaved}
      onClear={onClear}
    />,
  );
  return { ...utils, onSave, onSaved, onClear };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('BoardDecideControl: the button (AC-1, AC-54)', () => {
  it('renders disabled with a tooltip when nothing is ticked', () => {
    renderControl({ selectedKeys: [] });

    expect(screen.getByTestId('board-decide-button')).toBeDisabled();
  });

  it('is enabled once a row is ticked', () => {
    renderControl();

    expect(screen.getByTestId('board-decide-button')).toBeEnabled();
  });
});

describe('BoardDecideControl: the menu (AC-4)', () => {
  it('opens with exactly these items in this order, no Reserve/incoming items', () => {
    // A ticked row with a donor-named candidate (the setting on), so every item shows;
    // without one "Borrow from another order" is not offered (BORROW-LOCATION-ONLY D3).
    renderControl({
      contributions: [
        row({
          borrow_candidates: [
            {
              source: 'other_location',
              warehouse_code: 'MWH-IB',
              warehouse_id: 'wh-mwh',
              free_qty: '100',
              donor_impact: { free_before: '100', free_after_full_borrow: '60', committed_qty: '0' },
              rung: 'order_borrow',
              donor_so_number: 'SO397450',
              donor_line_no: 446,
              donor_core_line_id: 'core-1',
            },
          ],
        }),
      ],
    });
    openMenu();

    const items = screen.getAllByRole('menuitem').map((item) => item.textContent);
    expect(items).toEqual([
      'As suggested',
      'Use own location',
      'Borrow from another order',
      'Borrow other location',
      'Use BRW',
      'Buy',
      'Transfer to Cancel SO',
    ]);
    expect(screen.queryByRole('menuitem', { name: 'Reserve' })).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Use incoming' })).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Borrow incoming' })).not.toBeInTheDocument();
  });
});

describe('BoardDecideControl: As suggested (AC-3)', () => {
  it('saves with no dialog and no explanatory copy', async () => {
    const { onSave, onSaved } = renderControl();
    openMenu();

    fireEvent.click(screen.getByRole('menuitem', { name: 'As suggested' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(['so-1:line-10']));
    expect(toast.success).toHaveBeenCalledWith('1 saved as As suggested');
  });
});

describe('BoardDecideControl: Buy with no dialog when every row already suggests Buy (AC-12)', () => {
  it('saves at once, verdict approved, no dialog', async () => {
    const { onSave } = renderControl({
      contributions: [row({ sources: [{ kind: 'buy', qty: '40', reason: 'Nothing free.' }] })],
    });
    openMenu();

    fireEvent.click(screen.getByRole('menuitem', { name: 'Buy' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    const [entries] = onSave.mock.calls[0];
    expect(entries).toEqual([
      expect.objectContaining({
        key: 'so-1:line-10',
        decision: expect.objectContaining({ verdict: 'approved', buy_qty: '40' }),
      }),
    ]);
  });
});

describe('BoardDecideControl: Use own location opens the dialog when it differs from the suggestion', () => {
  it('opens with one Reason box and no picker, Save disabled until it is non-blank', async () => {
    renderControl({
      contributions: [
        row({
          sources: [{ kind: 'buy', qty: '40', reason: 'Nothing free.' }],
          locations: [
            { location: 'BRW-BB', where: 'own', warehouse_id: 'wh-own', qty_free_remaining: '40' },
          ],
        }),
      ],
    });
    openMenu();

    fireEvent.click(screen.getByRole('menuitem', { name: 'Use own location' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Decide 1 lines: Use own location')).toBeInTheDocument();
    expect(within(dialog).queryByRole('combobox')).not.toBeInTheDocument();
    const save = within(dialog).getByRole('button', { name: /^Save/ });
    expect(save).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText(/^Reason/), {
      target: { value: 'Own site can cover it.' },
    });
    expect(save).toBeEnabled();
  });
});

describe('BoardDecideControl: the Borrow dialog (AC-11, AC-38)', () => {
  it('lists donor orders by SO number only, no agent, no "covers k of n"', async () => {
    renderControl({
      contributions: [
        row({
          borrow_candidates: [
            {
              source: 'other_location',
              warehouse_code: 'BRW-BB',
              warehouse_id: 'wh-donor',
              free_qty: '100',
              donor_impact: { free_before: '100', free_after_full_borrow: '60', committed_qty: '0' },
              donor_so_number: 'SO415472',
              donor_agent_code: 'JEREMY',
            },
          ],
        }),
      ],
    });
    openMenu();

    fireEvent.click(screen.getByRole('menuitem', { name: 'Borrow from another order' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Donor order')).toBeInTheDocument();
    const select = within(dialog).getByLabelText('donor-or-location');
    const option = within(select).getByText('SO415472');
    expect(option.textContent).toBe('SO415472');
    expect(option.textContent).not.toMatch(/covers|JEREMY|of \d/);
  });

  it('keeps Save disabled until the picker has a value and the reason is non-blank', async () => {
    renderControl({
      contributions: [
        row({
          borrow_candidates: [
            {
              source: 'other_location',
              warehouse_code: 'DC1-IR',
              warehouse_id: 'wh-ir',
              free_qty: '100',
              donor_impact: { free_before: '100', free_after_full_borrow: '60', committed_qty: '0' },
            },
          ],
        }),
      ],
    });
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Borrow other location' }));

    const dialog = await screen.findByRole('dialog');
    const save = within(dialog).getByRole('button', { name: /^Save/ });
    expect(save).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText(/^Reason/), {
      target: { value: 'Nothing else is free before the date.' },
    });
    expect(save).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText('donor-or-location'), {
      target: { value: 'DC1-IR' },
    });
    expect(save).toBeEnabled();
  });

  // Review round 1, Blocking 4 kill test 2(a): the test above only ever types the reason
  // BEFORE picking the donor/location, so it never exercises "picker set, reason still blank"
  // - a kill of the reason check alone stayed green. Reversing the order guards that half too.
  it('keeps Save disabled with the picker set and the reason still blank', async () => {
    renderControl({
      contributions: [
        row({
          borrow_candidates: [
            {
              source: 'other_location',
              warehouse_code: 'DC1-IR',
              warehouse_id: 'wh-ir',
              free_qty: '100',
              donor_impact: { free_before: '100', free_after_full_borrow: '60', committed_qty: '0' },
            },
          ],
        }),
      ],
    });
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Borrow other location' }));

    const dialog = await screen.findByRole('dialog');
    const save = within(dialog).getByRole('button', { name: /^Save/ });

    fireEvent.change(within(dialog).getByLabelText('donor-or-location'), {
      target: { value: 'DC1-IR' },
    });
    expect(save).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText(/^Reason/), {
      target: { value: 'Nothing else is free before the date.' },
    });
    expect(save).toBeEnabled();
  });
});

describe('BoardDecideControl: the lenient toast (AC-16, R10)', () => {
  it('saves the ones it can and names the ones it cannot, no dialog, no alert', async () => {
    const coverable = row({
      key: 'so-1:line-10',
      sources: [{ kind: 'buy', qty: '40', reason: 'Nothing free.' }],
      locations: [
        { location: 'BRW-BB', where: 'own', warehouse_id: 'wh-own', qty_free_remaining: '40' },
      ],
    });
    const short = row({
      key: 'so-2:line-20',
      so_number: 'SO397451',
      line_no: 20,
      item_code: 'CB6633',
      sources: [{ kind: 'buy', qty: '40', reason: 'Nothing free.' }],
      locations: [
        { location: 'BRW-BB', where: 'own', warehouse_id: 'wh-own2', qty_free_remaining: '10' },
      ],
    });
    const alertSpy = vi.spyOn(window, 'alert');
    renderControl({ contributions: [coverable, short], selectedKeys: [coverable.key, short.key] });
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Use own location' }));

    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/^Reason/), {
      target: { value: 'Own site takes what it can.' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: /^Save/ }));

    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        '1 saved as Use own location · 1 skipped: CB6633 line 20 (only 10 free at own location)',
      ),
    );
    expect(alertSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });
});

/**
 * Review round 2, Should fix 2: Should fix 4 (review round 1) - the throwaway probe that
 * checks a covered row against its OWN frozen composition before it ever touches the real
 * claim tally - shipped with no test. Disabling that probe left every existing test green,
 * because none of them ticked a covered row ALREADY decided exactly the pick alongside another
 * row contesting the SAME pile: without the probe, the covered row is saved again (a wasted
 * re-save) AND claims the pile first, starving the row that genuinely needs it (R3/AC-52/AC-14).
 */
describe('BoardDecideControl: a covered row already decided exactly this way is skipped, without starving the pile (review round 2, Should fix 2)', () => {
  it('skips the covered row as "already decided that way" and still saves the other row off the same pile', async () => {
    const pool = {
      location: 'BRW-BB',
      where: 'own' as const,
      warehouse_id: 'wh-own',
      qty_free_remaining: '40',
    };
    const covered = row({
      key: 'so-1:line-10',
      covered: true,
      decision: {
        revision_no: 1,
        timely_spo_qty: '0',
        reserve: [{ warehouse_id: 'wh-own', location: 'BRW-BB', qty: '40' }],
        borrow: [],
        buy_qty: '0',
      },
      locations: [pool],
    });
    const other = row({
      key: 'so-2:line-20',
      so_number: 'SO397451',
      line_no: 20,
      locations: [pool],
    });
    const { onSave } = renderControl({
      contributions: [covered, other],
      selectedKeys: [covered.key, other.key],
    });
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Use own location' }));

    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText(/^Reason/), {
      target: { value: 'Consolidating onto the own location.' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: /^Save/ }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const [entries] = onSave.mock.calls[0];
    // ONLY the other row is actually saved - the covered row is skipped for taking nothing
    // new, never re-saved, and never allowed to claim the pile ahead of it.
    expect(entries).toEqual([expect.objectContaining({ key: 'so-2:line-20' })]);
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining('already decided that way'),
    );
  });
});

/**
 * BORROW-LOCATION-ONLY (D3, K8). The menu offers "Borrow from another order" only when a
 * TICKED row has a candidate that names a donor SO (switch ON); with the switch OFF the
 * server blanks every donor, so the item is never offered. The "Borrow other location"
 * picker never lists a location reachable only through a donor-NAMED candidate.
 */
describe('BoardDecideControl: Borrow from another order only where a donor is named (D3)', () => {
  const impact = { free_before: '100', free_after_full_borrow: '60', committed_qty: '0' };
  const named = {
    source: 'other_location' as const,
    warehouse_code: 'MWH-IB',
    warehouse_id: 'wh-mwh',
    free_qty: '100',
    donor_impact: impact,
    rung: 'order_borrow',
    donor_so_number: 'SO397450',
    donor_line_no: 446,
    donor_core_line_id: 'core-1',
  };
  const nameless = {
    source: 'other_location' as const,
    warehouse_code: 'DC1-IR',
    warehouse_id: 'wh-dc1',
    free_qty: '100',
    donor_impact: impact,
    rung: 'order_borrow',
    donor_so_number: null,
    donor_core_line_id: 'core-2',
  };

  it('does not offer it when no ticked row has a donor-named candidate', () => {
    renderControl({ contributions: [row({ borrow_candidates: [nameless] })] });
    openMenu();

    expect(screen.queryByRole('menuitem', { name: 'Borrow from another order' })).not.toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Borrow other location' })).toBeInTheDocument();
  });

  it('does not offer it when only an UNTICKED row has a donor-named candidate', () => {
    renderControl({
      contributions: [
        row({ key: 'so-1:line-10', borrow_candidates: [nameless] }),
        row({ key: 'so-2:line-20', so_number: 'SO000002', borrow_candidates: [named] }),
      ],
      selectedKeys: ['so-1:line-10'],
    });
    openMenu();

    expect(screen.queryByRole('menuitem', { name: 'Borrow from another order' })).not.toBeInTheDocument();
  });

  it('offers it when a ticked row has a donor-named candidate', () => {
    renderControl({ contributions: [row({ borrow_candidates: [named] })] });
    openMenu();

    expect(screen.getByRole('menuitem', { name: 'Borrow from another order' })).toBeInTheDocument();
  });

  it('keeps a donor-named location out of the Borrow other location picker (K8)', async () => {
    renderControl({ contributions: [row({ borrow_candidates: [named, nameless] })] });
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Borrow other location' }));

    const dialog = await screen.findByRole('dialog');
    const select = within(dialog).getByLabelText('donor-or-location');
    expect(within(select).getByText('DC1-IR')).toBeInTheDocument();
    expect(within(select).queryByText('MWH-IB')).not.toBeInTheDocument();
  });
});
