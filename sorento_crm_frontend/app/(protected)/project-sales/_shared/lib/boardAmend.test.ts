/**
 * Amending one board row into a full composition (PLAN 13, the captain: "I should be able to
 * amend the decision and quantity, like I can decide to reserve, or buy, or borrow").
 *
 * The board's amendment used to be ONE number - the Reserve - and everything the planner took
 * off it was pushed into Buy by the caller. That is not a decision, it is half of one: a
 * planner who can see a donor holding the stock cannot say "borrow it", and a planner whose
 * line reserved nothing at its own location had no row to type into at all.
 *
 * So the editor composes the SAME four kinds the per-order sheet composes, on the same
 * `DraftLine` shape and against the same `lineBalance` / `lineBlockers`, and these are the two
 * conversions either side of it.
 */
import { describe, expect, it, afterEach } from 'vitest';
import { borrowKinds } from './supplyVocabulary';
import {
  amendDraftFrom,
  amendSummary,
  borrowCandidatesOf,
  canDecide,
  canQuickSave,
  confirmLineFrom,
  decideComposition,
  decisionFromAmendDraft,
  suggestedDecisionFor,
  suggestionDraftFrom,
} from './boardAmend';
import { buildBoard, type BoardDemandLine } from './__testsupport__/boardFixture';
import { toMinor } from './supplyComposition';
import type { BoardContribution, BoardDraft } from '../types/fulfilmentPlanning.types';

const TODAY = '2026-08-18';

function line(overrides: Partial<BoardDemandLine> = {}): BoardDemandLine {
  return {
    sales_order_id: 'so-1',
    so_number: 'SO000001',
    line_no: 1,
    item_code: 'WESERP10B',
    qty: '100',
    required_date: '2026-09-04',
    fulfilment_location: 'BRW-BB',
    ...overrides,
  };
}

function contributionOf(
  freeStock: Record<string, string> = {},
  overrides: Partial<BoardDemandLine> = {},
): BoardContribution {
  return buildBoard([line(overrides)], { today: TODAY, freeStock }).cells[0]
    .contributions[0];
}

describe('amendDraftFrom: the proposal, as something a person can edit', () => {
  it('opens on the engine’s own numbers rather than on an empty form', () => {
    const draft = amendDraftFrom(contributionOf({ 'WESERP10B|BRW-BB': '40' }));

    expect(draft.open_qty).toBe('100');
    expect(draft.timely_spo_qty).toBe('0');
    expect(draft.reserve).toEqual([
      {
        key: 'reserve-BRW-BB',
        location: 'BRW-BB',
        warehouse_id: 'wh-BRW-BB',
        qty: '40',
        reason: 'Free unclaimed stock at BRW-BB covers this much by the delivery date.',
      },
    ]);
    expect(draft.borrow).toEqual([]);
    expect(draft.buy_qty).toBe('60');
  });

  it('opens with the line’s own location at zero when the proposal reserved nothing', () => {
    // Ladder v3 (`PLAN-scm-cs-planning-uat.md` section 1b rung 2): the own location is a
    // location of the line's ownership group again, so it is always somewhere the planner
    // may reserve from - and on a wholly-bought line it is the only row there would be.
    const draft = amendDraftFrom(contributionOf({}));

    expect(draft.reserve).toEqual([
      {
        key: 'reserve-BRW-BB',
        location: 'BRW-BB',
        warehouse_id: 'wh-BRW-BB',
        qty: '0',
        reason: '',
      },
    ]);
    expect(draft.buy_qty).toBe('100');
  });

  it('keeps every warehouse the proposal drew on, each with its own id', () => {
    // Own location plus the dealer pool: the pill reads one code, the payload needs two ids.
    const base = contributionOf({});
    const draft = amendDraftFrom({
      ...base,
      qty_proposed_reserve: '70',
      qty_proposed_buy: '30',
      sources: [
        {
          kind: 'reserve',
          qty: '40',
          location: 'BRW-BB',
          warehouse_id: 'wh-own',
          reason: 'Free stock at BRW-BB covers this much.',
        },
        {
          kind: 'reserve',
          qty: '30',
          location: 'BRW',
          warehouse_id: 'wh-pool',
          reason: 'The dealer pool covers the rest.',
        },
        { kind: 'buy', qty: '30', location: null, reason: 'The residual is bought.' },
      ],
    });

    expect(draft.reserve.map((row) => [row.warehouse_id, row.qty])).toEqual([
      ['wh-own', '40'],
      ['wh-pool', '30'],
    ]);
  });

  it('states no reserve row at all for a line whose sales order names no warehouse', () => {
    const draft = amendDraftFrom(contributionOf({}, { fulfilment_location: null }));
    expect(draft.reserve).toEqual([]);
  });

  it('carries the server’s incoming cover through unedited', () => {
    const base = contributionOf({});
    const draft = amendDraftFrom({
      ...base,
      qty_proposed_incoming: '15',
      qty_proposed_reserve: '0',
      qty_proposed_buy: '85',
    });
    expect(draft.timely_spo_qty).toBe('15');
  });

  it('seeds the discontinued flag from the item facts the board stated', () => {
    const base = contributionOf({});
    const draft = amendDraftFrom({
      ...base,
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
    });
    expect(draft.is_discontinued).toBe(true);
    expect(draft.buy_reason).toBe('');
  });

  it('claims nothing about the lifecycle when the board stated no item facts', () => {
    const base = contributionOf({});
    expect(amendDraftFrom({ ...base, item_flags: null }).is_discontinued).toBe(false);
  });
});

describe('amendDraftFrom on a covered line', () => {
  const frozen = {
    revision_no: 2,
    timely_spo_qty: '0',
    reserve: [],
    borrow: [],
    buy_qty: '100',
    buy_reason: 'Last batch for the site, agreed with purchasing.',
  };

  it('seeds the discontinued flag and the buy reason the revision froze', () => {
    const base = contributionOf({}, { decision: frozen });
    const draft = amendDraftFrom({
      ...base,
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
    });
    expect(draft.is_discontinued).toBe(true);
    expect(draft.buy_reason).toBe('Last batch for the site, agreed with purchasing.');
  });

  it('opens with an empty buy reason when the revision carries none', () => {
    const base = contributionOf({}, { decision: { ...frozen, buy_reason: undefined } });
    expect(amendDraftFrom(base).buy_reason).toBe('');
  });

  it('keeps a group-borrow donor’s fields through Amend and back onto the posted payload (review finding B2)', () => {
    // Dropping these on the round trip re-posts a covered group-borrow line as a plain
    // free-stock donor, which the own-location check (rule 7) then refuses.
    const groupBorrowFrozen = {
      revision_no: 3,
      timely_spo_qty: '0',
      reserve: [],
      borrow: [
        {
          source: 'other_location' as const,
          warehouse_id: 'wh-MWH-BB',
          location: 'MWH-BB',
          donor_project_id: null,
          qty: '90',
          reason: 'Group borrow, auto-proposed.',
          rung: 'group_borrow',
          donor_so_number: 'SO371334',
          donor_line_no: 2,
          donor_agent_code: 'JEREMY',
          same_agent: true,
          donor_core_line_id: 'core-line-1',
          donor_required_date: '2026-09-10',
          order_back_qty: '90',
        },
      ],
      buy_qty: '0',
    };
    const base = contributionOf({}, { decision: groupBorrowFrozen });
    const draft = amendDraftFrom(base);

    expect(draft.borrow).toEqual([
      expect.objectContaining({
        warehouse_id: 'wh-MWH-BB',
        warehouse_code: 'MWH-BB',
        qty: '90',
        donor_core_line_id: 'core-line-1',
        donor_so_number: 'SO371334',
        donor_line_no: 2,
        donor_agent_code: 'JEREMY',
        same_agent: true,
        donor_required_date: '2026-09-10',
      }),
    ]);

    // Re-approved as-is (or re-posted untouched by Amend), the composition still names
    // the SAME donor line - never a re-derived free-stock borrow at the same location.
    const posted = decisionFromAmendDraft(draft, '');
    expect(posted.borrow?.[0]).toEqual(
      expect.objectContaining({
        warehouse_id: 'wh-MWH-BB',
        donor_core_line_id: 'core-line-1',
        donor_so_number: 'SO371334',
        donor_line_no: 2,
        donor_agent_code: 'JEREMY',
        same_agent: true,
        donor_required_date: '2026-09-10',
      }),
    );
  });
});

/**
 * THE ENGINE'S SUGGESTION, on a line an active decision already covers (C9 / C11).
 *
 * The fixture is the plan's own canonical example: SO404352 line 22, SRTWB7518, confirmed at
 * Reserve 8 from BRW-AM plus 16 from the BRW pool while the engine suggests 9 plus 15. Amend
 * opens on the 8 and the 16 - the planner edits their OWN composition - and an approval has to
 * put the 9 and the 15 back, which is the whole reason this seeder exists beside
 * `amendDraftFrom`.
 */
describe('suggestionDraftFrom on a covered line', () => {
  const frozen = {
    revision_no: 4,
    confirmed_at: '2026-08-18T02:00:00',
    timely_spo_qty: '0',
    reserve: [
      { warehouse_id: 'wh-BRW-AM', location: 'BRW-AM', qty: '8' },
      { warehouse_id: 'wh-BRW', location: 'BRW', qty: '16' },
    ],
    borrow: [],
    buy_qty: '0',
  };
  const suggestion = [
    {
      kind: 'reserve' as const,
      qty: '9',
      location: 'BRW-AM',
      warehouse_id: 'wh-BRW-AM',
      reason: 'Free unclaimed stock at BRW-AM covers this much by the delivery date.',
    },
    {
      kind: 'reserve' as const,
      qty: '15',
      location: 'BRW',
      warehouse_id: 'wh-BRW',
      reason: 'The shared pool at BRW covers this much within its cap.',
    },
  ];

  /** The covered contribution as the board sends one, with the suggestion beside it. */
  function coveredContribution(overrides: Partial<BoardContribution> = {}): BoardContribution {
    const base = contributionOf(
      {},
      { qty: '24', fulfilment_location: 'BRW-AM', decision: frozen },
    );
    return { ...base, sources: suggestion, ...overrides };
  }

  it('opens on the engine’s numbers, not on the composition the revision froze', () => {
    const draft = suggestionDraftFrom(coveredContribution());

    expect(draft.reserve.map((row) => [row.warehouse_id, row.qty])).toEqual([
      ['wh-BRW-AM', '9'],
      ['wh-BRW', '15'],
    ]);
    expect(draft.buy_qty).toBe('0');
  });

  it('takes the proposal FROZEN beside the decision when the revision recorded one', () => {
    // `sources` states the decision on a covered line (the server's `_apply_frozen`); the
    // suggestion of the day it was confirmed is in `proposed`, and that is the one the
    // Suggestion card shows, so the reset has to agree with it.
    const draft = suggestionDraftFrom(
      coveredContribution({
        sources: [
          {
            kind: 'reserve',
            qty: '8',
            location: 'BRW-AM',
            warehouse_id: 'wh-BRW-AM',
            reason: 'Reserved at BRW-AM in revision 4.',
          },
          {
            kind: 'reserve',
            qty: '16',
            location: 'BRW',
            warehouse_id: 'wh-BRW',
            reason: 'Reserved at BRW in revision 4.',
          },
        ],
        proposed: { components: suggestion },
      }),
    );

    expect(draft.reserve.map((row) => [row.warehouse_id, row.qty])).toEqual([
      ['wh-BRW-AM', '9'],
      ['wh-BRW', '15'],
    ]);
  });

  it('leaves Amend itself on the frozen composition', () => {
    // Two different questions: Amend edits what was decided, an approval asks for the
    // engine's. Seeding both from the same place is the defect, not the fix.
    const draft = amendDraftFrom(coveredContribution());

    expect(draft.reserve.map((row) => [row.warehouse_id, row.qty])).toEqual([
      ['wh-BRW-AM', '8'],
      ['wh-BRW', '16'],
    ]);
  });

  it('shows the frozen composition when the revision recorded no proposal at all', () => {
    // Written before the proposal was frozen: `sources` is the decision and there is nothing
    // else the board holds for the line, so the reset states that rather than an empty form.
    const base = contributionOf(
      {},
      { qty: '24', fulfilment_location: 'BRW-AM', decision: frozen },
    );
    const draft = suggestionDraftFrom(base);

    expect(draft.reserve.map((row) => [row.warehouse_id, row.qty])).toEqual([
      ['wh-BRW-AM', '8'],
      ['wh-BRW', '16'],
    ]);
  });

  it('is the same draft as Amend on an UNCOVERED line', () => {
    const uncovered = contributionOf({ 'WESERP10B|BRW-BB': '40' });
    expect(suggestionDraftFrom(uncovered)).toEqual(amendDraftFrom(uncovered));
  });
});

describe('borrowCandidatesOf: only a donor the confirmation can name', () => {
  it('fills the sheet’s candidate shape from the board’s', () => {
    const base = contributionOf({});
    const candidates = borrowCandidatesOf({
      ...base,
      borrow_candidates: [
        {
          source: 'other_location',
          warehouse_code: 'BRW-IB',
          warehouse_id: 'wh-ib',
          free_qty: '25',
          qty_on_hand: '30',
          so_qty: '10',
          spo_qty: '5',
          available_qty: '25',
          qty_free: '25',
          qty_committed: '0',
          need_qty: '10',
          available_after_need: '15',
          recommended: true,
          donor_impact: {
            free_before: '25',
            free_after_full_borrow: '0',
            committed_qty: '0',
          },
        },
      ],
    });

    // The donor's own position travels whole, including where the server ranked it: the
    // dialog tabulates it, and a field dropped here is a field the screen cannot show.
    expect(candidates).toEqual([
      {
        source: 'other_location',
        warehouse_code: 'BRW-IB',
        warehouse_id: 'wh-ib',
        donor_project_ref: null,
        donor_project_id: null,
        free_qty: '25',
        qty_on_hand: '30',
        so_qty: '10',
        spo_qty: '5',
        available_qty: '25',
        qty_free: '25',
        qty_committed: '0',
        need_qty: '10',
        available_after_need: '15',
        recommended: true,
        donor_impact: {
          free_before: '25',
          free_after_full_borrow: '0',
          committed_qty: '0',
        },
        // Ladder v2 (section E): this donor came off the pre-v2 shape, so none of the
        // group-aware facts are stated.
        rung: null,
        donor_so_number: null,
        donor_line_no: null,
        donor_agent_code: null,
        donor_core_line_id: null,
        lower_ranked: false,
        same_agent: false,
      },
    ]);
  });

  it('states a donor position the server left out as absent, never as zero', () => {
    const base = contributionOf({});

    expect(
      borrowCandidatesOf({
        ...base,
        borrow_candidates: [
          {
            source: 'other_location',
            warehouse_code: 'BRW-IB',
            warehouse_id: 'wh-ib',
            free_qty: '25',
          },
        ],
      })[0],
    ).toMatchObject({
      qty_on_hand: null,
      so_qty: null,
      spo_qty: null,
      available_qty: null,
      qty_free: null,
      qty_committed: null,
      need_qty: null,
      available_after_need: null,
      recommended: false,
    });
  });

  it('leaves out a donor the server gave no warehouse id for, rather than inventing one', () => {
    const base = contributionOf({});
    expect(
      borrowCandidatesOf({
        ...base,
        borrow_candidates: [
          { source: 'other_location', warehouse_code: 'BRW-IB', free_qty: '25' },
        ],
      }),
    ).toEqual([]);
  });
});

describe('decisionFromAmendDraft: what the draft carries away', () => {
  it('carries the whole composition, not only the Reserve', () => {
    const contribution = contributionOf({ 'WESERP10B|BRW-BB': '40' });
    const draft = amendDraftFrom(contribution);

    const decision = decisionFromAmendDraft(
      {
        ...draft,
        reserve: [{ ...draft.reserve[0], qty: '20' }],
        borrow: [
          {
            key: 'borrow-1',
            source: 'other_location',
            warehouse_code: 'BRW-IB',
            warehouse_id: 'wh-ib',
            donor_project_ref: null,
            donor_project_id: null,
            qty: '10',
            reason: 'The site next door can wait a week.',
            donor_impact: {
              free_before: '25',
              free_after_full_borrow: '15',
              committed_qty: '0',
            },
          },
        ],
        buy_qty: '70',
      },
      'Holding the rest for the late site.',
    );

    expect(decision).toEqual({
      verdict: 'amended',
      reserve_qty: '20',
      timely_spo_qty: '0',
      reserve: [{ warehouse_id: 'wh-BRW-BB', location: 'BRW-BB', qty: '20' }],
      borrow: [
        {
          source: 'other_location',
          warehouse_id: 'wh-ib',
          warehouse_code: 'BRW-IB',
          donor_project_ref: null,
          donor_project_id: null,
          qty: '10',
          reason: 'The site next door can wait a week.',
          donor_core_line_id: null,
          donor_so_number: null,
          donor_line_no: null,
          donor_agent_code: null,
          same_agent: false,
          donor_required_date: null,
          // Ladder v7.1 step 3 (S4): a plain free-stock borrow names no document, and the
          // keys are still stated - the mapper is one spread now, and a partial one is how
          // `supply_key` came to reach the seeders and not Save.
          supply_key: null,
          supply_document: null,
          arrival_date: null,
        },
      ],
      buy_qty: '70',
      reason: 'Holding the rest for the late site.',
    });
  });

  it('carries the document a step-3 borrow names, all the way to the confirm body', () => {
    const draft = amendDraftFrom(contributionOf({}));
    const borrow = {
      key: 'borrow-1',
      source: 'other_location' as const,
      warehouse_code: 'DC1-IR',
      warehouse_id: 'wh-ir',
      donor_project_ref: null,
      donor_project_id: null,
      qty: '40',
      reason: 'Taking the container.',
      donor_impact: { free_before: '0', free_after_full_borrow: '0', committed_qty: '0' },
      supply_key: 'spo:alloc-1',
      supply_document: 'SPO 202607-S0105',
      arrival_date: '2026-09-15',
    };

    const decision = decisionFromAmendDraft({ ...draft, borrow: [borrow] }, 'Taking it.');
    const confirm = confirmLineFrom('line-1', decision);

    // Both hops: Save writes the decision the board holds, Confirm posts it. Either one
    // dropping the address turns a document borrow into a free-stock borrow at a bin the
    // container has not reached, which the server then refuses.
    expect(decision.borrow?.[0]).toMatchObject({
      supply_key: 'spo:alloc-1',
      supply_document: 'SPO 202607-S0105',
      arrival_date: '2026-09-15',
    });
    expect(confirm.borrow[0]).toMatchObject({
      supply_key: 'spo:alloc-1',
      supply_document: 'SPO 202607-S0105',
      arrival_date: '2026-09-15',
    });
  });

  it('drops a component nobody put a quantity on: a zero decides nothing', () => {
    const draft = amendDraftFrom(contributionOf({}));
    const decision = decisionFromAmendDraft({ ...draft, buy_qty: '100' }, '');

    expect(decision.reserve).toEqual([]);
    expect(decision.borrow).toEqual([]);
    expect(decision.reason).toBeUndefined();
  });

  it('carries the buy reason, trimmed, and leaves it off when nobody gave one', () => {
    const draft = amendDraftFrom(contributionOf({}));
    expect(
      decisionFromAmendDraft({ ...draft, buy_reason: '  Last batch for the site.  ' }, '')
        .buy_reason,
    ).toBe('Last batch for the site.');
    expect(decisionFromAmendDraft({ ...draft, buy_reason: '   ' }, '').buy_reason).toBeUndefined();
  });
});

/**
 * D14: a quick save has to be BYTE-IDENTICAL to opening the line and pressing Save with
 * nothing changed - `BoardLineDecisionPanel`'s own untouched-save path
 * (`{ ...decisionFromAmendDraft(suggestionDraftFrom(contribution), ''), verdict: 'approved' }`).
 */
describe('suggestedDecisionFor: the quick-save decision', () => {
  it('equals what an untouched Save on the row would post', () => {
    const contribution = contributionOf({ 'WESERP10B|BRW-BB': '40' });

    expect(suggestedDecisionFor(contribution)).toEqual({
      ...decisionFromAmendDraft(suggestionDraftFrom(contribution), ''),
      verdict: 'approved',
    });
  });

  it('carries the engine composition, not a bare verdict', () => {
    const contribution = contributionOf({ 'WESERP10B|BRW-BB': '40' });

    const decision = suggestedDecisionFor(contribution);

    expect(decision.verdict).toBe('approved');
    expect(decision.reserve).toEqual([
      { warehouse_id: 'wh-BRW-BB', location: 'BRW-BB', qty: '40' },
    ]);
    expect(decision.buy_qty).toBe('60');
    expect(decision.reason).toBeUndefined();
  });

  it('reads the SUGGESTION on a covered line, not the frozen decision it is leaving', () => {
    // The same fixture `suggestionDraftFrom on a covered line` exercises: a decision already
    // covers the line, and this reads what the engine would propose instead - the reason
    // D11's approval carries the composition rather than a bare `{verdict: 'approved'}`.
    const base = contributionOf(
      {},
      {
        qty: '100',
        fulfilment_location: 'BRW-BB',
        decision: {
          revision_no: 1,
          timely_spo_qty: '0',
          reserve: [{ warehouse_id: 'wh-BRW-BB', location: 'BRW-BB', qty: '100' }],
          borrow: [],
          buy_qty: '0',
        },
      },
    );
    const contribution: BoardContribution = {
      ...base,
      proposed: {
        components: [
          {
            kind: 'reserve',
            qty: '40',
            location: 'BRW-BB',
            warehouse_id: 'wh-BRW-BB',
            reason: 'Free unclaimed stock at BRW-BB covers this much by the delivery date.',
          },
          { kind: 'buy', qty: '60', reason: 'Nothing else free at BRW-BB.' },
        ],
      },
    };

    const decision = suggestedDecisionFor(contribution);

    expect(decision.buy_qty).toBe('60');
    expect(decision.reserve).toEqual([
      { warehouse_id: 'wh-BRW-BB', location: 'BRW-BB', qty: '40' },
    ]);
  });
});

/**
 * D15: the one predicate the list view, the dialog's select column, "Save all suggested" and
 * this round's own row/cell/board-wide icons all wrote out by hand as the same three clauses.
 * A truth table over the three, and the empty draft that every other test already assumes.
 */
describe('canQuickSave', () => {
  const EMPTY_DRAFT: BoardDraft = {};

  it('is eligible when covered, unplannable and drafted are all false', () => {
    const contribution = contributionOf({ 'WESERP10B|BRW-BB': '40' });
    expect(contribution.covered).toBe(false);
    expect(contribution.unplannable).toBeFalsy();

    expect(canQuickSave(contribution, EMPTY_DRAFT)).toBe(true);
  });

  it('is not eligible once covered - there is nothing to approve on a confirmed line', () => {
    const contribution: BoardContribution = {
      ...contributionOf({ 'WESERP10B|BRW-BB': '40' }),
      covered: true,
    };

    expect(canQuickSave(contribution, EMPTY_DRAFT)).toBe(false);
  });

  it('is not eligible once unplannable - no fulfilment location, nothing to suggest', () => {
    const contribution: BoardContribution = {
      ...contributionOf({ 'WESERP10B|BRW-BB': '40' }),
      unplannable: true,
    };

    expect(canQuickSave(contribution, EMPTY_DRAFT)).toBe(false);
  });

  it('is not eligible once a draft already exists for the key - a second save would overwrite it', () => {
    const contribution = contributionOf({ 'WESERP10B|BRW-BB': '40' });
    const draft: BoardDraft = { [contribution.key]: { verdict: 'approved' } };

    expect(canQuickSave(contribution, draft)).toBe(false);
  });

  it('a covered line already carrying an amended draft is still not eligible', () => {
    const contribution: BoardContribution = {
      ...contributionOf({ 'WESERP10B|BRW-BB': '40' }),
      covered: true,
    };
    const draft: BoardDraft = { [contribution.key]: { verdict: 'amended' } };

    expect(canQuickSave(contribution, draft)).toBe(false);
  });
});

describe('amendSummary: what the decided row reads', () => {
  /**
   * SECTION 2'S WORDS, the same table the bar under this pill is painted from: a reserve at
   * the line's own group location is "Own", the shared pool is "Shared", and a borrow says
   * which kind of borrow it is. It used to read "Reserve 20 BRW-BB · Borrow 10 · Buy 13"
   * beside an emerald "Own" segment describing the identical quantity.
   */
  it('states the composition in the vocabulary the bar beside it is drawn from', () => {
    expect(
      amendSummary(
        {
          verdict: 'amended',
          reserve: [{ warehouse_id: 'wh-own', location: 'BRW-BB', qty: '20' }],
          borrow: [
            {
              source: 'other_location',
              warehouse_id: 'wh-ib',
              warehouse_code: 'BRW-IB',
              qty: '10',
              reason: 'Agreed with the other site.',
            },
          ],
          buy_qty: '13',
          timely_spo_qty: '0',
        },
        'BRW-BB',
      ),
    ).toBe('Own 20 BRW-BB · Borrow 10 BRW-IB · Buy 13');
  });

  it('reads a borrow at the line own location as a Borrow beside the Reserve there', () => {
    // BORROW-OWN-LOC (owner, 13 Oct 2026): "take 98 own, borrow 2 from my own location". The
    // borrow is never folded into the Own segment, so the two quantities are counted once each.
    expect(
      amendSummary(
        {
          verdict: 'amended',
          reserve: [{ warehouse_id: 'wh-own', location: 'BRW-BB', qty: '98' }],
          borrow: [
            {
              source: 'other_location',
              warehouse_id: 'wh-own',
              warehouse_code: 'BRW-BB',
              qty: '2',
              reason: 'Another order holds these.',
            },
          ],
          buy_qty: '0',
          timely_spo_qty: '0',
        },
        'BRW-BB',
      ),
    ).toBe('Own 98 BRW-BB · Borrow 2 BRW-BB');
  });

  it('splits a reserve that draws on two different kinds of stock', () => {
    // The pool and the agent's own group are two different answers, and the bar already
    // draws them as two segments. One "Reserve" word over both said neither.
    expect(
      amendSummary(
        {
          verdict: 'amended',
          reserve: [
            { warehouse_id: 'wh-pool', location: 'BRW', qty: '71' },
            { warehouse_id: 'wh-dc1', location: 'DC1-BB', qty: '454' },
          ],
          buy_qty: '0',
          timely_spo_qty: '0',
        },
        'BRW-BB',
      ),
    ).toBe('Own 454 DC1-BB · BRW 71 BRW');
  });

  it('names a borrow that states its donor order as a borrow from another order', () => {
    borrowKinds.shownApart = true; // pins the separate borrow words (four-options flag)
    expect(
      amendSummary(
        {
          verdict: 'amended',
          reserve: [],
          borrow: [
            {
              source: 'other_location',
              warehouse_id: 'wh-own',
              warehouse_code: 'BRW-BB',
              qty: '71',
              reason: 'Authorised by the agent.',
              donor_so_number: 'SO415472',
            },
          ],
          buy_qty: '0',
          timely_spo_qty: '0',
        },
        'BRW-BB',
      ),
    ).toBe('Borrow (order) 71 BRW-BB');
  });

  it('falls back to the one number a decision taken before the editor carries', () => {
    expect(amendSummary({ verdict: 'amended', reserve_qty: '12' })).toBe(
      'Amended to reserve 12',
    );
  });
});

/**
 * S3 (D1): `canDecide` and `decideComposition`, tested straight off a plain `BoardContribution`
 * literal rather than `buildBoard` - the six items read `contribution.locations` and
 * `contribution.borrow_candidates` directly, and a literal keeps each case to exactly the
 * fields the row under test needs.
 */
function decideRow(overrides: Partial<BoardContribution> = {}): BoardContribution {
  return {
    key: 'so-1|1|WESERP10B|2026-09-04',
    sales_order_id: 'so-1',
    so_number: 'SO000001',
    line_no: 1,
    item_code: 'WESERP10B',
    qty: '100',
    unplannable: false,
    cancelled: false,
    rank_score: 1,
    rank_factors: [],
    sources: [],
    contested: false,
    fulfilment_location: 'BRW-BB',
    fulfilment_warehouse_id: 'wh-own',
    locations: [],
    borrow_candidates: [],
    ...overrides,
  };
}

describe('canDecide: which rows the Decide strip may tick (D1)', () => {
  it('accepts a plannable, live row - confirmed or already saved included', () => {
    expect(canDecide(decideRow())).toBe(true);
    expect(canDecide(decideRow({ covered: true }))).toBe(true);
  });

  it('refuses an unplannable row and a cancelled one', () => {
    expect(canDecide(decideRow({ unplannable: true }))).toBe(false);
    expect(canDecide(decideRow({ cancelled: true }))).toBe(false);
  });
});

describe('decideComposition: the six Decide items (D1, AC-5 to AC-9, AC-51)', () => {
  it('AC-5: Buy takes the whole open quantity, whatever the suggestion was', () => {
    const reserved = decideComposition(decideRow(), 'buy');
    expect(reserved).toMatchObject({
      reserve: [],
      borrow: [],
      timely_spo_qty: '0',
      buy_qty: '100',
      order_back: false,
    });

    const borrowed = decideComposition(
      decideRow({
        proposed: { components: [{ kind: 'borrow', qty: '100', location: 'DC1-BB' }] },
      }),
      'buy',
    );
    expect(borrowed.buy_qty).toBe('100');

    const incoming = decideComposition(
      decideRow({ proposed: { components: [{ kind: 'timely_spo', qty: '100' }] } }),
      'buy',
    );
    expect(incoming.buy_qty).toBe('100');
  });

  it('#1362: a Buy over stock already landed (own_arrival) is composed, no longer skipped', () => {
    // Owner, 29 Sep 2026: planning records the intent; Confirm keeps the Buy with a notice
    // and purchasing adjusts the linkage. This was AC-51's skip.
    const result = decideComposition(
      decideRow({
        proposed: {
          components: [{ kind: 'reserve', qty: '100', location: 'BRW-BB', source: 'own_arrival' }],
        },
      }),
      'buy',
    );
    expect(result.skip).toBeUndefined();
    expect(result.buy_qty).toBe('100');
    expect(result.reserve).toEqual([]);
  });

  it('AC-6: Use own location fills from own/group rows only, in order, never a site pool', () => {
    const row = decideRow({
      locations: [
        { location: 'BRW-BB', where: 'own', warehouse_id: 'wh-own', qty_free_remaining: '40' },
        { location: 'DC1-BB', where: 'group', warehouse_id: 'wh-group', qty_free_remaining: '60' },
        { location: 'BRW', where: 'site_pool', warehouse_id: 'wh-pool', qty_free_remaining: '500' },
      ],
    });

    const result = decideComposition(row, 'own');
    expect(result.skip).toBeUndefined();
    expect(result.reserve).toEqual([
      { warehouse_id: 'wh-own', location: 'BRW-BB', qty: '40' },
      { warehouse_id: 'wh-group', location: 'DC1-BB', qty: '60' },
    ]);
    expect(result.buy_qty).toBe('0');
  });

  it('OI-CLOSED-LINE: Use own location short of the line still composes the whole line, with a warning', () => {
    const row = decideRow({
      locations: [
        { location: 'BRW-BB', where: 'own', warehouse_id: 'wh-own', qty_free_remaining: '30' },
      ],
    });

    const result = decideComposition(row, 'own');
    expect(result.skip).toBeUndefined();
    expect(result.warning).toBe('only 30 free at own location');
    expect(result.reserve).toEqual([{ warehouse_id: 'wh-own', location: 'BRW-BB', qty: '100' }]);
  });

  it('AC-7: Use BRW fills from site_pool rows only, within the pool-share allowance', () => {
    const row = decideRow({
      qty: '80',
      locations: [
        { location: 'BRW-BB', where: 'own', warehouse_id: 'wh-own', qty_free_remaining: '999' },
        {
          location: 'BRW',
          where: 'site_pool',
          warehouse_id: 'wh-pool',
          qty_free_remaining: '500',
          available_for_project: '80',
        },
      ],
    });

    const result = decideComposition(row, 'shared');
    expect(result.reserve).toEqual([{ warehouse_id: 'wh-pool', location: 'BRW', qty: '80' }]);
  });

  it('OI-CLOSED-LINE: Use BRW short of the line still composes the whole line, with a warning', () => {
    const row = decideRow({
      locations: [
        {
          location: 'BRW',
          where: 'site_pool',
          warehouse_id: 'wh-pool',
          qty_free_remaining: '50',
          available_for_project: '50',
        },
      ],
    });

    const result = decideComposition(row, 'shared');
    expect(result.skip).toBeUndefined();
    expect(result.warning).toBe('only 50 free at BRW');
    expect(result.reserve).toEqual([{ warehouse_id: 'wh-pool', location: 'BRW', qty: '100' }]);
  });

  it('OI-CLOSED-LINE: Use BRW with no BRW row at all is still skipped (nothing to name)', () => {
    expect(decideComposition(decideRow(), 'shared').skip).toBe('only 0 free at BRW');
  });

  it('AC-8: Borrow from another order takes the whole line off the picked donor order', () => {
    const row = decideRow({
      borrow_candidates: [
        {
          source: 'other_location',
          warehouse_code: 'BRW-BB',
          warehouse_id: 'wh-donor',
          free_qty: '120',
          donor_impact: { free_before: '120', free_after_full_borrow: '20', committed_qty: '0' },
          donor_so_number: 'SO415472',
        },
      ],
    });

    const result = decideComposition(row, 'borrow_order', 'SO415472');
    expect(result.skip).toBeUndefined();
    expect(result.borrow).toEqual([
      expect.objectContaining({
        warehouse_id: 'wh-donor',
        warehouse_code: 'BRW-BB',
        qty: '100',
        donor_so_number: 'SO415472',
      }),
    ]);
  });

  it('AC-8: skips a donor with too little, or with no candidate at all', () => {
    const short = decideRow({
      borrow_candidates: [
        {
          source: 'other_location',
          warehouse_code: 'BRW-BB',
          warehouse_id: 'wh-donor',
          free_qty: '40',
          donor_impact: { free_before: '40', free_after_full_borrow: '0', committed_qty: '0' },
          donor_so_number: 'SO415472',
        },
      ],
    });
    expect(decideComposition(short, 'borrow_order', 'SO415472').skip).toBe(
      'SO415472 holds only 40',
    );
    expect(decideComposition(decideRow(), 'borrow_order', 'SO415472').skip).toBe(
      'SO415472 holds none',
    );
  });

  /**
   * Review round 1, Should fix 2: the donor order's TOTAL free stock used to gate the check
   * (10 across two lines >= 8), but the single component built afterwards posted the whole 8
   * against `candidates[0]` alone, which on its own holds only 5 - a claim `_check_borrow`
   * would refuse at Confirm, far from this press. Neither donor line covers 8 alone here, so
   * the row skips instead of over-claiming one of them.
   */
  it('Should fix 2: skips rather than over-drawing one donor line when two lines together would cover it but neither alone does', () => {
    const row = decideRow({
      qty: '8',
      borrow_candidates: [
        {
          source: 'other_location',
          warehouse_code: 'BRW-BB',
          warehouse_id: 'wh-donor',
          free_qty: '5',
          donor_impact: { free_before: '5', free_after_full_borrow: '0', committed_qty: '0' },
          donor_so_number: 'SO415472',
          donor_core_line_id: 'core-line-3',
        },
        {
          source: 'other_location',
          warehouse_code: 'BRW-BB',
          warehouse_id: 'wh-donor',
          free_qty: '5',
          donor_impact: { free_before: '5', free_after_full_borrow: '0', committed_qty: '0' },
          donor_so_number: 'SO415472',
          donor_core_line_id: 'core-line-7',
        },
      ],
    });

    const result = decideComposition(row, 'borrow_order', 'SO415472');
    expect(result.skip).toBe('SO415472 holds only 5');
    expect(result.borrow).toBeUndefined();
  });

  it('AC-9: Borrow takes the whole line off the picked location', () => {
    const row = decideRow({
      borrow_candidates: [
        {
          source: 'other_location',
          warehouse_code: 'DC1-IR',
          warehouse_id: 'wh-ir',
          free_qty: '200',
          donor_impact: { free_before: '200', free_after_full_borrow: '100', committed_qty: '0' },
        },
      ],
    });

    const result = decideComposition(row, 'borrow_other', 'DC1-IR');
    expect(result.borrow).toEqual([
      expect.objectContaining({ source: 'other_location', warehouse_code: 'DC1-IR', qty: '100' }),
    ]);
  });

  it('owner 14 Oct: a Borrow off the line\'s own location is never capped, saved short', () => {
    const own = {
      source: 'other_location' as const,
      warehouse_code: 'BRW-BB',
      warehouse_id: 'wh-own',
      free_qty: '0',
      donor_impact: { free_before: '0', free_after_full_borrow: '0', committed_qty: '0' },
      donor_so_number: null,
      donor_core_line_id: null,
    };
    const result = decideComposition(
      decideRow({ qty: '2', borrow_candidates: [own] }),
      'borrow_other',
      'BRW-BB',
    );

    expect(result.skip).toBeUndefined();
    expect(result.borrow).toEqual([
      expect.objectContaining({ warehouse_id: 'wh-own', warehouse_code: 'BRW-BB', qty: '2' }),
    ]);
    expect(result.warning).toBe('only 0 free at BRW-BB');
  });

  it('AC-9: skips a location short of the whole line', () => {
    const row = decideRow({
      borrow_candidates: [
        {
          source: 'other_location',
          warehouse_code: 'DC1-IR',
          warehouse_id: 'wh-ir',
          free_qty: '30',
          donor_impact: { free_before: '30', free_after_full_borrow: '0', committed_qty: '0' },
        },
      ],
    });

    expect(decideComposition(row, 'borrow_other', 'DC1-IR').skip).toBe(
      'only 30 free at DC1-IR',
    );
  });

  it('AC-10: a running tally makes the second of two rows contesting one donor skip', () => {
    const donor = {
      source: 'other_location' as const,
      warehouse_code: 'BRW-BB',
      warehouse_id: 'wh-donor',
      free_qty: '150',
      donor_impact: { free_before: '150', free_after_full_borrow: '0', committed_qty: '0' },
      donor_so_number: 'SO415472',
    };
    const first = decideRow({ qty: '100', borrow_candidates: [donor] });
    const second = decideRow({
      key: 'so-1|2|WESERP10B|2026-09-04',
      line_no: 2,
      qty: '100',
      borrow_candidates: [donor],
    });
    const claimed = new Map<string, number>();

    const firstResult = decideComposition(first, 'borrow_order', 'SO415472', claimed);
    expect(firstResult.skip).toBeUndefined();
    const secondResult = decideComposition(second, 'borrow_order', 'SO415472', claimed);
    expect(secondResult.skip).toBe('SO415472 holds only 50');
  });

  /**
   * Should fix 6 (review round 1): the ONLY existing AC-10 test contests a borrow donor - the
   * `own`/`shared` tally read at `:793` (site_pool/own-location rows) was unguarded, since
   * killing it left `boardAmend.test.ts` and `BoardDecideControl.test.tsx` fully green.
   */
  it('AC-10: a running tally makes the second of two rows contesting one BRW pile short (OI-CLOSED-LINE: warned, not skipped)', () => {
    const pool = {
      location: 'BRW',
      where: 'site_pool' as const,
      warehouse_id: 'wh-pool',
      qty_free_remaining: '150',
      available_for_project: '150',
    };
    const first = decideRow({ qty: '100', locations: [pool] });
    const second = decideRow({
      key: 'so-1|2|WESERP10B|2026-09-04',
      line_no: 2,
      qty: '100',
      locations: [pool],
    });
    const claimed = new Map<string, number>();

    const firstResult = decideComposition(first, 'shared', undefined, claimed);
    expect(firstResult.skip).toBeUndefined();
    const secondResult = decideComposition(second, 'shared', undefined, claimed);
    expect(secondResult.skip).toBeUndefined();
    expect(secondResult.warning).toBe('only 50 free at BRW');
  });

  it('AC-5/AC-6/AC-7: the suggested reserve/BRW item never draws a site_pool row for own location', () => {
    // Guards the filter itself, not only the sum: a site pool row with a huge free figure
    // must never appear in `reserve` for 'own', whatever order the rows arrive in.
    const row = decideRow({
      locations: [
        { location: 'BRW', where: 'site_pool', warehouse_id: 'wh-pool', qty_free_remaining: '999' },
        { location: 'BRW-BB', where: 'own', warehouse_id: 'wh-own', qty_free_remaining: '100' },
      ],
    });
    const result = decideComposition(row, 'own');
    expect(result.reserve).toEqual([{ warehouse_id: 'wh-own', location: 'BRW-BB', qty: '100' }]);
  });
});

/**
 * BORROW-LOCATION-ONLY (D3, K8): "Borrow" never resolves to a candidate that
 * NAMES a donor SO, so the location picker cannot leak a donor-named order borrow under the
 * wrong label (the rung leak, `pickerOptions` / `decideComposition` do not filter by rung).
 */
describe('decideComposition: borrow_other never resolves to a donor-named candidate (K8)', () => {
  const impact = { free_before: '100', free_after_full_borrow: '60', committed_qty: '0' };
  const donorNamed = {
    source: 'other_location' as const,
    warehouse_code: 'MWH-IB',
    warehouse_id: 'wh-mwh',
    free_qty: '100',
    donor_impact: impact,
    rung: 'order_borrow',
    donor_so_number: 'SO397450',
    donor_line_no: 446,
    donor_core_line_id: 'core-donor-1',
  };
  const donorless = {
    source: 'other_location' as const,
    warehouse_code: 'DC1-IR',
    warehouse_id: 'wh-dc1',
    free_qty: '100',
    donor_impact: impact,
    rung: 'order_borrow',
    donor_so_number: null,
    donor_core_line_id: 'core-donor-2',
  };

  it('skips a location that is only reachable through a donor-named candidate', () => {
    const result = decideComposition(
      decideRow({ borrow_candidates: [donorNamed] }),
      'borrow_other',
      'MWH-IB',
    );

    expect(result.borrow ?? []).toEqual([]);
    expect(result.skip).toBeTruthy();
  });

  it('resolves a donor-less order_borrow candidate and carries no donor SO', () => {
    const result = decideComposition(
      decideRow({ borrow_candidates: [donorNamed, donorless] }),
      'borrow_other',
      'DC1-IR',
    );

    expect(result.skip).toBeUndefined();
    expect(result.borrow).toHaveLength(1);
    expect(result.borrow?.[0]).toMatchObject({
      warehouse_id: 'wh-dc1',
      donor_core_line_id: 'core-donor-2',
    });
    expect(result.borrow?.[0].donor_so_number ?? null).toBeNull();
  });

  it('at a location with both, takes the donor-less candidate, never the named one', () => {
    const sameBin = { ...donorless, warehouse_code: 'MWH-IB', warehouse_id: 'wh-mwh' };
    const result = decideComposition(
      decideRow({ borrow_candidates: [donorNamed, sameBin] }),
      'borrow_other',
      'MWH-IB',
    );

    expect(result.borrow).toHaveLength(1);
    expect(result.borrow?.[0].donor_core_line_id).toBe('core-donor-2');
    expect(result.borrow?.[0].donor_so_number ?? null).toBeNull();
  });
});

/**
 * BORROW-LOCATION-ONLY S6 (B1, "summed per location"): with "Borrow from another order" off,
 * the candidates at a location name no donor SO, and "Borrow" draws across
 * that location's candidate lines in order - one borrow row per donor line, each within what
 * that line has left - rather than needing ONE line to cover the whole quantity.
 */
describe('decideComposition: borrow_other sums a location across its donor-less lines (S6)', () => {
  const impact = { free_before: '4', free_after_full_borrow: '0', committed_qty: '0' };
  const line = (core: string) => ({
    source: 'other_location' as const,
    warehouse_code: 'BRW-BB',
    warehouse_id: 'wh-brw-bb',
    free_qty: '4',
    donor_impact: impact,
    rung: 'order_borrow',
    donor_so_number: null,
    donor_core_line_id: core,
  });

  it('covers a need of 6 with 4 + 2 off two lines at BRW-BB', () => {
    const result = decideComposition(
      decideRow({ qty: '6', borrow_candidates: [line('core-a'), line('core-b')] }),
      'borrow_other',
      'BRW-BB',
    );

    expect(result.skip).toBeUndefined();
    expect(
      (result.borrow ?? []).map((b) => [b.donor_core_line_id, b.qty, b.warehouse_id]),
    ).toEqual([
      ['core-a', '4', 'wh-brw-bb'],
      ['core-b', '2', 'wh-brw-bb'],
    ]);
  });

  it('keeps a running claim per line, so a second row draws what the first left', () => {
    const claimed = new Map<string, number>();
    const candidates = [line('core-a'), line('core-b')];
    decideComposition(decideRow({ qty: '6', borrow_candidates: candidates }), 'borrow_other', 'BRW-BB', claimed);
    const second = decideComposition(
      decideRow({ key: 'so-2|1', qty: '3', borrow_candidates: candidates }),
      'borrow_other',
      'BRW-BB',
      claimed,
    );

    expect(second.skip).toBe('only 2 free at BRW-BB');
  });
});

/**
 * BORROW-LOCATION-ONLY defect A, the payload the browser measured: three rows at one bin, all
 * with a null `donor_core_line_id` - two step-2 lines of 4 whose ids the board had dropped,
 * and the donor project's hold of 8, which is the SAME stock again. A bin's capacity is
 * never counted twice: rows that cannot be told apart are one pile, and the project hold is
 * not a location row.
 */
describe('decideComposition: the measured null-id payload never double counts a bin', () => {
  const impact = { free_before: '4', free_after_full_borrow: '0', committed_qty: '0' };
  const stepTwo = {
    source: 'other_location' as const,
    warehouse_code: 'X',
    warehouse_id: 'wh-x',
    free_qty: '4',
    donor_impact: impact,
    rung: null,
    donor_so_number: null,
    donor_core_line_id: null,
  };
  const hold = {
    source: 'other_project' as const,
    warehouse_code: 'X',
    warehouse_id: 'wh-x',
    free_qty: '8',
    donor_impact: impact,
    donor_project_id: 'prj-1',
    donor_project_ref: null,
    donor_core_line_id: null,
  };

  it('reads the two indistinguishable rows as one pile of 4 and leaves the hold alone', () => {
    const result = decideComposition(
      decideRow({ qty: '6', borrow_candidates: [stepTwo, stepTwo, hold] }),
      'borrow_other',
      'X',
    );

    expect(result.skip).toBe('only 4 free at X');
  });

  it('with the donor lines the board now carries, covers 6 as 4 + 2 off the two lines', () => {
    const result = decideComposition(
      decideRow({
        qty: '6',
        borrow_candidates: [
          { ...stepTwo, rung: 'order_borrow', donor_core_line_id: 'core-a' },
          { ...stepTwo, rung: 'order_borrow', donor_core_line_id: 'core-b' },
          hold,
        ],
      }),
      'borrow_other',
      'X',
    );

    expect((result.borrow ?? []).map((b) => [b.donor_core_line_id, b.qty])).toEqual([
      ['core-a', '4'],
      ['core-b', '2'],
    ]);
  });
});

/**
 * OI-CLOSED-LINE hand test (owner, 12 Oct 2026, SO416732 CB2807-DIY delivered 2 of 2): the
 * delivered part of a line never needs free stock (owner Q1 (a), the same credit Confirm
 * gives), so the Decide dialog must not skip or warn on it.
 */
describe('decideComposition: the delivered part needs no free stock (OI-CLOSED-LINE)', () => {
  const pool = (free: string) => ({
    location: 'BRW',
    where: 'site_pool' as const,
    warehouse_id: 'wh-pool',
    qty_free_remaining: free,
    available_for_project: free,
  });

  it('Use BRW on a fully delivered line with 0 free composes the line, no skip, no warning', () => {
    const result = decideComposition(
      decideRow({ qty: '2', qty_delivered: '2', locations: [pool('0')] }),
      'shared',
    );
    expect(result.skip).toBeUndefined();
    expect(result.warning).toBeUndefined();
    expect(result.reserve).toEqual([{ warehouse_id: 'wh-pool', location: 'BRW', qty: '2' }]);
  });

  it('Use BRW on a partly delivered line needs free stock only for the undelivered part', () => {
    const claimed = new Map<string, number>();
    const result = decideComposition(
      decideRow({ qty: '10', qty_delivered: '4', locations: [pool('6')] }),
      'shared',
      undefined,
      claimed,
    );
    expect(result.skip).toBeUndefined();
    expect(result.warning).toBeUndefined();
    expect(result.reserve).toEqual([{ warehouse_id: 'wh-pool', location: 'BRW', qty: '10' }]);
    // Only the undelivered 6 claims the pile; the next ticked row sees 0 left, not -4.
    expect([...claimed.values()]).toEqual([toMinor('6')]);
  });

  it('Use BRW short on the undelivered part warns with what is free, and still composes', () => {
    const result = decideComposition(
      decideRow({ qty: '10', qty_delivered: '4', locations: [pool('2')] }),
      'shared',
    );
    expect(result.skip).toBeUndefined();
    expect(result.warning).toBe('only 2 free at BRW');
    expect(result.reserve).toEqual([{ warehouse_id: 'wh-pool', location: 'BRW', qty: '10' }]);
  });

  it('Use own location on a fully delivered line with 0 free composes the line', () => {
    const result = decideComposition(
      decideRow({
        qty: '5',
        qty_delivered: '5',
        locations: [
          { location: 'BRW-BB', where: 'own', warehouse_id: 'wh-own', qty_free_remaining: '0' },
        ],
      }),
      'own',
    );
    expect(result.skip).toBeUndefined();
    expect(result.warning).toBeUndefined();
    expect(result.reserve).toEqual([{ warehouse_id: 'wh-own', location: 'BRW-BB', qty: '5' }]);
  });

  it('Borrow from a location on a fully delivered line with 0 free composes the line', () => {
    const result = decideComposition(
      decideRow({
        qty: '8',
        qty_delivered: '8',
        borrow_candidates: [
          {
            source: 'other_location',
            warehouse_code: 'BRW-BB',
            warehouse_id: 'wh-loc',
            free_qty: '0',
            donor_impact: { free_before: '0', free_after_full_borrow: '0', committed_qty: '0' },
          },
        ],
      }),
      'borrow_other',
      'BRW-BB',
    );
    expect(result.skip).toBeUndefined();
    expect(result.borrow).toEqual([
      expect.objectContaining({ warehouse_id: 'wh-loc', qty: '8' }),
    ]);
  });

  it('Borrow from another order: the donor needs to cover only the undelivered part', () => {
    const result = decideComposition(
      decideRow({
        qty: '10',
        qty_delivered: '7',
        borrow_candidates: [
          {
            source: 'other_location',
            warehouse_code: 'BRW-BB',
            warehouse_id: 'wh-donor',
            free_qty: '3',
            donor_impact: { free_before: '3', free_after_full_borrow: '0', committed_qty: '0' },
            donor_so_number: 'SO415472',
          },
        ],
      }),
      'borrow_order',
      'SO415472',
    );
    expect(result.skip).toBeUndefined();
    expect(result.borrow).toEqual([
      expect.objectContaining({ warehouse_id: 'wh-donor', qty: '10' }),
    ]);
  });
});

afterEach(() => {
  borrowKinds.shownApart = false;
});
