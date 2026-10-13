/**
 * `board-verdict-actions-chips-acceptance-criteria.md` section B: the Verdict cell's own
 * row actions, pulled into ONE shared component (`BoardVerdictActions`, AC-B11) so the list
 * view and the grid dialog's contributing-lines table can never drift about what a
 * Suggested, Change-proposed, Saved or Confirmed line offers.
 *
 * RED today: this file, and the component it imports, do not exist yet.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { BoardVerdictActions } from './BoardVerdictActions';
import { suggestedDecisionFor } from '../../_shared/lib/boardAmend';
import type {
  BoardContribution,
  BoardDecision,
} from '../../_shared/types/fulfilmentPlanning.types';

// The same fixture shape `FulfilmentBoardListView.test.tsx` builds its rows with - not a
// second factory, per the tester's own brief.
function contribution(overrides: Partial<BoardContribution> = {}): BoardContribution {
  return {
    key: 'so-1:line-10',
    sales_order_id: 'so-1',
    line_id: 'core-line-10',
    product_id: 'prod-1',
    so_number: 'SO397450',
    customer_name: 'Tuju Residences Sdn Bhd',
    agent_code: 'JEREMY',
    agent_label: 'Jeremy Lee',
    project_label: 'Tuju Residences',
    line_no: 10,
    item_code: 'B2155-NL-BLUE',
    qty: '43',
    qty_outstanding: '43',
    required_date: '2026-09-04',
    unplannable: false,
    rank_score: 0.82,
    rank_factors: [],
    sources: [{ kind: 'buy', qty: '43', reason: 'Nothing free at any location.' }],
    trail: [],
    item_flags: null,
    contested: false,
    covered: false,
    cancelled: false,
    decision: null,
    ...overrides,
  } as BoardContribution;
}

/**
 * Rendered inside a `tr` carrying its own click spy - the same shape every real caller wraps
 * this in (`FulfilmentBoardListView`'s `onRowClick`, `BoardCellBreakdownDialog`'s row
 * expansion) - so AC-B6/AC-B9/AC-B10's "does not expand the row" can be pinned as "the
 * wrapping row's own click handler never fires", not merely "no crash".
 */
function renderActions(
  overrides: {
    contribution?: BoardContribution;
    decision?: BoardDecision | null;
    onDecide?: (decision: BoardDecision | null) => Promise<boolean> | void;
    onChange?: () => void;
  } = {},
) {
  const row = overrides.contribution ?? contribution();
  const onDecide = overrides.onDecide ?? vi.fn();
  const onChange = overrides.onChange ?? vi.fn();
  const onRowClick = vi.fn();
  const utils = render(
    <table>
      <tbody>
        <tr onClick={onRowClick}>
          <td>
            <BoardVerdictActions
              contribution={row}
              decision={overrides.decision ?? null}
              onDecide={onDecide}
              onChange={onChange}
            />
          </td>
        </tr>
      </tbody>
    </table>,
  );
  return { ...utils, onDecide, onChange, onRowClick, row };
}

describe('AC-B1: an actionable line with no session draft (Suggested) shows the full trio', () => {
  it('renders Accept, Reject and Change decision, with the exact aria-labels', () => {
    renderActions();

    expect(
      screen.getByRole('button', { name: 'Save SO397450 line 10 as suggested' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Reject SO397450 line 10' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Change decision for SO397450 line 10' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Undo/ })).not.toBeInTheDocument();
  });
});

describe('AC-B2: a bare pre-mark ("Change proposed") shows the same trio; Accept posts the suggestion Confirm would write', () => {
  it('shows Accept, Reject and Change decision for a preMarked draft with no server draft', () => {
    renderActions({ decision: { verdict: 'approved', preMarked: true } });

    expect(
      screen.getByRole('button', { name: 'Save SO397450 line 10 as suggested' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Reject SO397450 line 10' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Change decision for SO397450 line 10' }),
    ).toBeInTheDocument();
  });

  it('Accept posts suggestedDecisionFor(contribution) - the same body Confirm would write (R2)', async () => {
    const user = userEvent.setup();
    const row = contribution();
    const { onDecide } = renderActions({
      contribution: row,
      decision: { verdict: 'approved', preMarked: true },
    });

    await user.click(screen.getByRole('button', { name: 'Save SO397450 line 10 as suggested' }));

    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith(suggestedDecisionFor(row));
  });
});

describe('AC-B3: a real draft (Saved, Rejected, Suggestion changed) shows Undo and Change decision only', () => {
  it('a Saved line offers Undo and Change decision, no Accept, no Reject', () => {
    renderActions({ decision: { verdict: 'approved' } });

    expect(
      screen.getByRole('button', { name: 'Undo SO397450 line 10' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Change decision for SO397450 line 10' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /as suggested$/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Reject/ })).not.toBeInTheDocument();
  });

  it('a Rejected line offers the same pair, no Accept, no Reject', () => {
    renderActions({
      decision: {
        verdict: 'rejected',
        reason: 'Cancelled by the customer.',
        suspected_system_issue: false,
      },
    });

    expect(
      screen.getByRole('button', { name: 'Undo SO397450 line 10' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Change decision for SO397450 line 10' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /as suggested$/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Reject/ })).not.toBeInTheDocument();
  });

  it('a stale line (Suggestion changed) also offers Undo and Change decision only', () => {
    renderActions({
      contribution: contribution({
        draft: {
          decision: { verdict: 'approved' },
          stale: true,
        } as BoardContribution['draft'],
      }),
      decision: null,
    });

    expect(
      screen.getByRole('button', { name: 'Undo SO397450 line 10' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Change decision for SO397450 line 10' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /as suggested$/ }),
    ).not.toBeInTheDocument();
  });
});

function coveredContribution(overrides: Partial<BoardContribution> = {}): BoardContribution {
  return contribution({
    covered: true,
    decision: {
      revision_no: 1,
      timely_spo_qty: '0',
      reserve: [],
      borrow: [],
      buy_qty: '43',
    },
    ...overrides,
  });
}

describe('AC-R1 (`board-reject-on-confirmed-line-acceptance-criteria.md`, replaces AC-B4): a covered (Confirmed) line shows Change decision AND Reject', () => {
  it('renders Change decision and the X - no Undo, no Accept (R3(b))', () => {
    renderActions({ contribution: coveredContribution() });

    expect(
      screen.getByRole('button', { name: 'Change decision for SO397450 line 10' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Reject SO397450 line 10' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Undo/ })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /as suggested$/ }),
    ).not.toBeInTheDocument();
  });
});

describe('N1 (fix round, `PLAN-board-reject-on-confirmed-line.md`): an INQUIRY-ONLY covered line - no active decision, so no withdrawal seam', () => {
  it('renders Change decision only - no X, no Undo, no Accept', () => {
    // `covered: true` with `decision: null` is the SECOND kind `covered` spans: a live
    // order-inquiry row names the line with no `SOSupplyDecision` at all (#875, migrated
    // sheet lines). Confirm's `rejected_line_ids` reads an active decision's
    // `line_snapshots`, which this line has none of, so the X must not be offered.
    renderActions({ contribution: coveredContribution({ decision: null }) });

    expect(
      screen.getByRole('button', { name: 'Change decision for SO397450 line 10' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Reject/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Undo/ })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /as suggested$/ }),
    ).not.toBeInTheDocument();
  });
});

describe('AC-R2/AC-R3 (`board-reject-on-confirmed-line-acceptance-criteria.md`): the X on a covered line opens the same reject popover', () => {
  it('AC-R2: opens on the X with the reason textarea, the checkbox and a Reject button disabled while blank', async () => {
    const user = userEvent.setup();
    renderActions({ contribution: coveredContribution() });

    await user.click(screen.getByRole('button', { name: 'Reject SO397450 line 10' }));

    expect(await screen.findByText('Why this differs')).toBeInTheDocument();
    const textarea = screen.getByPlaceholderText('In your own words');
    const rejectSubmit = screen.getByRole('button', { name: 'Reject' });
    expect(rejectSubmit).toBeDisabled();

    await user.type(textarea, 'Wrong site');
    expect(rejectSubmit).toBeEnabled();
  });

  it('AC-R3: submitting a typed reason calls onDecide with verdict rejected, and the row click never fires', async () => {
    const user = userEvent.setup();
    const { onDecide, onRowClick } = renderActions({ contribution: coveredContribution() });

    await user.click(screen.getByRole('button', { name: 'Reject SO397450 line 10' }));
    await user.type(screen.getByPlaceholderText('In your own words'), 'Wrong site');
    await user.click(screen.getByRole('button', { name: 'Reject' }));

    expect(onDecide).toHaveBeenCalledWith({
      verdict: 'rejected',
      reason: 'Wrong site',
      suspected_system_issue: false,
    });
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it('AC-R3/AC-B8 reused: a failed onDecide keeps the popover open with the typed reason', async () => {
    const user = userEvent.setup();
    const onDecide = vi.fn().mockResolvedValue(false);
    renderActions({ contribution: coveredContribution(), onDecide });

    await user.click(screen.getByRole('button', { name: 'Reject SO397450 line 10' }));
    await user.type(screen.getByPlaceholderText('In your own words'), 'Wrong site');
    await user.click(screen.getByRole('button', { name: 'Reject' }));

    await waitFor(() => expect(onDecide).toHaveBeenCalledTimes(1));
    expect(screen.getByPlaceholderText('In your own words')).toHaveValue('Wrong site');
  });
});

describe('AC-B5: a cancelled or unplannable line renders no buttons at all', () => {
  it('a cancelled line renders nothing', () => {
    renderActions({ contribution: contribution({ cancelled: true }) });

    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('an unplannable line renders nothing', () => {
    renderActions({ contribution: contribution({ unplannable: true, cancelled: false }) });

    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('AC-B6: Accept never expands the row', () => {
  it('stops propagation so the wrapping rows own click handler never fires', async () => {
    const user = userEvent.setup();
    const { onRowClick } = renderActions();

    await user.click(screen.getByRole('button', { name: 'Save SO397450 line 10 as suggested' }));

    expect(onRowClick).not.toHaveBeenCalled();
  });
});

describe('AC-B7/AC-B8/AC-B9: the Reject popover', () => {
  it('AC-B7: opens on the X button with the label, textarea, checkbox and a Reject button disabled while blank', async () => {
    const user = userEvent.setup();
    renderActions();

    await user.click(screen.getByRole('button', { name: 'Reject SO397450 line 10' }));

    expect(await screen.findByText('Why this differs')).toBeInTheDocument();
    const textarea = screen.getByPlaceholderText('In your own words');
    expect(textarea).toBeInTheDocument();
    expect(
      screen.getByText('This might be a system problem, flag it for investigation'),
    ).toBeInTheDocument();

    const rejectSubmit = screen.getByRole('button', { name: 'Reject' });
    expect(rejectSubmit).toBeDisabled();
    expect(rejectSubmit).toHaveAttribute('title', 'Say why this line is being refused first.');

    await user.type(textarea, 'The group is short');
    expect(rejectSubmit).toBeEnabled();
  });

  it('AC-B8: submitting calls onDecide with the trimmed reason and the flag, and closes the popover', async () => {
    const user = userEvent.setup();
    const { onDecide } = renderActions();

    await user.click(screen.getByRole('button', { name: 'Reject SO397450 line 10' }));
    await user.type(screen.getByPlaceholderText('In your own words'), '  Wrong quantity in the book  ');
    await user.click(
      screen.getByText('This might be a system problem, flag it for investigation'),
    );
    await user.click(screen.getByRole('button', { name: 'Reject' }));

    expect(onDecide).toHaveBeenCalledWith({
      verdict: 'rejected',
      reason: 'Wrong quantity in the book',
      suspected_system_issue: true,
    });
    await waitFor(() =>
      expect(screen.queryByPlaceholderText('In your own words')).not.toBeInTheDocument(),
    );
  });

  /**
   * BL-1 (reviewer, fix round 2): the harness MIRRORS `FulfilmentBoardPanel.decide`, which is
   * local-first - it writes the session draft synchronously, awaits the network, reverts the
   * key on failure and returns `false`. So the `decision` prop really does flip to `rejected`
   * and back around the failed write, and a popover whose state lives below that flip loses
   * the typed reason (its own subtree unmounts the moment the line stops being "proposed").
   * A `vi.fn().mockResolvedValue(false)` that never moves the prop cannot see that at all.
   */
  it('AC-B8: a rejection whose write FAILS keeps the popover open with the typed reason, across the optimistic flip to rejected and back', async () => {
    const user = userEvent.setup();
    const row = contribution();
    const decided = vi.fn();

    function Harness() {
      const [decision, setDecision] = React.useState<BoardDecision | null>(null);
      const onDecide = async (next: BoardDecision | null) => {
        decided(next);
        // Local-first, exactly as the board writes it.
        setDecision(next);
        await Promise.resolve();
        // The write failed: only this key goes back to what the click found it as.
        setDecision(null);
        return false;
      };
      return (
        <table>
          <tbody>
            <tr>
              <td>
                <BoardVerdictActions
                  contribution={row}
                  decision={decision}
                  onDecide={onDecide}
                  onChange={vi.fn()}
                />
              </td>
            </tr>
          </tbody>
        </table>
      );
    }

    render(<Harness />);

    await user.click(screen.getByRole('button', { name: 'Reject SO397450 line 10' }));
    await user.type(screen.getByPlaceholderText('In your own words'), 'The group is short');
    await user.click(screen.getByRole('button', { name: 'Reject' }));

    await waitFor(() => expect(decided).toHaveBeenCalledTimes(1));
    expect(screen.getByPlaceholderText('In your own words')).toHaveValue('The group is short');
    // And the line is offerable again, because the board put the draft back.
    expect(
      screen.getByRole('button', { name: 'Reject SO397450 line 10' }),
    ).toBeInTheDocument();
  });

  /**
   * DELTA-2 (reviewer, fix round 3): the guard on the `|| rejecting` half of the render
   * condition. The write is held open here, so the assertion lands while the draft ALREADY
   * says `rejected` - the exact moment the line stops being a "proposed" one - and the
   * popover the planner is looking at has to still be there. Drop `|| rejecting` from the
   * render condition and this fails mid-flight, before any promise is released.
   */
  it('AC-B8/BL-1: the popover stays on screen while the write is in flight, draft already flipped to rejected', async () => {
    const user = userEvent.setup();
    const row = contribution();
    let release: (written: boolean) => void = () => {};
    const onTheWire = new Promise<boolean>((resolve) => {
      release = resolve;
    });

    function Harness() {
      const [decision, setDecision] = React.useState<BoardDecision | null>(null);
      const onDecide = async (next: BoardDecision | null) => {
        // Local-first, exactly as `FulfilmentBoardPanel.decide` writes it: the draft moves
        // before the network answers.
        setDecision(next);
        const written = await onTheWire;
        if (!written) setDecision(null);
        return written;
      };
      return (
        <table>
          <tbody>
            <tr>
              <td>
                <BoardVerdictActions
                  contribution={row}
                  decision={decision}
                  onDecide={onDecide}
                  onChange={vi.fn()}
                />
              </td>
            </tr>
          </tbody>
        </table>
      );
    }

    render(<Harness />);

    await user.click(screen.getByRole('button', { name: 'Reject SO397450 line 10' }));
    await user.type(screen.getByPlaceholderText('In your own words'), 'The group is short');
    await user.click(screen.getByRole('button', { name: 'Reject' }));

    // The optimistic draft has landed: the cell now reads as a line with a decision on it
    // (Undo is offered, Accept is not), which is what makes `proposed` false.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Undo SO397450 line 10' })).toBeInTheDocument(),
    );
    expect(screen.queryByRole('button', { name: /as suggested$/ })).not.toBeInTheDocument();
    // And the popover is still up, with the reason still in it, nothing resolved yet.
    expect(screen.getByPlaceholderText('In your own words')).toHaveValue('The group is short');

    // Now the write comes back a failure: the board reverts the key, and the planner still
    // has what they typed.
    await act(async () => {
      release(false);
    });

    expect(screen.getByPlaceholderText('In your own words')).toHaveValue('The group is short');
    expect(
      screen.getByRole('button', { name: 'Reject SO397450 line 10' }),
    ).toBeInTheDocument();
  });

  it('AC-B9: Escape closes the popover without calling onDecide, and reopening starts blank', async () => {
    const user = userEvent.setup();
    const { onDecide } = renderActions();

    await user.click(screen.getByRole('button', { name: 'Reject SO397450 line 10' }));
    await user.type(screen.getByPlaceholderText('In your own words'), 'Half a reason');
    await user.keyboard('{Escape}');

    await waitFor(() =>
      expect(screen.queryByPlaceholderText('In your own words')).not.toBeInTheDocument(),
    );
    expect(onDecide).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Reject SO397450 line 10' }));
    expect(screen.getByPlaceholderText('In your own words')).toHaveValue('');
  });

  it('AC-B9: outside click closes the popover without calling onDecide', async () => {
    const user = userEvent.setup();
    const { onDecide } = renderActions();

    await user.click(screen.getByRole('button', { name: 'Reject SO397450 line 10' }));
    await screen.findByPlaceholderText('In your own words');

    // Radix closes on a pointerdown outside the content - `document.body` is outside every
    // popover this component can render.
    fireEvent.pointerDown(document.body);
    fireEvent.mouseDown(document.body);

    await waitFor(() =>
      expect(screen.queryByPlaceholderText('In your own words')).not.toBeInTheDocument(),
    );
    expect(onDecide).not.toHaveBeenCalled();
  });

  it('AC-B9: clicks inside the popover do not expand the row', async () => {
    const user = userEvent.setup();
    const { onRowClick } = renderActions();

    await user.click(screen.getByRole('button', { name: 'Reject SO397450 line 10' }));
    await user.click(screen.getByText('This might be a system problem, flag it for investigation'));
    await user.type(screen.getByPlaceholderText('In your own words'), 'x');

    expect(onRowClick).not.toHaveBeenCalled();
  });
});

describe('AC-B10: Change decision expands the row and does not toggle it closed', () => {
  it('calls onChange and stops the click from reaching the wrapping row', async () => {
    const user = userEvent.setup();
    const { onChange, onRowClick } = renderActions();

    await user.click(
      screen.getByRole('button', { name: 'Change decision for SO397450 line 10' }),
    );

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it('pressing it twice still calls onChange each time - the caller, not this component, makes it idempotent', async () => {
    const user = userEvent.setup();
    const { onChange } = renderActions();

    const changeButton = screen.getByRole('button', {
      name: 'Change decision for SO397450 line 10',
    });
    await user.click(changeButton);
    await user.click(changeButton);

    expect(onChange).toHaveBeenCalledTimes(2);
  });
});
