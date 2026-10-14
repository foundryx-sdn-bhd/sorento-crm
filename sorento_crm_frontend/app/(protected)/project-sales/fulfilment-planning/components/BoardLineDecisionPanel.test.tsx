/**
 * The decision on one contributing line, taken IN THE ROW (PLAN section 3.C, ruling R7).
 *
 * The fixture is the plan's own canonical example (UAC header line, R1): SO404352 line 22,
 * SRTWB7518, BRW-AM on hand 10 with SO383850 holding 1 there (so 9 available, B1), the shared
 * pool at BRW holding 16. The engine's suggestion is Reserve 9 at BRW-AM plus Reserve 15 at
 * BRW (the 24 outstanding, C7/C8's own numbers), so every test below traces to a UAC id.
 */
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { BoardDecisionPill } from './BoardDecisionPill';
import { BoardLineDecisionPanel } from './BoardLineDecisionPanel';
import type {
  BoardBorrowCandidate,
  BoardCellLocation,
  BoardContribution,
  BoardDecision,
  BoardLineDecision,
} from '../../_shared/types/fulfilmentPlanning.types';

const KEY = 'so-a|22|SRTWB7518|2026-06-29';

function contributionOf(
  overrides: Partial<BoardContribution> = {},
): BoardContribution {
  return {
    key: KEY,
    sales_order_id: 'so-a',
    so_number: 'SO404352',
    customer_name: 'ABC SDN BHD',
    project_label: null,
    agent_code: 'AG01',
    line_no: 22,
    item_code: 'SRTWB7518',
    qty: '24',
    qty_ordered: '24',
    qty_delivered: '0',
    qty_outstanding: '24',
    project_line_id: 'pl-so-a-22',
    required_date: '2026-06-29',
    is_past: false,
    fulfilment_location: 'BRW-AM',
    fulfilment_warehouse_id: 'wh-BRW-AM',
    unplannable: false,
    priority: null,
    sources: [
      {
        kind: 'reserve',
        qty: '9',
        location: 'BRW-AM',
        warehouse_id: 'wh-BRW-AM',
        reason:
          'Free unclaimed stock at BRW-AM covers this much by the delivery date.',
      },
      {
        kind: 'reserve',
        qty: '15',
        location: 'BRW',
        warehouse_id: 'wh-BRW',
        reason: 'The shared pool at BRW covers this much within its cap.',
      },
    ],
    qty_proposed_reserve: '24',
    qty_proposed_incoming: '0',
    qty_proposed_buy: '0',
    contested: false,
    rank_score: 0,
    rank_factors: [],
    covered: false,
    decision: null,
    order_inquiry: null,
    item_flags: null,
    borrow_candidates: [],
    ...overrides,
  };
}

const LOCATIONS: BoardCellLocation[] = [
  {
    location: 'BRW-AM',
    warehouse_id: 'wh-BRW-AM',
    qty: '0',
    available_qty: '9',
    qty_free: '9',
    qty_free_remaining: '9',
  },
  {
    location: 'BRW',
    warehouse_id: 'wh-BRW',
    qty: '0',
    available_qty: '16',
    qty_free: '16',
    qty_free_remaining: '16',
  },
];

function renderPanel(
  overrides: Partial<BoardContribution> = {},
  decision: BoardDecision | null = null,
) {
  const onDecide = vi.fn();
  const onDirtyChange = vi.fn();
  render(
    <BoardLineDecisionPanel
      contribution={contributionOf(overrides)}
      decision={decision}
      locations={LOCATIONS}
      onDecide={onDecide}
      onDirtyChange={onDirtyChange}
    />,
  );
  return { onDecide, onDirtyChange };
}

describe('BoardLineDecisionPanel: no read-only strip', () => {
  it('does not repeat Ordered, Delivered, Outstanding or Incoming - the row above states them', () => {
    renderPanel({
      qty_ordered: '24',
      qty_delivered: '0',
      qty_outstanding: '24',
    });

    const panel = screen.getByTestId(`line-decision-${KEY}`);
    expect(panel).not.toHaveTextContent('Ordered');
    expect(panel).not.toHaveTextContent('Delivered');
    expect(panel).not.toHaveTextContent('Outstanding');
    expect(panel).not.toHaveTextContent('Incoming by the delivery date');
  });
});

/**
 * The Options table used to render open, five rows tall, ABOVE the editor on every line -
 * wasting the space a planner opened the row to compose a decision in. It folds behind a
 * plain toggle now, closed until asked for (owner, 22 Sep 2026); the table itself
 * (`BoardLadderOptionsTable`) is unchanged, so these tests only prove the fold.
 */
describe('BoardLineDecisionPanel: the Options ladder is collapsible, and closed by default', () => {
  const OPTIONS = [
    {
      step: 'use' as const,
      label: 'Use our locations',
      whole: true,
      fulfil_date: '2026-06-29',
      days_late: 0,
      chosen: true,
      gives_qty: '24',
    },
    {
      step: 'buy' as const,
      label: 'Buy',
      whole: true,
      fulfil_date: '2026-07-10',
      days_late: 11,
      chosen: false,
      gives_qty: '24',
    },
  ];

  it('starts closed: the table is not in the document, and the toggle states so', () => {
    renderPanel({ options: OPTIONS });

    expect(screen.queryByText('Use our locations')).not.toBeInTheDocument();
    const toggle = screen.getByTestId(`line-options-toggle-${KEY}`);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('opens the table on click, and hides it again on a second click', () => {
    renderPanel({ options: OPTIONS });

    const toggle = screen.getByTestId(`line-options-toggle-${KEY}`);
    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Use our locations')).toBeInTheDocument();

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Use our locations')).not.toBeInTheDocument();
  });
});

describe('BoardLineDecisionPanel: Reserve inputs carry the server figure beside them (C4, B1)', () => {
  it('opens on the suggestion, and shows what each location has available', () => {
    renderPanel();

    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(9);
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(15);
    const panel = screen.getByTestId(`line-decision-${KEY}`);
    expect(panel).toHaveTextContent('9 available');
    expect(panel).toHaveTextContent('16 available');
  });
});

/**
 * TWO VERBS (C9), because the captain would not press two Saves: "if the suggestion is same as
 * decision then it is approved, if suggestion different from decision then it is amended, so I
 * just click on 1 button". So the comparison takes the verdict, and these tests assert WHICH
 * verdict one press produces rather than which button was chosen.
 */
describe('BoardLineDecisionPanel: the two verbs (C9)', () => {
  it('Save on the untouched suggestion approves it, with no reason and no flag', () => {
    const { onDecide } = renderPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));

    // D11: an approved decision carries the suggested COMPOSITION too, not only the verdict -
    // `so_supply_decision_drafts.decision` is read verbatim by the SO page's own "Decided"
    // column, and a bare `{verdict:'approved'}` read there as no components at all.
    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'approved',
        suspected_system_issue: false,
        reserve: expect.arrayContaining([
          expect.objectContaining({ warehouse_id: 'wh-BRW-AM', qty: '9' }),
          expect.objectContaining({ warehouse_id: 'wh-BRW', qty: '15' }),
        ]),
        borrow: [],
        buy_qty: '0',
      }),
    );
  });

  it('Save on a changed composition amends it, once it balances and carries a reason', () => {
    const { onDecide } = renderPanel();

    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '5' },
    });
    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '19' },
    });
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The site asked for less from BRW-AM.' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'amended',
        reserve: expect.arrayContaining([
          expect.objectContaining({ warehouse_id: 'wh-BRW-AM', qty: '5' }),
          expect.objectContaining({ warehouse_id: 'wh-BRW', qty: '19' }),
        ]),
        reason: 'The site asked for less from BRW-AM.',
      }),
    );
  });

  it('reject requires a reason, and is disabled without one', async () => {
    const { onDecide } = renderPanel();

    const reject = screen.getByRole('button', { name: 'Reject' });
    expect(reject).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The customer cancelled this line.' },
    });
    expect(reject).toBeEnabled();
    // `reject()` is async (mirrors `save()`), so the state it sets afterwards lands past an
    // `await` - wrapped so that settling is inside `act`, same as every Save assertion below.
    await act(async () => {
      fireEvent.click(reject);
    });

    expect(onDecide).toHaveBeenCalledWith({
      verdict: 'rejected',
      reason: 'The customer cancelled this line.',
      suspected_system_issue: false,
    });
  });
});

/**
 * D11 (gap found in D10's report). `_saved_components` (`sales_order_service.py`) reads the
 * SAVED decision JSON verbatim for the SO page's own "Decided" column, and an approval that
 * posted only `{verdict: 'approved'}` read as no components at all - the SO page showed "-"
 * beside a pill that already read Saved. An approved decision now carries the suggested
 * COMPOSITION too, the same way `confirmLinesFor` already derives one for Confirm
 * (`decisionFromAmendDraft(suggestionDraftFrom(contribution), '')`), so the two never disagree
 * about what an approval actually composed.
 */
describe('BoardLineDecisionPanel: an approved draft carries the suggested composition (D11)', () => {
  it('approving a Reserve-only suggestion carries the reserve row and a zero Buy', () => {
    const onDecide = vi.fn();
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          key: 'so-a|7|BRW3|2026-06-29',
          line_no: 7,
          item_code: 'BRW3',
          qty: '3',
          qty_ordered: '3',
          qty_outstanding: '3',
          sources: [
            {
              kind: 'reserve',
              qty: '3',
              location: 'BRW',
              warehouse_id: 'wh-BRW',
              reason: 'The shared pool at BRW covers this line.',
            },
          ],
          qty_proposed_reserve: '3',
          qty_proposed_incoming: '0',
          qty_proposed_buy: '0',
        })}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'approved',
        reserve: [expect.objectContaining({ location: 'BRW', qty: '3' })],
        buy_qty: '0',
      }),
    );
  });

  it('approving a Buy-only suggestion carries the Buy quantity and no reserve', () => {
    const onDecide = vi.fn();
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          key: 'so-a|8|BUY3|2026-06-29',
          line_no: 8,
          item_code: 'BUY3',
          qty: '3',
          qty_ordered: '3',
          qty_outstanding: '3',
          sources: [
            {
              kind: 'buy',
              qty: '3',
              location: null,
              warehouse_id: null,
              reason: 'Nothing on hand covers this line.',
            },
          ],
          qty_proposed_reserve: '0',
          qty_proposed_incoming: '0',
          qty_proposed_buy: '3',
        })}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'approved',
        reserve: [],
        buy_qty: '3',
      }),
    );
  });

  it('leaves an amended save unchanged - it already composes everything it posts', () => {
    const { onDecide } = renderPanel();

    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '5' },
    });
    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '19' },
    });
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The site asked for less from BRW-AM.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'amended',
        reserve: expect.arrayContaining([
          expect.objectContaining({ warehouse_id: 'wh-BRW-AM', qty: '5' }),
          expect.objectContaining({ warehouse_id: 'wh-BRW', qty: '19' }),
        ]),
      }),
    );
  });
});

/**
 * BOARD-CONFIRM-LEFT-OUT (SO420745, 21 Sep 2026): an approving Save used to drop the reasons
 * the planner typed - the discontinued Buy's own reason, and a suggested borrow row's - because
 * the approving branch posted `decisionFromAmendDraft(suggestionDraftFrom(contribution), '')`
 * verbatim (which seeds `buy_reason` from nothing) and the reseed afterwards put the SAME
 * reason-less draft back on screen. `matchesSuggestion` rightly compares quantities only, so
 * typing a reason with the quantities untouched still reads as an approval - the fix is that
 * approval carrying the reason across, not the comparison.
 */
describe('BoardLineDecisionPanel: an approving save carries the reasons the planner typed (AC-1/AC-2/AC-3)', () => {
  const discontinuedFlags = {
    dealer_hot_selling: false,
    dealer_hot_selling_where: [],
    project_hot_selling: false,
    project_hot_selling_where: [],
    dealer_classified: false,
    project_classified: false,
    discontinued: true,
    retail_classification_available: true,
  };

  it('a discontinued Buy suggestion keeps the typed reason after Save, and the blocker does not return', async () => {
    const onDecide = vi.fn().mockResolvedValue(true);
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          key: 'so-a|32|SRTWT9610-GM|2026-09-10',
          line_no: 32,
          item_code: 'SRTWT9610-GM',
          qty: '3',
          qty_ordered: '3',
          qty_outstanding: '3',
          sources: [
            {
              kind: 'buy',
              qty: '3',
              location: null,
              warehouse_id: null,
              reason: 'Nothing on hand covers this line.',
            },
          ],
          qty_proposed_reserve: '0',
          qty_proposed_incoming: '0',
          qty_proposed_buy: '3',
          item_flags: discontinuedFlags,
        })}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'project order' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    // AC-1: the SAME press carries verdict and reason both - one Save, one call.
    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'approved',
        buy_reason: 'project order',
        // AC-28 (review round 1, Blocking 3): the ONE box is `reason` too, not only
        // `buy_reason` - the approving branch used to post `reason: undefined` unconditionally.
        reason: 'project order',
      }),
    );

    // AC-1: the box still holds what was typed, and the red blocker is gone - the reseed used
    // to put the engine's own (reason-less) draft back, which emptied the box and brought the
    // "buying a discontinued product needs a reason" blocker straight back.
    expect(screen.getByLabelText(/^Reason/)).toHaveValue('project order');
    expect(screen.queryByText(/needs a reason/)).not.toBeInTheDocument();
  });

  it('a suggested borrow row keeps its typed reason after an approving Save (AC-3)', async () => {
    const onDecide = vi.fn().mockResolvedValue(true);
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          key: 'so-a|5|MWH-BRW|2026-09-10',
          line_no: 5,
          item_code: 'MWH-BRW',
          qty: '5',
          qty_ordered: '5',
          qty_outstanding: '5',
          sources: [
            {
              kind: 'borrow',
              qty: '5',
              location: 'MWH-IB',
              warehouse_id: 'wh-mwh-ib',
              reason: 'Cross-group cap allows this.',
            },
          ],
          qty_proposed_reserve: '0',
          qty_proposed_incoming: '0',
          qty_proposed_buy: '0',
        })}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Confirmed with the other site on WhatsApp.' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'approved',
        borrow: expect.arrayContaining([
          expect.objectContaining({
            warehouse_id: 'wh-mwh-ib',
            reason: 'Confirmed with the other site on WhatsApp.',
          }),
        ]),
      }),
    );
    // Fix round 2 (reviewer, nit c - kill test K2): the BORROW half of the reseed was never
    // actually asserted on screen, only the outgoing payload - this is the same box the Buy
    // reason test above checks, for the row the save just carried a reason for.
    expect(screen.getByLabelText(/^Reason/)).toHaveValue(
      'Confirmed with the other site on WhatsApp.',
    );
  });
});

/**
 * Review round 1, Blocking 4, kill test 3 (AC-24): re-adding the Buy block's own retired
 * "Reason *" textarea under a DIFFERENT label ("Why buy it") stayed green, because the only
 * existing assertion looks up the box by its label (`/^Reason/`) rather than counting how many
 * text areas the discontinued Buy state renders. D3's whole point is ONE box, so this pins the
 * count directly, label aside.
 */
describe('BoardLineDecisionPanel: a discontinued Buy renders exactly one Reason textarea (AC-24)', () => {
  it('renders one <textarea>, not two, when the line is discontinued and buying', () => {
    const { container } = render(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          key: 'so-a|32|SRTWT9610-GM|2026-09-10',
          line_no: 32,
          item_code: 'SRTWT9610-GM',
          qty: '3',
          qty_ordered: '3',
          qty_outstanding: '3',
          sources: [
            {
              kind: 'buy',
              qty: '3',
              location: null,
              warehouse_id: null,
              reason: 'Nothing on hand covers this line.',
            },
          ],
          qty_proposed_reserve: '0',
          qty_proposed_incoming: '0',
          qty_proposed_buy: '3',
          item_flags: {
            dealer_hot_selling: false,
            dealer_hot_selling_where: [],
            project_hot_selling: false,
            project_hot_selling_where: [],
            dealer_classified: false,
            project_classified: false,
            discontinued: true,
            retail_classification_available: true,
          },
        })}
        decision={null}
        locations={LOCATIONS}
        onDecide={vi.fn()}
      />,
    );

    expect(screen.getByText('Discontinued')).toBeInTheDocument();
    expect(container.querySelectorAll('textarea')).toHaveLength(1);
  });
});

/**
 * Review round 1, Blocking 2: a same-agent borrow `BorrowAddDialog` adds by hand seeds the
 * row's `reason` with `storedReason`'s own "Authorised by ..." sentence. `foldReasonIntoDraft`
 * used to replace that outright with the panel's own box text on Save, so the authorisation
 * the server requires (`_check_borrow`) never reached the server. The fix keeps the seeded
 * prefix and appends the box text, the same shape `BoardDecideControl` already builds fresh.
 */
describe('BoardLineDecisionPanel: a hand-added same-agent borrow keeps its authorisation (review round 1, Blocking 2)', () => {
  it('appends the typed Reason to the seeded "Authorised by" sentence instead of replacing it', async () => {
    const onDecide = vi.fn().mockResolvedValue(true);
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          borrow_candidates: [
            {
              source: 'other_location',
              warehouse_code: 'MWH-BB',
              warehouse_id: 'wh-mwh-bb',
              free_qty: '90',
              donor_impact: {
                free_before: '90',
                free_after_full_borrow: '0',
                committed_qty: '0',
              },
              donor_agent_code: 'JEREMY',
              donor_core_line_id: 'core-line-1',
              same_agent: true,
              location: {
                location: 'MWH-BB',
                where: 'other_group',
                product_id: 'prod-1',
                warehouse_id: 'wh-mwh-bb',
                qty: '0',
                qty_on_hand: '90',
                so_qty: '0',
                spo_qty: '0',
                available_qty: '90',
                po_open_qty: '0',
                incoming: [],
              },
            },
          ],
        })}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    // Free 9 off the BRW reserve (15 -> 6) so the whole line still balances once the 9 is
    // borrowed instead - D7 derives a Buy for the gap otherwise, which the borrow then closes.
    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '6' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Add a borrow' }));

    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '9' } });
    fireEvent.change(screen.getByLabelText(/^Authorised by agent JEREMY/), {
      target: { value: 'Agreed on the phone, 25 Aug' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add the borrow' }));

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Stock held for handover.' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        borrow: expect.arrayContaining([
          expect.objectContaining({
            warehouse_id: 'wh-mwh-bb',
            reason:
              'Authorised by agent JEREMY: Agreed on the phone, 25 Aug. Stock held for handover.',
          }),
        ]),
      }),
    );
  });
});

/**
 * Review round 2, Blocking 1: the round 1 fix above kept the seeded "Authorised by" sentence
 * and appended the box text unconditionally, so re-saving a reopened line with nothing edited
 * appended the SAME box text a second time - `foldBorrowReason` never checked whether the
 * seeded reason already carried it. The fix rebuilds the reason from the authorisation
 * sentence plus the current box text every time, instead of appending onto whatever was
 * stored last, so an unedited re-save reproduces the same string rather than growing it.
 */
const BORROW_CANDIDATE_JEREMY: BoardBorrowCandidate = {
  source: 'other_location',
  warehouse_code: 'MWH-BB',
  warehouse_id: 'wh-mwh-bb',
  free_qty: '90',
  donor_impact: {
    free_before: '90',
    free_after_full_borrow: '0',
    committed_qty: '0',
  },
  donor_agent_code: 'JEREMY',
  donor_core_line_id: 'core-line-1',
  same_agent: true,
  location: {
    location: 'MWH-BB',
    where: 'other_group',
    product_id: 'prod-1',
    warehouse_id: 'wh-mwh-bb',
    qty: '0',
    qty_on_hand: '90',
    so_qty: '0',
    spo_qty: '0',
    available_qty: '90',
    po_open_qty: '0',
    incoming: [],
  },
};

describe('BoardLineDecisionPanel: re-saving a reopened same-agent borrow line (review round 2, Blocking 1)', () => {
  it('does not double the box text when the reopened line is saved again unedited', async () => {
    const onDecide = vi.fn().mockResolvedValue(true);
    const first = render(
      <BoardLineDecisionPanel
        contribution={contributionOf({ borrow_candidates: [BORROW_CANDIDATE_JEREMY] })}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '6' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add a borrow' }));
    fireEvent.change(screen.getByLabelText('Quantity'), { target: { value: '9' } });
    fireEvent.change(screen.getByLabelText(/^Authorised by agent JEREMY/), {
      target: { value: 'Agreed on the phone, 25 Aug' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add the borrow' }));
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Stock held for handover.' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    const firstDecision = onDecide.mock.calls[0][0] as BoardDecision;
    expect(firstDecision.borrow?.[0]?.reason).toBe(
      'Authorised by agent JEREMY: Agreed on the phone, 25 Aug. Stock held for handover.',
    );

    first.unmount();

    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({ borrow_candidates: [BORROW_CANDIDATE_JEREMY] })}
        decision={firstDecision}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    expect(screen.getByRole('button', { name: 'Save decision' })).toBeEnabled();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    const secondDecision = onDecide.mock.calls[1][0] as BoardDecision;
    expect(secondDecision.borrow?.[0]?.reason).toBe(firstDecision.borrow?.[0]?.reason);
  });

  it('does not double the box text when the box was seeded from a Decide-saved reason that already carries the whole "Authorised by" sentence', async () => {
    const onDecide = vi.fn().mockResolvedValue(true);
    // D3/R9: a Decide save folds the whole "Authorised by ...: <name>. <reason>" sentence into
    // BOTH the top-level `reason` (what `seedReason` reads first) and every borrow row's own
    // `reason` (`BoardDecideControl.tsx`), so on reopen the box seeds with the whole sentence,
    // not just the typed reason.
    const decideSavedDecision: BoardDecision = {
      verdict: 'amended',
      reserve: [
        { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '9' },
        { warehouse_id: 'wh-BRW', location: 'BRW', qty: '6' },
      ],
      borrow: [
        {
          source: 'other_location',
          warehouse_id: 'wh-mwh-bb',
          warehouse_code: 'MWH-BB',
          qty: '9',
          reason:
            'Authorised by agent JEREMY: Agreed on the phone, 25 Aug. Stock held for handover.',
        },
      ],
      timely_spo_qty: '0',
      buy_qty: '0',
      reason:
        'Authorised by agent JEREMY: Agreed on the phone, 25 Aug. Stock held for handover.',
    };

    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({ borrow_candidates: [BORROW_CANDIDATE_JEREMY] })}
        decision={decideSavedDecision}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    expect(screen.getByRole('button', { name: 'Save decision' })).toBeEnabled();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        borrow: expect.arrayContaining([
          expect.objectContaining({
            warehouse_id: 'wh-mwh-bb',
            reason:
              'Authorised by agent JEREMY: Agreed on the phone, 25 Aug. Stock held for handover.',
          }),
        ]),
      }),
    );
  });
});

/**
 * Review round 1, Blocking 4, kill test 2(b): `hasUnsuggestedBorrow` (AC-27) is unguarded - a
 * re-opened, UNEDITED covered line whose frozen decision already carries a borrow the engine
 * itself never suggested, with no reason recorded for it (an older revision, frozen before the
 * one-Reason-box shape existed - `seedReason` seeds nothing from a blank `borrow[0].reason`),
 * matches its own baseline (`amendNeedsReason` reads false), so only `hasUnsuggestedBorrow`
 * still asks for a reason. Killing that check alone left every existing test green because none
 * of them opened a line in exactly this state.
 */
describe('BoardLineDecisionPanel: a reopened covered line with a hand borrow already frozen still needs a reason (AC-27)', () => {
  it('disables Save while the Reason box is blank, and enables it once typed, with nothing else edited', () => {
    const onDecide = vi.fn().mockResolvedValue(true);
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          covered: true,
          qty: '9',
          qty_ordered: '9',
          qty_outstanding: '9',
          sources: [
            {
              kind: 'reserve',
              qty: '9',
              location: 'BRW-AM',
              warehouse_id: 'wh-BRW-AM',
              reason: 'Free unclaimed stock at BRW-AM covers this much.',
            },
          ],
          qty_proposed_reserve: '9',
          qty_proposed_incoming: '0',
          qty_proposed_buy: '0',
          decision: {
            revision_no: 1,
            confirmed_at: '2026-08-18T02:00:00',
            timely_spo_qty: '0',
            reserve: [],
            borrow: [
              {
                source: 'other_location',
                warehouse_id: 'wh-mwh-bb',
                location: 'MWH-BB',
                qty: '9',
                reason: '',
              },
            ],
            buy_qty: '0',
            suspected_system_issue: false,
          },
        })}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));

    expect(screen.getByLabelText(/^Reason/)).toHaveValue('');
    const save = screen.getByRole('button', { name: 'Save decision' });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Reconfirming the same borrow on reopen.' },
    });
    expect(save).toBeEnabled();
  });
});

/**
 * NO MANUAL ORDER BACK (owner, 14 Oct 2026, hand test of #1675): "remove this manual order back
 * trigger since we have established borrow = order back". A Borrow already reads ORDER BACK on
 * the server (`_saved_line_parts`, `_raise_borrow_shortfalls`), so the panel offers no switch
 * and never writes `order_back` / `cited_document` into a draft, not even one it reopened with
 * the flag on.
 */
describe('BoardLineDecisionPanel: no manual Order back (borrow = order back)', () => {
  const BUY_ONLY: Partial<BoardContribution> = {
    key: KEY,
    sources: [
      {
        kind: 'buy',
        qty: '24',
        location: null,
        warehouse_id: null,
        reason: 'Nothing on hand covers this line.',
      },
    ],
    qty_proposed_reserve: '0',
    qty_proposed_buy: '24',
  };

  it('a line that buys offers no Order back switch and no Document cited box', async () => {
    const onDecide = vi.fn().mockResolvedValue(true);
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf(BUY_ONLY)}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    expect(screen.queryByRole('switch', { name: 'Order back' })).toBeNull();
    expect(screen.queryByLabelText('Document cited')).toBeNull();

    // Not even once the whole-line switch is off and a Buy is typed.
    fireEvent.click(screen.getByRole('switch', { name: 'Buy the whole line' }));
    fireEvent.change(screen.getByLabelText('Buy quantity'), { target: { value: '5' } });
    expect(screen.queryByRole('switch', { name: 'Order back' })).toBeNull();
    expect(screen.queryByLabelText('Document cited')).toBeNull();
  });

  it('a reopened decision that carried an order back saves without it', async () => {
    const { onDecide } = renderPanel(BUY_ONLY, {
      verdict: 'amended',
      reserve: [{ warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '9' }],
      borrow: [],
      timely_spo_qty: '0',
      buy_qty: '15',
      order_back: true,
      cited_document: 'SPO-2026/09-0042',
      reason: 'Saved before the switch went away.',
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    const saved = onDecide.mock.calls.at(-1)?.[0] ?? {};
    expect(saved).toMatchObject({ verdict: 'amended', buy_qty: '15' });
    expect(saved.order_back).toBeFalsy();
    expect(saved.cited_document).toBeFalsy();
  });

  it('a confirmed line whose frozen decision carried an order back amends without it', async () => {
    const { onDecide } = renderPanel({
      ...BUY_ONLY,
      covered: true,
      decision: {
        revision_no: 1,
        confirmed_at: '2026-10-01T02:00:00',
        timely_spo_qty: '0',
        reserve: [],
        borrow: [],
        buy_qty: '24',
        order_back: true,
        cited_document: 'SPO-2026/09-0042',
      },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));
    fireEvent.click(screen.getByRole('switch', { name: 'Buy the whole line' }));
    fireEvent.change(screen.getByLabelText('Buy quantity'), { target: { value: '20' } });
    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), { target: { value: '4' } });
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: '4 found at BRW-AM, the rest bought.' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    const saved = onDecide.mock.calls.at(-1)?.[0] ?? {};
    expect(saved).toMatchObject({ verdict: 'amended', buy_qty: '20' });
    expect(saved.order_back).toBeFalsy();
    expect(saved.cited_document).toBeFalsy();
  });

  it('a borrow is saved as the borrow it is, which the server reads as ORDER BACK', async () => {
    const { onDecide } = renderPanel({
      sources: [
        {
          kind: 'reserve',
          qty: '9',
          location: 'BRW-AM',
          warehouse_id: 'wh-BRW-AM',
          reason: 'Free unclaimed stock at BRW-AM covers this much.',
        },
        {
          kind: 'borrow',
          qty: '15',
          location: 'MWH-AM',
          warehouse_id: 'wh-MWH-AM',
          reason: 'Borrowed from the group.',
        },
      ],
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    const saved = onDecide.mock.calls.at(-1)?.[0] ?? {};
    expect(saved.borrow).toEqual([
      expect.objectContaining({ warehouse_id: 'wh-MWH-AM', qty: '15' }),
    ]);
    expect(saved.order_back).toBeFalsy();
  });
});

/**
 * C7, in the UAC's own words: "Editing Reserve BRW-AM from 9 to 5 shows the hint 4 short and
 * Save is disabled; setting BRW to 19 clears the hint, and Save enables once the reason is
 * typed (a composition that differs from the suggestion always needs the reason)."
 *
 * D7 (captain, 3 Sep) supersedes the FIRST half of that: Buy now follows the remainder, so
 * dropping BRW-AM to 5 no longer leaves the line "short" - the 4 it gave up moves into a
 * derived Buy of 4 (`edit()`), which balances the line again.
 *
 * The 8 Sep 2026 ruling supersedes what used to be the SECOND half: 20 from stock beside a
 * derived Buy of 4 no longer refuses outright (`lineBlockers(draft, poolLimits,
 * { mixAllowed: true })` on this panel) - it is a legal hand composition here, gated the same
 * way any other amendment already is, by `amend_reason` (`amendNeedsReason`).
 */
describe('BoardLineDecisionPanel: the balance hint and Save gating (C7, D7)', () => {
  it('moves the gap into a derived Buy instead of reading short, and asks for a reason rather than refusing the mix (8 Sep 2026)', () => {
    const { onDecide } = renderPanel();

    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '5' },
    });

    expect(
      screen.queryByTestId(`line-decision-hint-${KEY}`),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText('Buy quantity')).toHaveValue(4);
    expect(
      screen.queryByText(/either met wholly from stock or wholly bought/),
    ).not.toBeInTheDocument();
    const save = screen.getByRole('button', { name: 'Save decision' });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Customer takes 5 now, the rest on the next shipment.' },
    });
    expect(save).toBeEnabled();
    fireEvent.click(save);

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'amended',
        reserve: expect.arrayContaining([
          expect.objectContaining({ warehouse_id: 'wh-BRW-AM', qty: '5' }),
          expect.objectContaining({ warehouse_id: 'wh-BRW', qty: '15' }),
        ]),
        buy_qty: '4',
        reason: 'Customer takes 5 now, the rest on the next shipment.',
      }),
    );
  });

  it('shows "N over" once a composition exceeds it', () => {
    renderPanel();

    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '30' },
    });

    expect(screen.getByTestId(`line-decision-hint-${KEY}`)).toHaveTextContent(
      'Over by 15',
    );
  });

  it('clears the hint once the composition balances again, and enables Save once a reason is typed', () => {
    renderPanel();

    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '5' },
    });
    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '19' },
    });

    expect(
      screen.queryByTestId(`line-decision-hint-${KEY}`),
    ).not.toBeInTheDocument();
    const save = screen.getByRole('button', { name: 'Save decision' });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Agreed a smaller own-location share with the site.' },
    });
    expect(save).toBeEnabled();
  });

  it('never needs a reason to save the suggestion as it stands', () => {
    renderPanel();

    // The composition on open IS the suggestion, so the press is an approval: there is
    // nothing to justify, and Save is enabled with nothing typed at all.
    expect(
      screen.getByRole('button', { name: 'Save decision' }),
    ).toBeEnabled();
  });
});

/**
 * C10: the flag rides on every verdict, and survives a reload because it is echoed on the
 * contribution's own frozen decision, not only in this session's draft.
 */
describe('BoardLineDecisionPanel: the suspected-system-issue flag (C10)', () => {
  function checkbox() {
    return screen.getByRole('checkbox', {
      name: 'This might be a system problem, flag it for investigation',
    });
  }

  it('carries the flag on an approval (the suggestion, untouched)', () => {
    const { onDecide } = renderPanel();

    fireEvent.click(checkbox());
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'approved',
        suspected_system_issue: true,
      }),
    );
  });

  it('carries the flag on a saved amendment', () => {
    const { onDecide } = renderPanel();

    fireEvent.click(checkbox());
    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '5' },
    });
    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '19' },
    });
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The availability beside this line looks wrong.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'amended',
        suspected_system_issue: true,
      }),
    );
  });

  it('carries the flag on a rejection', async () => {
    const { onDecide } = renderPanel();

    fireEvent.click(checkbox());
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Cancelled by the customer.' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    });

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'rejected',
        suspected_system_issue: true,
      }),
    );
  });

  it('unticking on a covered line is a change: it enables Save, amends (never approves), and clears the flag in the draft AND on screen', () => {
    // Frozen at the engine's own composition, so the untouched FORM matches the suggestion -
    // R1/R2 (captain, 17 Sep) still refuses the plain re-save, and only the flag itself is
    // what this test changes.
    const frozen: BoardLineDecision = {
      revision_no: 1,
      confirmed_at: '2026-08-18T02:00:00',
      timely_spo_qty: '0',
      reserve: [
        { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '9' },
        { warehouse_id: 'wh-BRW', location: 'BRW', qty: '15' },
      ],
      borrow: [],
      buy_qty: '0',
      suspected_system_issue: true,
    };
    const { onDecide } = renderPanel({ covered: true, decision: frozen });

    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));
    expect(checkbox()).toBeChecked();
    const save = screen.getByRole('button', { name: 'Save decision' });
    expect(save).toBeDisabled();

    fireEvent.click(checkbox());

    // The tick alone is a change on a covered line (R1/R2): Save takes it with no reason typed.
    expect(checkbox()).not.toBeChecked();
    expect(save).toBeEnabled();
    fireEvent.click(save);

    // The BOOLEAN, not an absent key: `lineFor` posts `false`, so the pill must read `false`
    // rather than falling through to the frozen `true` and contradicting the body. D11: the
    // composition rides along too, even though only the flag changed - and on a covered line
    // it is always Amended, never Approved (R1/R2): the server refuses `approved` outright.
    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'amended',
        suspected_system_issue: false,
        reserve: expect.arrayContaining([
          expect.objectContaining({ warehouse_id: 'wh-BRW-AM', qty: '9' }),
          expect.objectContaining({ warehouse_id: 'wh-BRW', qty: '15' }),
        ]),
        buy_qty: '0',
      }),
    );
  });

  it('shows the checkbox already ticked when the frozen decision carried the flag (persisted after reload)', () => {
    renderPanel({
      covered: true,
      decision: {
        revision_no: 1,
        confirmed_at: '2026-08-18T02:00:00',
        timely_spo_qty: '0',
        reserve: [{ warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '9' }],
        borrow: [],
        buy_qty: '15',
        suspected_system_issue: true,
      },
    });

    expect(checkbox()).toBeChecked();
  });
});

/**
 * The Buy switch is a DETOUR, not a demolition: a planner trying it has not thrown away the
 * composition they typed, and the panel's own note promises the rows are still theirs when
 * they switch it back.
 */
describe('BoardLineDecisionPanel: Buy on then off restores the composition', () => {
  function buySwitch() {
    return screen.getByRole('switch', { name: 'Buy the whole line' });
  }

  it('puts back the reserve quantities, the borrow and its reason', () => {
    renderPanel({
      borrow_candidates: [
        {
          source: 'other_location',
          warehouse_code: 'MWH-AM',
          warehouse_id: 'wh-MWH-AM',
          donor_project_ref: null,
          donor_project_id: null,
          free_qty: '15',
          donor_impact: {
            free_before: '15',
            free_after_full_borrow: '0',
            committed_qty: '0',
          },
          same_agent: true,
        },
      ],
    });

    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '9' },
    });
    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '15' },
    });

    fireEvent.click(buySwitch());
    expect(
      screen.getAllByText('The whole line is being bought.').length,
    ).toBeGreaterThan(0);

    fireEvent.click(buySwitch());

    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(9);
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(15);
  });

  it('keeps a borrow row and the reason typed against it', () => {
    renderPanel({
      sources: [
        {
          kind: 'reserve',
          qty: '9',
          location: 'BRW-AM',
          warehouse_id: 'wh-BRW-AM',
          reason: 'Free unclaimed stock at BRW-AM covers this much.',
        },
        {
          kind: 'borrow',
          qty: '15',
          location: 'MWH-AM',
          warehouse_id: 'wh-MWH-AM',
          reason: 'Borrowed from the group.',
        },
      ],
    });

    const borrowInput = screen.getByLabelText('Borrow from MWH-AM');
    expect(borrowInput).toHaveValue(15);

    fireEvent.click(buySwitch());
    expect(
      screen.queryByLabelText('Borrow from MWH-AM'),
    ).not.toBeInTheDocument();

    fireEvent.click(buySwitch());

    expect(screen.getByLabelText('Borrow from MWH-AM')).toHaveValue(15);
    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(9);
  });
});

/**
 * A line whose sales order names no fulfilment location cannot be decided here at all: the
 * confirmation leaves it out (`lineFor` returns null), so an editable panel over it would let
 * a planner compose something the press silently drops.
 */
describe('BoardLineDecisionPanel: an unplannable line states why, and offers no verb', () => {
  it('renders the figures and the reason, with no inputs and no buttons', () => {
    renderPanel({
      unplannable: true,
      fulfilment_location: null,
      fulfilment_warehouse_id: null,
    });

    expect(
      screen.getByTestId(`line-decision-blocked-${KEY}`),
    ).toHaveTextContent('states no fulfilment location');
    expect(
      screen.queryByRole('button', { name: 'Save decision' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Reject' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^Reserve at/)).not.toBeInTheDocument();
    expect(screen.getByTestId(`line-decision-${KEY}`)).not.toHaveTextContent(
      'Outstanding',
    );
  });
});

/**
 * C11: a line an active decision already covers opens locked, with Amend the only way in - the
 * database already holds a decision, and an editable form over it invites an edit nobody has
 * asked to make.
 */
describe('BoardLineDecisionPanel: a covered row opens locked with Amend (C11)', () => {
  const frozen: BoardLineDecision = {
    revision_no: 1,
    confirmed_at: '2026-08-18T02:00:00',
    timely_spo_qty: '0',
    reserve: [{ warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '9' }],
    borrow: [],
    buy_qty: '15',
  };

  it('offers only Amend, and disables the inputs, while the row is not being edited', () => {
    renderPanel({ covered: true, decision: frozen });

    expect(screen.getByRole('button', { name: 'Amend' })).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Save decision' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Reject' }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText('Reserve at BRW-AM')).toBeDisabled();
  });

  it('unlocks the same panel once Amend is pressed', () => {
    renderPanel({ covered: true, decision: frozen });

    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));

    expect(screen.getByLabelText('Reserve at BRW-AM')).toBeEnabled();
    expect(
      screen.getByRole('button', { name: 'Save decision' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Amend' }),
    ).not.toBeInTheDocument();
  });

  /**
   * A confirmed row is not silently un-decided by typing the engine's own numbers back into
   * it. It looked like it was, once - the inputs snapped back to the suggestion and the pill
   * read Approved - while `confirmLinesFor` dropped every covered line the planner had not
   * amended, so the press wrote nothing and the reload showed the old revision.
   *
   * R1/R2 (captain, 17 Sep 2026): on a CONFIRMED line every change is an amendment - the
   * server refuses `approved` there outright, whatever the typed composition happens to
   * match. This row was confirmed at 8 from BRW-AM plus 16 from the pool while the engine
   * suggests 9 plus 15; typing the engine's own numbers back is still a change from what was
   * frozen, so Save takes it as an amendment, and the pill still reads Saved either way.
   */
  it('typing the engine’s numbers back on the unlocked row is still an amendment (never approved), and the pill reads Saved (S4)', () => {
    const contribution = contributionOf({
      covered: true,
      decision: {
        revision_no: 4,
        confirmed_at: '2026-08-18T02:00:00',
        timely_spo_qty: '0',
        reserve: [
          { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '8' },
          { warehouse_id: 'wh-BRW', location: 'BRW', qty: '16' },
        ],
        borrow: [],
        buy_qty: '0',
      },
    });
    const onDecide = vi.fn();
    render(
      <BoardLineDecisionPanel
        contribution={contribution}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));
    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '9' },
    });
    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '15' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));

    // D11: the composition rides along with the amendment too.
    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'amended',
        suspected_system_issue: false,
        reserve: expect.arrayContaining([
          expect.objectContaining({ warehouse_id: 'wh-BRW-AM', qty: '9' }),
          expect.objectContaining({ warehouse_id: 'wh-BRW', qty: '15' }),
        ]),
        buy_qty: '0',
      }),
    );
    // The row the planner is looking at afterwards: Saved (S4, R-F), not the Confirmed the
    // covered flag alone would print, because the draft's verdict outranks what is in the
    // database.
    render(
      <BoardDecisionPill
        contribution={contribution}
        decision={onDecide.mock.calls[0][0] as BoardDecision}
      />,
    );
    expect(screen.getByTestId(`decision-pill-${KEY}`)).toHaveTextContent(
      'Saved',
    );
  });

  /**
   * AMEND OPENS ON WHAT WAS DECIDED, not on the engine's numbers (C9): SO404352 line 22 was
   * confirmed at 8 from BRW-AM plus 16 from the pool while the engine suggests 9 plus 15, so
   * the two compositions are not the same answer.
   *
   * R1/R2 (captain, 17 Sep): on a covered line the server accepts nothing but a real
   * amendment - re-saving the FROZEN composition untouched is refused with the R1 sentence
   * (AC-F1's own case), so this test now proves the OTHER half: once the composition actually
   * moves, Save takes it, and it is always posted as Amended, never Approved.
   */
  it('opens on the composition the revision froze; Save is refused until it changes, then amends', async () => {
    const { onDecide } = renderPanel({
      covered: true,
      decision: {
        revision_no: 4,
        confirmed_at: '2026-08-18T02:00:00',
        timely_spo_qty: '0',
        reserve: [
          { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '8' },
          { warehouse_id: 'wh-BRW', location: 'BRW', qty: '16' },
        ],
        borrow: [],
        buy_qty: '0',
      },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));
    // Amend opens on what was DECIDED: the planner edits their own numbers.
    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(8);
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(16);

    const save = screen.getByRole('button', { name: 'Save decision' });
    // Untouched, the draft still IS the frozen decision: refused, not a silent no-op re-save.
    expect(save).toBeDisabled();
    // A `title` on a disabled button never reaches a real browser's hover (AC-F1's own
    // reason): the sentence lives in a Radix Tooltip on a wrapper around the button instead.
    fireEvent.focus(screen.getByTestId(`save-decision-trigger-${KEY}`));
    expect((await screen.findByRole('tooltip')).textContent).toBe(
      'This line is already confirmed. Amend it to change the decision, reject it with a ' +
        'reason, or undo the confirmation.',
    );

    // Still balances against the 24 outstanding (10 + 14), but neither number the revision
    // froze (8 + 16) - a genuine amendment.
    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '10' },
    });
    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '14' },
    });
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'BRW-AM actually had more free stock than recorded.' },
    });

    expect(save).toBeEnabled();
    fireEvent.click(save);

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'amended',
        reserve: expect.arrayContaining([
          expect.objectContaining({ warehouse_id: 'wh-BRW-AM', qty: '10' }),
          expect.objectContaining({ warehouse_id: 'wh-BRW', qty: '14' }),
        ]),
      }),
    );
  });
});

/**
 * R1/R2 (`PLAN-board-draft-on-confirmed-line.md`, AC-B1/AC-B2/AC-B6): the server refuses a
 * plain Save or Reject on a covered line with a 409, and the panel says so before the round
 * trip rather than sending a PUT the server would refuse anyway. TEST-FIRST: neither Save nor
 * Reject carries this gate yet, so both are red against current code.
 */
describe('BoardLineDecisionPanel: a covered line only saves a real amendment (R2)', () => {
  const REFUSAL =
    'This line is already confirmed. Amend it to change the decision, reject it with a ' +
    'reason, or undo the confirmation.';
  const frozen: BoardLineDecision = {
    revision_no: 1,
    confirmed_at: '2026-08-18T02:00:00',
    timely_spo_qty: '0',
    reserve: [
      { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '8' },
      { warehouse_id: 'wh-BRW', location: 'BRW', qty: '16' },
    ],
    borrow: [],
    buy_qty: '0',
  };

  /**
   * A `title` attribute on a DISABLED button never reaches a real browser's hover: the
   * Button primitive carries `disabled:pointer-events-none`, so no pointer event ever lands
   * on it to trigger the native tooltip. The R1 sentence lives in a Radix Tooltip on a
   * wrapper `<span>` around each disabled button instead - reached the same way
   * `BoardCellBreakdownDialog.test.tsx`'s `sourceNoteOf` reaches one: `fireEvent.focus` the
   * trigger (Radix calls the tooltip's own open handler directly on focus, unlike a pointer
   * event jsdom cannot synthesize the way a real mouse would), then read the accessible
   * `role="tooltip"` node's text.
   */
  /**
   * AC-F2 (`board-reject-on-confirmed-line-acceptance-criteria.md`, R3(b), 22 Sep 2026):
   * Save keeps R1/R2's own gate untouched (still the REFUSAL sentence while the draft
   * matches the frozen composition), but Reject is no longer dead weight on a covered line
   * - it follows the uncovered branch's own rule, disabled only while the reason is blank,
   * carrying the same "Say why this differs first" sentence `BoardVerdictActions`' popover
   * Reject already uses. Was AC-F1 of `PLAN-board-draft-on-confirmed-line.md`, which this
   * test used to pin Reject as dead-disabled with the R1 sentence too.
   */
  it('AC-F2: Save carries the R1 sentence disabled; Reject is disabled with "Say why this line is being refused first." while the reason is blank', async () => {
    renderPanel({ covered: true, decision: frozen });
    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));

    const save = screen.getByRole('button', { name: 'Save decision' });
    const reject = screen.getByRole('button', { name: 'Reject' });
    expect(save).toBeDisabled();
    expect(reject).toBeDisabled();

    fireEvent.focus(screen.getByTestId(`save-decision-trigger-${KEY}`));
    expect((await screen.findByRole('tooltip')).textContent).toBe(REFUSAL);

    fireEvent.focus(screen.getByTestId(`reject-decision-trigger-${KEY}`));
    expect((await screen.findByRole('tooltip')).textContent).toBe(
      'Say why this line is being refused first.',
    );
  });

  it('AC-F3: typing a reason enables Reject on a covered line, and clicking it calls onDecide with the rejection', async () => {
    const { onDecide } = renderPanel({ covered: true, decision: frozen });
    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));

    const reject = screen.getByRole('button', { name: 'Reject' });
    expect(reject).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Wrong site' },
    });
    expect(reject).toBeEnabled();

    fireEvent.click(reject);

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'rejected',
        reason: 'Wrong site',
        suspected_system_issue: false,
      }),
    );
  });

  it('AC-F4: the Reject trigger is the same DOM node before and after typing the reason (no unmount on the enabled flip)', () => {
    renderPanel({ covered: true, decision: frozen });
    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));

    const trigger = screen.getByTestId(`reject-decision-trigger-${KEY}`);
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Wrong site' },
    });

    expect(screen.getByTestId(`reject-decision-trigger-${KEY}`)).toBe(trigger);
  });

  it('AC-F1: before Amend is pressed, the covered line shows only Amend - no Save, no Reject', () => {
    renderPanel({ covered: true, decision: frozen });

    expect(screen.getByRole('button', { name: 'Amend' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save decision' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument();
  });

  it('AC-F2: enables Save once the draft differs from the frozen composition and a reason is typed', () => {
    renderPanel({ covered: true, decision: frozen });
    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));

    // Still balances against the 24 outstanding (6 + 18), but neither number the revision
    // froze (8 + 16) nor the engine's own suggestion (9 + 15) - a genuine amendment.
    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '6' },
    });
    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '18' },
    });
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The BRW-AM count looked short on the floor.' },
    });

    expect(screen.getByRole('button', { name: 'Save decision' })).toBeEnabled();
  });

  it('AC-F1b: the suspected-system-issue tick alone is a change too: ticking it enables Save', () => {
    renderPanel({ covered: true, decision: frozen });
    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));

    const save = screen.getByRole('button', { name: 'Save decision' });
    expect(save).toBeDisabled();

    fireEvent.click(
      screen.getByRole('checkbox', {
        name: 'This might be a system problem, flag it for investigation',
      }),
    );

    expect(save).toBeEnabled();
  });
});

/**
 * N1 (fix round, `PLAN-board-reject-on-confirmed-line.md`): `covered` spans TWO kinds of
 * line - an ACTIVE decision (the describe block above, `decision: frozen`), or a LIVE
 * order-inquiry row naming it with none at all (`inquiry_decided`, migrated sheet lines,
 * #875) - `covered: true, decision: null` here. Confirm's `rejected_line_ids` reads an
 * active decision's `line_snapshots`, which the second kind has none of, so Reject keeps
 * the ORIGINAL #989 shape for it: disabled outright, with `CONFIRMED_LINE_TITLE`, same as
 * Save - never the uncovered branch's "reason non-blank" rule the ACTIVE-decision case now
 * follows.
 */
describe('BoardLineDecisionPanel: an INQUIRY-ONLY covered line keeps Reject disabled (N1, fix round)', () => {
  const REFUSAL =
    'This line is already confirmed. Amend it to change the decision, reject it with a ' +
    'reason, or undo the confirmation.';

  it('after Amend, Reject stays disabled with CONFIRMED_LINE_TITLE even once a reason is typed', async () => {
    renderPanel({ covered: true, decision: null });
    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));

    const reject = screen.getByRole('button', { name: 'Reject' });
    expect(reject).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Wrong site' },
    });
    // UNLIKE the active-decision case (AC-F3), typing a reason does not unlock Reject here.
    expect(reject).toBeDisabled();

    fireEvent.focus(screen.getByTestId(`reject-decision-trigger-${KEY}`));
    expect((await screen.findByRole('tooltip')).textContent).toBe(REFUSAL);
  });
});

/**
 * The saved amendment overlays the engine's suggestion on reopen: collapsing an amended row
 * and opening it again must not show the engine's numbers under a pill reading Amended.
 */
describe('BoardLineDecisionPanel: the saved amendment overlays on reopen', () => {
  it('shows what was amended, not the suggestion, when the draft already holds a decision', () => {
    renderPanel(
      {},
      {
        verdict: 'amended',
        reserve: [
          { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '5' },
          { warehouse_id: 'wh-BRW', location: 'BRW', qty: '19' },
        ],
        borrow: [],
        buy_qty: '0',
        timely_spo_qty: '0',
        reason: 'Agreed a smaller own-location share with the site.',
      },
    );

    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(5);
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(19);
    expect(screen.getByLabelText(/^Reason/)).toHaveValue(
      'Agreed a smaller own-location share with the site.',
    );
  });
});

/**
 * S3 - Reserve add-location (AC-3.1 to AC-3.3, R-G): "any location with free stock, the site
 * pool included, can be added to Reserve by hand; the server's on-hand check stays the guard."
 *
 * A fixture of its own, distinct from the header fixture above: `contributionOf`'s line is
 * already fully reserved across both `LOCATIONS` rows, so there is nothing left for "Add
 * location" to offer and no existing test here exercises it. This line reserves only 9 of
 * BRW-AM's own 24 outstanding, leaving exactly BRW's whole 16 free to add.
 */
const ADD_KEY = 'so-c|5|SRTWB9001|2026-07-10';

function contributionForAddLocation(
  overrides: Partial<BoardContribution> = {},
): BoardContribution {
  return {
    key: ADD_KEY,
    sales_order_id: 'so-c',
    so_number: 'SO410000',
    customer_name: 'ZZT Sdn Bhd',
    project_label: null,
    agent_code: 'AG02',
    line_no: 5,
    item_code: 'SRTWB9001',
    qty: '25',
    qty_ordered: '25',
    qty_delivered: '0',
    qty_outstanding: '25',
    project_line_id: 'pl-so-c-5',
    required_date: '2026-07-10',
    is_past: false,
    fulfilment_location: 'BRW-AM',
    fulfilment_warehouse_id: 'wh-BRW-AM',
    unplannable: false,
    priority: null,
    sources: [
      {
        kind: 'reserve',
        qty: '9',
        location: 'BRW-AM',
        warehouse_id: 'wh-BRW-AM',
        reason: 'Free unclaimed stock at BRW-AM covers this much by the delivery date.',
      },
    ],
    qty_proposed_reserve: '9',
    qty_proposed_incoming: '0',
    qty_proposed_buy: '0',
    contested: false,
    rank_score: 0,
    rank_factors: [],
    covered: false,
    decision: null,
    order_inquiry: null,
    item_flags: null,
    borrow_candidates: [],
    ...overrides,
  };
}

/** BRW-AM already carries 9 reserved (`available_qty` states the 9 the ONE other line left
 * free, per B1's own reading); BRW is a site pool with its whole 16 still free to add. */
const ADD_LOCATION_FIXTURE: BoardCellLocation[] = [
  {
    location: 'BRW-AM',
    warehouse_id: 'wh-BRW-AM',
    where: 'own',
    qty: '0',
    available_qty: '9',
    qty_free: '9',
    qty_free_remaining: '9',
  },
  {
    location: 'BRW',
    warehouse_id: 'wh-BRW',
    where: 'site_pool',
    qty: '0',
    available_qty: '16',
    qty_free: '16',
    qty_free_remaining: '16',
  },
];

function renderAddLocationPanel(overrides: Partial<BoardContribution> = {}) {
  const onDecide = vi.fn();
  render(
    <BoardLineDecisionPanel
      contribution={contributionForAddLocation(overrides)}
      decision={null}
      locations={ADD_LOCATION_FIXTURE}
      onDecide={onDecide}
    />,
  );
  return { onDecide };
}

describe('BoardLineDecisionPanel: Reserve add-location (S3, AC-3.1 to AC-3.3)', () => {
  it('offers only the free location not already on the Reserve list, and Save carries the new row at its own warehouse (AC-3.1, AC-3.2)', () => {
    const { onDecide } = renderAddLocationPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Add location' }));

    // BRW-AM is already a Reserve row; only BRW is offered.
    expect(screen.getByTestId('reserve-location-table')).toBeInTheDocument();
    expect(screen.queryByTestId('reserve-location-BRW-AM')).not.toBeInTheDocument();
    expect(screen.getByTestId('reserve-location-BRW')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Add the location' }));

    // Seeded to the whole remainder (16 open beyond the 9 already reserved), and editable.
    const added = screen.getByLabelText('Reserve at BRW');
    expect(added).toHaveValue(16);
    fireEvent.change(added, { target: { value: '16' } });

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'BRW can spare the rest of this line.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'amended',
        reserve: expect.arrayContaining([
          expect.objectContaining({ warehouse_id: 'wh-BRW-AM', qty: '9' }),
          expect.objectContaining({ warehouse_id: 'wh-BRW', qty: '16' }),
        ]),
        reason: 'BRW can spare the rest of this line.',
      }),
    );
  });

  it('echoes the server’s own on-hand guard on the newly added row, and keeps the row rather than dropping it (AC-3.3)', () => {
    renderAddLocationPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Add location' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add the location' }));

    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '30' },
    });

    expect(screen.getByText('Only 16 available here')).toBeInTheDocument();
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(30);
  });

  it('reads the echo off qty_free_remaining, never the signed whole-book available_qty (B2, code review round 3)', () => {
    // MWH-IB style figures: `available_qty` is AutoCount's own signed whole-book number and
    // reads deeply negative even though the location has plenty free for THIS board's own
    // proposals; `qty_free` and `qty_free_remaining` are positive and DIFFER from each other
    // (and from `available_qty`), so a fix that fell back to the wrong one would show either
    // the wrong number or a false "Only N available" on the engine's own suggestion.
    const oversoldWholeBook: BoardCellLocation[] = [
      {
        location: 'BRW-AM',
        warehouse_id: 'wh-BRW-AM',
        where: 'own',
        qty: '0',
        available_qty: '-15514',
        qty_free: '20',
        qty_free_remaining: '9',
      },
      {
        location: 'BRW',
        warehouse_id: 'wh-BRW',
        where: 'site_pool',
        qty: '0',
        available_qty: '-999',
        qty_free: '30',
        qty_free_remaining: '16',
      },
    ];
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={null}
        locations={oversoldWholeBook}
        onDecide={vi.fn()}
      />,
    );

    // The engine's own suggestion (Reserve 9 at BRW-AM, Reserve 15 at BRW) reads its echo off
    // `qty_free_remaining`, not `available_qty` - no red "Only N available here" anywhere on
    // an unedited, engine-suggested composition.
    expect(screen.getByText('9 available')).toBeInTheDocument();
    expect(screen.getByText('16 available')).toBeInTheDocument();
    expect(screen.queryByText(/-15514/)).not.toBeInTheDocument();
    expect(screen.queryByText(/-999/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Only .* available here/)).not.toBeInTheDocument();
  });

  it('says no other location holds free stock once every candidate is already on the Reserve list', () => {
    renderAddLocationPanel({
      sources: [
        {
          kind: 'reserve',
          qty: '9',
          location: 'BRW-AM',
          warehouse_id: 'wh-BRW-AM',
          reason: 'Free unclaimed stock at BRW-AM covers this much.',
        },
        {
          kind: 'reserve',
          qty: '16',
          location: 'BRW',
          warehouse_id: 'wh-BRW',
          reason: 'The shared pool at BRW covers the rest.',
        },
      ],
    });

    expect(
      screen.getByText('No other location holds free stock of this item.'),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Add location' }),
    ).not.toBeInTheDocument();
  });

  it('offers no Add location while the row is locked - Amend has to be pressed first', () => {
    const frozen: BoardLineDecision = {
      revision_no: 1,
      confirmed_at: '2026-08-18T02:00:00',
      timely_spo_qty: '0',
      reserve: [{ warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '9' }],
      borrow: [],
      buy_qty: '16',
    };
    renderAddLocationPanel({ covered: true, decision: frozen });

    expect(
      screen.queryByRole('button', { name: 'Add location' }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));

    expect(screen.getByRole('button', { name: 'Add location' })).toBeInTheDocument();
  });

  it('offers no Add location while the whole line is being bought', () => {
    renderAddLocationPanel();

    fireEvent.click(screen.getByRole('switch', { name: 'Buy the whole line' }));

    expect(
      screen.queryByRole('button', { name: 'Add location' }),
    ).not.toBeInTheDocument();
  });
});

/**
 * The button answers the click itself (S4, AC-4.1), rather than leaving the planner to
 * notice the pill above it and the toast below it.
 *
 * D4 (captain, 3 Sep): it STAYS answered. The check used to last about 600 ms and then go
 * back to "Save decision", which read as the save reverting - "shows saved then jumps back".
 * The state is now the line's, not a moment's: saved until the line is edited again.
 */
describe('BoardLineDecisionPanel: the Save button says it saved (S4, AC-4.1)', () => {
  it('stays Saved and disabled while the line is untouched', async () => {
    const { onDecide } = renderPanel();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    expect(onDecide).toHaveBeenCalledTimes(1);
    const saved = screen.getByRole('button', { name: 'Saved' });
    expect(saved).toBeInTheDocument();
    expect(saved).toBeDisabled();
    expect(
      screen.queryByRole('button', { name: 'Save decision' }),
    ).not.toBeInTheDocument();
  });

  it('goes back to Save decision the moment the planner edits the line again', async () => {
    renderPanel();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });
    expect(screen.getByRole('button', { name: 'Saved' })).toBeInTheDocument();

    // An edit that still BALANCES and carries its reason, so what the button says is about
    // the save being stale and nothing else: 5 + 19 against the line's 24, and a reason.
    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '5' },
    });
    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '19' },
    });
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Agreed a smaller own-location share with the site.' },
    });

    const again = screen.getByRole('button', { name: 'Save decision' });
    expect(again).toBeInTheDocument();
    expect(again).toBeEnabled();
  });

  it('opens Saved and disabled on a line somebody has already saved', () => {
    renderPanel({
      draft: {
        decision: { verdict: 'approved' },
        saved_by: 'Eling',
        saved_at: '2026-09-03T02:00:00',
      },
    });

    const saved = screen.getByRole('button', { name: 'Saved' });
    expect(saved).toBeInTheDocument();
    expect(saved).toBeDisabled();
  });

  it('waits for the save to resolve before showing the check', async () => {
    vi.useFakeTimers();
    try {
      let settle: () => void = () => {};
      const onDecide = vi.fn(
        () => new Promise<boolean>((resolve) => {
          settle = () => resolve(true);
        }),
      );
      render(
        <BoardLineDecisionPanel
          contribution={contributionOf()}
          decision={null}
          locations={LOCATIONS}
          onDecide={onDecide}
          onDirtyChange={vi.fn()}
        />,
      );

      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
      // In flight: the server has not answered, so the button has nothing to confirm yet.
      expect(
        screen.queryByRole('button', { name: 'Saved' }),
      ).not.toBeInTheDocument();

      await act(async () => {
        settle();
      });

      expect(screen.getByRole('button', { name: 'Saved' })).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows no check state when the write did not land (S2, code review round 3)', async () => {
    // `FulfilmentBoardPanel.decide` never lets a save REJECT into this panel - it catches its
    // own write and resolves `false` - so this is the shape a failure actually arrives in.
    const onDecide = vi.fn().mockResolvedValue(false);

    render(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'Saved' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save decision' })).toBeInTheDocument();
  });

  it('does not lock a re-save as Saved when the server rejects it (B1, fix round 5)', async () => {
    // First save lands; the planner edits again and saves a second time, and THAT write is
    // the one the server refuses. The button must answer the second click, not the first.
    const onDecide = vi
      .fn()
      .mockResolvedValueOnce(true)
      .mockResolvedValueOnce(false);
    const onDirtyChange = vi.fn();

    render(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
        onDirtyChange={onDirtyChange}
      />,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });
    expect(screen.getByRole('button', { name: 'Saved' })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '5' },
    });
    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '19' },
    });
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Agreed a smaller own-location share with the site.' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    expect(onDecide).toHaveBeenCalledTimes(2);
    expect(
      screen.getByRole('button', { name: 'Save decision' }),
    ).toBeEnabled();
    expect(
      screen.queryByRole('button', { name: 'Saved' }),
    ).not.toBeInTheDocument();
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
  });

  /**
   * N3 (fix round 5). `savedOnce` used to seed ONLY at mount (`useState(() =>
   * Boolean(contribution.draft))`), so a panel a planner left OPEN across a change that did
   * not come through its own `save()` / `reject()` - a refetch after another planner saved
   * this line, or an Undo fired from the pill (`BoardDecisionPill`) - kept reading whatever
   * was true when it first opened.
   */
  it('re-seeds when the contribution s own draft changes under it, not only at mount', () => {
    const { rerender } = render(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={null}
        locations={LOCATIONS}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Save decision' })).toBeInTheDocument();

    // A refetch delivers a draft this panel never wrote itself (another planner's save).
    rerender(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          draft: {
            decision: { verdict: 'approved' },
            saved_by: 'Mei',
            saved_at: '2026-09-03T02:00:00',
          },
        })}
        decision={null}
        locations={LOCATIONS}
        onDecide={vi.fn()}
      />,
    );
    const saved = screen.getByRole('button', { name: 'Saved' });
    expect(saved).toBeInTheDocument();
    expect(saved).toBeDisabled();

    // An Undo fired elsewhere (the pill) clears the draft under the same open panel.
    rerender(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={null}
        locations={LOCATIONS}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Save decision' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Saved' })).not.toBeInTheDocument();
  });
});

/**
 * BOARD-DECIDE-RESEED (owner, 7 Oct 2026, SO424856 lines 16 / 32): the draft and the Reason
 * box used to seed ONCE at mount (`useState(() => draftFor(contribution, decision))`), so a
 * panel left open while the planner picked a way from the Decide dropdown (`BoardDecideControl`,
 * e.g. "Use BRW") kept the OLD Reserve rows under a pill already reading the new decision, and
 * Save then posted the stale rows - which the server refused with "The components add up to X
 * and the line is open for Y".
 */
describe('BoardLineDecisionPanel: re-seeds from the decision when it changes under an untouched panel', () => {
  const useBrw: BoardDecision = {
    verdict: 'amended',
    reserve: [{ warehouse_id: 'wh-BRW', location: 'BRW', qty: '24' }],
    borrow: [],
    buy_qty: '0',
    timely_spo_qty: '0',
    reason: 'Use BRW',
  };

  it('follows a Decide-dropdown amendment: the Reserve inputs, the reason, and what Save posts', async () => {
    const onDecide = vi.fn().mockResolvedValue(true);
    const { rerender } = render(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );
    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(9);
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(15);

    // The Decide dropdown wrote "Use BRW" for this line; the board's draft map moves first.
    rerender(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={useBrw}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );
    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(0);
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(24);
    expect(screen.getByLabelText(/^Reason/)).toHaveValue('Use BRW');

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });
    expect(onDecide).toHaveBeenCalledTimes(1);
    const posted = onDecide.mock.calls[0][0] as BoardDecision;
    expect(posted.verdict).toBe('amended');
    // A zero row is not posted (`decisionFromAmendDraft` drops it); what matters is that the
    // old 9 at BRW-AM never reaches the server.
    expect(posted.reserve).toEqual([
      expect.objectContaining({ warehouse_id: 'wh-BRW', qty: '24' }),
    ]);
  });

  it('follows a Decide-dropdown approval back to the engine suggestion', () => {
    const { rerender } = render(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={{
          verdict: 'amended',
          reserve: [
            { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '5' },
            { warehouse_id: 'wh-BRW', location: 'BRW', qty: '19' },
          ],
          borrow: [],
          buy_qty: '0',
          timely_spo_qty: '0',
          reason: 'Less from BRW-AM.',
        }}
        locations={LOCATIONS}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(5);
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(19);

    // "As suggested" from the Decide dropdown: an approval (it carries the suggested rows,
    // D11, but `draftFor` reads an approval as the CURRENT suggestion, the same reading
    // `confirmLinesFor` commits).
    rerender(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={{
          verdict: 'approved',
          reserve: [
            { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '9' },
            { warehouse_id: 'wh-BRW', location: 'BRW', qty: '15' },
          ],
          borrow: [],
          buy_qty: '0',
          timely_spo_qty: '0',
        }}
        locations={LOCATIONS}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(9);
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(15);
    expect(screen.getByLabelText(/^Reason/)).toHaveValue('');
  });

  it('does not clobber a panel the planner has already typed into', () => {
    const onDirtyChange = vi.fn();
    const { rerender } = render(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={null}
        locations={LOCATIONS}
        onDecide={vi.fn()}
        onDirtyChange={onDirtyChange}
      />,
    );
    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '5' },
    });
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The site asked for less.' },
    });

    rerender(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={useBrw}
        locations={LOCATIONS}
        onDecide={vi.fn()}
        onDirtyChange={onDirtyChange}
      />,
    );
    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(5);
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(15);
    expect(screen.getByLabelText(/^Reason/)).toHaveValue('The site asked for less.');
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
  });
});

/**
 * Save answers its own click (S4, AC-4.1); Reject used to answer nothing at all - no pill, no
 * toast, no change to the button itself. It mirrors Save now: the button becomes a landed
 * "Rejected" state once the write actually resolves, and only edits since it landed put the
 * plain button back (owner, 22 Sep 2026).
 */
describe('BoardLineDecisionPanel: Reject says it landed', () => {
  it('reads Rejected, and disabled, once the write resolves true', async () => {
    const onDecide = vi.fn().mockResolvedValue(true);
    const { rerender } = render(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The customer cancelled this line.' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    });

    const rejectedButton = await screen.findByRole('button', { name: 'Rejected' });
    expect(rejectedButton).toBeDisabled();
    // Save is untouched by a Reject landing - it never claims "Saved" for a line the planner
    // just refused.
    expect(
      screen.getByRole('button', { name: 'Save decision' }),
    ).toBeInTheDocument();

    // Fix round 1, B1: the REAL board patches `contribution.draft` off the same write
    // (`useLineDraftMutation.save.onSuccess`), ahead of this panel's own `onDecide` promise
    // resolving - so the N3 re-seed effect has to read a REJECTED draft the same honest way,
    // never as "a draft exists, therefore Saved". Simulated here by rerendering with exactly
    // that shape, the same way the pre-existing N3 tests above patch `contribution.draft`.
    rerender(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          draft: {
            decision: {
              verdict: 'rejected',
              reason: 'The customer cancelled this line.',
            },
            saved_by: 'Test Planner',
            saved_at: '2026-09-22T02:00:00',
          },
        })}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );
    expect(screen.getByRole('button', { name: 'Rejected' })).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Save decision' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Saved' }),
    ).not.toBeInTheDocument();
  });

  /**
   * The mirror of the case above: a line whose LAST landed verb is a Save, after an earlier
   * rejection, must not still read Rejected - `save()` clears `rejectedOnce` the same way
   * `reject()` clears `savedOnce`.
   */
  it('a Save after a landed rejection reads Saved, and Reject drops back to plain', async () => {
    const onDecide = vi.fn().mockResolvedValue(true);
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The customer cancelled this line.' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    });
    expect(screen.getByRole('button', { name: 'Rejected' })).toBeInTheDocument();

    // An edit (any edit) is what unlocks Save again - the same D4 rule already governs it.
    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '9' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    expect(screen.getByRole('button', { name: 'Saved' })).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Rejected' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument();
  });

  it('stays Reject, enabled, when the write does not land', async () => {
    const onDecide = vi.fn().mockResolvedValue(false);
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The customer cancelled this line.' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    });

    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole('button', { name: 'Rejected' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeEnabled();
  });

  it('puts Reject back, enabled, once the reason is edited after a landed rejection', async () => {
    const onDecide = vi.fn().mockResolvedValue(true);
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The customer cancelled this line.' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    });
    expect(
      screen.getByRole('button', { name: 'Rejected' }),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The customer cancelled this line, on second thought no.' },
    });

    expect(
      screen.queryByRole('button', { name: 'Rejected' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reject' })).toBeEnabled();
  });
});

/**
 * Fix round 1, S4: neither Save nor Reject guarded against a SECOND click landing before the
 * first write settled - a double-click fired two PUTs, and the board toasted twice for one
 * press. `pending` disables the plain Save and Reject; the COVERED Reject twin was already
 * unconditionally disabled and needs nothing more, but the COVERED Save twin is LIVE once
 * Amend has unlocked the row (fix round 2), and a double-click there fired two `onDecide`
 * calls the same way the plain button's did.
 */
describe('BoardLineDecisionPanel: Reject disables itself while its own write is in flight (fix round 1, S4)', () => {
  it('disables Reject before the write resolves, and a second click posts nothing more', async () => {
    let settle: (value: boolean) => void = () => {};
    const onDecide = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          settle = resolve;
        }),
    );
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The customer cancelled this line.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));

    // In flight: disabled before the write has answered at all.
    expect(screen.getByRole('button', { name: 'Reject' })).toBeDisabled();

    // A second click while it is still disabled reaches nothing - `fireEvent.click` on a
    // disabled DOM button never fires its handler, the same guarantee `disabled` always gives.
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
    expect(onDecide).toHaveBeenCalledTimes(1);

    await act(async () => {
      settle(true);
    });
    expect(screen.getByRole('button', { name: 'Rejected' })).toBeInTheDocument();
    expect(onDecide).toHaveBeenCalledTimes(1);
  });

  it('disables the COVERED line’s own Save, once Amend has unlocked it, while its write is in flight (fix round 2)', async () => {
    let settle: (value: boolean) => void = () => {};
    const onDecide = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          settle = resolve;
        }),
    );
    const frozen: BoardLineDecision = {
      revision_no: 1,
      confirmed_at: '2026-08-18T02:00:00',
      timely_spo_qty: '0',
      reserve: [
        { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '8' },
        { warehouse_id: 'wh-BRW', location: 'BRW', qty: '16' },
      ],
      borrow: [],
      buy_qty: '0',
    };
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({ covered: true, decision: frozen })}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));
    // A genuine amendment (neither the frozen 8/16 nor the engine's own 9/15), the same shape
    // AC-F2 already uses to unlock this button.
    fireEvent.change(screen.getByLabelText('Reserve at BRW-AM'), {
      target: { value: '6' },
    });
    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '18' },
    });
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The BRW-AM count looked short on the floor.' },
    });

    const save = screen.getByRole('button', { name: 'Save decision' });
    fireEvent.click(save);
    expect(save).toBeDisabled();

    fireEvent.click(save);
    expect(onDecide).toHaveBeenCalledTimes(1);

    await act(async () => {
      settle(true);
    });
    expect(onDecide).toHaveBeenCalledTimes(1);
  });

  /**
   * Fix round 3 (browser evidence, AC-8): two synchronous clicks in the SAME tick still fired
   * two `PUT /draft` (200 then 500) - `pending` is React STATE, so it does not apply to the
   * DOM (and therefore to `disabled`) until the re-render, and a second click landing before
   * that commit reads the pre-render value. Both clicks here are inside ONE `act`, so React
   * never commits between them - the same shape a real double-click hits.
   */
  it('guards a same-tick double click with a ref, not only the disabled state', async () => {
    let settle: (value: boolean) => void = () => {};
    const onDecide = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          settle = resolve;
        }),
    );
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={null}
        locations={LOCATIONS}
        onDecide={onDecide}
      />,
    );

    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The customer cancelled this line.' },
    });
    const reject = screen.getByRole('button', { name: 'Reject' });

    await act(async () => {
      fireEvent.click(reject);
      fireEvent.click(reject);
    });

    expect(onDecide).toHaveBeenCalledTimes(1);

    await act(async () => {
      settle(true);
    });
    expect(screen.getByRole('button', { name: 'Rejected' })).toBeInTheDocument();
    expect(onDecide).toHaveBeenCalledTimes(1);
  });
});

/**
 * D5 (captain, 3 Sep, SO419208 line 3, CSK14A-NL). The engine proposed "BRW 62 + Buy 73" -
 * the site pool's share inside the immediate window, remainder bought (R-B/R-C) - and this
 * panel refused to save its own suggestion, because the client's whole-line rule knew
 * nothing about the carve-out the server's confirm has always applied.
 */
describe('BoardLineDecisionPanel: a pool share beside a Buy is saveable (D5)', () => {
  const POOL_LOCATIONS: BoardCellLocation[] = [
    {
      location: 'BRW-AM',
      warehouse_id: 'wh-BRW-AM',
      qty: '0',
      available_qty: '0',
      qty_free: '0',
      qty_free_remaining: '0',
      where: 'own',
    },
    {
      location: 'BRW',
      warehouse_id: 'wh-BRW',
      qty: '0',
      available_qty: '124',
      qty_free: '124',
      qty_free_remaining: '124',
      where: 'site_pool',
      // What the SERVER says this pool may lend a project line, and the five pools' net.
      available_for_project: '62',
      net: '400',
      net_of: 'pools',
    },
  ];

  function renderSplit() {
    const onDecide = vi.fn();
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          line_no: 3,
          item_code: 'CSK14A-NL',
          qty: '135',
          qty_ordered: '135',
          qty_outstanding: '135',
          // The engine's own totals for this line, which is where the draft's Buy comes from
          // on an uncovered line (`draftFromSources`).
          qty_proposed_reserve: '62',
          qty_proposed_buy: '73',
          sources: [
            {
              kind: 'reserve',
              qty: '62',
              location: 'BRW',
              warehouse_id: 'wh-BRW',
              reason: 'BRW may spare 62 of its pile to a project line.',
              rung: 'pool',
            },
            {
              kind: 'buy',
              qty: '73',
              location: null,
              warehouse_id: null,
              reason: 'The remainder has to be bought.',
              rung: 'buy',
            },
          ],
        })}
        decision={null}
        locations={POOL_LOCATIONS}
        onDecide={onDecide}
      />,
    );
    return { onDecide };
  }

  it('never calls the engine s own split a mix, and saves it', () => {
    renderSplit();

    expect(
      screen.queryByText(/either met wholly from stock or wholly bought/),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save decision' })).toBeEnabled();
  });

  /**
   * Beyond the allowance the refusal stands, and it names the figure. There is no Buy INPUT
   * on this panel - Buy is the all-or-nothing switch - so an overdrawn split cannot be typed
   * here; the predicate's own tests (`supplyComposition.test.ts`) cover that shape, and this
   * one covers what the panel can actually reach: the pool row moved past its allowance,
   * which leaves the composition short and says so.
   */
  it('still refuses a pool draw beyond what that pool may spare', () => {
    renderSplit();

    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '70' },
    });

    expect(screen.getByRole('button', { name: 'Save decision' })).toBeDisabled();
  });
});

/**
 * D7 (captain, 3 Sep, SO419208 line 3, CSK14A-NL). "BRW 62 · Buy 73", open 135: editing the
 * BRW reserve down to 60 used to leave `draft.buy_qty` frozen at 73 - short by 2, and blocked
 * - because Buy was never anything but the all-or-nothing switch. Buy is now the remainder,
 * derived inside `edit()` on every change until the planner types it (BUY-FIRST-PANEL pins a
 * typed Buy), so the line stays composable while the reserve is being adjusted.
 */
describe('BoardLineDecisionPanel: Buy follows the remainder of the line (D7)', () => {
  const POOL_LOCATIONS: BoardCellLocation[] = [
    {
      location: 'BRW-AM',
      warehouse_id: 'wh-BRW-AM',
      qty: '0',
      available_qty: '0',
      qty_free: '0',
      qty_free_remaining: '0',
      where: 'own',
    },
    {
      location: 'BRW',
      warehouse_id: 'wh-BRW',
      qty: '0',
      available_qty: '124',
      qty_free: '124',
      qty_free_remaining: '124',
      where: 'site_pool',
      available_for_project: '62',
      net: '400',
      // `poolShareLimitsOf` bounds a split by `net_raw`, never the display-only `net` (N1,
      // fix round 5) - without it the five-pool net reads 0 and an edited, otherwise legal,
      // split is refused for a reason that has nothing to do with what D7 is testing.
      net_raw: '400',
      net_of: 'pools',
    },
  ];

  function renderRemainder(locations: BoardCellLocation[] = POOL_LOCATIONS) {
    const onDecide = vi.fn();
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          line_no: 3,
          item_code: 'CSK14A-NL',
          qty: '135',
          qty_ordered: '135',
          qty_outstanding: '135',
          qty_proposed_reserve: '62',
          qty_proposed_buy: '73',
          sources: [
            {
              kind: 'reserve',
              qty: '62',
              location: 'BRW',
              warehouse_id: 'wh-BRW',
              reason: 'BRW may spare 62 of its pile to a project line.',
              rung: 'pool',
            },
            {
              kind: 'buy',
              qty: '73',
              location: null,
              warehouse_id: null,
              reason: 'The remainder has to be bought.',
              rung: 'buy',
            },
          ],
        })}
        decision={null}
        locations={locations}
        onDecide={onDecide}
      />,
    );
    return { onDecide };
  }

  it('reads "Buy 75" and drops the short blocker once the reserve is edited down to 60', () => {
    renderRemainder();

    expect(screen.getByLabelText('Buy quantity')).toHaveValue(73);

    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '60' },
    });

    expect(screen.getByLabelText('Buy quantity')).toHaveValue(75);
    expect(
      screen.queryByText(/short of the open quantity/),
    ).not.toBeInTheDocument();

    // The composition now differs from the engine's own suggestion (62 became 60), so Save
    // still needs the reason C7 already requires of any amendment - once it has one, the
    // 60/75 split is a legal pool-share carve-out (D5) and nothing else blocks it.
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The site can only spare 60 today.' },
    });
    expect(
      screen.getByRole('button', { name: 'Save decision' }),
    ).toBeEnabled();
  });

  it('carries the derived buy_qty of 75 on Save', async () => {
    const { onDecide } = renderRemainder();

    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '60' },
    });
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'The site can only spare 60 today.' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'amended',
        buy_qty: '75',
        reserve: expect.arrayContaining([
          expect.objectContaining({ warehouse_id: 'wh-BRW', qty: '60' }),
        ]),
      }),
    );
  });

  it('reads "Buy 0" and drops the Buy component once the reserve covers the whole line', () => {
    renderRemainder();

    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '135' },
    });

    expect(screen.getByLabelText('Buy quantity')).toHaveValue(0);
    // `amendSummary` only prints a Buy segment for a positive quantity (`boardAmend.ts`), so
    // the Decision text on the right names no Buy component either.
    expect(
      screen.getByTestId(`line-decision-summary-${KEY}`),
    ).not.toHaveTextContent('Buy');
  });

  /**
   * B-1 (fix round 7). `buying` used to be DERIVED from the numbers
   * (`toMinor(draft.buy_qty) > 0 && fromStockMinor === 0`), so clearing the BRW box (or typing
   * 0) made `buy_qty` follow the whole open quantity and `fromStockMinor` hit zero in the same
   * render - the switch read ON, and the Reserve section and the Add-location button unmounted
   * under the planner mid-edit. It is STATE now, seeded once from the opening draft and changed
   * only by the switch itself.
   */
  it('B-1: clearing or zeroing the Reserve box never flips the switch on, and turning it on then off restores what was typed', () => {
    // A third, free location beside BRW-AM (0 free) and BRW (already reserved), so
    // "Add location" has something to offer and its own unmount is part of what this proves.
    renderRemainder([
      ...POOL_LOCATIONS,
      {
        location: 'MWH-IB',
        warehouse_id: 'wh-MWH-IB',
        qty: '0',
        available_qty: '400',
        qty_free: '400',
        qty_free_remaining: '400',
        where: 'own',
      },
    ]);

    const reserveInput = screen.getByLabelText('Reserve at BRW');
    const switchControl = screen.getByRole('switch', {
      name: 'Buy the whole line',
    });

    fireEvent.change(reserveInput, { target: { value: '' } });
    expect(switchControl).not.toBeChecked();
    expect(screen.getByLabelText('Buy quantity')).toHaveValue(135);
    expect(screen.getByLabelText('Reserve at BRW')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Add location' }),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '0' },
    });
    expect(switchControl).not.toBeChecked();
    expect(screen.getByLabelText('Buy quantity')).toHaveValue(135);
    expect(screen.getByLabelText('Reserve at BRW')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Reserve at BRW'), {
      target: { value: '60' },
    });
    expect(screen.getByLabelText('Buy quantity')).toHaveValue(75);

    fireEvent.click(switchControl);
    expect(
      screen.getAllByText('The whole line is being bought.'),
    ).toHaveLength(2);

    fireEvent.click(switchControl);
    // The `stockBefore` path (D7): the 60 that was typed comes back, never a zero.
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(60);
    expect(screen.getByLabelText('Buy quantity')).toHaveValue(75);
  });
});

/**
 * S-1 (fix round 7). `ReserveAddDialog`'s opening quantity is the line's remainder capped at
 * the location's free stock (`openingQty`); D7 made `totalMinor` include the derived Buy, so
 * `open - totalMinor` read 0 on an already-composed line and the dialog fell back to the
 * location's WHOLE free stock instead. The remainder has to exclude the Buy component itself.
 */
describe('BoardLineDecisionPanel: Add-location seeds the remainder, not the whole free stock (S-1, fix round 7)', () => {
  it('seeds 73 on a bin with 400 free, on a line already carrying BRW 62 + derived Buy 73', () => {
    const onDecide = vi.fn();
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          line_no: 3,
          item_code: 'CSK14A-NL',
          qty: '135',
          qty_ordered: '135',
          qty_outstanding: '135',
          qty_proposed_reserve: '62',
          qty_proposed_buy: '73',
          sources: [
            {
              kind: 'reserve',
              qty: '62',
              location: 'BRW',
              warehouse_id: 'wh-BRW',
              reason: 'BRW may spare 62 of its pile to a project line.',
              rung: 'pool',
            },
            {
              kind: 'buy',
              qty: '73',
              location: null,
              warehouse_id: null,
              reason: 'The remainder has to be bought.',
              rung: 'buy',
            },
          ],
        })}
        decision={null}
        locations={[
          {
            location: 'BRW',
            warehouse_id: 'wh-BRW',
            qty: '0',
            available_qty: '124',
            qty_free: '124',
            qty_free_remaining: '124',
            where: 'site_pool',
            available_for_project: '62',
            net: '400',
            net_raw: '400',
            net_of: 'pools',
          },
          {
            location: 'MWH-IB',
            warehouse_id: 'wh-MWH-IB',
            qty: '0',
            available_qty: '400',
            qty_free: '400',
            qty_free_remaining: '400',
            where: 'own',
          },
        ]}
        onDecide={onDecide}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Add location' }));

    expect(screen.getByLabelText('Quantity')).toHaveValue(73);
  });
});

/**
 * OI-CLOSED-LINE, owner rule 12 Oct 2026 ("the user will come in to plan AFTER they issue the
 * DO"): SO424764 line 144's shape, fake codes. Plan 100, all 100 delivered, 77 free at the
 * line's own bin. CS decides Reserve 98 there + order back 2. The delivered part is never
 * capped by free stock; only the undelivered part may warn, and a warning never blocks Save.
 */
describe('BoardLineDecisionPanel: the delivered part is never capped (OI-CLOSED-LINE)', () => {
  const BB: BoardCellLocation[] = [
    {
      location: 'ZZT-BB',
      warehouse_id: 'wh-ZZT-BB',
      where: 'own',
      qty: '0',
      available_qty: '77',
      qty_free: '77',
      qty_free_remaining: '77',
    },
  ];

  function renderDelivered(delivered: string, locations: BoardCellLocation[] = BB) {
    const onDecide = vi.fn();
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          qty: '100',
          qty_ordered: '100',
          qty_delivered: delivered,
          qty_outstanding: String(100 - Number(delivered)),
          fulfilment_location: 'ZZT-BB',
          fulfilment_warehouse_id: 'wh-ZZT-BB',
          sources: [
            {
              kind: 'reserve',
              qty: '77',
              location: 'ZZT-BB',
              warehouse_id: 'wh-ZZT-BB',
              reason: 'Free stock at ZZT-BB covers this much.',
            },
            { kind: 'buy', qty: '23', reason: 'Nothing else is free.' },
          ],
          qty_proposed_reserve: '77',
          qty_proposed_buy: '23',
        })}
        decision={null}
        locations={locations}
        onDecide={onDecide}
      />,
    );
    return { onDecide };
  }

  it('Reserve 98 over 77 free on a fully delivered line: no shortfall said, the 2 left is a Buy with no Order back, saves', () => {
    const { onDecide } = renderDelivered('100');

    fireEvent.change(screen.getByLabelText('Reserve at ZZT-BB'), { target: { value: '98' } });

    expect(screen.queryByText(/Only .* available here/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('Buy quantity')).toHaveValue(2);
    expect(screen.queryByRole('switch', { name: 'Order back' })).toBeNull();
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: '98 used from BB, 2 bought.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'amended',
        reserve: [expect.objectContaining({ warehouse_id: 'wh-ZZT-BB', qty: '98' })],
        buy_qty: '2',
      }),
    );
    expect(onDecide.mock.calls.at(-1)?.[0].order_back).toBeFalsy();
  });

  it('short on the undelivered part: warns with what is free, and Save still saves', () => {
    const { onDecide } = renderDelivered('10');

    fireEvent.change(screen.getByLabelText('Reserve at ZZT-BB'), { target: { value: '98' } });

    // 10 delivered, 2 bought: 8 of the 98 are delivered, 90 need stock, 77 free.
    const said = screen.getByText('Only 77 available here');
    expect(said.className).not.toContain('text-destructive');
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Take what BB has; purchasing is told.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));

    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        reserve: [expect.objectContaining({ qty: '98' })],
        buy_qty: '2',
      }),
    );
  });

  it('a delivered line can still add a location that shows 0 free (the goods already went out)', () => {
    renderDelivered('100', [
      { ...BB[0], qty_free: '0', qty_free_remaining: '0', available_qty: '0' },
      {
        location: 'ZZT-POOL',
        warehouse_id: 'wh-ZZT-POOL',
        where: 'site_pool',
        qty: '0',
        available_qty: '0',
        qty_free: '0',
        qty_free_remaining: '0',
      },
    ]);

    expect(
      screen.queryByText('No other location holds free stock of this item.'),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add location' }));
    expect(screen.getByTestId('reserve-location-ZZT-POOL')).toBeInTheDocument();
  });
});

/**
 * PR 1622 (owner Q1, review B1): the planner's own words on a line kept as Buy travel as the
 * Buy's reason to purchasing's new order inquiry row - never a sentence the Reason box was
 * merely SEEDED with (which can be the engine's own borrow sentence).
 */
describe('BoardLineDecisionPanel: the note purchasing reads (PR 1622)', () => {
  const buying: Partial<BoardContribution> = {
    sources: [{ kind: 'buy', qty: '24', reason: 'remaining uncovered need' }] as BoardContribution['sources'],
    qty_proposed_reserve: '0',
    qty_proposed_buy: '24',
  };
  const lastSaved = (onDecide: ReturnType<typeof vi.fn>) => onDecide.mock.calls.at(-1)?.[0] ?? {};

  it('carries what the planner typed on a line kept as Buy, as cs_note only', () => {
    const { onDecide } = renderPanel(buying);
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Customer accepted the chrome finish' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    expect(lastSaved(onDecide)).toMatchObject({
      verdict: 'approved',
      cs_note: 'Customer accepted the chrome finish',
    });
    expect(lastSaved(onDecide).buy_reason).toBeFalsy();
  });

  it('never carries a sentence the box was only seeded with, on approve', () => {
    const { onDecide } = renderPanel(buying, {
      verdict: 'approved',
      reason: 'ENGINE: free stock at DC1 outside the group',
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    expect(lastSaved(onDecide).cs_note).toBeFalsy();
  });

  it('never carries a seeded sentence on an AMEND either (review B1, amend path)', () => {
    // The reserve-only suggestion, amended into a whole-line Buy, with the box still holding
    // the sentence it was seeded with.
    const { onDecide } = renderPanel({}, {
      verdict: 'approved',
      reason: 'ENGINE: free stock at DC1 outside the group',
    });
    fireEvent.click(screen.getByLabelText('Buy the whole line'));
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    const saved = lastSaved(onDecide);
    expect(saved.verdict).toBe('amended');
    expect(saved.buy_qty).toBe('24');
    expect(saved.cs_note).toBeFalsy();
  });

  it('keeps the line\'s own earlier note on an untouched re-save', () => {
    const { onDecide } = renderPanel(buying, {
      verdict: 'approved',
      reason: 'Customer accepted the chrome finish',
      cs_note: 'Customer accepted the chrome finish',
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    expect(lastSaved(onDecide).cs_note).toBe('Customer accepted the chrome finish');
  });

  it('carries nothing when the line buys nothing', () => {
    const { onDecide } = renderPanel();
    fireEvent.change(screen.getByLabelText(/^Reason/), { target: { value: 'Took it from stock' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    expect(lastSaved(onDecide).cs_note).toBeFalsy();
  });
});

/**
 * BUY-FIRST-PANEL (owner, 14 Oct 2026): "I want to key in the BUY quantity first; now I must
 * calculate reserve and borrow and the rest is buy". Buy is a number input. Until it is typed
 * it follows the remainder exactly as D7 had it; once typed it is PINNED, and the panel says
 * what is left ("Unassigned N" / "Over by N") instead of changing the typed number. Save stays
 * locked by the existing sum blocker until the line adds up.
 */
describe('BoardLineDecisionPanel: Buy can be typed first (BUY-FIRST-PANEL)', () => {
  const buyInput = () => screen.getByLabelText('Buy quantity');
  const hint = () => screen.getByTestId(`line-decision-hint-${KEY}`);
  const type = (label: string, value: string) =>
    fireEvent.change(screen.getByLabelText(label), { target: { value } });

  /** SO419208 line 3's shape (D7): BRW 62 + Buy 73 of 135, with a free bin to add. */
  function renderPoolLine() {
    const onDecide = vi.fn();
    render(
      <BoardLineDecisionPanel
        contribution={contributionOf({
          line_no: 3,
          item_code: 'CSK14A-NL',
          qty: '135',
          qty_ordered: '135',
          qty_outstanding: '135',
          qty_proposed_reserve: '62',
          qty_proposed_buy: '73',
          sources: [
            {
              kind: 'reserve',
              qty: '62',
              location: 'BRW',
              warehouse_id: 'wh-BRW',
              reason: 'BRW may spare 62 of its pile to a project line.',
              rung: 'pool',
            },
            {
              kind: 'buy',
              qty: '73',
              location: null,
              warehouse_id: null,
              reason: 'The remainder has to be bought.',
              rung: 'buy',
            },
          ],
        })}
        decision={null}
        locations={[
          {
            location: 'BRW',
            warehouse_id: 'wh-BRW',
            qty: '0',
            available_qty: '124',
            qty_free: '124',
            qty_free_remaining: '124',
            where: 'site_pool',
            available_for_project: '62',
            net: '400',
            net_raw: '400',
            net_of: 'pools',
          },
          {
            location: 'MWH-IB',
            warehouse_id: 'wh-MWH-IB',
            qty: '0',
            available_qty: '400',
            qty_free: '400',
            qty_free_remaining: '400',
            where: 'own',
          },
        ]}
        onDecide={onDecide}
      />,
    );
    return { onDecide };
  }

  it('Buy is an input that follows the remainder until typed', () => {
    renderPanel();

    expect(buyInput()).toHaveValue(0);
    type('Reserve at BRW', '10');
    expect(buyInput()).toHaveValue(5);
  });

  it('a typed Buy is pinned: editing Reserve afterwards never changes it, and the line saves once it adds up', async () => {
    const { onDecide } = renderPanel();

    type('Buy quantity', '10');
    expect(buyInput()).toHaveValue(10);
    expect(hint()).toHaveTextContent('Over by 10');
    const save = screen.getByRole('button', { name: 'Save decision' });
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Customer wants 10 of the new batch.' },
    });
    expect(save).toBeDisabled();

    // Unpinned, Buy would follow to 12 here; pinned it stays 10 and the gap is stated.
    type('Reserve at BRW', '3');
    expect(buyInput()).toHaveValue(10);
    expect(hint()).toHaveTextContent('Unassigned 2');
    expect(save).toBeDisabled();

    type('Reserve at BRW', '5');
    expect(buyInput()).toHaveValue(10);
    expect(
      screen.queryByTestId(`line-decision-hint-${KEY}`),
    ).not.toBeInTheDocument();
    expect(save).toBeEnabled();

    await act(async () => {
      fireEvent.click(save);
    });
    expect(onDecide).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'amended',
        buy_qty: '10',
        reserve: expect.arrayContaining([
          expect.objectContaining({ warehouse_id: 'wh-BRW-AM', qty: '9' }),
          expect.objectContaining({ warehouse_id: 'wh-BRW', qty: '5' }),
        ]),
      }),
    );
  });

  it('says what is Unassigned while the parts fall short, and keeps Save locked', () => {
    renderPanel();

    type('Buy quantity', '10');
    type('Reserve at BRW-AM', '0');
    type('Reserve at BRW', '0');

    expect(buyInput()).toHaveValue(10);
    expect(hint()).toHaveTextContent('Unassigned 14');
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Working it out.' },
    });
    expect(screen.getByRole('button', { name: 'Save decision' })).toBeDisabled();
  });

  it('Rest to Buy puts everything unassigned on the Buy', () => {
    renderPanel();

    type('Buy quantity', '10');
    type('Reserve at BRW-AM', '0');
    type('Reserve at BRW', '0');
    fireEvent.click(screen.getByRole('button', { name: 'Rest to Buy' }));

    expect(buyInput()).toHaveValue(24);
    expect(
      screen.queryByTestId(`line-decision-hint-${KEY}`),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Rest to Buy' }),
    ).not.toBeInTheDocument();
  });

  it('Rest to Reserve adds everything unassigned to the FIRST Reserve row, and leaves Buy alone', () => {
    renderPanel();

    type('Buy quantity', '10');
    type('Reserve at BRW-AM', '3');
    type('Reserve at BRW', '0');
    expect(hint()).toHaveTextContent('Unassigned 11');
    fireEvent.click(screen.getByRole('button', { name: 'Rest to Reserve' }));

    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(14);
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(0);
    expect(buyInput()).toHaveValue(10);
    expect(
      screen.queryByTestId(`line-decision-hint-${KEY}`),
    ).not.toBeInTheDocument();
  });

  it('offers the helpers only while something is unassigned, and never a Rest to Borrow', () => {
    renderPanel();

    expect(screen.queryByRole('button', { name: 'Rest to Buy' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Rest to Reserve' })).toBeNull();

    type('Buy quantity', '10');
    expect(hint()).toHaveTextContent('Over by 10');
    expect(screen.queryByRole('button', { name: 'Rest to Buy' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Rest to Reserve' })).toBeNull();

    type('Reserve at BRW', '0');
    expect(screen.getByRole('button', { name: 'Rest to Buy' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Rest to Reserve' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Rest to Borrow' })).toBeNull();
  });

  it('Buy the whole line: ON buys it all, OFF puts back exactly what was there, the typed Buy and its pin included', () => {
    renderPanel();

    type('Buy quantity', '10');
    type('Reserve at BRW', '5');
    const wholeLine = screen.getByRole('switch', { name: 'Buy the whole line' });

    fireEvent.click(wholeLine);
    expect(screen.getByText('Buy the whole 24')).toBeInTheDocument();
    expect(screen.queryByLabelText('Buy quantity')).toBeNull();

    fireEvent.click(wholeLine);
    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(9);
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(5);
    expect(buyInput()).toHaveValue(10);

    // Still pinned after the detour (crew ruling (a), 14 Oct): a derived Buy would read 12.
    type('Reserve at BRW', '3');
    expect(buyInput()).toHaveValue(10);
    expect(hint()).toHaveTextContent('Unassigned 2');
  });

  it('Buy the whole line ON then OFF never replaces a typed Buy on a line that does not add up (review S2, crew ruling (a))', () => {
    renderPanel();

    type('Buy quantity', '10');
    type('Reserve at BRW', '0');
    expect(hint()).toHaveTextContent('Unassigned 5');
    const wholeLine = screen.getByRole('switch', { name: 'Buy the whole line' });

    fireEvent.click(wholeLine);
    fireEvent.click(wholeLine);

    expect(buyInput()).toHaveValue(10);
    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(9);
    expect(screen.getByLabelText('Reserve at BRW')).toHaveValue(0);
    expect(hint()).toHaveTextContent('Unassigned 5');
  });

  it('Buy the whole line ON then OFF on a Buy nobody typed: Buy still follows the remainder', () => {
    renderPanel();

    type('Reserve at BRW', '10');
    expect(buyInput()).toHaveValue(5);
    const wholeLine = screen.getByRole('switch', { name: 'Buy the whole line' });

    fireEvent.click(wholeLine);
    fireEvent.click(wholeLine);

    expect(buyInput()).toHaveValue(5);
    type('Reserve at BRW', '12');
    expect(buyInput()).toHaveValue(3);
  });

  it('Rest to Buy settles a below-zero Buy to exactly what is unassigned (review nit)', () => {
    renderPanel();

    type('Buy quantity', '-5');
    type('Reserve at BRW', '0');
    expect(hint()).toHaveTextContent('Unassigned 20');
    fireEvent.click(screen.getByRole('button', { name: 'Rest to Buy' }));

    expect(buyInput()).toHaveValue(15);
    expect(
      screen.queryByTestId(`line-decision-hint-${KEY}`),
    ).not.toBeInTheDocument();
  });

  it('Rest to Reserve settles a below-zero first Reserve row to exactly what is unassigned (review nit)', () => {
    renderPanel();

    type('Buy quantity', '10');
    type('Reserve at BRW-AM', '-3');
    type('Reserve at BRW', '0');
    expect(hint()).toHaveTextContent('Unassigned 17');
    fireEvent.click(screen.getByRole('button', { name: 'Rest to Reserve' }));

    expect(screen.getByLabelText('Reserve at BRW-AM')).toHaveValue(14);
    expect(
      screen.queryByTestId(`line-decision-hint-${KEY}`),
    ).not.toBeInTheDocument();
  });

  it('Rest to Buy pins the Buy on a line that opened short (review: surviving mutant)', () => {
    renderPanel({}, {
      verdict: 'amended',
      reserve: [
        { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '9' },
        { warehouse_id: 'wh-BRW', location: 'BRW', qty: '5' },
      ],
      borrow: [],
      timely_spo_qty: '0',
      buy_qty: '0',
      reason: 'Saved short.',
    });

    expect(hint()).toHaveTextContent('Unassigned 10');
    fireEvent.click(screen.getByRole('button', { name: 'Rest to Buy' }));
    expect(buyInput()).toHaveValue(10);

    // Pinned by the click: a derived Buy would follow to 12 here.
    type('Reserve at BRW', '3');
    expect(buyInput()).toHaveValue(10);
    expect(hint()).toHaveTextContent('Unassigned 2');
  });

  it('Add location seeds what is left after a pinned Buy too', () => {
    renderPoolLine();

    type('Buy quantity', '50');
    expect(hint()).toHaveTextContent('Unassigned 23');
    fireEvent.click(screen.getByRole('button', { name: 'Add location' }));

    expect(screen.getByLabelText('Quantity')).toHaveValue(23);
  });

  it('a decision re-seeded under the panel drops the pin: Buy follows the remainder again', async () => {
    const onDecide = vi.fn().mockResolvedValue(true);
    const props = { locations: LOCATIONS, onDecide, onDirtyChange: vi.fn() };
    const { rerender } = render(
      <BoardLineDecisionPanel contribution={contributionOf()} decision={null} {...props} />,
    );

    type('Buy quantity', '10');
    type('Reserve at BRW', '5');
    fireEvent.change(screen.getByLabelText(/^Reason/), {
      target: { value: 'Customer wants 10 of the new batch.' },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save decision' }));
    });

    // A Decide-dropdown pick lands on the untouched panel (BOARD-DECIDE-RESEED).
    rerender(
      <BoardLineDecisionPanel
        contribution={contributionOf()}
        decision={{
          verdict: 'amended',
          reserve: [
            { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '9' },
            { warehouse_id: 'wh-BRW', location: 'BRW', qty: '15' },
          ],
          borrow: [],
          timely_spo_qty: '0',
          buy_qty: '0',
          reason: 'Picked from Decide.',
        }}
        {...props}
      />,
    );
    expect(buyInput()).toHaveValue(0);

    type('Reserve at BRW', '10');
    expect(buyInput()).toHaveValue(5);
  });

  it('the Buy input is disabled on a locked (covered) row until Amend', () => {
    renderPanel({
      covered: true,
      decision: {
        revision_no: 1,
        confirmed_at: '2026-08-18T02:00:00',
        timely_spo_qty: '0',
        reserve: [{ warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '9' }],
        borrow: [],
        buy_qty: '15',
      },
    });

    expect(buyInput()).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Amend' }));
    expect(buyInput()).toBeEnabled();
  });
});
