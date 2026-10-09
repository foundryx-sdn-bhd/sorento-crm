/**
 * The Was / Now table a changed line reads on its board cell (AC-P3-2, AC-P3-3, AC-P3-12) and
 * the suggestion printed under it (AC-C1).
 *
 * Four things are pinned here and nowhere else: a closed line still lands on a cell, the
 * retired reaction vocabulary (replan / retire / accept) never reaches a string a person
 * reads, the engine's own composed sentences are carried through VERBATIM and in its order,
 * and the pre-mark covers exactly the changed lines.
 */
import { describe, expect, it } from 'vitest';
import {
  annotationOf,
  annotationsByCell,
  annotationsByLine,
  cellKeyOf,
  decisionWords,
  preMarkedKeys,
  uncoverChangedLines,
} from './boardChangeAnnotations';
import type {
  BoardCell,
  BoardContribution,
} from '../types/fulfilmentPlanning.types';
import type {
  PlanningChangeBatch,
  PlanningChangeRow,
} from '../types/planningChange.types';

function contribution(over: Partial<BoardContribution>): BoardContribution {
  return {
    key: 'k1',
    sales_order_id: 'so-1',
    so_number: 'SO381895',
    project_sales_order_id: 'pso-1',
    project_line_id: 'pl-1',
    line_no: 1,
    item_code: 'SRTWCX7405-RL-S-PJ',
    qty: '25',
    required_date: '2026-08-19',
    fulfilment_location: 'BRW-IB',
    rank_score: 1,
    sources: [],
    trail: [],
    covered: false,
    unplannable: false,
    contested: false,
    ...over,
  } as BoardContribution;
}

function cell(over: Partial<BoardCell>): BoardCell {
  return {
    item_code: 'SRTWCX7405-RL-S-PJ',
    bucket_key: '2026-08-17',
    total_qty: '25',
    locations: [],
    contributions: [contribution({})],
    unplannable_count: 0,
    contested_count: 0,
    ...over,
  } as BoardCell;
}

function row(over: Partial<PlanningChangeRow>): PlanningChangeRow {
  return {
    id: 'pcr-1',
    project_line_id: 'pl-1',
    line_no: 1,
    item_code: 'SRTWCX7405-RL-S-PJ',
    kind: 'advanced',
    from: { required_date: '2026-08-25', qty: '10', status: 'open' },
    to: { required_date: '2026-08-19', qty: '25', status: 'open' },
    days_moved: -6,
    held: {
      reserve: [],
      borrow: [],
      buy_qty: '10',
      timely_spo_qty: '0',
      revision_no: 2,
    },
    facts: {
      dealer_hot_selling: { value: false, where: [] },
      project_hot_selling: { value: false, where: [] },
      discontinued: false,
      days_moved: -6,
      within_reserve_window: {
        value: true,
        window_days: 60,
        new_date: '2026-08-19',
        window_end: '2026-10-24',
      },
      buy_actioned: { value: false, po_number: null },
    },
    suggestion: null,
    proposal: null,
    inquiry_rows: [],
    decision: null,
    applied_state: 'pending',
    board_link:
      '/project-sales/fulfilment-planning?orders=SO381895&cell=X|2026-08-19',
    ...over,
  } as PlanningChangeRow;
}

function batchOf(rows: PlanningChangeRow[]): PlanningChangeBatch {
  return {
    id: 'pcb-9',
    created_at: '2026-08-19T09:23:00Z',
    created_by_name: 'Cyndi',
    source: {
      upload_id: 'imp-1',
      file_name: 'SO book.xlsx',
      kind: 'so_book_upload',
    },
    orders: [
      {
        project_sales_order_id: 'pso-1',
        so_number: 'SO381895',
        revision_no: 2,
        rows,
        is_adopted: true,
      },
    ],
  } as PlanningChangeBatch;
}

describe('the Was / Now table of a changed line', () => {
  it('states the quantity, the date and the decision on both sides', () => {
    const annotation = annotationOf(row({}), 'SO381895', 'BRW-IB');
    expect(annotation.was).toEqual({
      qty: '10',
      date: '2026-08-25',
      decision: 'Buy 10',
    });
    expect(annotation.now.qty).toBe('25');
    expect(annotation.now.date).toBe('2026-08-19');
    expect(annotation.closed).toBe(false);
  });

  it('reads Closed on a line the book closed, and states no new quantity or date', () => {
    const annotation = annotationOf(
      row({
        id: 'pcr-2',
        project_line_id: 'pl-2',
        line_no: 2,
        kind: 'cancelled',
        from: { required_date: '2026-09-05', qty: '10', status: 'open' },
        to: { required_date: null, qty: null, status: 'closed' },
      }),
      'SO381895',
      'BRW-IB',
    );
    expect(annotation.closed).toBe(true);
    expect(annotation.now.qty).toBeNull();
    expect(annotation.now.date).toBeNull();
    expect(annotation.now.decision).toBeNull();
    expect(annotation.was.qty).toBe('10');
  });

  it('never prints a retired reaction verb - the decision is in board words', () => {
    const annotation = annotationOf(
      row({
        held: {
          reserve: [{ location: 'BRW-IB', warehouse_id: 'wh-1', qty: '40' }],
          borrow: [],
          buy_qty: '0',
          timely_spo_qty: '0',
          revision_no: 2,
        },
        suggestion: {
          components: [
            {
              action: 'keep',
              source: 'reserve',
              qty_now: '40',
              location: 'BRW-IB',
              label: 'Keep 40',
            },
          ],
        },
      }),
      'SO381895',
      'BRW-IB',
    );
    expect(annotation.was.decision).toBe('Use own location 40 from BRW-IB');
    // The machinery, not the suggestion: an action code, a decision value and the three
    // retired verbs are all things the reader has no use for. `Keep 40` IS printed - it is
    // the engine's own sentence about a component, which is the whole point of the row.
    const printed = JSON.stringify(annotation).toLowerCase();
    for (const word of ['replan', 'retire', 'accept', '"keep"', '"board"']) {
      expect(printed).not.toContain(word);
    }
    expect(annotation.suggestionLines).toEqual(['Keep 40']);
  });

  it('carries every composed sentence verbatim, in the order the engine wrote them (S2)', () => {
    const annotation = annotationOf(
      row({
        kind: 'qty_down',
        from: { required_date: '2026-09-04', qty: '234', status: 'open' },
        to: { required_date: '2026-09-04', qty: '100', status: 'open' },
        suggestion: {
          components: [
            {
              action: 'reduce',
              source: 'buy',
              qty_was: '100',
              qty_now: '0',
              label: 'Reduce Buy 100 to 0',
            },
            {
              action: 'keep',
              source: 'po',
              qty_was: '134',
              qty_now: '100',
              document: 'PO-A',
              label: 'Keep PO-A 100 of 134',
            },
            {
              action: 'reallocate',
              source: 'po',
              qty_now: '34',
              document: 'PO-A',
              target: 'SO420103 ORDER 50',
              label: 'Reallocate PO-A 34 to SO420103 ORDER 50',
            },
          ],
        },
      }),
      'SO403765',
    );
    expect(annotation.suggestionLines).toEqual([
      'Reduce Buy 100 to 0',
      'Keep PO-A 100 of 134',
      'Reallocate PO-A 34 to SO420103 ORDER 50',
    ]);
  });

  it('states lateness and shortfall as their own facts, not as a sentence to parse', () => {
    const late = annotationOf(
      row({
        suggestion: {
          components: [
            {
              action: 'keep',
              source: 'po',
              qty_now: '134',
              document: 'PO-A',
              label: 'Keep PO-A 134',
            },
          ],
          late_days: 3,
        },
      }),
      'SO401220',
    );
    expect(late.lateDays).toBe(3);
    expect(late.shortfallQty).toBeNull();

    const short = annotationOf(
      row({
        suggestion: {
          components: [
            {
              action: 'use_own',
              source: 'pool_share',
              qty_now: '90',
              location: 'BRW',
              label: 'Pool share 90 at BRW',
            },
          ],
          shortfall_qty: '44',
        },
      }),
      'SO401220',
    );
    expect(short.shortfallQty).toBe('44');
    expect(short.lateDays).toBeNull();
  });

  it('names the product a product_changed row used to be, and nothing on any other kind', () => {
    const swapped = annotationOf(
      row({
        kind: 'product_changed',
        item_code: 'B2155-NL-WHITE',
        from: {
          required_date: '2026-09-04',
          qty: '134',
          status: 'open',
          item_code: 'B2155-NL-BLUE',
        },
        to: {
          required_date: '2026-09-04',
          qty: '134',
          status: 'open',
          item_code: 'B2155-NL-WHITE',
        },
      }),
      'SO400875',
    );
    expect(swapped.productChangedFrom).toBe('B2155-NL-BLUE');
    expect(swapped.itemCode).toBe('B2155-NL-WHITE');
    expect(annotationOf(row({}), 'SO400875').productChangedFrom).toBeNull();
  });

  it('reads the Now decision off the pre-filled composition, ahead of the re-run proposal', () => {
    // Slice C fills `composition` at build, and Amend edits THAT; printing the proposal
    // ahead of it would show the engine's first answer over the planner's own.
    const annotation = annotationOf(
      row({
        proposal: { sources: [{ kind: 'buy', qty: '25' }] } as never,
        composition: {
          project_line_id: 'pl-1',
          timely_spo_qty: '0',
          reserve: [{ warehouse_id: 'BRW-IB', qty: '25' }],
          borrow: [],
          buy_qty: '0',
        },
      }),
      'SO381895',
      'BRW-IB',
    );
    expect(annotation.now.decision).toBe('Use own location 25 from BRW-IB');
  });

  it('never prints a warehouse id on the Now side, even when the composition only carries one (AC-C1)', () => {
    // `composition.reserve` is `ConfirmReserveComponent[]` on the wire - `warehouse_id` and
    // `qty` only, no `location` - because that is what Apply posts. A reserve drawn from the
    // line's own site pool (`BRW`, no ownership-group suffix) still has to read as a pool
    // share, not leak the raw id as if it named some other ownership group.
    const annotation = annotationOf(
      row({
        held: {
          reserve: [
            {
              location: 'BRW',
              warehouse_id: '21608757-0065-4ef2-bd05-1397452411eb',
              qty: '8',
            },
          ],
          borrow: [],
          buy_qty: '0',
          timely_spo_qty: '0',
          revision_no: 1,
        },
        composition: {
          project_line_id: 'pl-1',
          timely_spo_qty: '0',
          reserve: [
            { warehouse_id: '21608757-0065-4ef2-bd05-1397452411eb', qty: '15' },
          ],
          borrow: [],
          buy_qty: '0',
        },
      }),
      'SO381895',
      'BRW',
    );
    expect(annotation.now.decision).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-/i);
    expect(annotation.now.decision).toMatch(/Pool share|Use/);
    expect(annotation.now.decision).not.toContain('Borrow');
  });

  it('says where an APPLIED row\'s held composition actually went, under "Where it went" (AC-P3-9-ish)', () => {
    const annotation = annotationOf(
      row({
        applied_state: 'applied',
        result: {
          executed_reallocations: ['Reallocate 202607-S0080 3 to pool'],
          released_documents: [],
        },
      }),
      'SO381895',
    );
    expect(annotation.whereItWent).toEqual([
      'Reallocate 202607-S0080 3 to pool',
    ]);
  });

  it('wraps a bare released document number and prints a released notice sentence verbatim', () => {
    const annotation = annotationOf(
      row({
        applied_state: 'applied',
        result: {
          executed_reallocations: [],
          released_documents: [
            'SPO-2026/08-0061',
            'SO396347 line 3: 220 of 202607-S0083 received in full, goods are stock now, nothing to move; purchasing adjusts the link at Order Inquiries',
          ],
        },
      }),
      'SO396347',
    );
    expect(annotation.whereItWent).toEqual([
      'Released SPO-2026/08-0061 for purchasing',
      'SO396347 line 3: 220 of 202607-S0083 received in full, goods are stock now, nothing to move; purchasing adjusts the link at Order Inquiries',
    ]);
  });

  it('prints no "Where it went" section on a row Apply has not run yet (result: null)', () => {
    const annotation = annotationOf(
      row({ applied_state: 'pending', result: null }),
      'SO381895',
    );
    expect(annotation.whereItWent).toEqual([]);
  });

  it('prints a composed borrow at the location the component itself states (S1)', () => {
    // The engine writes `location` beside `warehouse_id` on a BORROW component too, and a
    // borrow's donor warehouse is precisely the one that appears NOWHERE else on the row -
    // not in what the line held, not in the proposal - so the stated code is the only thing
    // standing between the reader and "another location".
    const annotation = annotationOf(
      row({
        held: {
          reserve: [
            {
              location: 'BRW',
              warehouse_id: 'bb6f3f1e-0a2e-4a1a-9d1e-0c5f5b9f77aa',
              qty: '2',
            },
          ],
          borrow: [],
          buy_qty: '0',
          timely_spo_qty: '0',
          revision_no: 1,
        },
        composition: {
          project_line_id: 'pl-1',
          timely_spo_qty: '0',
          reserve: [],
          borrow: [
            {
              source: 'other_location',
              warehouse_id: '9f2a1c44-6b3d-4f7e-8a55-11c0de77bead',
              qty: '6',
              reason: 'ZZT donor has it',
              location: 'BRW-IB',
            },
          ],
          buy_qty: '0',
        },
      }),
      'SO381895',
      'BRW',
    );
    expect(annotation.now.decision).toContain('BRW-IB');
    expect(annotation.now.decision).not.toContain('another location');
    expect(annotation.now.decision).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-/i);
  });

  it('carries the moved-transfer phrase when the batch flagged one', () => {
    const annotation = annotationOf(
      row({
        kind: 'cancelled',
        moved_transfer: '10 moved BRW -> BRW-IB, line cancelled',
      }),
      'SO381895',
    );
    expect(annotation.movedTransfer).toBe(
      '10 moved BRW -> BRW-IB, line cancelled',
    );
  });
});

describe('decisionWords', () => {
  it('is null when there is nothing held', () => {
    expect(decisionWords([])).toBeNull();
  });

  it('names the location and the quantity per location', () => {
    expect(
      decisionWords(
        [
          { kind: 'reserve', qty: '40', location: 'BRW-IB' },
          { kind: 'buy', qty: '25' },
        ],
        'BRW-IB',
      ),
    ).toBe('Use own location 40 from BRW-IB · Buy 25');
  });
});

describe('annotationsByCell', () => {
  const surviving = cell({});
  const key = cellKeyOf(surviving);

  it('puts a changed line on the cell its own line contributes to', () => {
    const map = annotationsByCell(batchOf([row({})]), [surviving]);
    expect(map.get(key)?.map((entry) => entry.rowId)).toEqual(['pcr-1']);
  });

  it('puts a closed line on the surviving cell of the same product on the same order', () => {
    const map = annotationsByCell(
      batchOf([
        row({}),
        row({
          id: 'pcr-2',
          project_line_id: 'pl-2',
          line_no: 2,
          kind: 'cancelled',
        }),
        row({
          id: 'pcr-3',
          project_line_id: 'pl-3',
          line_no: 3,
          kind: 'cancelled',
        }),
      ]),
      [surviving],
    );
    expect(map.get(key)?.map((entry) => entry.rowId)).toEqual([
      'pcr-1',
      'pcr-2',
      'pcr-3',
    ]);
  });

  it('lands a proposal-less row on its OWN cell, not the first cell of its product', () => {
    // The second instalment of the same product on the same order. Only a `replan` row
    // carries a proposal, so reading the line off the proposal alone sent every other
    // changed line to the (SO, item) fallback - the FIRST cell of that product - and the
    // second instalment's Was / Now table landed on the first instalment's cell.
    const second = cell({
      bucket_key: '2026-09-07',
      contributions: [
        contribution({ key: 'k2', project_line_id: 'pl-2', line_no: 2 }),
      ],
    });
    const map = annotationsByCell(
      batchOf([
        row({ proposal: null }),
        row({
          id: 'pcr-2',
          project_line_id: 'pl-2',
          line_no: 2,
          kind: 'qty_down',
          proposal: null,
        }),
      ]),
      [surviving, second],
    );
    expect(map.get(key)?.map((entry) => entry.rowId)).toEqual(['pcr-1']);
    expect(map.get(cellKeyOf(second))?.map((entry) => entry.rowId)).toEqual([
      'pcr-2',
    ]);
  });

  it('drops a row whose product is nowhere on the board', () => {
    const map = annotationsByCell(
      batchOf([
        row({ id: 'pcr-9', project_line_id: 'pl-9', item_code: 'NOT-HERE' }),
      ]),
      [surviving],
    );
    expect(map.size).toBe(0);
  });

  it('is empty without a batch', () => {
    expect(annotationsByCell(null, [surviving]).size).toBe(0);
  });
});

describe('preMarkedKeys', () => {
  it('marks exactly the lines the batch changed', () => {
    const contributions = [
      contribution({ key: 'k1', project_line_id: 'pl-1' }),
      contribution({ key: 'k2', project_line_id: 'pl-other' }),
    ];
    expect(preMarkedKeys(batchOf([row({})]), contributions)).toEqual(['k1']);
  });

  it('never marks a line whose sales order states no location', () => {
    const contributions = [
      contribution({ key: 'k1', project_line_id: 'pl-1', unplannable: true }),
    ];
    expect(preMarkedKeys(batchOf([row({})]), contributions)).toEqual([]);
  });
});

describe('uncoverChangedLines', () => {
  /**
   * The defect this pins, measured live on SO381895 (26 August 2026): a covered line's
   * `sources` and `qty_proposed_*` are its FROZEN composition rebuilt, so uncovering it
   * without carrying the batch's own proposal left the cell offering to confirm 10 against
   * a line the book had opened for 25 - which the server refuses, at the last step, after
   * the planner has pressed Confirm.
   */
  const frozen = contribution({
    key: 'k1',
    project_line_id: 'pl-1',
    qty: '25',
    covered: true,
    decision: { revision_no: 2, components: [] },
    sources: [{ kind: 'buy', qty: '10' }],
    qty_proposed_buy: '10',
  } as unknown as Partial<BoardContribution>);
  const board = {
    cells: [cell({ contributions: [frozen] })],
    contributions: [frozen],
  };
  const withProposal = batchOf([
    row({
      proposal: {
        ...contribution({
          key: 'built-earlier',
          project_line_id: 'pl-1',
          qty: '25',
        }),
        sources: [{ kind: 'buy', qty: '25' }],
        qty_proposed_buy: '25',
      } as BoardContribution,
    }),
  ]);

  it('carries the batch proposal onto the line and stops calling it covered', () => {
    const out = uncoverChangedLines(board, withProposal);
    const line = out.contributions[0];
    expect(line.covered).toBe(false);
    expect(line.decision).toBeNull();
    expect(line.qty_proposed_buy).toBe('25');
    expect(line.key).toBe('k1');
    expect(out.cells[0].contributions[0].qty_proposed_buy).toBe('25');
  });

  it('leaves a line the batch never named exactly as it was', () => {
    const other = contribution({
      key: 'k9',
      project_line_id: 'pl-other',
      covered: true,
    });
    const out = uncoverChangedLines(
      { cells: [cell({ contributions: [other] })], contributions: [other] },
      withProposal,
    );
    expect(out.contributions[0].covered).toBe(true);
  });

  it('is the board itself without a batch', () => {
    expect(uncoverChangedLines(board, null)).toBe(board);
  });

  /**
   * AC-RL-06, measured live on SO314594 line 5 (CB2807-DIY, 17 September 2026): the board's
   * own read returned `order_inquiry.documents = [{SPO-2026/04-0058, received}]`, and the
   * page showed no `received` word beside the product. `proposal_json` is a SNAPSHOT of the
   * contribution as it stood when the batch was raised (15 September 09:11, before documents
   * were read at all), so spreading it over the live row replaced the LIVE instruction with
   * a stale copy carrying no `documents` and no `redirected`.
   *
   * What a batch proposal is about is the COMPOSITION - the quantity, the sources, the date
   * it was walked at. What purchasing was told, and what has landed against it, is read
   * fresh off `order_inquiry_rows` on every board build, so the LIVE row's own
   * `order_inquiry` is the current fact and the snapshot's copy of it never is.
   */
  it('keeps the LIVE instruction and its documents, not the snapshot copy', () => {
    const live = contribution({
      key: 'k1',
      project_line_id: 'pl-1',
      covered: true,
      order_inquiry: {
        inquiry_no: 'OI-000539',
        state: 'placed',
        ack_state: 'acknowledged',
        documents: [
          { document: 'SPO-2026/04-0058', kind: 'spo', received: true },
        ],
        redirected: false,
      },
    } as unknown as Partial<BoardContribution>);
    const staleSnapshot = batchOf([
      row({
        proposal: {
          ...contribution({ key: 'built-earlier', project_line_id: 'pl-1' }),
          order_inquiry: {
            inquiry_no: 'OI-000539',
            state: 'placed',
            ack_state: 'acknowledged',
          },
        } as unknown as BoardContribution,
      }),
    ]);
    const out = uncoverChangedLines(
      { cells: [cell({ contributions: [live] })], contributions: [live] },
      staleSnapshot,
    );
    expect(out.contributions[0].order_inquiry?.documents).toEqual([
      { document: 'SPO-2026/04-0058', kind: 'spo', received: true },
    ]);
    expect(out.cells[0].contributions[0].order_inquiry?.documents).toEqual([
      { document: 'SPO-2026/04-0058', kind: 'spo', received: true },
    ]);
  });

  /**
   * T7 / AC-9 (`PLAN-esb-change-row-refresh.md` S3, issue #1240): a batch's `proposal_json`
   * is frozen at the moment the batch was built - a re-push that moved the line again writes
   * a fresh `required_date` / `qty_outstanding` / `is_past` / `qty_delivered` onto the LIVE
   * contribution, but the old `{...contribution, ...proposal}` spread let the frozen
   * proposal's copies of those same fields win, so the board printed the stale date. Only
   * the composition (`sources`, `qty_proposed_*`) should come from the proposal; the live
   * facts must survive the merge unchanged.
   */
  it('T7/AC-9: keeps the live required_date, qty_outstanding, is_past and qty_delivered, and only takes the composition (including trail and rank_score) from the proposal', () => {
    const live = contribution({
      key: 'k1',
      project_line_id: 'pl-1',
      required_date: '2026-12-01',
      qty_outstanding: '5',
      is_past: false,
      qty_delivered: '5',
      covered: true,
      decision: { revision_no: 2, components: [] },
      rank_score: 1,
      trail: [],
    } as unknown as Partial<BoardContribution>);
    const proposedTrail = [
      {
        step: 1,
        kind: 'reserve_own',
        question: 'Any reserved stock at BRW?',
        answer: 'yes',
        took: '5',
      },
    ];
    const frozenProposalBatch = batchOf([
      row({
        proposal: {
          ...contribution({ key: 'built-earlier', project_line_id: 'pl-1' }),
          required_date: '2026-01-15',
          qty_outstanding: '39',
          is_past: true,
          qty_delivered: '39',
          sources: [{ kind: 'buy', qty: '39' }],
          qty_proposed_buy: '39',
          rank_score: 9,
          trail: proposedTrail,
        } as BoardContribution,
      }),
    ]);
    const out = uncoverChangedLines(
      { cells: [cell({ contributions: [live] })], contributions: [live] },
      frozenProposalBatch,
    );
    const merged = out.contributions[0];
    expect(merged.required_date).toBe('2026-12-01');
    expect(merged.qty_outstanding).toBe('5');
    expect(merged.is_past).toBe(false);
    expect(merged.qty_delivered).toBe('5');
    expect(merged.covered).toBe(false);
    expect(merged.decision).toBeNull();
    expect(merged.sources).toEqual([{ kind: 'buy', qty: '39' }]);
    expect(merged.qty_proposed_buy).toBe('39');
    // The composition's OWN facts - what the ladder walk found and how it scored - come
    // from the proposal too, exactly like `sources` and `qty_proposed_buy` above.
    expect(merged.rank_score).toBe(9);
    expect(merged.trail).toEqual(proposedTrail);
    // Same on the cell copy - the two must never disagree.
    const cellMerged = out.cells[0].contributions[0];
    expect(cellMerged.required_date).toBe('2026-12-01');
    expect(cellMerged.qty_outstanding).toBe('5');
    expect(cellMerged.qty_delivered).toBe('5');
    expect(cellMerged.rank_score).toBe(9);
    expect(cellMerged.trail).toEqual(proposedTrail);
  });

  /**
   * T8 / AC-13 to AC-15 (`PLAN-esb-change-row-refresh.md` S4, issue #1240): measured on prod
   * after the 25 Sep datafix - `get_batch` returns every row a batch ever carried, superseded
   * and applied included (the append-only record), and `proposalsByLine` (which
   * `uncoverChangedLines` and `preMarkedKeys` both read through) never looked at
   * `applied_state`. A superseded row's frozen proposal kept overlaying the board and
   * pre-marking the line even after the datafix retired it.
   */
  it('T8/AC-13: a superseded row is ignored - the contribution, pre-mark and annotations are untouched', () => {
    const live = contribution({
      key: 'k1',
      project_line_id: 'pl-1',
      required_date: '2026-12-01',
      covered: true,
      decision: { revision_no: 2, components: [] },
    } as unknown as Partial<BoardContribution>);
    const supersededBatch = batchOf([
      row({
        applied_state: 'superseded',
        project_line_id: 'pl-1',
        proposal: {
          ...contribution({ key: 'built-earlier', project_line_id: 'pl-1' }),
          required_date: '2026-01-15',
          sources: [{ kind: 'buy', qty: '39' }],
          qty_proposed_buy: '39',
        } as BoardContribution,
      }),
    ]);
    const board = {
      cells: [cell({ contributions: [live] })],
      contributions: [live],
    };

    const out = uncoverChangedLines(board, supersededBatch);
    expect(out.contributions[0]).toEqual(live);
    expect(out.cells[0].contributions[0]).toEqual(live);

    expect(preMarkedKeys(supersededBatch, [live])).toEqual([]);
    expect(annotationsByLine(supersededBatch).size).toBe(0);
    expect(
      annotationsByCell(supersededBatch, [cell({ contributions: [live] })])
        .size,
    ).toBe(0);
  });

  it('T8/AC-14: a line with a superseded row (D1) AND a pending row (D3) uses only the pending row', () => {
    const live = contribution({
      key: 'k1',
      project_line_id: 'pl-1',
      required_date: '2026-12-01',
      covered: true,
      decision: { revision_no: 2, components: [] },
    } as unknown as Partial<BoardContribution>);
    const supersededFirst = batchOf([
      // Superseded row first in the payload, on purpose - the fix must not depend on row
      // order to pick the live one.
      row({
        id: 'pcr-superseded',
        applied_state: 'superseded',
        project_line_id: 'pl-1',
        proposal: {
          ...contribution({ key: 'built-earlier', project_line_id: 'pl-1' }),
          required_date: '2026-01-15',
          sources: [{ kind: 'buy', qty: '39' }],
          qty_proposed_buy: '39',
        } as BoardContribution,
      }),
      row({
        id: 'pcr-pending',
        applied_state: 'pending',
        project_line_id: 'pl-1',
        proposal: {
          ...contribution({ key: 'built-later', project_line_id: 'pl-1' }),
          required_date: '2026-12-01',
          sources: [{ kind: 'buy', qty: '5' }],
          qty_proposed_buy: '5',
        } as BoardContribution,
      }),
    ]);
    const board = {
      cells: [cell({ contributions: [live] })],
      contributions: [live],
    };

    const out = uncoverChangedLines(board, supersededFirst);
    expect(out.contributions[0].sources).toEqual([{ kind: 'buy', qty: '5' }]);
    expect(out.contributions[0].qty_proposed_buy).toBe('5');
    expect(out.contributions[0].covered).toBe(false);

    // `preMarkedKeys` runs on the ALREADY-UNCOVERED contributions in production
    // (`FulfilmentBoardPanel.tsx` calls it after `uncoverChangedLines` on the same batch),
    // so a line with a pending row is no longer `covered` by the time it gets here - the
    // `!contribution.covered` guard itself is pinned separately (AC-F7,
    // `FulfilmentBoardPanel.change.test.tsx`) and is not what this test is about.
    expect(preMarkedKeys(supersededFirst, out.contributions)).toEqual(['k1']);
    const byLine = annotationsByLine(supersededFirst);
    expect(byLine.get('pl-1')?.map((entry) => entry.rowId)).toEqual([
      'pcr-pending',
    ]);
  });

  /**
   * T8/AC-15, R3 ruling (`PLAN-board-draft-on-confirmed-line.md`, review round 3, restated
   * for #1240): only the BATCH's own `applied_at` gates whether a line stays covered and
   * blocked from a second Confirm - pinned by `FulfilmentBoardPanel.change.test.tsx`'s "does
   * not block Confirm ... even if a row says it was". A ROW's own `applied_state` of
   * `'applied'` is not the same signal - `superseded` is the only state S4 retires from the
   * overlay - so a batch not yet applied but already carrying an `applied` row (an order this
   * upload named twice, one line already actioned) must still overlay and pre-mark that row
   * exactly like a pending one.
   */
  it('T8/AC-15 (R3 ruling): an applied row still overlays and pre-marks like a pending one', () => {
    const live = contribution({
      key: 'k1',
      project_line_id: 'pl-1',
      required_date: '2026-12-01',
      covered: true,
      decision: { revision_no: 2, components: [] },
    } as unknown as Partial<BoardContribution>);
    const appliedBatch = batchOf([
      row({
        id: 'pcr-applied',
        applied_state: 'applied',
        project_line_id: 'pl-1',
        proposal: {
          ...contribution({ key: 'built-earlier', project_line_id: 'pl-1' }),
          required_date: '2026-12-01',
          sources: [{ kind: 'buy', qty: '5' }],
          qty_proposed_buy: '5',
        } as BoardContribution,
      }),
    ]);
    const board = {
      cells: [cell({ contributions: [live] })],
      contributions: [live],
    };

    const out = uncoverChangedLines(board, appliedBatch);
    expect(out.contributions[0].covered).toBe(false);
    expect(out.contributions[0].decision).toBeNull();
    expect(out.contributions[0].sources).toEqual([{ kind: 'buy', qty: '5' }]);
    expect(out.contributions[0].qty_proposed_buy).toBe('5');

    // Pre-marked off the ALREADY-UNCOVERED contributions, same as AC-14 above.
    expect(preMarkedKeys(appliedBatch, out.contributions)).toEqual(['k1']);
    const byLine = annotationsByLine(appliedBatch);
    expect(byLine.get('pl-1')?.map((entry) => entry.rowId)).toEqual([
      'pcr-applied',
    ]);
  });
});
