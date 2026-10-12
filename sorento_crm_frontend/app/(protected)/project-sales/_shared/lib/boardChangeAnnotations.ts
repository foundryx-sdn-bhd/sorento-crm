/**
 * What a re-uploaded sales-order book did to a line, said on the board cell that line sits in
 * (`PLAN-scm-cs-planning-uat.md` part 3, AC-P3-2 / AC-P3-3).
 *
 * The captain, 25 August 2026: "same page as fulfilment, with the change annotated" - and
 * structure, not a sentence. So a changed line reads as a small Was / Now table of three rows
 * (Qty, Date, Decision) and nothing else.
 *
 * TWO RULES THIS FILE EXISTS TO HOLD:
 *
 * 1. **The batch's own machinery never reaches the screen.** `replan` / `retire` / `accept`
 *    named a reaction the row took to itself, and agreeing with a verb executed nothing; they
 *    are retired with the rule table (Slice C,
 *    `documentation/plans/scm/PLAN-scm-change-management-one-engine.md`). What a planner reads
 *    instead is the SUGGESTION the engine composed - one sentence per component, Keep /
 *    Reduce / Release / Reallocate on what is held and Use own / Borrow / SPO / Buy for new
 *    quantity (AC-C1) - printed verbatim, plus a Decision row built from the line's HELD
 *    composition on the Was side and what Apply would post on the Now side, through the same
 *    `partsBreakdown` the Suggestion and Decision cards are built from.
 * 2. **A closed line still has to be visible.** It contributes nothing to the board any more -
 *    the book closed it - so it has no cell of its own, and dropping it would make two thirds
 *    of the fixture's change invisible. It is annotated on the surviving cell of the SAME
 *    product on the SAME sales order instead, and reads `Closed` in the Now column, which is
 *    where a planner is already looking to see what replaced it.
 *
 * Pure: no fetch, no clock, no React. The panel hands it the batch and the cells it is about
 * to render, and gets back the annotations keyed exactly as the matrix keys its cells.
 */
import {
  LABELS,
  partsBreakdown,
  rowText,
  type SupplyPart,
} from './supplyVocabulary';
import type {
  BoardCell,
  BoardContribution,
} from '../types/fulfilmentPlanning.types';
import type {
  PlanningChangeBatch,
  PlanningChangeHeld,
  PlanningChangeRow,
} from '../types/planningChange.types';

/** One side of the Was / Now table. `null` on a value the row cannot state. */
export interface BoardChangeSide {
  qty: string | null;
  date: string | null;
  /**
   * The supply decision in board words, e.g. `Buy 25` or `Use own location 40 from BRW-BB`.
   *
   * Was = what the line HELD. Now = what Apply would post: the row's own pre-filled
   * `composition` first (Slice C fills it at build, so Confirm posts it unchanged), then the
   * re-run `proposal`, then the hold. Never a reaction verb - the row no longer carries one.
   */
  decision: string | null;
}

/**
 * One field the book moved, ready to print as `Qty 234 -> 334` (AC-C10).
 *
 * Display text on both sides, resolved here rather than in the dialog: the board, the
 * lightbox and the column the icon sits in all read this one list, so what counts as
 * "changed" cannot come to mean two things on one screen.
 */
export interface BoardChangeField {
  key: 'qty' | 'date' | 'decision';
  label: string;
  /** Both empty on a field that is a STATEMENT rather than a move: a cancelled line. */
  from: string;
  to: string;
}

/** What one changed line reads on its cell. */
export interface BoardChangeAnnotation {
  /** The batch row this came from - the id a decision is PUT against. */
  rowId: string;
  soNumber: string;
  lineNo: number;
  itemCode: string;
  /** The book's own change kind, for the caption. Never a reaction verb. */
  kind: PlanningChangeRow['kind'];
  /** The line is closed in the book: the Now column reads `Closed` and states no quantity. */
  closed: boolean;
  was: BoardChangeSide;
  now: BoardChangeSide;
  /**
   * The composed suggestion, one server-written sentence per component, in the engine's own
   * order: held components first, then new sourcing (AC-C1). Printed verbatim - the board
   * does not re-phrase it, so the words on screen are the words the engine chose.
   */
  suggestionLines: string[];
  /** The unit is kept but lands N days late (S12, AC-C6). `null` when it is on time. */
  lateDays: number | null;
  /**
   * Quantity nothing covers in time (S11, AC-B3). `null` when the unit is covered.
   *
   * NOTHING PRINTS IT since AC-C11: the engine's own label already reads "Short 44 by 22 Aug
   * (was Buy 134)", and a bare "Short 44" beside it was that fact said twice. It stays as
   * the engine's own figure, which `boardChangeAnnotations.test.ts` reads directly, and
   * because a caller that needs the number rather than the sentence has nowhere else to get
   * it.
   */
  shortfallQty: string | null;
  /** The product this line used to be, on a `product_changed` row only (S7, AC-C5). */
  productChangedFrom: string | null;
  /** `10 moved BRW -> BRW-IB, line cancelled` (AC-P3-9), or null. */
  movedTransfer: string | null;
  /**
   * WHERE THE HELD QUANTITY ACTUALLY WENT once Apply ran (Slice D): the server's own
   * executed sentences first, then the documents it gave back to purchasing. Empty on every
   * row Apply has not written yet, which is what keeps the section off a pending row.
   *
   * Optional on the TYPE and never optional in practice: `annotationOf` always fills it,
   * and the callers that hand-build an annotation predate the field.
   */
  whereItWent?: string[];
  /** Which line the change is about, when the batch knows it. Used to match a cell's lines. */
  projectLineId: string | null;
}

/**
 * `4 Sep 2026` - a date in a sentence a person reads, the same shape the engine's own labels
 * use, WITH the year (PLAN-oi-replan-received-links.md AC-RL-01, owner ruling 16 Sep 2026): a
 * bare `1 Jun -> 1 Mar` reads as an advance within the same year when the book actually moved
 * a line from 2026 into 2027, which is exactly what SO314593 did.
 *
 * The months are named here rather than left to `Intl`, whose `en-GB` short form spells
 * September "Sept": the server composes "Buy 134 for 15 Mar" with this vocabulary, and a
 * lightbox that spelled the same month differently two lines apart would read as two
 * different facts.
 */
const SHORT_MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

export function shortDay(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return '';
  return `${date.getUTCDate()} ${SHORT_MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

/**
 * ONLY what moved (owner feedback, 13 September 2026), of the three fields the retired
 * Was / Now table printed unconditionally.
 *
 * A field whose two sides are equal is not a change, and printing it as one is the clutter
 * that feedback was about. ONE source for two readers: the lightbox prints these lines, and
 * the list puts the change icon in the column each key names, so the two cannot come to
 * disagree about what moved.
 *
 * A CANCELLED line says it ONCE, as the single word: the book closed it, so its quantity,
 * its date and its decision all end in the same place, and three lines each reading
 * "-> Cancelled" is one fact printed three times. What was HELD is not lost with them - the
 * suggestion lines under it say where each part of the hold went, in the engine's own words
 * ("Release 50 to dealer pool", "Reallocate PO-B 84 to dealer pool").
 */
export function changedFieldsOf(
  annotation: Pick<BoardChangeAnnotation, 'was' | 'now' | 'closed'>,
): BoardChangeField[] {
  const { was, now, closed } = annotation;
  if (closed) {
    return [{ key: 'qty', label: 'Cancelled', from: '', to: '' }];
  }
  const out: BoardChangeField[] = [];
  const push = (
    key: BoardChangeField['key'],
    label: string,
    from: string,
    to: string,
  ) => {
    if (!from && !to) return;
    if (from === to) return;
    out.push({
      key,
      label,
      from: from || 'Not stated',
      to: to || 'Not stated',
    });
  };
  push('qty', 'Qty', was.qty ?? '', now.qty ?? '');
  push('date', 'Date', shortDay(was.date), shortDay(now.date));
  push('decision', 'Decision', was.decision ?? '', now.decision ?? '');
  return out;
}

/** How the matrix keys a cell: the row's key (item code, or an id on a pivoted axis). */
export function cellKeyOf(
  cell: Pick<BoardCell, 'item_code' | 'row_key' | 'bucket_key'>,
): string {
  return `${cell.row_key ?? cell.item_code}|${cell.bucket_key}`;
}

/** The composition a line HELD before the book moved, as supply parts. */
function heldParts(held: PlanningChangeHeld | null | undefined): SupplyPart[] {
  if (!held) return [];
  const parts: SupplyPart[] = [];
  for (const reserve of held.reserve ?? []) {
    parts.push({
      kind: 'reserve',
      qty: reserve.qty,
      location: reserve.location,
    });
  }
  for (const borrow of held.borrow ?? []) {
    parts.push({ kind: 'borrow', qty: borrow.qty, location: borrow.location });
  }
  if (Number(held.timely_spo_qty ?? '0') > 0) {
    parts.push({ kind: 'timely_spo', qty: held.timely_spo_qty });
  }
  if (Number(held.buy_qty ?? '0') > 0)
    parts.push({ kind: 'buy', qty: held.buy_qty });
  return parts;
}

/**
 * Board words for one composition: `Use own location 40 from BRW-BB · Buy 25`.
 *
 * Through `partsBreakdown`, so the annotation, the Suggestion card and the cell's own bar all
 * say the same thing about the same parts.
 */
export function decisionWords(
  parts: SupplyPart[],
  ownLocation?: string | null,
): string | null {
  if (parts.length === 0) return null;
  const rows = partsBreakdown([{ parts, ownLocation }]);
  if (rows.length === 0) return null;
  return rows.map((row) => `${LABELS[row.key]} ${rowText(row)}`).join(' · ');
}

/**
 * The Now side's decision: what Apply would post for the line at its new state.
 *
 * The row's own `composition` FIRST. Slice C pre-fills it at build from the re-run, so it is
 * what Confirm posts unchanged and what Amend edited if CS has been here - reading the
 * `proposal` ahead of it would print the engine's first answer over the planner's own. Then
 * the `proposal` (a row the engine could compose nothing for still ran the ladder), then what
 * the line held - a change that leaves supply alone reads the same decision on both sides,
 * which is the honest answer rather than a blank.
 */
/**
 * The warehouse CODE for a composed component, never the id it is addressed by.
 *
 * A composition is what Apply POSTS, so it carries `warehouse_id` and, until the engine
 * started writing one beside it, no code at all - and the board reads a component's
 * `location` as a code ("BRW", "BRW-IB"). Printed raw, the id read as a warehouse nobody
 * recognises: measured on SO419595 line 9 (13 September 2026), the Now side said "Borrow
 * other location 15 from 21608757-0065-4ef2-bd05-1397452411eb" for what was a pool share at
 * BRW - a UUID on screen, and the wrong rung with it, because a location that matches no
 * known code can only read as somebody else's.
 *
 * Three sources, in order: what the component itself says, the code the SAME id is already
 * spelled with elsewhere on this row, and failing both, plain words. Never an id.
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function locationOf(
  stated: string | null | undefined,
  warehouseId: string | null | undefined,
  codes: Map<string, string>,
): string | undefined {
  const said = (stated ?? '').trim();
  if (said) return said;
  const id = (warehouseId ?? '').trim();
  if (!id) return undefined;
  // Not an id at all: a caller that already had the code and put it in the only field the
  // confirm payload has for a warehouse. Read as what it is rather than resolved.
  if (!UUID.test(id)) return id;
  const resolved = codes.get(id);
  if (resolved) return resolved;
  // Nowhere on this row spells that id. "Another location" is the honest reading, and it
  // is the same phrase the supply vocabulary uses for a warehouse it cannot name.
  return 'another location';
}

/**
 * Every warehouse id this row already spells a code for, from the two places that carry
 * both: what the line HELD, and the proposal the engine composed for it.
 */
function warehouseCodesOf(row: PlanningChangeRow): Map<string, string> {
  const codes = new Map<string, string>();
  const note = (
    id: string | null | undefined,
    code: string | null | undefined,
  ) => {
    if (!id || !code) return;
    if (!codes.has(String(id))) codes.set(String(id), code);
  };
  for (const reserve of row.held?.reserve ?? [])
    note(reserve.warehouse_id, reserve.location);
  for (const borrow of row.held?.borrow ?? [])
    note(borrow.warehouse_id, borrow.location);
  const proposal = (row.proposal ?? null) as BoardContribution | null;
  for (const source of proposal?.sources ?? []) {
    note(
      (source as { warehouse_id?: string | null }).warehouse_id,
      (source as { location?: string | null }).location,
    );
  }
  note(proposal?.fulfilment_warehouse_id, proposal?.fulfilment_location);
  return codes;
}

function proposedParts(row: PlanningChangeRow): SupplyPart[] {
  if (row.composition) {
    const codes = warehouseCodesOf(row);
    const parts: SupplyPart[] = [];
    for (const reserve of row.composition.reserve ?? []) {
      parts.push({
        kind: 'reserve',
        qty: reserve.qty,
        location: locationOf(reserve.location, reserve.warehouse_id, codes),
      });
    }
    for (const borrow of row.composition.borrow ?? []) {
      parts.push({
        kind: 'borrow',
        qty: borrow.qty,
        location: locationOf(borrow.location, borrow.warehouse_id, codes),
      });
    }
    if (Number(row.composition.timely_spo_qty ?? '0') > 0) {
      parts.push({ kind: 'timely_spo', qty: row.composition.timely_spo_qty });
    }
    if (Number(row.composition.buy_qty ?? '0') > 0) {
      parts.push({ kind: 'buy', qty: row.composition.buy_qty });
    }
    return parts;
  }
  if (row.proposal) {
    const proposal = row.proposal as BoardContribution;
    return proposal.proposed?.components ?? proposal.sources ?? [];
  }
  return heldParts(row.held);
}

/**
 * What Apply DID, in the order a person asks it in: what moved, then what was given back.
 *
 * The executed sentences are the server's own words, printed verbatim for the same reason the
 * suggestion is - only the engine knows which document covered what, and re-phrasing here
 * could only drift from the record. A released document is a bare document number in the
 * result, so it is the one thing given a sentence around it. A released entry that is already
 * a sentence (a skipped closed purchase-order line's notice, a "nothing to move" record) is
 * printed verbatim for the same reason the executed ones are.
 *
 * Takes the RESULT rather than the row: the sales-order detail reads the same fact off a line
 * that carries only the batch row's result (a cancelled line leaves the board once Apply has
 * run, so the dialog it would have opened there is unreachable), and the two screens must not
 * word it differently.
 */
export function whereItWentFrom(result: PlanningChangeRow['result']): string[] {
  if (!result) return [];
  return [
    ...(result.executed_reallocations ?? []),
    ...(result.released_documents ?? []).map((document) =>
      /\s/.test(document.trim())
        ? document
        : `Released ${document} for purchasing`,
    ),
  ];
}

/** The sentences the engine composed for this row, in its own order. */
function suggestionLinesOf(row: PlanningChangeRow): string[] {
  return (row.suggestion?.components ?? [])
    .map((component) => (component.label ?? '').trim())
    .filter((label) => label.length > 0);
}

/** One batch row turned into what its cell reads. */
export function annotationOf(
  row: PlanningChangeRow,
  soNumber: string,
  ownLocation?: string | null,
): BoardChangeAnnotation {
  // `cancelled`, renamed from `closed` (Slice A,
  // `documentation/plans/scm/PLAN-scm-change-management-one-engine.md` rule 5).
  const closed = row.kind === 'cancelled';
  const proposal = (row.proposal ?? null) as BoardContribution | null;
  const location = ownLocation ?? proposal?.fulfilment_location ?? null;
  const lineId = row.project_line_id ?? proposal?.project_line_id ?? null;
  const was: BoardChangeSide = {
    qty: row.from?.qty ?? null,
    date: row.from?.required_date ?? null,
    decision: decisionWords(heldParts(row.held), location),
  };
  const now: BoardChangeSide = {
    qty: closed ? null : (row.to?.qty ?? null),
    date: closed ? null : (row.to?.required_date ?? null),
    decision: closed ? null : decisionWords(proposedParts(row), location),
  };
  return {
    rowId: row.id,
    soNumber,
    lineNo: row.line_no,
    itemCode: row.item_code,
    kind: row.kind,
    closed,
    was,
    now,
    suggestionLines: suggestionLinesOf(row),
    lateDays: row.suggestion?.late_days ?? null,
    shortfallQty: row.suggestion?.shortfall_qty ?? null,
    // The row's own `item_code` is already the NEW product (Slice A: `Change.item_code` is
    // built from the after side), so the product that CHANGED is the one on the from side.
    productChangedFrom:
      row.kind === 'product_changed' ? (row.from?.item_code ?? null) : null,
    movedTransfer: row.moved_transfer ?? null,
    whereItWent: whereItWentFrom(row.result),
    projectLineId: lineId,
  };
}

/**
 * Every annotation the board should draw, keyed by the cell it belongs on.
 *
 * A row lands on the cell whose lines include the row's own project line. Failing that - the
 * closed case, where the line has left the board - it lands on the FIRST cell of the same
 * product on the same sales order, so what replaced it and what it was are read together. A
 * row whose product is nowhere in the selection is dropped: there is no cell to draw it on.
 */
export function annotationsByCell(
  batch: Pick<PlanningChangeBatch, 'orders'> | null | undefined,
  cells: BoardCell[],
): Map<string, BoardChangeAnnotation[]> {
  const out = new Map<string, BoardChangeAnnotation[]>();
  if (!batch) return out;

  /** Which cell key each project line contributes to, and each (SO, item) pair sits in. */
  const cellByLine = new Map<string, string>();
  const locationByLine = new Map<string, string | null>();
  const cellByOrderItem = new Map<string, string>();
  for (const cell of cells) {
    const key = cellKeyOf(cell);
    for (const contribution of cell.contributions) {
      if (contribution.project_line_id) {
        cellByLine.set(contribution.project_line_id, key);
        locationByLine.set(
          contribution.project_line_id,
          contribution.fulfilment_location ?? null,
        );
      }
      const pair = `${contribution.so_number} ${contribution.item_code}`;
      if (!cellByOrderItem.has(pair)) cellByOrderItem.set(pair, key);
    }
  }

  for (const order of batch.orders ?? []) {
    for (const row of order.rows ?? []) {
      if (isRetiredChangeRow(row)) continue;
      const proposal = (row.proposal ?? null) as BoardContribution | null;
      // The ROW's own line first, exactly as `annotationOf` and `proposalsByLine` read it.
      // A row the engine could compose nothing for carries no proposal, so reading the
      // proposal alone sent such a line to the (SO, item) fallback - which is the FIRST cell
      // of that product on that order, so the second instalment of a product landed its
      // Was / Now table on the first instalment's cell instead of its own.
      const lineId = row.project_line_id ?? proposal?.project_line_id ?? null;
      const pair = `${order.so_number} ${row.item_code}`;
      const key =
        (lineId ? cellByLine.get(lineId) : undefined) ??
        cellByOrderItem.get(pair) ??
        null;
      if (!key) continue;
      const annotation = annotationOf(
        row,
        order.so_number,
        lineId ? (locationByLine.get(lineId) ?? null) : null,
      );
      const held = out.get(key);
      if (held) held.push(annotation);
      else out.set(key, [annotation]);
    }
  }
  return out;
}

/**
 * Every annotation the LIST should draw, keyed by the planning line it is about.
 *
 * The grid keys by cell because a cell is where a product and a date meet; the list keys by
 * line because a row IS one line. Same annotations, same `annotationOf` - the two readings
 * of the board never build the change twice.
 */
export function annotationsByLine(
  batch: Pick<PlanningChangeBatch, 'orders'> | null | undefined,
): Map<string, BoardChangeAnnotation[]> {
  const out = new Map<string, BoardChangeAnnotation[]>();
  for (const order of batch?.orders ?? []) {
    for (const row of order.rows ?? []) {
      if (isRetiredChangeRow(row)) continue;
      const proposal = (row.proposal ?? null) as BoardContribution | null;
      const lineId = row.project_line_id ?? proposal?.project_line_id ?? null;
      // NO PLANNING LINE, STILL A CHANGE (R3). An order nobody has adopted has no mirror
      // line for the row to name, and dropping it here left the LIST silent about a change
      // the grid still drew - `annotationsByCell` has always had a fallback of its own.
      // The sales order and the line number are what both sides of that shape do carry.
      const key = lineId ?? lineKeyOf(order.so_number, row.line_no);
      const annotation = annotationOf(
        row,
        order.so_number,
        proposal?.fulfilment_location ?? null,
      );
      const held = out.get(key);
      if (held) held.push(annotation);
      else out.set(key, [annotation]);
    }
  }
  return out;
}

/** How a list row is addressed when it has no planning line of its own: `SO400875|2`. */
export function lineKeyOf(
  soNumber: string,
  lineNo: number | null | undefined,
): string {
  return `${soNumber}|${lineNo ?? ''}`;
}

/**
 * A row that is HISTORY, never worth drawing on the board: one another, later row in the same
 * batch has already replaced (S1/S4, `PLAN-esb-change-row-refresh.md`, issue #1240), or one an
 * earlier Confirm already APPLIED (BOARD-FROZEN-DISPLAY, 12 Oct 2026). `get_batch` returns
 * every row a batch ever carried, by design, so the batch lightbox and history still show
 * them; only the LIVE overlay (this file) drops one.
 *
 * ONE predicate, used everywhere a row feeds the board overlay - `proposalsByLine` (and
 * through it `uncoverChangedLines`, `changedLineIds`, `preMarkedKeys`), `annotationsByLine`
 * and `annotationsByCell` (review round 1, B1 ruling: the S2 list-vs-grid split this used to
 * carry was a defect, not a feature).
 *
 * WHY AN APPLIED ROW IS RETIRED TOO (reverses the R3 ruling of
 * `PLAN-board-draft-on-confirmed-line.md`, review round 3). A batch stays open while ANY of
 * its rows is pending (S5b, #1245), so after a partial Confirm the rows that press DID apply
 * kept uncovering their lines and pre-marking them with the batch's OLD `proposal_json`,
 * painted over the line's frozen decision. SO422492 on prod: C-FH14, confirmed BUY in rev 3
 * and rev 4 and handed to purchasing as ORDER, read "Own N (BRW-NTC)" as both Suggested and
 * Decided. What an applied row asked for is already in the active revision; the line shows
 * that. A failed row is NOT retired: its order's apply rolled back, so the change it carries
 * is still undone. Confirm's own block still reads only the batch's `applied_at`
 * (`FulfilmentBoardPanel.change.test.tsx`, "does not block Confirm ...").
 */
function isRetiredChangeRow(row: PlanningChangeRow): boolean {
  return row.applied_state === 'superseded' || row.applied_state === 'applied';
}

/**
 * A line the BOOK has moved is no longer covered by the decision that was taken for it.
 *
 * The board's own rule is that a covered line offers Amend and nothing else, and that its
 * frozen composition is what gets carried forward - both correct while the line still says
 * what it said when it was decided. Once the book has changed its quantity or its date, the
 * frozen decision is about a line that no longer exists; the lazy drift check
 * (`challenge_if_drifted`) says so already, one read too late. So on a board opened with a
 * batch, the lines that batch changed arrive UNCOVERED, carrying the fresh proposal the
 * batch computed for them.
 *
 * Nothing is thrown away: what the line held is the Was column of its own Was / Now table.
 *
 * A pure remap of the payload, cells and top-level contributions alike, because Confirm,
 * the previews and Approve all read the top-level list while the grid reads the cells - a
 * change on one copy only is invisible to half the screen.
 */

/**
 * Composition-only fields a batch's own proposal may override on a contribution (S3,
 * `PLAN-esb-change-row-refresh.md`, AC-9; review round 1 widened it to the whole ladder-walk
 * shape). Everything else about the line - `required_date`, `qty`, `qty_outstanding`,
 * `is_past`, `order_inquiry`, whatever else the board states about it - is the LIVE fact and
 * stays the live contribution's own value: `proposal_json` is a SNAPSHOT frozen the moment the
 * batch was built, and a re-push that moved the line again (SO419122) left the board printing
 * that frozen snapshot's copies of fields the live row had long since moved past.
 *
 * `trail`, `rank_factors`, `rank_score`, `contested`, `available_to_this_line`,
 * `so_qty_ahead` and `lines_ahead` describe HOW the proposal's own composition was arrived at
 * - the ladder walked at the batch's new date, not a fact about the line itself - so they
 * belong here beside `sources`, not left to leak the live board's own (stale, pre-change) walk.
 */
function compositionOf(
  proposal: BoardContribution,
): Partial<BoardContribution> {
  return {
    sources: proposal.sources,
    qty_proposed_reserve: proposal.qty_proposed_reserve,
    qty_proposed_incoming: proposal.qty_proposed_incoming,
    qty_proposed_buy: proposal.qty_proposed_buy,
    proposed: proposal.proposed,
    options: proposal.options,
    locations: proposal.locations,
    buy_origin: proposal.buy_origin,
    trail: proposal.trail,
    rank_factors: proposal.rank_factors,
    rank_score: proposal.rank_score,
    contested: proposal.contested,
    available_to_this_line: proposal.available_to_this_line,
    so_qty_ahead: proposal.so_qty_ahead,
    lines_ahead: proposal.lines_ahead,
  };
}

export function uncoverChangedLines<
  T extends { cells: BoardCell[]; contributions: BoardContribution[] },
>(board: T, batch: Pick<PlanningChangeBatch, 'orders'> | null | undefined): T {
  const proposals = proposalsByLine(batch);
  if (proposals.size === 0) return board;
  const uncover = (contribution: BoardContribution): BoardContribution => {
    if (!contribution.project_line_id) return contribution;
    const entry = proposals.get(contribution.project_line_id);
    if (!entry) return contribution;
    // THE BATCH'S OWN PROPOSAL, not a second one. A covered line's `sources` and
    // `qty_proposed_*` are its FROZEN composition rebuilt - the old quantity, at the old
    // date - so uncovering it alone would leave the cell offering to confirm 10 against a
    // line the book has opened for 25, which the server refuses (measured live on
    // SO381895, 26 August 2026). The batch walked the ladder at the new date when it was
    // built (AC-R07, "the row and the board show one proposal, not two"), and this is
    // that walk. Built from the LIVE contribution first, not the proposal - S3
    // (`PLAN-esb-change-row-refresh.md`, AC-9): only the composition below comes from the
    // proposal, everything else (identity, `required_date`, `qty`, `qty_outstanding`,
    // `is_past`, `order_inquiry` - AC-RL-06 - and anything else the board states about the
    // line) is the live board's own fact, never the batch's frozen snapshot of it.
    const proposal = entry.proposal;
    return {
      ...contribution,
      ...(proposal ? compositionOf(proposal) : {}),
      covered: false,
      decision: null,
    };
  };
  return {
    ...board,
    cells: board.cells.map((cell) => ({
      ...cell,
      contributions: cell.contributions.map(uncover),
    })),
    contributions: board.contributions.map(uncover),
  };
}

/** The planning lines a batch names, and the fresh proposal it holds for each. A retired row
 * is skipped (`isRetiredChangeRow`): a superseded one was replaced by another row (S4/B1), an
 * applied one is already in the active revision (BOARD-FROZEN-DISPLAY). */
function proposalsByLine(
  batch: Pick<PlanningChangeBatch, 'orders'> | null | undefined,
): Map<string, { proposal: BoardContribution | null }> {
  const out = new Map<string, { proposal: BoardContribution | null }>();
  for (const order of batch?.orders ?? []) {
    for (const row of order.rows ?? []) {
      if (isRetiredChangeRow(row)) continue;
      const proposal = (row.proposal ?? null) as BoardContribution | null;
      const lineId = row.project_line_id ?? proposal?.project_line_id ?? null;
      if (lineId) out.set(lineId, { proposal });
    }
  }
  return out;
}

/** The planning lines a batch names, however the row spells them. */
function changedLineIds(
  batch: Pick<PlanningChangeBatch, 'orders'> | null | undefined,
): Set<string> {
  return new Set(proposalsByLine(batch).keys());
}

/**
 * The decision every changed line arrives PRE-MARKED with (AC-P3-3).
 *
 * A row whose suggestion leaves the line's own supply alone is approved as it stands; a row
 * carrying a fresh proposal is approved against that proposal, which is exactly what the
 * board's own Approve does to an undecided cell. Nothing is written here: this seeds the
 * board's DRAFT, and Confirm is still the only write.
 *
 * Confirm does NOT count or post a bare pre-mark on its own any more (S5, owner ruling 25 Sep
 * 2026, `PLAN-esb-change-row-refresh.md`, issue #1245 - supersedes the AC-C7 line of
 * `PLAN-board-change-proposed-pill`, 18 Sep 2026): SO419122 read "Confirm (119)" with nothing
 * ticked and one press handed 49 rows to purchasing. The pill, the icons and this seeding
 * still fire unchanged - only the count, and what Confirm actually sends, changed, in
 * `FulfilmentBoardPanel.tsx`'s `draftWithoutPreMarks`.
 */
export function preMarkedKeys(
  batch: Pick<PlanningChangeBatch, 'orders'> | null | undefined,
  contributions: BoardContribution[],
): string[] {
  if (!batch) return [];
  // `changed` drops a retired (superseded or applied) row's line (`isRetiredChangeRow`).
  // The `covered` check below is a SEPARATE guard this function still needs: it takes
  // `Pick<PlanningChangeBatch, 'orders'>`, never the
  // batch's own `applied_at`, so a batch that resolves APPLIED after the board's contributions
  // are already on screen (AC-F7, `FulfilmentBoardPanel.change.test.tsx`) is indistinguishable
  // here from one still pending by row shape alone - `contribution.covered` (already current,
  // read straight off the live board) is what actually tells the two apart.
  const changed = changedLineIds(batch);
  return contributions
    .filter(
      (contribution) =>
        contribution.project_line_id !== null &&
        contribution.project_line_id !== undefined &&
        changed.has(contribution.project_line_id) &&
        !contribution.unplannable &&
        // A line an active decision covers has nothing to pre-mark (the same rule
        // `canQuickSave` states) - without this, a batch resolving as APPLIED after the
        // board already uncovered the line seeded a Saved draft onto a Confirmed line.
        !contribution.covered,
    )
    .map((contribution) => contribution.key);
}
