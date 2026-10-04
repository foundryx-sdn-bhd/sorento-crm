/**
 * What the fulfilment board needs from the CLIENT, now that the server computes the board.
 *
 * Seam B owns the arithmetic: bucketing, ranking, allocation, the selection-scoped totals and
 * the contribution keys all come down the wire. Three things stay here because they are the
 * client's own business:
 *
 * - `standingsFor`, because verdicts live in the board's draft and the server always sends
 *     `decided_count: 0` (deviation 4). Everything ELSE about a standing is read, not counted:
 *     anything counted off the cells is window-scoped and therefore wrong;
 * - `commitPreviewFor`, which turns a standing into the sentence beside its Confirm;
 * - `amendNeedsReason`, which decides whether an edit is displacing the ranking, and
 *     `matchesSuggestion`, which decides which verdict the one Save button takes.
 *
 * The board-CONSTRUCTION engine that used to live here (bucketing, scoring, allocation) moved to
 * `__testsupport__/boardFixture.ts` when the mock was deleted. It builds realistic boards for the
 * component tests and is not shipped: keeping it in production would be a second implementation
 * of the thing the server now owns, which is exactly the defect `priority.py` warns about.
 */
import type {
  BoardAxisRow,
  BoardCell,
  BoardCommitPreview,
  BoardContribution,
  BoardDecision,
  BoardDraft,
  BoardOrderStanding,
  BoardRowAxis,
  BoardSource,
  ConfirmBorrowComponent,
  ConfirmLine,
  ConfirmReserveComponent,
} from '../types/fulfilmentPlanning.types';
import {
  borrowPassThrough,
  borrowReasonKeyOf,
  confirmLineFrom,
  suggestionWithReasons,
} from './boardAmend';
import { formatDateTimeInMalaysia, parseDateTimeAsUTC, timeAgo } from '@/lib/helpers';
import { fromMinor, toMinor } from './supplyComposition';
import { unpostableNotices } from './unpostableNotices';

/**
 * A column header with the week-commencing abbreviation taken off.
 *
 * The captain, verbatim: "what does w/c 2 Nov 2026 mean? what does w/c mean?" - and then, on
 * being offered replacements, "just remove it". Having to ask IS the verdict. The granularity
 * control already says the board is by week, so the column has nothing left to restate.
 *
 * The label is formatted SERVER-side, so this is a bridge until that lane drops the prefix: it
 * is idempotent, and does nothing at all once the server stops sending it. It is the only
 * reformatting the client does to a server label, and it exists because jargon on screen is not
 * something to wait on.
 */
export function bucketLabelText(label: string): string {
  return label.replace(/^w\/c\s+/i, '');
}

/**
 * AC-DT-1/AC-DT-4 (`PLAN-oi-decision-trail-ui.md`): the board header line beside "N to
 * confirm - N rejected" - "Revision N - confirmed by <name>, <date time> - N lines", or
 * "No decision yet" when the order has no active decision. One segment per order, order
 * number first, when several orders are planned together - a single-order board (the
 * ordinary case) prints the bare sentence with no order number in front of it.
 */
export function decisionHeaderText(orders: BoardOrderStanding[]): string {
  if (orders.length === 0) return 'No decision yet';
  const segments = orders.map((order) => {
    const decision = order.decision;
    const body = decision
      ? [
          `Revision ${decision.revision_no}`,
          decision.confirmed_by_name || decision.confirmed_at
            ? `confirmed by ${decision.confirmed_by_name ?? 'someone'}${
                decision.confirmed_at
                  ? `, ${formatDateTimeInMalaysia(decision.confirmed_at)}`
                  : ''
              }`
            : null,
          `${decision.line_count} line${decision.line_count === 1 ? '' : 's'}`,
        ]
          .filter(Boolean)
          .join(' · ')
      : 'No decision yet';
    return orders.length > 1 ? `${order.so_number}: ${body}` : body;
  });
  return segments.join('; ');
}

/**
 * How many day columns the server renders at day granularity (deviation 7): the board's
 * `DAY_WINDOW_COLUMNS`. Paging moves by this, never by the columns that happened to come back,
 * so a window with nothing owed in it is still a page and no day is skipped or shown twice.
 */
export const DAY_WINDOW_COLUMNS = 30;

/** The first day of the window one page on from `anchor` (an ISO date), or one page back. */
export function shiftedDayWindow(anchor: string, direction: 1 | -1): string {
  const next = new Date(`${anchor}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + direction * DAY_WINDOW_COLUMNS);
  return next.toISOString().slice(0, 10);
}

/**
 * A ranking factor named in words a planner uses.
 *
 * The chips printed `po_document_sequence absent` and `need_by_date` - database column names,
 * shown to somebody who never chose them. Same fault as "w/c", same answer: nothing on this
 * screen should need decoding. A factor nobody has named yet is humanised rather than printed
 * raw, so a new one from the policy never regresses the screen to identifiers.
 */
const FACTOR_LABELS: Record<string, string> = {
  need_by_date: 'Delivery date',
  document_age: 'Order date',
  customer_credit: 'Payment terms',
  demand_class: 'Demand type',
  po_document_sequence: 'Purchase order sequence',
};

export function factorLabel(key: string): string {
  const known = FACTOR_LABELS[key];
  if (known) return known;
  const words = key.replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Why one line stands in front of another, named.
 *
 * The policy's factors, plus the three the TIE-BREAK produces when the policy separated
 * nothing, in the queue's own order: an earlier delivery date, an earlier line of the same
 * order, or a lower sales-order number. Those three are not factors and must not read as if
 * they were - the date tie is named apart from the "Delivery date" factor, because a factor
 * label there would claim a score difference the two lines do not have.
 */
export function aheadFactorLabel(key: string): string {
  if (key === 'earlier_date') return 'Earlier delivery date (tie)';
  if (key === 'line_order') return 'same order';
  if (key === 'tie_break') return 'tie-break';
  return factorLabel(key);
}

/**
 * Which sales order each contribution key belongs to.
 *
 * Accumulated by the panel across every board it has shown, rather than rebuilt from the cells
 * currently on screen: a verdict given on a cell that a later window no longer displays is
 * still that order's verdict, and dropping it would make the counter fall as the planner
 * scrolled. The key itself is never parsed - the server owns its format (deviation 5).
 */
export type ContributionOwners = Map<string, string>;

/**
 * Per order: how many lines it has IN THE SELECTION, and how many of them carry a verdict yet.
 *
 * This is the number that makes the partial-decision reality visible (13.4). A cell holds one
 * product on one date and an order spans many lines across many dates, so approving one cell
 * almost never finishes an order.
 *
 * `line_count` and `unplannable_count` are the SERVER's, off `board.orders`, which is built
 * from every row of the selection. They used to be counted from the cells on screen, and at day
 * granularity the cells are a 30-day window: a forty-line order read "3 of 3 lines decided" and
 * the Confirm beside it promised to leave nothing behind. A count that shrinks when you change
 * the view is not a count of anything.
 *
 * Only `decided_count` is ours, because verdicts live in the client draft; the server's field is
 * always 0 by design (deviation 4) and is overwritten here. A draft key nobody owns counts for
 * nobody rather than being guessed at.
 */
export function standingsFor(
  orders: BoardOrderStanding[],
  owners: ContributionOwners,
  draft: BoardDraft,
  /**
   * The contributions an ACTIVE decision already covers, accumulated the same way `owners` is.
   *
   * A confirmed line IS decided - it is decided in the database rather than in the draft - and
   * a card reading "0 of 2 lines decided" beside an order whose first line was confirmed
   * yesterday describes nothing that is true.
   */
  covered: ReadonlySet<string> = new Set(),
): BoardOrderStanding[] {
  const decided = new Map<string, number>();
  const committing = new Map<string, number>();
  const carried = new Map<string, number>();
  const counted = new Set<string>();
  const count = (key: string, commits: boolean) => {
    const salesOrderId = owners.get(key);
    // Counted once per LINE: a covered line the planner has amended is one decision, not two.
    if (!salesOrderId || counted.has(key)) return;
    counted.add(key);
    decided.set(salesOrderId, (decided.get(salesOrderId) ?? 0) + 1);
    if (commits) committing.set(salesOrderId, (committing.get(salesOrderId) ?? 0) + 1);
  };
  for (const [key, decision] of Object.entries(draft)) {
    // A REJECTED line is decided but not committed: the planner said no, and the confirm body
    // deliberately omits it so it stays undecided and keeps flowing to reorder planning.
    count(key, decision.verdict !== 'rejected');
  }
  for (const key of covered) {
    // A covered line the planner has given NO verdict on is carried by the server on the next
    // confirm (the body never names it - `confirmLinesFor`), so it is decided after the press
    // without being posted by it. An amendment and an approval are both posted, so neither is
    // carried; this has to follow `lineFor` or the two describe different presses.
    const verdict = draft[key]?.verdict;
    const salesOrderId = owners.get(key);
    if (salesOrderId && verdict !== 'amended' && verdict !== 'approved') {
      carried.set(salesOrderId, (carried.get(salesOrderId) ?? 0) + 1);
    }
    count(key, true);
  }
  return [...orders]
    .map((order) => ({
      ...order,
      decided_count: decided.get(order.sales_order_id) ?? 0,
      committing_count: committing.get(order.sales_order_id) ?? 0,
      carried_count: carried.get(order.sales_order_id) ?? 0,
    }))
    .sort((left, right) => left.so_number.localeCompare(right.so_number));
}

/**
 * What confirming this order right now would do, and what it would leave behind.
 *
 * Confirm is NOT gated on completeness (PLAN 13.4, the captain's decision): a planner commits
 * the lines they are sure about so the undecided ones keep flowing to reorder planning. So the
 * screen owes a plain statement of the consequence instead of a disabled button, and this is the
 * number behind that sentence.
 *
 * A line that can never be decided here (its sales order states no location) is counted inside
 * `leaving_undecided` and named again in `blocked`, because it is undecided for a reason the
 * planner cannot fix on this screen.
 */
export function commitPreviewFor(
  standing: BoardOrderStanding,
  /**
   * How many lines the body would actually carry. Pass it whenever the body is known: a
   * decided line with no mirror on the planning record cannot be posted either, and only the
   * body knows that.
   */
  postable?: number,
): BoardCommitPreview {
  // What would be POSTED, which is not every verdict: a rejection decides a line and commits
  // nothing for it. A button reading "Confirm 2 lines" that posts one is a button that lies.
  const committing = postable ?? standing.committing_count ?? standing.decided_count;
  // A covered line the body does not name is CARRIED by the server into the new revision, so
  // it is decided after the press without being posted by it - and not "left undecided". Only
  // netted beside a BODY length: `committing_count` already counts the covered lines itself.
  const carried = postable === undefined ? 0 : (standing.carried_count ?? 0);
  return {
    committing,
    leaving_undecided: Math.max(standing.line_count - committing - carried, 0),
    blocked: standing.unplannable_count,
  };
}

/**
 * The body one order's Confirm posts, built from the board's draft.
 *
 * The captain asked whether the board's Confirm did anything - "so now when i click the confirm
 * 8 lines, it won't work and won't flow to order inquiries isit?" - and it did not. This is the
 * mapping onto the endpoint that already exists (`POST .../sales-orders/{pso_id}/confirm`), so
 * the board commits through the same per-order confirmation the sheet does rather than growing
 * a second write path (13.4: the board is a LENS).
 *
 * CONFIRM POSTS SAVED LINES ONLY (8 Sep 2026 captain's ruling, reverses R11). R11 read an
 * untouched, uncovered line as agreement and posted the engine's own suggestion for it; the
 * board-wide Confirm now leaves that line alone - not posted, not counted - because nobody
 * decided it. Only a SAVED decision moves an uncovered line into the body: an amendment
 * composed in the editor, or an approval saved via the row or "Save all suggested". A COVERED
 * line keeps its pre-existing rule: untouched, it is carried by the server; an explicit verdict
 * (amend or approve) is posted.
 *
 * What is NOT named is the point. A line the body omits is left UNDECIDED and keeps flowing to
 * reorder planning. So:
 *
 * - an UNCOVERED line with no saved decision is omitted: nobody has agreed with the engine yet;
 * - a REJECTED line is omitted. The planner refused the proposal; committing it anyway would
 *     be the opposite of what they said, and there is no "commit nothing for this line" verb;
 * - a line with no `project_line_id` is omitted rather than posted with a null, because the
 *     endpoint keys on it and a null would fail the whole confirmation for the others;
 * - an UNPLANNABLE line is omitted: its sales order states no fulfilment location (AC-FP16),
 *     so there is no warehouse to reserve at and the server would refuse the whole order.
 *
 * An AMENDMENT moves the difference into Buy. The quantity a planner takes off a Reserve does
 * not evaporate: it is still owed, and somebody still has to buy it.
 *
 * THE BODY NAMES ONLY WHAT WAS DECIDED OR AMENDED NOW; the union is the SERVER's (13.4). A
 * confirmation supersedes the active revision, and every line that revision covers which the
 * body does not name is carried into the new one verbatim from its frozen snapshot - same
 * holds, same reasons, no re-validation. The board briefly built that union itself, re-posting
 * every covered line, and that had two holes: at day granularity the cells are a window, so a
 * covered line outside it was not re-posted and was silently un-decided; and the re-posted line
 * was rebuilt without its buy reason and re-judged against live facts, so a discontinued
 * product's covered line 422'd the confirmation of an unrelated one. So a covered line the
 * planner has not touched is NEVER posted from here.
 *
 * A confirmation carrying nothing new is not sent at all (an empty body), because there is
 * nothing to decide: the covered lines are already in the database.
 */
export interface ConfirmScopeOptions {
  excludeKeys?: ReadonlySet<string>;
  /**
   * Orders with no planning record yet. Given, a `no_mirror` line counts (and is listed) only
   * on one of these: the press adopts them first and mints the mirror ids, whereas on an
   * adopted order a line with no mirror is left out and named. Absent, every `no_mirror`
   * line counts, as it did before this option existed.
   */
  unadoptedSalesOrderIds?: ReadonlySet<string>;
}

/**
 * What one press does with one contribution: the ONE classification the count, the summary and
 * the pre-confirm dialog all read, so title, list, button and body are views of one population.
 * (`confirmLinesFor` builds the body from `lineFor`, which this calls.)
 */
type PressRole =
  | { kind: 'skip' }
  | { kind: 'cancelled' }
  | { kind: 'withdraw' }
  | { kind: 'blocked_reject' }
  | { kind: 'stale' }
  | { kind: 'post'; line: ConfirmLine }
  | { kind: 'unpostable'; reason: UnpostableReason };

function pressRoleFor(
  contribution: BoardContribution,
  decision: BoardDecision | undefined,
  batchBlocked: boolean,
): PressRole {
  if (contribution.unplannable) return { kind: 'skip' };
  // A CANCELLED line is retired by the press (R3); it needs no composition. One that also
  // carries a saved decision still posts it, as the body always did.
  if (contribution.cancelled) {
    const built = lineFor(contribution, decision);
    return built !== null && typeof built !== 'string'
      ? { kind: 'post', line: built }
      : { kind: 'cancelled' };
  }
  // A covered reject on an ACTIVE decision is a withdrawal Confirm carries out, unless a
  // pending planning change holds it back (`rejected_line_ids` is refused beside a batch).
  if (contribution.decision && decision?.verdict === 'rejected') {
    return { kind: batchBlocked ? 'blocked_reject' : 'withdraw' };
  }
  if (decision && contribution.draft?.stale && decision.verdict !== 'rejected') {
    return { kind: 'stale' };
  }
  const built = lineFor(contribution, decision);
  if (built === null) return { kind: 'skip' };
  if (typeof built === 'string') return { kind: 'unpostable', reason: built };
  return { kind: 'post', line: built };
}

/**
 * Whether one press sends this line: the ONE predicate the Saved | Others toggle, `confirmLinesFor`,
 * `rejectedCoveredLineIdsFor` and `plannedLineCount` all read, so "Saved" is exactly what Confirm
 * posts. True for a line `lineFor` builds a body line for, a covered line whose staged rejection
 * the press withdraws, and (on an order about to be adopted) a line whose mirror adoption mints.
 * A saved line the press will not post (no mirror on an adopted order, stale, unplannable) and a
 * rejection held back by a pending planning change are not.
 */
export function pressPostsContribution(
  contribution: BoardContribution,
  decision: BoardDecision | undefined,
  options?: { batchBlocked?: boolean; unadopted?: boolean },
): boolean {
  const role = pressRoleFor(contribution, decision, options?.batchBlocked ?? false);
  if (role.kind === 'post' || role.kind === 'withdraw') return true;
  return role.kind === 'unpostable' && role.reason === 'no_mirror' && Boolean(options?.unadopted);
}

export function confirmLinesFor(
  contributions: BoardContribution[],
  salesOrderId: string,
  draft: BoardDraft,
  /** Contributions the planner unticked in the pre-confirm dialog: left out of the body. */
  options?: ConfirmScopeOptions,
): ConfirmLine[] {
  const lines: ConfirmLine[] = [];
  for (const contribution of contributions) {
    if (contribution.sales_order_id !== salesOrderId) continue;
    if (options?.excludeKeys?.has(contribution.key)) continue;
    const role = pressRoleFor(contribution, draft[contribution.key], false);
    if (role.kind === 'post') lines.push(role.line);
  }
  return lines;
}

/**
 * `ConfirmSupplyBody.rejected_line_ids` for one order - the mirror ids of COVERED lines a
 * `rejected` draft is staged on (owner ruling 23 Sep 2026,
 * `PLAN-board-reject-on-confirmed-line.md`: "we should confirm the rejection" - reject on a
 * confirmed line is a STAGED decision like every other board decision now, and Confirm is
 * what actually withdraws it, never the draft save). `confirmLinesFor` above already leaves
 * a rejected line OUT of `lines` (covered or not, `lineFor`'s own rule) - this is the OTHER
 * half of the same press for exactly the covered ones: an UNCOVERED rejected line has
 * nothing active to withdraw, so it is excluded here, same as it always was.
 */
export function rejectedCoveredLineIdsFor(
  contributions: BoardContribution[],
  salesOrderId: string,
  draft: BoardDraft,
  options?: ConfirmScopeOptions,
): string[] {
  const ids: string[] = [];
  for (const contribution of contributions) {
    if (contribution.sales_order_id !== salesOrderId) continue;
    if (options?.excludeKeys?.has(contribution.key)) continue;
    // N1 (fix round, `PLAN-board-reject-on-confirmed-line.md`): `covered` spans TWO kinds
    // of line - an ACTIVE decision, or a live order-inquiry row naming it with none
    // (`inquiry_decided`, #875). Only the first has a `line_snapshots` entry Confirm's
    // `rejected_line_ids` could ever name, so this reads `decision` (non-null exactly
    // then), not `covered`.
    if (pressRoleFor(contribution, draft[contribution.key], false).kind !== 'withdraw') continue;
    if (contribution.project_line_id) ids.push(contribution.project_line_id);
  }
  return ids;
}

/**
 * Why a decided line cannot be posted by this confirmation. Every one of these is a line the
 * server would refuse, and the confirmation is atomic across the order, so posting it would take
 * every other line down with it. It is left out and NAMED instead.
 */
export type UnpostableReason = 'no_mirror' | 'no_reserve_warehouse';

export interface UnpostableLine {
  contribution: BoardContribution;
  reason: UnpostableReason;
}

/**
 * ONE line of the body, or the reason it cannot be one, or null when nothing is to be posted
 * for it at all (rejected, unplannable, or covered and untouched - none of which is a loss).
 *
 * The single place the rule lives, so the body, the notice that names what the body left out
 * and the count on the button cannot disagree about which lines those are.
 *
 * `decision` UNDEFINED on an UNCOVERED line means the planner left it alone: the 8 Sep 2026
 * ruling (reverses R11) reads that as UNDECIDED, not agreement, so nothing is posted for it -
 * it stays on the board. Only a SAVED decision (an approved draft included) moves an uncovered
 * line into the confirmation; "Save all suggested" is the bulk way to agree with the engine
 * before pressing Confirm. A COVERED line keeps its own rule below: untouched is carried by
 * the server, and an explicit verdict is still posted.
 */
function lineFor(
  contribution: BoardContribution,
  decision: BoardDecision | undefined,
): ConfirmLine | UnpostableReason | null {
  // NO LOCATION ON THE SALES ORDER: there is no warehouse to reserve at, and the server
  // refuses the whole order over one such line. It was never reachable while a line had to
  // be ticked first (the row offers no verb); silence-means-yes makes it reachable.
  if (contribution.unplannable) return null;
  // SAVED, BUT THE ENGINE HAS RE-SUGGESTED SINCE (S4, AC-4.4): the pill already says
  // "Suggestion changed" - excluded here rather than posted on the strength of a comparison
  // that is no longer true, until the planner opens the row and saves it again.
  if (contribution.draft?.stale) return null;
  // UNCOVERED AND UNTOUCHED (8 Sep 2026 ruling, reverses R11): the planner has not saved a
  // decision for this line, so it is left UNDECIDED - not posted, not counted as rejected.
  // `decision` is the caller's own draft, already merged with whatever the server sent as
  // `contribution.draft` (the Panel's seeding effect does this on every board read), so its
  // being undefined here is the whole signal that nobody saved anything. A covered line's
  // untouched case is handled separately below (it is carried by the server).
  if (!contribution.covered && decision === undefined) return null;
  // ALREADY CONFIRMED, AND NOT TOUCHED SINCE: the server carries it. Nothing to post, and
  // nothing to derive - the board proposes nothing for a covered line, and inventing one
  // would overwrite a person's composition with the engine's opinion of it.
  //
  // AN EXPLICIT VERDICT IS NOT SILENCE, though, and both of them are posted: an AMENDMENT as
  // composed, and an APPROVAL as the engine's suggestion, down the same branch an uncovered
  // approval takes. A planner who amends a confirmed line, changes their mind and puts the
  // engine's numbers back has asked for the suggestion to be written; this swallowed it, so
  // the pill read Approved, the Confirm counter never moved and a reload showed the old
  // revision.
  if (contribution.covered && !isAmendment(decision) && decision?.verdict !== 'approved') {
    return null;
  }
  if (decision?.verdict === 'rejected') return null;

  const buyReason = decision?.buy_reason?.trim() || undefined;

  // AN AMENDMENT COMPOSED IN THE EDITOR IS POSTED AS COMPOSED. Every warehouse and every
  // donor was chosen by a person against the balance in front of them, so there is nothing
  // left to derive - and the derivation is what used to lose them: a line the engine met
  // entirely from Buy has no Reserve source to read a warehouse off, so an amendment moving
  // it into a Reserve was dropped from the body while the row still read "Amended".
  if (decision?.verdict === 'amended' && decision.reserve) {
    if (!contribution.project_line_id) return 'no_mirror';
    return confirmLineFrom(contribution.project_line_id, decision);
  }

  // AN APPROVAL ON A COVERED LINE POSTS THE ENGINE'S SUGGESTION, COMPOSED, NOT DERIVED.
  //
  // The derivation below reads `qty_proposed_*`, the source strip and the borrow strip, and
  // on a covered line the server fills all three from the ACTIVE DECISION (`_apply_frozen`).
  // So an approval on SO404352 line 22 - confirmed at 8 from BRW-AM and 16 from the pool,
  // suggested at 9 and 15 - flipped the pill to Approved, moved the Confirm counter and
  // then posted the 8 and the 16 again: "0 transfers proposed", and the revision unchanged
  // after a reload. The suggestion is composed exactly as the editor composes it and posted
  // exactly as an amendment is, with no `amend_reason`, because an approval is not an
  // override. An UNCOVERED approval keeps the derivation: there the three fields are the
  // engine's own answer, and re-deriving from them is what makes the board agree with the
  // sheet.
  if (contribution.covered && decision?.verdict === 'approved') {
    // Board-confirm-left-out (measured cause 1): this used to rebuild the suggestion off
    // `decisionFromAmendDraft` alone, which drops the `buy_reason` and any borrow reason the
    // SAVED decision carried - a discontinued line whose planner had already typed a reason
    // into the approving Save (below) still read `buy_reason_missing` here. Composed by the
    // SAME helper the panel's own approving `save()` uses, off the reasons THAT decision saved.
    const suggested = suggestionWithReasons(contribution, {
      buy_reason: decision.buy_reason ?? undefined,
      borrow: decision.borrow ?? [],
      // S2 (fix round 2, reviewer): the same reasons this branch already carries across
      // (measured cause 1) apply to the order-back instruction too - it is part of what the
      // SAVED decision recorded, not a detail riding beside it.
      order_back: decision.order_back,
      cited_document: decision.cited_document ?? undefined,
    });
    suggested.suspected_system_issue = decision.suspected_system_issue ?? false;
    if (!contribution.project_line_id) return 'no_mirror';
    return confirmLineFrom(contribution.project_line_id, suggested);
  }

  // THE PLAN QUANTITY, not the still-owed one (14 Sep 2026 ruling). `qty` is what the
  // server's own balance check validates a composition against (`_LineFacts.open_qty`),
  // and since a delivered unit nobody sourced is a unit to put back the two have stopped
  // being the same number: a composition built against `qty_outstanding` on a delivered
  // line sums to less than the line asks for and is refused at confirm.
  const owed = toMinor(contribution.qty);
  // The engine's own numbers when it sends them. The board now proposes what the SHEET
  // proposes - pool and borrow are considered, not just own-location reserve then buy - so
  // re-deriving a composition from the source strip would be a second, worse allocator
  // quietly disagreeing with the real one.
  const incoming = numberOr(contribution.qty_proposed_incoming, () =>
    sumSources(contribution.sources, 'timely_spo'),
  );
  const proposedReserve = numberOr(contribution.qty_proposed_reserve, () =>
    sumSources(contribution.sources, 'reserve'),
  );
  const reserveQty =
    decision?.verdict === 'amended' && decision.reserve_qty !== undefined
      ? toMinor(decision.reserve_qty)
      : proposedReserve;
  // Whatever the Reserve and the incoming stock do not cover is bought. Derived rather than
  // read off the proposal so an amendment cannot leave a quantity owed by nobody.
  // On an unchanged proposal the server's own Buy stands; an amendment moves the quantity
  // the planner took off the Reserve into it, because that quantity is still owed.
  const buy =
    decision?.verdict === 'amended' || contribution.qty_proposed_buy === undefined ||
    contribution.qty_proposed_buy === null
      ? Math.max(owed - incoming - reserveQty, 0)
      : toMinor(contribution.qty_proposed_buy);
  const reserve = reserveWarehouses(contribution, reserveQty);
  // A Reserve nobody can address is not a Reserve. Leaving the line out keeps it undecided,
  // which is recoverable; posting a Reserve with no warehouse would fail the whole
  // confirmation and take the other lines down with it.
  if (reserve === null) return 'no_reserve_warehouse';
  if (!contribution.project_line_id) return 'no_mirror';

  return {
    project_line_id: contribution.project_line_id,
    timely_spo_qty: fromMinor(incoming),
    reserve,
    // Ladder v2 (section E rules 4/5): group borrow and cross-group borrow are now
    // AUTO-PROPOSED, so an approved-as-is line can carry one - posted verbatim, the same
    // way `reserve` is, UNLESS the SAVED decision typed its own reason over the engine's
    // (S1, fix round 2, reviewer): the approving Save (`BoardLineDecisionPanel`) already
    // carries `decision.borrow` with whatever the planner typed on a suggested row, and this
    // derivation used to rebuild straight off `contribution.sources` and drop it - the
    // reason it posted was always the engine's own sentence, however this Save actually read.
    borrow: borrowComponents(contribution, decision?.borrow ?? []),
    buy_qty: fromMinor(buy),
    buy_reason: buyReason,
    // Present only on the legacy single-number amendment, which is still an override.
    amend_reason: decision?.verdict === 'amended' ? decision.reason : undefined,
    // An approved or untouched line can still carry the flag (R10).
    suspected_system_issue: decision?.suspected_system_issue ?? false,
  };
}

/**
 * The engine's own auto-proposed borrows (group / cross-group, section E rules 4/5),
 * posted exactly as the proposal named them - donor, warehouse and quantity included. An
 * approved line never edits these, so there is nothing to re-derive: a source without an
 * addressable warehouse is dropped rather than posted as a guess, the same rule
 * `reserveWarehouses` follows.
 *
 * The REASON is the one field that can differ from the source: `decisionSaved`, when it names
 * the same row (warehouse + donor, `borrowReasonKeyOf` - the same key `matchesSuggestion`
 * compares by), is what the SAVED approval actually carries, and the engine's own sentence is
 * only the fallback for a row the save never touched (S1, fix round 2, reviewer).
 */
function borrowComponents(
  contribution: BoardContribution,
  decisionSaved: { warehouse_id?: string | null; donor_project_id?: string | null; reason: string }[],
): ConfirmBorrowComponent[] {
  const typedReasons = new Map(
    decisionSaved.map((row) => [borrowReasonKeyOf(row), row.reason]),
  );
  return contribution.sources
    .filter((source) => source.kind === 'borrow' && source.warehouse_id && toMinor(source.qty) > 0)
    .map((source) => ({
      source: 'other_location',
      warehouse_id: source.warehouse_id as string,
      donor_project_id: null,
      qty: source.qty,
      // `donor_project_id: null` here (not `source.donor_project_id`): the engine's own
      // auto-proposed borrow (group / cross-group) never names a donor PROJECT, only a
      // warehouse, and `suggestionDraftFrom`'s borrow rows agree - so every row an approving
      // Save can carry a typed reason for is keyed on `null` here too (nit, fix round 3 review).
      reason:
        typedReasons.get(
          borrowReasonKeyOf({ warehouse_id: source.warehouse_id, donor_project_id: null }),
        ) ?? source.reason,
      // The donor and the document, through the one spread every mapper in this chain uses
      // (`borrowPassThrough`): approved as it stands, the borrow still moves the placement
      // the engine named rather than being re-checked against free stock at a bin holding a
      // container that has not landed.
      ...borrowPassThrough(source),
    }));
}

/** An amendment composed in the editor, which replaces whatever was there before. */
function isAmendment(decision: BoardDecision | undefined): boolean {
  return decision?.verdict === 'amended';
}

/** The server's figure when it sent one, else the fallback. Absent is not zero. */
function numberOr(value: string | null | undefined, fallback: () => number): number {
  return value === null || value === undefined ? fallback() : toMinor(value);
}

function sumSources(sources: BoardSource[], kind: string): number {
  return sources
    .filter((source) => source.kind === kind)
    .reduce((total, source) => total + toMinor(source.qty), 0);
}

/**
 * The Reserve, one component per warehouse the engine drew on.
 *
 * A line can carry TWO reserve components at two different warehouses - its own location and
 * the dealer pool - and each has to be addressed by ITS OWN id. This is the part no display
 * string could have carried: the pill reads "BRW-BB", the payload needs two UUIDs, so the
 * warehouse is read off the SOURCE and never off the location label.
 *
 * An AMENDMENT is taken off the components in the order the engine proposed them, each capped
 * at what it proposed, and anything the planner adds beyond the whole proposal lands on the
 * first warehouse - the only one we can address it to. Whatever the Reserve does not cover is
 * Buy, computed by the caller, so a quantity a planner takes off a Reserve is never lost.
 *
 * Returns null when a quantity cannot be addressed to any warehouse at all, which the caller
 * treats as "leave this line out" rather than posting something the server must refuse.
 */
function reserveWarehouses(
  contribution: BoardContribution,
  reserveQty: number,
): ConfirmReserveComponent[] | null {
  if (reserveQty <= 0) return [];
  const proposed = contribution.sources.filter((source) => source.kind === 'reserve');
  // A reserve the server did not address is one we cannot post. Inventing a warehouse from the
  // location code would be guessing at an id, which is the one thing an id must never be.
  if (proposed.length === 0 || proposed.some((source) => !source.warehouse_id)) {
    const fallback = contribution.fulfilment_warehouse_id;
    if (proposed.length > 0 || !fallback) return null;
    return [{ warehouse_id: fallback, qty: fromMinor(reserveQty) }];
  }

  const components: ConfirmReserveComponent[] = [];
  let left = reserveQty;
  for (const source of proposed) {
    if (left <= 0) break;
    const take = Math.min(left, toMinor(source.qty));
    components.push({ warehouse_id: source.warehouse_id as string, qty: fromMinor(take) });
    left -= take;
  }
  if (left > 0 && components.length > 0) {
    // Amended ABOVE the whole proposal: the excess goes to the first warehouse, which is the
    // only one this side can name. The server rechecks the stock and refuses if it is not there.
    components[0] = {
      warehouse_id: components[0].warehouse_id,
      qty: fromMinor(toMinor(components[0].qty) + left),
    };
  }
  return components;
}

/**
 * The lines this confirmation cannot carry, and why, so the screen can say so and the count on
 * the button agrees with the notice beside it.
 *
 * Since the 8 Sep 2026 ruling (reverses R11) an untouched, uncovered line is never posted, so it
 * cannot be unpostable either - it is simply left on the board, undecided. What this walks now
 * is a SAVED decision (amend or approve) or a covered-and-decided line, left out for one of the
 * three reasons below. The caller says which is which - a line the planner composed is named.
 *
 * Adoption mirrored the order's open lines at the time it ran, so a later upload can add a core
 * line with no mirror: its order stays confirmable and that one contribution has no
 * `project_line_id`. Posting an invented id is refused outright, and silently dropping the row
 * would tell a planner they committed something they did not. The fix is a re-sync on the
 * sheet, which is somewhere else entirely - which is exactly why saying nothing would be wrong.
 * The other two reasons are fixed in the editor: a Reserve the board cannot address to a
 * warehouse, and a Buy of a discontinued product with no reason given.
 *
 * A REJECTED line is not counted: it was never going to post, so it is not a loss.
 */
export function unpostableDecidedFor(
  contributions: BoardContribution[],
  salesOrderId: string,
  draft: BoardDraft,
  /**
   * Whether the order HAS a planning record. On one that was simply never adopted every line
   * lacks a mirror, which is not this problem at all - the press adopts first and the mirrors
   * appear - so naming all of them would be eleven false alarms. The other reasons still name
   * their line: adoption fixes none of them.
   */
  isAdopted = true,
): UnpostableLine[] {
  const unpostable: UnpostableLine[] = [];
  for (const contribution of contributions) {
    if (contribution.sales_order_id !== salesOrderId) continue;
    const built = lineFor(contribution, draft[contribution.key]);
    if (typeof built !== 'string') continue;
    if (built === 'no_mirror' && !isAdopted) continue;
    unpostable.push({ contribution, reason: built });
  }
  return unpostable;
}

/**
 * The lines this press INTENDS to commit, whether or not their mirrors exist yet.
 *
 * On an adopted order this is the body's own length. On one that has not been adopted the body
 * cannot be built at all yet - `project_line_id` is null everywhere until adoption mirrors the
 * open lines - so the count comes from the verdicts instead, and the press adopts before it
 * builds anything. Without this the Confirm on a fresh order would read "Confirm 0 lines" and
 * be disabled, which is exactly the dead end the captain hit. A verdict the body could not
 * carry for any OTHER reason is not counted: adoption does not fix it, and the notice names it.
 */
export function plannedLineCount(
  contributions: BoardContribution[],
  salesOrderId: string,
  draft: BoardDraft,
  /**
   * Orders a PENDING planning-change batch currently covers (S4, fix round,
   * `PLAN-board-reject-on-confirmed-line.md`): a batch apply has no shape for a
   * withdrawal riding beside it, so the server refuses `rejected_line_ids` alongside
   * `batch_id` (AC-B12) and the caller sends `[]` for such an order instead
   * (`rejectedCoveredLineIdsFor`'s own result, zeroed). A covered-rejected line on one
   * of these orders must not count here either, or the counter promises a withdrawal
   * this press cannot actually carry out - the "Confirm (1)" that then posts nothing
   * for it. Empty by default: every OTHER caller (the panel's own per-decision toast,
   * every test that does not name a batch) is unaffected.
   */
  batchBlockedSalesOrderIds: ReadonlySet<string> = new Set(),
  options?: ConfirmScopeOptions,
): number {
  const batchBlocked = batchBlockedSalesOrderIds.has(salesOrderId);
  const unadopted = options?.unadoptedSalesOrderIds;
  return contributions.filter((contribution) => {
    if (contribution.sales_order_id !== salesOrderId) return false;
    if (options?.excludeKeys?.has(contribution.key)) return false;
    const role = pressRoleFor(contribution, draft[contribution.key], batchBlocked);
    if (role.kind === 'cancelled' || role.kind === 'withdraw' || role.kind === 'post') return true;
    // Only an order that is about to be adopted can mint the missing mirror.
    if (role.kind === 'unpostable' && role.reason === 'no_mirror') {
      return unadopted === undefined || unadopted.has(salesOrderId);
    }
    return false;
  }).length;
}

/**
 * What one press of Confirm would do over a given draft: "N to confirm - M rejected"
 * (D1/D3), extracted out of `FulfilmentBoardPanel`'s own `confirmSummary` so a decision the
 * planner has not yet seen re-rendered - S4's Save toast needs the count as of the draft it
 * JUST wrote, before React has re-rendered with it - can be read the same way the header
 * counter is, off a plain `draft` object rather than off component state.
 *
 * CONFIRM POSTS SAVED LINES ONLY (8 Sep 2026 ruling, reverses R11): an uncovered line nobody
 * has saved a decision for is undecided, not agreed, so the counter reports nothing for it -
 * "Save all suggested" is the bulk way to agree with the engine before Confirm. A REJECTED
 * line is a decision that commits nothing, counted apart rather than simply excluded - EXCEPT
 * a COVERED rejected line (owner ruling 23 Sep 2026, `PLAN-board-reject-on-confirmed-line.md`):
 * that one is a withdrawal Confirm actually carries out (`rejected_line_ids`), so it counts in
 * `toConfirm` too, beside `rejected`. A line an active decision already COVERS and nobody has
 * amended OR rejected is not counted: the server carries it into the next revision itself. A
 * SAVED-BUT-STALE line (S4, AC-4.4) is not counted either, the same reason `lineFor` will not
 * post it.
 */
export function confirmSummaryFor(
  contributions: BoardContribution[],
  draft: BoardDraft,
  /** Threaded straight through to `plannedLineCount` (S4, fix round) - see its own doc. */
  batchBlockedSalesOrderIds: ReadonlySet<string> = new Set(),
  options?: ConfirmScopeOptions,
): { toConfirm: number; rejected: number; orderCount: number; changed: number } {
  // N6 (code review round 3): `confirmed > rejected > stale > saved`, the same order
  // `BoardDecisionPill` reads by. A covered line's frozen composition is what the server
  // carries forward regardless of a local click, so it is checked FIRST - a click of
  // "rejected" on an already-confirmed line cannot make Confirm refuse it, and must not be
  // counted as a rejection either. REJECTED is now read BEFORE the covered/amended check
  // (23 Sep 2026 rework): a covered line's own rule below only ever exempted an UNTOUCHED or
  // AMENDED one, and a rejected covered line is neither - it is a THIRD thing Confirm acts on.
  let rejected = 0;
  // C4 (code review round 3 batch 2): a saved line the engine has re-suggested is dropped
  // from Confirm with no trace beyond the pill itself, which is easy to miss on a board of
  // forty lines - `changed` is the count the header and the Confirm dialog say it with.
  let changed = 0;
  const orderIds = new Set<string>();
  for (const contribution of contributions) {
    if (contribution.unplannable) continue;
    if (options?.excludeKeys?.has(contribution.key)) continue;
    // A CANCELLED line is decided BY THE BOOK (R3, scenario S5): the order it was on removed
    // it, and Confirm retires it. Nobody saves a decision for it, so the untouched-line skip
    // below would drop it from the count and the press would silently do one thing more than
    // it said - which is the one thing the counter exists to stop.
    if (contribution.cancelled) {
      orderIds.add(contribution.sales_order_id);
      continue;
    }
    const decision = draft[contribution.key];
    // 8 Sep 2026 ruling (reverses R11): an untouched, uncovered line is undecided, not
    // agreed - it must not pull its order into the confirmable set on its own. `decision`
    // is the caller's own draft, already merged with whatever the server sent as
    // `contribution.draft`, so its being falsy here is the whole signal that nobody saved it.
    if (!contribution.covered && !decision) continue;
    if (decision?.verdict === 'rejected') {
      rejected += 1;
      // An ACTIVELY covered reject (an active decision, not merely a live order-inquiry row
      // - N1, fix round, `PLAN-board-reject-on-confirmed-line.md`) is a WITHDRAWAL Confirm
      // carries out this same press - its order belongs in the confirmable set, same as an
      // amendment does, so `toConfirm` counts it (`plannedLineCount`'s own new branch is
      // what actually adds the +1 for this line). An UNCOVERED reject, or an inquiry-only
      // one with no active decision to withdraw, stays excluded, as it always was.
      if (contribution.decision) orderIds.add(contribution.sales_order_id);
      continue;
    }
    // An Approved draft on a covered line IS posted (`lineFor`), so it admits its order too.
    if (contribution.covered && decision?.verdict !== 'amended' && decision?.verdict !== 'approved') {
      continue;
    }
    if (contribution.draft?.stale) {
      changed += 1;
      continue;
    }
    orderIds.add(contribution.sales_order_id);
  }
  const toConfirm = [...orderIds].reduce(
    (total, salesOrderId) =>
      total +
      plannedLineCount(contributions, salesOrderId, draft, batchBlockedSalesOrderIds, options),
    0,
  );
  return { toConfirm, rejected, orderCount: orderIds.size, changed };
}

/**
 * Saved before this board was opened: at least a minute earlier, so a draft the planner just
 * saved (stamped a moment before the panel mounted) is not flagged as somebody's old work.
 */
function savedBeforeOpen(savedAt: string | null, openedAt: Date): boolean {
  if (!savedAt) return false;
  return openedAt.getTime() - parseDateTimeAsUTC(savedAt).getTime() >= 60_000;
}

/** How long ago a draft was saved, reading the server's naive-UTC stamp as UTC. */
export function savedAgoText(savedAt: string): string {
  return timeAgo(parseDateTimeAsUTC(savedAt));
}

/** One line the pre-confirm dialog lists: what this press will write for it. */
export interface ConfirmDialogRow {
  key: string;
  sales_order_id: string;
  so_number: string;
  line_no: number;
  item_code: string;
  verdict: 'approved' | 'amended' | 'rejected';
  composition: string;
  saved_by: string | null;
  saved_at: string | null;
  /** Saved by somebody other than the person pressing Confirm. */
  savedByOther: boolean;
  /** Saved before this board was opened. */
  savedBefore: boolean;
}

/** A saved line this press cannot post, with the reason the panel already words. */
export interface ConfirmDialogNotPosted {
  key: string;
  sales_order_id: string;
  so_number: string;
  line_no: number;
  item_code: string;
  reason: string;
}

const STALE_NOT_POSTED_REASON = 'Suggestion changed since it was saved; re-save it first.';
const WITHDRAWN_COMPOSITION = 'Withdrawn from the confirmation';

/**
 * The composition `lineFor` will post, in the words the board uses: `Reserve 20 BRW`,
 * `Borrow 43 + 57 from BRW-BB`, `Incoming 10`, `Buy 239`, joined with ' · '.
 */
function compositionText(
  line: ConfirmLine,
  contribution: BoardContribution,
  decision: BoardDecision,
): string {
  const codes = new Map<string, string>();
  const remember = (id: string | null | undefined, code: string | null | undefined) => {
    if (id && code && !codes.has(id)) codes.set(id, code);
  };
  for (const source of contribution.sources) remember(source.warehouse_id, source.location);
  for (const row of decision.reserve ?? []) remember(row.warehouse_id, row.location);
  for (const row of decision.borrow ?? []) remember(row.warehouse_id, row.warehouse_code);
  const codeOf = (id: string, own?: string | null) => own || codes.get(id) || '';

  const parts: string[] = [];
  for (const row of line.reserve) {
    if (toMinor(row.qty) <= 0) continue;
    parts.push(`Reserve ${row.qty} ${codeOf(row.warehouse_id, row.location)}`.trim());
  }
  const borrowByLocation = new Map<string, string[]>();
  for (const row of line.borrow) {
    if (toMinor(row.qty) <= 0) continue;
    const location = codeOf(row.warehouse_id, row.location);
    borrowByLocation.set(location, [...(borrowByLocation.get(location) ?? []), row.qty]);
  }
  for (const [location, quantities] of borrowByLocation) {
    const text = `Borrow ${quantities.join(' + ')}${location ? ` from ${location}` : ''}`;
    parts.push(decision.order_back ? `${text} (order-back)` : text);
  }
  if (toMinor(line.timely_spo_qty) > 0) parts.push(`Incoming ${line.timely_spo_qty}`);
  if (toMinor(line.buy_qty) > 0) parts.push(`Buy ${line.buy_qty}`);
  return parts.join(' \u00b7 ');
}

/** A cancelled line the press releases by the book: listed, not tickable. */
export interface ConfirmDialogCancelled {
  key: string;
  sales_order_id: string;
  so_number: string;
  line_no: number;
  item_code: string;
}

const BLOCKED_REJECT_REASON = 'staged rejection is held until the pending change is applied';

/**
 * What the pre-confirm dialog lists: one row per line this press will post (or withdraw), the
 * cancelled lines it releases, and the saved lines it cannot post, with why. Classified by the
 * SAME `pressRoleFor` the count reads, so list, count and body cannot disagree.
 */
export function confirmDialogRowsFor(
  contributions: BoardContribution[],
  draft: BoardDraft,
  context: {
    currentUserName: string | null | undefined;
    openedAt: Date;
    /** Orders with no planning record yet (see `ConfirmScopeOptions`). */
    unadoptedSalesOrderIds?: ReadonlySet<string>;
    /** Orders a pending planning change covers: a staged rejection there is held, not posted. */
    batchBlockedSalesOrderIds?: ReadonlySet<string>;
  },
): {
  rows: ConfirmDialogRow[];
  notPosted: ConfirmDialogNotPosted[];
  cancelled: ConfirmDialogCancelled[];
} {
  const rows: ConfirmDialogRow[] = [];
  const notPosted: ConfirmDialogNotPosted[] = [];
  const cancelled: ConfirmDialogCancelled[] = [];
  for (const contribution of contributions) {
    const decision = draft[contribution.key];
    const identity = {
      key: contribution.key,
      sales_order_id: contribution.sales_order_id,
      so_number: contribution.so_number,
      line_no: contribution.line_no,
      item_code: contribution.item_code,
    };
    const savedBy = contribution.draft?.saved_by ?? null;
    const savedAt = contribution.draft?.saved_at ?? null;
    const rowFor = (verdict: ConfirmDialogRow['verdict'], composition: string) => ({
      ...identity,
      verdict,
      composition,
      saved_by: savedBy,
      saved_at: savedAt,
      // No known current user means nobody can be "somebody else".
      savedByOther:
        !!savedBy && !!context.currentUserName && savedBy !== context.currentUserName,
      // The server sends naive UTC (no Z); `new Date()` would read it as local time.
      savedBefore: savedBeforeOpen(savedAt, context.openedAt),
    });
    const adoptedLater =
      !contribution.project_line_id &&
      context.unadoptedSalesOrderIds?.has(contribution.sales_order_id);
    const role = pressRoleFor(
      adoptedLater ? { ...contribution, project_line_id: 'pending-adoption' } : contribution,
      decision,
      context.batchBlockedSalesOrderIds?.has(contribution.sales_order_id) ?? false,
    );
    switch (role.kind) {
      case 'cancelled':
        cancelled.push(identity);
        break;
      case 'withdraw':
        rows.push(rowFor('rejected', WITHDRAWN_COMPOSITION));
        break;
      case 'blocked_reject':
        notPosted.push({ ...identity, reason: BLOCKED_REJECT_REASON });
        break;
      case 'stale':
        notPosted.push({ ...identity, reason: STALE_NOT_POSTED_REASON });
        break;
      case 'unpostable': {
        const [notice] = unpostableNotices(role.reason, [
          { contribution, reason: role.reason },
        ]);
        notPosted.push({ ...identity, reason: notice?.clause ?? role.reason });
        break;
      }
      case 'post':
        if (decision && decision.verdict !== 'rejected') {
          rows.push(rowFor(decision.verdict, compositionText(role.line, contribution, decision)));
        }
        break;
      default:
        break;
    }
  }
  return { rows, notPosted, cancelled };
}

/**
 * Whether an amend needs a reason, read over the WHOLE composition.
 *
 * Moving stock the rule proposed takes it away from this line, or from somebody else, and
 * hands it to nobody in particular, so a person has to say why. Accepting the proposal
 * unchanged does not: demanding a reason for agreeing is how a mandatory field becomes a
 * rubber stamp.
 *
 * It looked at the Reserve alone while the Reserve was all a board amendment could change.
 * Now that the editor composes all four kinds, a planner could take 40 out of the Reserve and
 * put 40 into a Borrow - displacing the rule completely - and be asked for nothing.
 *
 * The BASELINE is what the composition is compared against, and it differs by line. On an
 * undecided line it is the engine's proposal, on which a Borrow of any size is an override,
 * because the engine proposes none. On a line an active decision COVERS it is the frozen
 * composition itself: a Borrow decided on the sheet is already the decision, and demanding a
 * reason to re-save it unchanged is the rubber stamp again from the other side.
 */
export function amendNeedsReason(
  contribution: BoardContribution,
  composition: AmendComposition,
): boolean {
  const frozen = contribution.covered ? contribution.decision : null;
  const baseline: AmendComposition = frozen
    ? {
        timely_spo_qty: frozen.timely_spo_qty,
        reserve: frozen.reserve.map((row) => ({ qty: row.qty, warehouse_id: row.warehouse_id })),
        borrow: frozen.borrow.map((row) => ({
          qty: row.qty,
          warehouse_id: row.warehouse_id ?? null,
          donor_project_id: row.donor_project_id ?? null,
        })),
        buy_qty: frozen.buy_qty,
      }
    : proposalBaseline(contribution);

  return (
    !sameRows(baseline.reserve, composition.reserve, (row) => row.warehouse_id ?? '') ||
    toMinor(composition.timely_spo_qty) !== toMinor(baseline.timely_spo_qty) ||
    toMinor(composition.buy_qty) !== toMinor(baseline.buy_qty) ||
    // The same key `borrowComponents` and `suggestionWithReasons` (`boardAmend.ts`) match a
    // typed reason by - one function, so the three can never quietly disagree about which
    // row is which (nit, fix round 2 review).
    !sameRows(baseline.borrow, composition.borrow, borrowReasonKeyOf)
  );
}

/**
 * Whether the composition carries a Borrow the engine itself never proposed (D3, S2): the
 * server requires a reason on every borrow (`_check_borrow`), so the one Reason box is
 * required whenever one of these is present, independent of whether the rest of the line still
 * matches its baseline (a re-opened, unedited amendment that already borrowed by hand is still
 * "a borrow row the engine did not suggest").
 *
 * Compared against the SUGGESTION baseline (never the frozen one): a covered line's own frozen
 * borrow is what the planner already decided, and `amendNeedsReason`'s comparison against it is
 * the rule that asks for a fresh reason there, not this one.
 */
export function hasUnsuggestedBorrow(
  contribution: BoardContribution,
  borrow: { qty: string; warehouse_id?: string | null; donor_project_id?: string | null }[],
): boolean {
  const suggested = new Set(
    suggestionBaseline(contribution).borrow.map((row) => borrowReasonKeyOf(row)),
  );
  return borrow.some(
    (row) => toMinor(row.qty) > 0 && !suggested.has(borrowReasonKeyOf(row)),
  );
}

/** The four kinds as the editor holds them; the ids are what makes "the same" mean something. */
interface AmendComposition {
  timely_spo_qty: string;
  reserve: { qty: string; warehouse_id?: string | null }[];
  borrow: { qty: string; warehouse_id?: string | null; donor_project_id?: string | null }[];
  buy_qty: string;
}

/**
 * The engine's proposal as a composition: the Reserve per warehouse off the sources (the
 * server's total when it addressed none of them), the incoming cover, the Buy, and no Borrow.
 */
function proposalBaseline(contribution: BoardContribution): AmendComposition {
  const reserve = contribution.sources
    .filter((source) => source.kind === 'reserve')
    .map((source) => ({ qty: source.qty, warehouse_id: source.warehouse_id ?? null }));
  const statedReserve = numberOr(contribution.qty_proposed_reserve, () =>
    sumSources(contribution.sources, 'reserve'),
  );
  return {
    timely_spo_qty: fromMinor(
      numberOr(contribution.qty_proposed_incoming, () => sumSources(contribution.sources, 'timely_spo')),
    ),
    reserve:
      reserve.length > 0 || statedReserve === 0
        ? reserve
        : [{ qty: fromMinor(statedReserve), warehouse_id: null }],
    borrow: [],
    buy_qty: fromMinor(numberOr(contribution.qty_proposed_buy, () => sumSources(contribution.sources, 'buy'))),
  };
}

/**
 * Whether what is in the editor IS the engine's suggestion, which is what tells the one Save
 * button which verdict it is taking (the captain, 28 August: "if the suggestion is same as
 * decision then it is approved, if suggestion different from decision then it is amended, so I
 * just click on 1 button").
 *
 * It is NOT `!amendNeedsReason(...)`. That one asks whether the composition OVERRIDES what the
 * line already stands on, so on a covered line it compares against the FROZEN decision - and a
 * planner typing the engine's numbers back onto a covered line is approving the suggestion,
 * which that comparison would call an amendment.
 *
 * Compared the same way, per warehouse for a Reserve and per warehouse-and-donor for a Borrow,
 * because 9 at the pool is not the same answer as 9 at the own location.
 */
export function matchesSuggestion(
  contribution: BoardContribution,
  composition: AmendComposition,
): boolean {
  const baseline = suggestionBaseline(contribution);
  return (
    sameRows(baseline.reserve, composition.reserve, (row) => row.warehouse_id ?? '') &&
    toMinor(composition.timely_spo_qty) === toMinor(baseline.timely_spo_qty) &&
    toMinor(composition.buy_qty) === toMinor(baseline.buy_qty) &&
    // The SAME key `borrowComponents`/`suggestionWithReasons` (`boardAmend.ts`) match a typed
    // reason by (nit, fix round 2 review): this is the comparison their own doc comments
    // claim it is, not a second literal that could quietly drift from it.
    sameRows(baseline.borrow, composition.borrow, borrowReasonKeyOf)
  );
}

/**
 * The engine's suggestion as a composition, seeded exactly as the editor seeds its opening
 * draft from it (`suggestionDraftFrom`), or the two would disagree about an untouched form.
 *
 * ON A COVERED LINE THE FROZEN PROPOSAL IS THE SUGGESTION: `sources` and `qty_proposed_*`
 * state what was DECIDED there, so `proposalBaseline` would compare the draft against the
 * decision and never once report the suggestion. A revision written before the proposal was
 * frozen records none, and then those fields are all the board holds for the line.
 *
 * The Borrow is read off the sources rather than left empty the way `proposalBaseline` leaves
 * it: a borrow the engine composed itself (the cross-group rung) is part of the suggestion,
 * and the editor opens holding it, so a form nobody touched has to read as the suggestion.
 */
function suggestionBaseline(contribution: BoardContribution): AmendComposition {
  const frozen = contribution.covered ? contribution.proposed?.components : null;
  const borrow = (frozen ?? contribution.sources)
    .filter((source) => source.kind === 'borrow')
    .map((source) => ({
      qty: source.qty,
      warehouse_id: source.warehouse_id ?? null,
      // The editor's own borrow rows seeded from sources name no donor project either.
      donor_project_id: null,
    }));
  if (!frozen) return { ...proposalBaseline(contribution), borrow };
  return {
    timely_spo_qty: fromMinor(sumSources(frozen, 'timely_spo')),
    reserve: frozen
      .filter((source) => source.kind === 'reserve')
      .map((source) => ({ qty: source.qty, warehouse_id: source.warehouse_id ?? null })),
    borrow,
    buy_qty: fromMinor(sumSources(frozen, 'buy')),
  };
}

/**
 * Whether two lists of quantities are the same composition. Compared per key when every row on
 * both sides carries one, so 40 at the pool is not "the same" as 40 at the own location; by
 * total otherwise, which is all an unaddressed row can be compared on. A zero row is nobody's.
 */
function sameRows<T extends { qty: string }>(
  left: T[],
  right: T[],
  keyOf: (row: T) => string,
): boolean {
  const kept = (rows: T[]) => rows.filter((row) => toMinor(row.qty) !== 0);
  const a = kept(left);
  const b = kept(right);
  const addressed = [...a, ...b].every((row) => keyOf(row) !== '' && keyOf(row) !== '|');
  if (!addressed) {
    return sumMinor(a) === sumMinor(b);
  }
  const byKey = (rows: T[]) => {
    const totals = new Map<string, number>();
    for (const row of rows) {
      totals.set(keyOf(row), (totals.get(keyOf(row)) ?? 0) + toMinor(row.qty));
    }
    return totals;
  };
  const mine = byKey(a);
  const theirs = byKey(b);
  if (mine.size !== theirs.size) return false;
  for (const [key, qty] of mine) {
    if (theirs.get(key) !== qty) return false;
  }
  return true;
}

function sumMinor(rows: { qty: string }[]): number {
  return rows.reduce((total, row) => total + toMinor(row.qty), 0);
}


/**
 * How one contribution is keyed and labelled on each row axis.
 *
 * The KEY is an id wherever an id exists and is never rendered; the LABEL is what the reader
 * sees. Keeping them apart is the whole reason two customers with one name stay two rows - a
 * board that merged them would show a single row totalling two companies' demand, and nothing
 * on screen would say so.
 *
 * Where the server sends no id the label becomes the key, which IS that merge. It is a stated
 * compromise for a payload that has not caught up, not the design: `customer_id` and
 * `project_id` are what this is written against.
 */
function axisKeyOf(
  contribution: BoardContribution,
  axis: BoardRowAxis,
): { key: string; label: string } {
  if (axis === 'sales_order') {
    return { key: contribution.sales_order_id, label: contribution.so_number };
  }
  if (axis === 'customer') {
    const label = contribution.customer_name || 'Customer not recorded';
    return { key: contribution.customer_id || `name:${label}`, label };
  }
  if (axis === 'project') {
    const label = contribution.project_label || 'Not named on the order';
    // The server's normalised key, which is a STRING because an adopted order has no project
    // registration: the project string on the order is its identity. Grouping on the raw label
    // would merge two spellings of one project and split one project written two ways.
    return { key: contribution.project_key || `name:${label}`, label };
  }
  return { key: contribution.item_code, label: contribution.item_code };
}

/**
 * The board's rows and cells for one axis, out of the contributions already on hand.
 *
 * A pivot is a different GROUPING of the same lines, never a second fetch and never a second
 * idea of what a line is - which is why a decision made under one axis is still that line's
 * decision under another.
 *
 * The per-cell counts are summed from per-line facts the SERVER stated (`is_past`,
 * `unplannable`, `contested`), never re-decided here. The stock position is left EMPTY on
 * purpose: on-hand and free are facts about one product at one location, and a cell holding
 * three products has no single stock position to state.
 */
export function boardAxis(
  axis: BoardRowAxis,
  // The SERVER's cells, not a flat list of contributions: the bucket a line sits in is a fact
  // about the cell, so flattening first would lose the date and lump a year into one column.
  cells: BoardCell[],
): { rows: BoardAxisRow[]; cells: BoardCell[] } {
  const rows = new Map<string, BoardAxisRow>();
  const grouped = new Map<string, { row: BoardAxisRow; bucket: string; lines: BoardContribution[] }>();

  for (const cell of cells) {
    for (const contribution of cell.contributions) {
      const { key, label } = axisKeyOf(contribution, axis);
      if (!rows.has(key)) rows.set(key, { key, label });
      const cellKey = `${key}|${cell.bucket_key}`;
      const existing = grouped.get(cellKey);
      if (existing) existing.lines.push(contribution);
      else {
        grouped.set(cellKey, {
          row: rows.get(key) as BoardAxisRow,
          bucket: cell.bucket_key,
          lines: [contribution],
        });
      }
    }
  }

  const pivoted: BoardCell[] = [...grouped.values()].map((entry) => ({
    // Labelled by what the reader sees, keyed by what cannot collide.
    item_code: entry.row.label,
    row_key: entry.row.key,
    bucket_key: entry.bucket,
    // What this cell ASKS FOR, which is the plan quantity per line - the same figure the
    // server totals a cell by. Summing `qty_outstanding` made a cell holding a delivered
    // line read lower than the lines inside it.
    total_qty: fromMinor(
      entry.lines.reduce((total, line) => total + toMinor(line.qty), 0),
    ),
    locations: [],
    contributions: entry.lines,
    unplannable_count: entry.lines.filter((line) => line.unplannable).length,
    contested_count: entry.lines.filter((line) => line.contested).length,
    past_count: entry.lines.filter((line) => line.is_past).length,
  }));

  return { rows: [...rows.values()].sort(byLabel), cells: pivoted };
}

/** Sales-order numbers, customer names and project labels all read best in their own order. */
function byLabel(left: BoardAxisRow, right: BoardAxisRow): number {
  return left.label.localeCompare(right.label);
}

/**
 * Whether a row survives the board's search box.
 *
 * The captain asked for all four: "i also need sales order search, project search, customer
 * search". A ROW stays when ANY of its lines matches ANY of them, and the cells in that row
 * keep ALL their contributions - filtering inside a cell would print a total that is not the
 * cell's, which is the same rule that keeps the selection totals still under a filter.
 */
/**
 * The ONE matcher a search box on this board reads (S6, PLAN-scm-oi-worklist-excel-
 * parity.md R-J): SO number, customer, agent code, item code - the same fields whichever
 * of the grid's row filter or the list view's own row filter is asking. Sharing this
 * function is what keeps "one search box drives Grid and List alike" true: a field added
 * here reaches both without either one drifting out of step with the other.
 */
export function contributionMatchesSearch(
  contribution: BoardContribution,
  search: string,
): boolean {
  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  return [
    contribution.so_number,
    contribution.customer_name,
    contribution.project_label,
    contribution.agent_code,
    contribution.item_code,
  ].some((field) => (field ?? '').toLowerCase().includes(needle));
}

export function rowMatchesSearch(
  row: BoardAxisRow,
  contributions: BoardContribution[],
  search: string,
): boolean {
  const needle = search.trim().toLowerCase();
  if (!needle) return true;
  if (
    row.label.toLowerCase().includes(needle) ||
    (row.description ?? '').toLowerCase().includes(needle)
  ) {
    return true;
  }
  return contributions.some((contribution) => contributionMatchesSearch(contribution, search));
}


/**
 * What a cell says about its own ranking, from ONE place.
 *
 * "The active policy separates none of these rows" is TRUE whenever nothing separated them, but
 * under the fair policy the usual cause is not the policy at all: one sales order's lines in one
 * week share their required date, their document date and their payment terms, so of course
 * they tie. Reading that as a policy failure sent people looking for a broken weighting.
 *
 * So the sentence is chosen by what actually happened, and it lives here rather than in the two
 * components that show it, because a cell reading one thing while the banner reads another is
 * how two explanations of one fact appear.
 */
export function rankingNote(
  cell: Pick<BoardCell, 'contributions' | 'distinct_order_count' | 'rank_separates'>,
): { cell: string; note: string | null } | null {
  if (cell.rank_separates) return null;
  // A cell that states neither flag was not ranked as one queue: a pivoted cell spans several
  // piles, so it has no single ranking sentence and each line keeps its own score.
  if (cell.rank_separates === undefined && cell.distinct_order_count === undefined) return null;
  // ONE line in the cell still reads "Not ranked" in the Rank column - a flat 0.00 there would
  // claim a ranking nothing computed - but it carries NO sentence (25 August 2026). "Only line
  // in this cell" restated the single row the reader was already looking at, and a sentence
  // that repeats the screen is one more thing to read past on the way to the decision.
  if (cell.contributions.length === 1) return { cell: 'Not ranked', note: null };
  // LADDER V4 (26 August 2026): "Same sales order; line order decided which line was served
  // first" is retired. It was true while the rank queue decided how much a line could take at
  // its own location; availability is the ownership GROUP's now, and rank decides the order
  // lines are served in, not what exists - so the sentence claimed a cause the engine no
  // longer has. Lines of one sales order that tie carry no note at all.
  const note =
    (cell.distinct_order_count ?? 0) === 1
      ? null
      : 'The active policy separates none of these rows';
  return { cell: 'Not ranked', note };
}

/**
 * The LIST view's own row order (S4, PLAN-so-lines-autocount-order.md, fix round #1076 -
 * owner ruling, 21 Sep 2026): sales order, then the line number AutoCount itself sent.
 *
 * SUPERSEDES the list borrowing the grid's product axis (`orderByProductRows`, retired -
 * fix round #1076 delta review: it had no production caller left once the list moved
 * here, and its own unit coverage moved to the panel level -
 * `FulfilmentBoardPanel.test.tsx`'s "AC-S4-1 (panel)" describe block). The GRID's own axis
 * was never built from that function either - it is `boardAxis` over the server's
 * `productRows`, unrelated to either list ordering - so this change moves the list alone:
 * a planner comparing it against the source document reads it top to bottom the way
 * AutoCount does, while the grid keeps its own product-by-product axis for the
 * cross-order view. The two are no longer position-aligned and the toggle genuinely shows
 * two different readings - the owner's call, not a defect.
 *
 * No `productRows` argument: the product axis plays no part in this ordering at all, so a
 * caller cannot accidentally influence it with the grid's own row order.
 *
 * `so_number` ascending, then `line_no` ascending - numeric, never lexicographic, and a
 * line AutoCount never numbered (null/undefined) sorts LAST within its own sales order -
 * then `item_code`, then `required_date` (undated last) only where even the item code
 * ties.
 */
export function orderListRows<
  T extends {
    so_number?: string;
    line_no?: number;
    item_code: string;
    required_date?: string | null;
  },
>(contributions: readonly T[]): T[] {
  return [...contributions].sort((a, b) => {
    const byOrder = (a.so_number ?? '').localeCompare(b.so_number ?? '');
    if (byOrder !== 0) return byOrder;
    const aNumbered = a.line_no !== null && a.line_no !== undefined;
    const bNumbered = b.line_no !== null && b.line_no !== undefined;
    if (aNumbered !== bNumbered) return aNumbered ? -1 : 1;
    if (aNumbered && bNumbered && a.line_no !== b.line_no) {
      return (a.line_no as number) - (b.line_no as number);
    }
    if (a.item_code !== b.item_code) return a.item_code.localeCompare(b.item_code);
    return (a.required_date ?? '9999-12-31').localeCompare(b.required_date ?? '9999-12-31');
  });
}

/**
 * #1362 item 5 (owner, 29 Sep 2026): the number a person reads for a sales-order line is
 * AutoCount's own line number, the "No." the order's Lines tab shows - never the planning
 * row index. "Line 2912" when AutoCount numbered the line; "row 110" (the planning position)
 * only when it did not, so the two numberings are never printed under one name.
 */
export function soLineLabel(line: {
  so_line_no?: number | null;
  line_no?: number | null;
}): string {
  const text = soLineNoText(line);
  if (!text) return 'Line';
  return `Line ${text}`;
}

/**
 * The Line column's own cell: AutoCount's line number ("2912") when the core line carries one,
 * else the positional line number, printed plainly. `soLineNoIsSynced` says which of the two it is,
 * so the cell can carry the "not synced" title.
 */
export function soLineNoText(line: {
  so_line_no?: number | null;
  line_no?: number | null;
}): string {
  if (line.so_line_no !== null && line.so_line_no !== undefined) return String(line.so_line_no);
  if (line.line_no === null || line.line_no === undefined) return '';
  return String(line.line_no);
}

/** False when AutoCount gave the line no number (`so_line_no: null`) and the position is shown. */
export function soLineNoIsSynced(line: { so_line_no?: number | null }): boolean {
  return line.so_line_no != null;
}

/**
 * One refused line of a Confirm, as the board lists it: "Line 2912, B2154-NL: <reason>"
 * (#1362 item 5, AutoCount's number), the bare reason when it is about the whole order.
 */
export function failingLineText(line: {
  so_line_no?: number | null;
  line_no?: number | null;
  item_code?: string | null;
  reason: string;
}): string {
  if (!line.line_no && line.so_line_no == null) return line.reason;
  return `${soLineLabel(line)}${line.item_code ? `, ${line.item_code}` : ''}: ${line.reason}`;
}

/**
 * #1362 (owner, 29 Sep 2026: "i think we are too restrictive already"): what a CONFIRMED
 * order's result still has to tell the planner, one line each. A line held back by the
 * recheck ("row 29, B2154-NL held back, decision kept: <reason>") and a Buy kept over goods
 * that landed for its line (the notice purchasing also reads on the Buy's inquiry row).
 */
export function confirmNoticeLines(result: {
  lines_held_back?: Array<{
    so_line_no?: number | null;
    line_no?: number | null;
    item_code?: string | null;
    reason: string;
  }> | null;
  landed_buy_notices?: Array<{
    so_line_no?: number | null;
    line_no?: number | null;
    item_code?: string | null;
    reason: string;
  }> | null;
}): string[] {
  const held = (result.lines_held_back ?? []).map((line) =>
    failingLineText({ ...line, reason: `held back, decision kept: ${line.reason}` }),
  );
  const landed = (result.landed_buy_notices ?? []).map((line) => failingLineText(line));
  return [...held, ...landed];
}
