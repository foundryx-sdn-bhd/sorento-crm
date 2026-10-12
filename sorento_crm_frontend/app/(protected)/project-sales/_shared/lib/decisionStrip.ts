/**
 * The board's DECISION STRIP: per kind of supply, what was suggested and what was decided
 * across the whole selection (PLAN-scm-cs-planning-uat.md section 3.D, AC-D2).
 *
 * The captain asked for "one page that shows, per line, what was SUGGESTED (buy / own /
 * shared / borrow) and what was DECIDED, in the same words", and ruled that the page is the
 * fulfilment-planning board, with cards.
 *
 * TWO FIGURES, NOT A FIGURE AND A DELTA. A kind the engine never suggested and the planner
 * decided anyway reads Suggested 0 beside Decided 71, and that pair - not the difference
 * between them - is the thing worth seeing: it says the plan on screen is not the plan the
 * engine produced, and names which way it moved.
 *
 * The words, the colours and the reading order all come from `supplyVocabulary`. Nothing here
 * decides what a kind is called or what colour it is: this file is arithmetic.
 */
import {
  contributionDecision,
  contributionSuggestion,
  rowOf,
} from './supplyVocabulary';
import type { SupplyKind, SupplyPart } from './supplyVocabulary';
import { fromMinor, toMinor } from './supplyComposition';
import type {
  BoardCell,
  BoardContribution,
  BoardDecision,
  BoardDraft,
} from '../types/fulfilmentPlanning.types';

/** One card of the strip. */
export interface DecisionStripTotal {
  kind: SupplyKind;
  /** What the engine proposed, summed over the selection. A decimal string, like every
   * quantity here: a float round trip loses the tail of a quantity somebody signed for. */
  suggested: string;
  /** What was decided - confirmed, or ticked into this session's draft. */
  decided: string;
  /** The two disagree. What the amber mark on the card is drawn from. */
  changed: boolean;
}

/**
 * The strip's FOUR cards, in the bulk Decide menu's order (owner, 12 Oct 2026,
 * `PLAN-plan-four-options-12oct.md`): Use own location, Borrow, Use BRW, Buy.
 *
 * The planner no longer suggests the other kinds, but a decision frozen before that, or a
 * saved draft, still carries them. Display only: every borrow counts under Borrow, and old
 * water (`incoming`) counts on no card. `rowOf` itself is untouched, so a line's own bar
 * and legend still say what the stored composition is.
 */
export const STRIP_ORDER: SupplyKind[] = ['own', 'borrow_other', 'shared', 'buy'];

function stripKindOf(kind: SupplyKind | null): SupplyKind | null {
  switch (kind) {
    case 'borrow_order':
    case 'borrow_incoming':
    case 'borrow_other':
      return 'borrow_other';
    case 'incoming':
      return null;
    default:
      return kind;
  }
}

/** Sum a composition into `{kind: minor units}`, resolved against the line's own location. */
function byKind(
  parts: SupplyPart[] | null,
  ownLocation: string | null | undefined,
  into: Map<SupplyKind, number>,
): void {
  for (const part of parts ?? []) {
    const kind = stripKindOf(rowOf(part, ownLocation));
    if (!kind) continue;
    into.set(kind, (into.get(kind) ?? 0) + toMinor(part.qty));
  }
}

/**
 * The whole strip, over the contributing lines it is handed.
 *
 * THE RULE, and the caller owes it: the strip sums exactly the contributions THE CURRENT VIEW
 * CAN SHOW, and `contributionCarriesKind` filters that same population. The two must not come
 * apart. They did: the totals were summed over the whole selection while the grid was filtered
 * over its cells, which at day granularity are a 30-day window - so a card could read
 * "Shared 71" off lines three months out and pressing it emptied the board. A figure the view
 * in front of you cannot produce is worse than no figure, because it is acted on.
 *
 * So the grid passes the contributions of the cells it renders, and the list passes its own
 * rows (the whole selection, which is what a list view is for). The window is still a window;
 * what changes is that the strip now describes the board being looked at rather than a
 * different, larger one.
 *
 * Resolved PER LINE, because two lines of one board need not share an ownership group:
 * `DC1-BB` is the agent's own group on a `BRW-BB` line and somebody else's on a `BRW-IB` one.
 *
 * Every kind in `ORDER` gets a card whether or not it has a quantity. What makes two boards
 * comparable is that Buy is always in the same place; a card that came and went would move
 * every card beside it, and a strip is read by glancing at a position.
 */
export function decisionStripTotals(
  contributions: BoardContribution[],
  draft: BoardDraft,
): DecisionStripTotal[] {
  const suggested = new Map<SupplyKind, number>();
  const decided = new Map<SupplyKind, number>();
  for (const contribution of contributions) {
    const where = contribution.fulfilment_location;
    byKind(contributionSuggestion(contribution), where, suggested);
    byKind(
      contributionDecision(contribution, draft[contribution.key] ?? null),
      where,
      decided,
    );
  }
  return STRIP_ORDER.map((kind) => {
    const left = suggested.get(kind) ?? 0;
    const right = decided.get(kind) ?? 0;
    return {
      kind,
      suggested: fromMinor(left),
      decided: fromMinor(right),
      changed: left !== right,
    };
  });
}

/**
 * Does this LINE carry that kind of supply on EITHER side?
 *
 * Either, deliberately: filtering by Buy has to keep the line the planner has just amended
 * OFF Buy, or the card they pressed empties itself under them as they work.
 *
 * The unit is the CONTRIBUTION, not the cell, because that is the unit `decisionStripTotals`
 * counts - and the grid and the list disagree about what a row is while agreeing exactly
 * about what a line is. `cellCarriesKind` is this, asked of a cell's lines.
 */
export function contributionCarriesKind(
  contribution: BoardContribution,
  drafted: BoardDecision | null,
  kind: SupplyKind,
): boolean {
  const where = contribution.fulfilment_location;
  const sides = [
    contributionSuggestion(contribution),
    contributionDecision(contribution, drafted),
  ];
  for (const parts of sides) {
    for (const part of parts ?? []) {
      if (stripKindOf(rowOf(part, where)) !== kind) continue;
      if (toMinor(part.qty) !== 0) return true;
    }
  }
  return false;
}

/** A cell stays when ANY of its lines carries the kind. */
export function cellCarriesKind(
  cell: Pick<BoardCell, 'contributions'>,
  draft: Record<string, BoardDecision>,
  kind: SupplyKind,
): boolean {
  return cell.contributions.some((contribution) =>
    contributionCarriesKind(contribution, draft[contribution.key] ?? null, kind),
  );
}

/**
 * How many LINES purchasing has refused across the selection (AC-RB2).
 *
 * A refusal bounces the line back to CS, and until this it showed on the Order Inquiries
 * page alone: CS found out by going to look. The strip carries the count so the bounce is
 * visible from the board they already work on.
 *
 * Counted per LINE, keyed by the contribution's own draft key, because one line can appear
 * in one cell only and a cell routinely holds several lines - a per-cell count would say
 * "1 rejected" over a cell holding two refusals. The flag itself is the board's own
 * (`order_inquiry.ack_state`), which the backend already clears once CS decides the line
 * again, so this drops to zero the moment the refusal is answered.
 */
export function rejectedLineCount(contributions: BoardContribution[]): number {
  const lines = new Set<string>();
  for (const contribution of contributions) {
    if (contribution.order_inquiry?.ack_state !== 'rejected') continue;
    lines.add(contribution.key);
  }
  return lines.size;
}
