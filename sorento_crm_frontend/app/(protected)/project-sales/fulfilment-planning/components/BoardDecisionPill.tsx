'use client';

import { AlertTriangle } from 'lucide-react';
import { STATUS_PILL_BASE } from '@/lib/status-pill';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import {
  Popover,
  PopoverContent,
  PopoverPortal,
  PopoverTrigger,
} from '@/components/ui/popover';
import type {
  BoardContribution,
  BoardDecision,
} from '../../_shared/types/fulfilmentPlanning.types';

/**
 * How far one line has got, in one word (R6).
 *
 * ONE pill for both readings of the board - the cell breakdown's contributing-lines table and
 * the list view - because they are two views of one draft and a reader toggles between them
 * to find the same line. Two pills that merely resembled each other would drift the first
 * time either changed.
 *
 * STATUS ONLY. It used to print the whole composition ("Reserve 20 BRW-BB · Borrow 10 · Buy
 * 13") and, on a confirmed line, the revision number as well - a sentence in a fixed-width
 * cell, truncated at the term that mattered. The composition is in the expanded row and on
 * the Sourced from column beside it; what the cell is scanned for is how far the line has got.
 *
 * SAVED, NOT APPROVED/AMENDED (S4, R-F, markup 2 Sep): once a decision has been SAVED - here,
 * on another device, or by another planner - the pill reads the plain word "Saved" rather
 * than which of the two verbs produced it. Approved vs amended is a fact for the expanded
 * row, which already shows the composition; the cell answers "has this been dealt with",
 * and "Saved" answers it in one word the same way "Suggested" and "Confirmed" do. A
 * REJECTED decision keeps its own label - it commits nothing, and "Saved" would say the
 * opposite of what happened to it.
 */
const VERDICT_PILL: Record<string, string> = {
  // Nobody has said anything about this line yet. Since the 8 Sep 2026 ruling (reverses
  // R11) that is undecided, not agreement: Confirm leaves it alone until it is saved. Grey
  // because it is the state a board opens in, not an outcome.
  suggested: 'bg-muted text-muted-foreground',
  saved: 'bg-emerald-100 text-emerald-800',
  rejected: 'bg-red-100 text-red-800',
  // Outlined rather than filled: it is in the DATABASE, not a verdict given on this board,
  // and a solid green beside a solid green approval said the two were the same thing.
  confirmed: 'border border-emerald-400 text-emerald-700',
  // PLAN-board-change-proposed-pill (owner ruling 18 Sep 2026): the board's OWN pre-mark,
  // nothing written yet. Outlined amber - the change-hazard family the rest of the board
  // already uses (see `stale` below) - never the solid green `saved` wears, which read as an
  // autosave over a line the book had just moved (SO421287 line 15, 18 Sep 2026).
  change_proposed: 'border border-amber-400 text-amber-800',
  // S4/AC-4.4: the line was saved against a suggestion the engine no longer makes. Amber,
  // the same warning tone the rest of the board uses for "look at this before you trust it".
  stale: 'bg-amber-100 text-amber-800',
  // R3, scenario S5: the BOOK removed this line. Slate rather than red - nothing went wrong,
  // the customer simply does not want it - and not the grey "Suggested" wears, or the two
  // states a planner has to tell apart would look the same.
  cancelled: 'bg-slate-200 text-slate-700',
};

const VERDICT_LABEL: Record<string, string> = {
  suggested: 'Suggested',
  saved: 'Saved',
  rejected: 'Rejected',
  // NO REVISION NUMBER (R6). "Confirmed rev 3" told a planner the record had been written
  // three times, which is not a question anybody asks of this column.
  confirmed: 'Confirmed',
  stale: 'Suggestion changed',
  cancelled: 'Cancelled',
  change_proposed: 'Change proposed',
};

/**
 * The states the list's Status filter offers, in the order an owner reads them: the four the
 * planner works with first, then the rest so nothing becomes unfilterable.
 */
export const VERDICT_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'saved', label: VERDICT_LABEL.saved },
  { value: 'suggested', label: VERDICT_LABEL.suggested },
  { value: 'confirmed', label: VERDICT_LABEL.confirmed },
  { value: 'change_proposed', label: VERDICT_LABEL.change_proposed },
  { value: 'rejected', label: VERDICT_LABEL.rejected },
  { value: 'stale', label: VERDICT_LABEL.stale },
  { value: 'cancelled', label: VERDICT_LABEL.cancelled },
  { value: 'unplannable', label: 'Needs a location' },
];

/**
 * A pre-mark and NOTHING ELSE: seeded into the session draft by the board's own pre-mark
 * effect (`preMarkedKeys`, `FulfilmentBoardPanel`), with no server-saved draft behind it yet.
 *
 * `decision.preMarked` alone is not enough - once the server-saved draft arrives
 * (`contribution.draft`), the line genuinely has been saved (by this pre-mark's own eventual
 * Confirm write, or by another planner in the meantime) and reads "Saved" like any other, even
 * though the session's own draft entry has not been replaced. Exported so the Verdict column
 * (`FulfilmentBoardListView`) and its grid equivalent (`BoardCellBreakdownDialog`) withhold the
 * Undo arrow off the SAME rule the pill reads - there is nothing here yet to undo.
 */
export function isPreMarkOnly(
  contribution: Pick<BoardContribution, 'draft'>,
  decision: BoardDecision | null,
): boolean {
  return Boolean(decision?.preMarked) && !contribution.draft;
}

export type VerdictKey =
  | 'unplannable'
  | 'cancelled'
  | 'confirmed'
  | 'rejected'
  | 'stale'
  | 'change_proposed'
  | 'saved'
  | 'suggested';

/**
 * How far one line has got, as the ONE-WORD KEY this pill renders - pulled out of the pill's
 * own body (board-confirm-left-out, AC-7) so the Verdict column's own SORT
 * (`FulfilmentBoardListView`) reads off the exact rule the pill does, rather than a second
 * derivation the two could quietly disagree about.
 */
export function verdictOf(
  contribution: Pick<
    BoardContribution,
    'cancelled' | 'unplannable' | 'covered' | 'draft' | 'decision'
  >,
  decision: BoardDecision | null,
): VerdictKey {
  if (!contribution.cancelled && contribution.unplannable) return 'unplannable';

  const covered = Boolean(contribution.covered) && !decision;
  // What was saved, from wherever it came: THIS session's own click first, the server's own
  // row otherwise - a fresh page load has not run the seeding effect yet on the very first
  // paint, and the pill reads right either way rather than flashing Suggested for a frame.
  const draftSource = decision ?? contribution.draft?.decision ?? null;
  const stale = !covered && Boolean(contribution.draft?.stale);

  // N6 (code review round 3): `confirmed > rejected > stale > saved`, the same order
  // `confirmSummaryFor` (`_shared/lib/fulfilmentBoard.ts`) counts by. A REJECTED verdict on
  // a stale line commits nothing either way, and "Rejected" is what the planner actually did
  // about it - reading "Suggestion changed" instead would say something happened that had
  // already been answered.
  //
  // CANCELLED WINS OVER EVERYTHING (R3). Whatever was decided for this line, and whoever
  // decided it, the book has since removed the line: reading "Confirmed" or "Saved" over a
  // quantity nobody is owed would be the board agreeing to supply it.
  if (contribution.cancelled) return 'cancelled';
  if (covered) return 'confirmed';
  if (draftSource?.verdict === 'rejected') return 'rejected';
  if (stale) return 'stale';
  if (isPreMarkOnly(contribution, decision)) {
    // PLAN-board-change-proposed-pill: sits with `saved` in the resolution order (it IS a
    // session draft, just not yet a written one) - `rejected` and `stale` both still win over
    // it for the same reason they win over `saved`.
    return 'change_proposed';
  }
  if (draftSource) return 'saved';
  return 'suggested';
}

/**
 * The Verdict column's own sort order (AC-7, the UAC's own words: "Suggested, Saved,
 * Confirmed, Rejected"). `unplannable` and `cancelled` are not a verdict a planner takes on
 * this board, so they sort at the ends rather than among the four the UAC names; `stale` and
 * `change_proposed` are both a kind of "saved", so they sit beside it.
 */
export const VERDICT_SORT_RANK: Record<VerdictKey, number> = {
  unplannable: 0,
  suggested: 1,
  change_proposed: 2,
  saved: 3,
  stale: 4,
  confirmed: 5,
  cancelled: 6,
  rejected: 7,
};

export function BoardDecisionPill({
  contribution,
  decision,
}: {
  contribution: BoardContribution;
  /** This line's entry in the board's draft, or null while nobody has decided it here. */
  decision: BoardDecision | null;
}) {
  // CANCELLED FIRST, before "needs a location" (R3, the 13 September walk). The guard below
  // is for a line whose sales order genuinely states no warehouse, which is something a
  // planner can go and fix; a cancelled line is read-only, and telling them to fix its
  // location would be asking them to mend a line the book has already removed.
  if (!contribution.cancelled && contribution.unplannable) {
    return (
      <span
        className="block truncate text-sm text-destructive"
        title="This line cannot be decided here: its sales order states no fulfilment location."
      >
        Needs a location
      </span>
    );
  }

  // What was saved, from wherever it came: THIS session's own click first, the server's own
  // row otherwise - a fresh page load has not run the seeding effect yet on the very first
  // paint, and the pill reads right either way rather than flashing Suggested for a frame.
  // Needed here for `suspected` below; `verdictOf` computes its OWN copy of the same value.
  const draftSource = decision ?? contribution.draft?.decision ?? null;
  const verdict = verdictOf(contribution, decision);

  // Flagged in this session's draft, or - while nobody has decided it here - flagged on the
  // decision that is already in the database: the icon has to survive a reload, or the doubt
  // reads as answered (R10).
  //
  // THE DRAFT WINS OUTRIGHT, false included. A planner who unticks the box on a confirmed
  // line has answered the question; falling through to the frozen `true` behind it left the
  // warning on screen while the body about to be posted said `false`, so the row and the
  // write disagreed about the one fact the flag exists to carry.
  const suspected = draftSource
    ? Boolean(draftSource.suspected_system_issue)
    : Boolean(contribution.decision?.suspected_system_issue);

  const savedBy = contribution.draft?.saved_by;
  const savedAt = contribution.draft?.saved_at;
  const pill = (
    <span
      data-testid={`decision-pill-${contribution.key}`}
      // `truncate` (AC-C2, `board-verdict-actions-chips-acceptance-criteria.md`): the Verdict
      // cell now carries the row's own actions beside this pill, and at 375px - or on a
      // column dragged to its minimum - the PILL is what gives way, never the buttons. Left
      // to wrap, "Change proposed" made the whole row two text lines tall.
      className={`${STATUS_PILL_BASE} normal-case truncate ${VERDICT_PILL[verdict]}`}
      title={decision?.reason ?? contribution.decision?.amend_reason ?? ''}
    >
      {VERDICT_LABEL[verdict]}
    </span>
  );

  return (
    <div className="flex min-w-0 items-center gap-1">
      {/* Who saved this, and when (AC-4.2: "the pill reads 'Saved' only, the saver's name
          is in the popover"). Only wrapped in a popover once there is something to say - a
          plain `title` is not reachable at 375px, so a small `Popover` carries it instead
          (the `BoardRankPopover` shape).
          Round 2 (`PLAN-oi-decision-trail-ui.md`, owner ruling after hand-testing round 1):
          the CONFIRMED chip carries no popover of its own any more, even on the rare line
          where a draft also lingers on it (a covered line somebody has since re-saved) - the
          History icon beside this pill (`DecisionTrailButton`, wired in the caller's own
          Verdict/Decision cell) opens the full trail, confirmed AND saved facts together,
          instead. `verdict !== 'confirmed'` is the gate that keeps this a DIFFERENT question
          (a draft saved on a line nobody has confirmed yet) from that one. */}
      {verdict !== 'confirmed' && savedBy ? (
        <Popover>
          <PopoverTrigger
            asChild
            // Stops here so a click meant for the popover does not also toggle the row this
            // pill sits inside (the board grid and the contributing-lines table both expand
            // on a row click) - the same reason `BoardRankPopover`'s own trigger stops it.
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              aria-label={`Saved by ${savedBy}`}
              data-testid={`decision-saved-by-${contribution.key}`}
              // `block min-w-0 overflow-hidden` (N-4, reviewer): this wrapper is the flex
              // item, so without it the button keeps its content's full width and the
              // pill's own `truncate` inside it has nothing to truncate against - a Saved
              // line's pill would still push the Verdict cell's icons out at 375px.
              className="block min-w-0 overflow-hidden rounded-sm outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
            >
              {pill}
            </button>
          </PopoverTrigger>
          <PopoverPortal>
            <PopoverContent
              align="start"
              className="w-auto max-w-[92vw] px-3 py-2 text-xs"
              onOpenAutoFocus={(event) => event.preventDefault()}
            >
              {`Saved by ${savedBy}${savedAt ? ` · ${formatDateTimeInMalaysia(savedAt)}` : ''}`}
            </PopoverContent>
          </PopoverPortal>
        </Popover>
      ) : (
        pill
      )}
      {suspected ? (
        <AlertTriangle
          data-testid={`decision-flag-${contribution.key}`}
          aria-label="Flagged as a possible system problem"
          className="size-3.5 shrink-0 text-amber-600"
        />
      ) : null}
    </div>
  );
}
