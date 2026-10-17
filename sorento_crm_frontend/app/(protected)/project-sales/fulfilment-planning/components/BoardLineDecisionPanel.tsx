'use client';

import * as React from 'react';
import {
  Check,
  CheckCircle2,
  ChevronRight,
  Pencil,
  Plus,
  ShoppingCart,
  Trash2,
  X,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Textarea } from '@/components/ui/textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import {
  amendNeedsReason,
  hasUnsuggestedBorrow,
  matchesSuggestion,
} from '../../_shared/lib/fulfilmentBoard';
import {
  amendDraftFrom,
  amendSummary,
  borrowCandidatesOf,
  decisionFromAmendDraft,
  foldReasonIntoDraft,
  suggestionDraftFrom,
  suggestionWithReasons,
} from '../../_shared/lib/boardAmend';
import {
  fromMinor,
  lineBalance,
  lineBlockers,
  toMinor,
  type DraftBorrow,
  type DraftLine,
} from '../../_shared/lib/supplyComposition';
import { poolShareLimitsOf } from '../../_shared/lib/poolShare';
import type {
  BoardCellLocation,
  BoardContribution,
  BoardDecision,
  BorrowCandidate,
} from '../../_shared/types/fulfilmentPlanning.types';
import { BoardLadderOptionsTable } from './BoardLadderOptionsTable';
import { BorrowAddDialog } from './BorrowAddDialog';
import { ReserveAddDialog } from './ReserveAddDialog';

/**
 * R1's own 409 sentence (`project_line_draft_service.save_draft`), stated here before the
 * round trip rather than after it (R2): a covered line refuses a plain Save or Reject, so
 * the panel says so up front instead of sending a PUT the server would only refuse.
 *
 * Carried in a Radix `Tooltip`, never a bare `title` (review round 1, S1): the `Button`
 * primitive disables `pointer-events` on a disabled button, so a `title` attribute there
 * never reaches a real hover - the sentence has to live on a focusable wrapper instead.
 */
const CONFIRMED_LINE_TITLE =
  'This line is already confirmed. Amend it to change the decision, reject it with a reason, ' +
  'or undo the confirmation.';

/**
 * D3 (S2): what the ONE Reason box seeds from - the first non-blank of a record's own
 * "why this differs" (`reason` on a session decision, `amend_reason` on a frozen one), its
 * `buy_reason` and its first borrow row's `reason`. Duck-typed rather than two near-identical
 * functions: the session decision (`BoardDecision`) and the frozen one (`BoardLineDecision`)
 * name the same field differently, and every field this reads is optional on both.
 */
function seedReason(
  source:
    | {
        reason?: string | null;
        amend_reason?: string | null;
        buy_reason?: string | null;
        borrow?: { reason?: string | null }[] | null;
      }
    | null
    | undefined,
): string {
  if (!source) return '';
  const candidates = [
    source.reason,
    source.amend_reason,
    source.buy_reason,
    source.borrow?.[0]?.reason,
  ];
  return candidates.find((text) => Boolean(text && text.trim()))?.trim() ?? '';
}

/**
 * The decision on one contributing line, taken IN THE ROW (PLAN section 3.C, ruling R7).
 *
 * It used to be a modal over a modal: the row carried Approve / Amend / Reject buttons, and
 * Amend opened `BoardAmendDialog` on top of the breakdown dialog that opened it. Two stacked
 * dialogs put the line the planner is deciding on behind the thing they decide it with, and
 * the row's own facts - what was ordered, what is already delivered, what each location can
 * actually give - were on the layer underneath. So the row expands instead, the same gesture
 * reorder planning's group rows already use, and everything about the decision is in one
 * window with the numbers it is made against.
 *
 * It composes on the SAME `DraftLine` the per-order sheet and the old dialog composed on,
 * against the same `lineBalance` and `lineBlockers` - the sheet and the board have to agree
 * about what balances, or one screen refuses what the other accepted.
 *
 * TWO VERBS, and they are the whole decision: Save (take what is in these inputs) and Reject
 * (take none of it, with a reason). Save used to be two buttons - Approve suggestion beside
 * Save amendment - and the captain, on 28 August: "having 1 button save amendment for me to
 * click when an amendment is needed, and another button Approve when an amendment is not
 * needed, is too much of a work for me to think ... if the suggestion is same as decision then
 * it is approved, if suggestion different from decision then it is amended, so I just click on
 * 1 button". So the COMPARISON takes the verdict, not the planner: a form still holding the
 * engine's composition is approved, and one holding anything else is amended.
 *
 * NO BALANCE EQUATION (R9). The line "24 outstanding = 0 incoming + 9 reserve + ..." restated
 * four inputs the planner had just typed. What they cannot see is whether it ADDS UP, so that
 * is all that is said, as "Unassigned 4" or "Over by 4", and only while it does not.
 */
/** The sentence for a quantity box that was typed into but cannot be read; a cleared box is "none". */
function quantityError(raw: string): string | undefined {
  return raw.trim() ? numberFieldError(raw, { min: 0 }) : undefined;
}

export function BoardLineDecisionPanel({
  contribution,
  decision,
  locations,
  onDecide,
  onDirtyChange,
}: {
  contribution: BoardContribution;
  /** This line's entry in the board's draft, or null while nobody has decided it here. */
  decision: BoardDecision | null;
  /**
   * The cell's own stock rows, so each Reserve input can say what that location has
   * available beside it. The figure is the SERVER's - the panel never recomputes one.
   */
  locations: BoardCellLocation[];
  /**
   * S4/R-F: saves (or, on `null`, undoes) the decision on the SERVER, not only in this
   * session's draft - `await`ed here so the Save button's own check state (AC-4.1) can wait
   * for it to settle before showing. Resolves `true` on success (S2, code review round 3):
   * a rejected write must not show the check either, and `void` stays for callers that have
   * no result to report (a `?batch=` board pre-marking a row locally, for instance).
   */
  onDecide: (decision: BoardDecision | null) => Promise<boolean> | void;
  /**
   * Whether this panel holds an edit nobody has saved. The dialog opens one row at a time,
   * so it has to ask before it closes this one over the planner's work.
   */
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [draft, setDraft] = React.useState<DraftLine>(() =>
    draftFor(contribution, decision),
  );
  // D3 (S2): ONE Reason box now covers "why this differs", the discontinued Buy reason and
  // every borrow reason, so it seeds from the first NON-BLANK of the three the row already
  // carries (a record with three different texts shows the first; the others stay readable in
  // the trail) - never blank while any of them holds something, which the server's whole-line
  // mix rule (`mixAllowed` below) also depends on reaching Save unchanged.
  const [reason, setReason] = React.useState(
    () => seedReason(decision) || seedReason(contribution.decision) || '',
  );
  // What the box was SEEDED with (PR 1622 review B1). The seed can be the engine's own borrow
  // sentence, so only text the planner actually changed - or this line's own earlier Buy
  // reason - is a reason CS gave for the Buy, the one purchasing reads on its new row.
  const reasonSeed = React.useRef(reason);
  // The draft's own answer, false included: an untick is a decision, and reading past it to
  // the frozen `true` on a covered line put the tick straight back on screen.
  const [suspected, setSuspected] = React.useState(() =>
    decision
      ? Boolean(decision.suspected_system_issue)
      : Boolean(contribution.decision?.suspected_system_issue),
  );
  /**
   * A line an active decision covers opens READ-ONLY with an Amend button (C11): what is on
   * screen is what the database holds, and an editable form over it invites a planner to
   * change something they have not decided to change yet.
   */
  const [locked, setLocked] = React.useState(
    () => Boolean(contribution.covered) && !decision,
  );
  const [adding, setAdding] = React.useState(false);
  const [addingReserve, setAddingReserve] = React.useState(false);
  /** Untouched since it opened. Saving or approving puts it back, because it is saved now. */
  const [dirty, setDirty] = React.useState(false);
  /**
   * Fix round 1, S4: the write is in flight. Without this a second click before the first
   * `onDecide` resolves fires a second PUT (and a second toast) - `disabled` on the plain
   * Save/Reject buttons reads this alongside their own rules, so a click while one is already
   * on the wire is a no-op rather than a race.
   */
  const [pending, setPending] = React.useState(false);
  /**
   * Fix round 3: `pending` is the visible answer, but it is STATE - two synchronous clicks in
   * the SAME tick (a real double-click, or two `fireEvent.click`s before React re-renders)
   * both read the pre-render `pending` (still `false`) and both proceed. `inFlight` is the
   * actual guard, checked and set synchronously before either `save()`/`reject()` does
   * anything else.
   */
  const inFlight = React.useRef(false);
  /**
   * Whether this line's decision is SAVED as the panel stands (S4, AC-4.1; amended by D4,
   * captain 3 Sep).
   *
   * It used to be a 600 ms flash after `onDecide` resolved, and the captain read the flash
   * as the save undoing itself: "shows saved then jumps back". So it is the LINE's state
   * now, not a moment's - the button stays on the check, disabled, for as long as nothing
   * has been edited since, and any edit puts "Save decision" back (`dirty` below is what
   * every input already sets). A line that opens on somebody else's saved draft starts
   * there too: pressing Save on it would write what is already written.
   */
  /**
   * A rejected draft is NOT a saved one (fix round 1, B1): `useLineDraftMutation.save.onSuccess`
   * patches `contribution.draft` before `mutateAsync` resolves, so `Boolean(contribution.draft)`
   * alone is true for a landed Reject too - the button read Saved AND Rejected side by side.
   * The two states are mutually exclusive by construction: seeded (and re-seeded below) off the
   * SAME `contribution.draft`, split by its own verdict.
   */
  // Seed only - the N3 effect below recomputes its own local each time it re-seeds.
  const rejectedDraft = contribution.draft?.decision?.verdict === 'rejected';
  const [savedOnce, setSavedOnce] = React.useState(
    () => Boolean(contribution.draft) && !rejectedDraft,
  );
  /**
   * Whether this line's Reject has LANDED, the same reading `savedOnce` above gives a Save -
   * `decide()` saves a rejection through the identical draft write a Save uses, so
   * `contribution.draft.decision.verdict` carries `'rejected'` the same way it carries
   * `'approved'`/`'amended'`. Seeded off `contribution.draft` rather than the `decision` prop
   * for the same reason `savedOnce` is: the prop is the board's own session map and lags a
   * beat behind what this panel can already read at mount.
   */
  const [rejectedOnce, setRejectedOnce] = React.useState(() => rejectedDraft);
  /** Options closed until the planner asks - untouched per row, no persistence. */
  const [optionsOpen, setOptionsOpen] = React.useState(false);

  /**
   * N3 (fix round 5): re-seeded whenever the contribution's OWN saved draft changes - a
   * refetch (another planner saved this line elsewhere) or an Undo fired from the pill
   * (`BoardDecisionPill`), neither of which goes through this panel's own `save()` /
   * `reject()`. Seeded at MOUNT only left a panel that stayed open across such a change
   * reading a state the server had already moved on from: Undo cleared the draft server-side
   * while this panel's own `savedOnce` (still `true` from mount) kept the button on "Saved".
   */
  React.useEffect(() => {
    const rejected = contribution.draft?.decision?.verdict === 'rejected';
    setSavedOnce(Boolean(contribution.draft) && !rejected);
    setRejectedOnce(rejected);
  }, [contribution.draft]);

  /**
   * Saved AND untouched since (D4). `dirty` is set by every input on this panel, so an edit
   * of any kind puts "Save decision" back without this having to know which one moved.
   */
  const saved = savedOnce && !dirty;
  /** Rejected AND untouched since, the same reading `saved` gives a Save. */
  const rejected = rejectedOnce && !dirty;

  React.useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  // Nothing is left dirty behind a panel that is going away: the row above is closing and the
  // dialog's prompt has already been answered by then.
  React.useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const candidates = React.useMemo<BorrowCandidate[]>(
    () => borrowCandidatesOf(contribution),
    [contribution],
  );

  // Reserve add-location (S3, R-G): any location the cell's own stock rows carry, with free
  // stock left and not already on the Reserve list. The site pool is not filtered out - R-A
  // asks it first, so a planner adding it by hand is asking for exactly what the ladder itself
  // would have asked for.
  const reserveWarehouseIds = React.useMemo(
    () =>
      draft.reserve
        .map((row) => row.warehouse_id)
        .filter((id): id is string => Boolean(id)),
    [draft.reserve],
  );
  // OI-CLOSED-LINE (owner, 12 Oct 2026): a line with goods already delivered may name the
  // location they went out from even when it shows nothing free now.
  const anyDelivered = toMinor(contribution.qty_delivered ?? '0') > 0;
  const reserveCandidates = React.useMemo(
    () =>
      locations.filter(
        (location) =>
          Boolean(location.warehouse_id) &&
          !reserveWarehouseIds.includes(location.warehouse_id as string) &&
          (anyDelivered ||
            toMinor(location.qty_free_remaining ?? location.qty_free ?? '0') > 0),
      ),
    [locations, reserveWarehouseIds, anyDelivered],
  );

  const balance = lineBalance(draft);
  // OI-CLOSED-LINE: the delivered part of the line, credited front-first across the Reserve
  // rows the way Confirm credits it (`delivered_credits`): after the Buy and incoming take
  // their share, what the customer already received needs no free stock.
  const reserveCredits = React.useMemo(() => {
    let left = Math.max(
      Math.min(toMinor(contribution.qty_delivered ?? '0'), toMinor(draft.open_qty)) -
        toMinor(draft.buy_qty) -
        toMinor(draft.timely_spo_qty),
      0,
    );
    return draft.reserve.map((row) => {
      const credit = Math.min(left, Math.max(toMinor(row.qty), 0));
      left -= credit;
      return credit;
    });
  }, [contribution.qty_delivered, draft]);
  /**
   * D5: the board CAN check the pool-share carve-out (R-C), because its own cell states each
   * pool's allowance and the pools' net. Without them this panel refused "BRW 62 + Buy 73" -
   * the engine's own suggestion on SO419208 line 3, and a composition the server's confirm
   * has always admitted.
   */
  const poolLimits = React.useMemo(() => poolShareLimitsOf(locations), [locations]);
  // D3 (S2): the ONE box fanned onto the draft it would be saved from, so `lineBlockers`'s
  // own borrow-reason check (shared with the per-order sheet, unchanged there) reads the box
  // the same way Save will - a borrow row added by hand blocks Save until the box carries
  // text, exactly as its own per-row Reason input used to.
  const foldedDraft = React.useMemo(
    () => foldReasonIntoDraft(draft, reason),
    [draft, reason],
  );
  // `mixAllowed`: the 8 Sep 2026 ruling. AC-L5's whole-line rule is lifted on this panel
  // because `reason` (seeded from the frozen decision's own reason above, or typed fresh) is
  // what Save sends as `amend_reason` on the confirm line - `needsReason` below only forces a
  // NEW one when the draft has moved since that baseline (`amendNeedsReason`), so an unchanged
  // frozen mix re-saves on the reason it already carried. The per-order sheet does not pass
  // this and still refuses the mix (`SupplyLineCard`).
  const blockers = lineBlockers(foldedDraft, poolLimits, { mixAllowed: true });
  // D3: required exactly when the composition differs from its baseline, OR a borrow row the
  // engine did not suggest is on the line - the server's own borrow-reason rule, asked here
  // before the round trip. Reject's own requirement is unconditional and lives on its button.
  const needsReason =
    amendNeedsReason(contribution, draft) || hasUnsuggestedBorrow(contribution, draft.borrow);
  /**
   * Which verdict Save takes, and therefore what it may be pressed for: approving the
   * engine's own composition is never blocked, because there is nothing about it to balance
   * or justify. NEVER on a COVERED line, though (R1, `save()` below) - the server has
   * refused an `approved` verdict there with a 409 since the SO314595 incident (17 Sep
   * 2026), and the suspected-system-issue tick is part of what was decided, not a detail
   * riding beside it, so it counts toward whether there is anything left to amend.
   */
  const approving = matchesSuggestion(contribution, draft);
  const covered = Boolean(contribution.covered);
  /**
   * `covered` alone spans TWO kinds of line (N1, fix round,
   * `PLAN-board-reject-on-confirmed-line.md`): an ACTIVE `SOSupplyDecision` covers it, or a
   * live order-inquiry row names it with no decision at all (`inquiry_decided`, migrated
   * sheet lines, #875) - `project_fulfilment_board_service.py`'s own `covered` property.
   * Confirm has a withdrawal seam (`rejected_line_ids`) for the first kind only: it reads
   * the active decision's `line_snapshots`, which the second kind has none of. So Reject
   * after Amend stays live only where `contribution.decision` (the frozen decision object)
   * is non-null - the #989 disabled-with-`CONFIRMED_LINE_TITLE` shape is kept for the
   * inquiry-only case, same predicate `BoardVerdictActions`' own `rejectable` reads.
   */
  const activelyCovered = covered && Boolean(contribution.decision);
  const suspectedChanged =
    suspected !== Boolean(contribution.decision?.suspected_system_issue);
  /**
   * R1/R2: on a covered line the server only accepts a real amendment, so the panel refuses
   * BEFORE the round trip rather than after. `needsReason` is already "has the composition
   * moved since the frozen decision" (`amendNeedsReason`'s own baseline, on a covered line,
   * IS `contribution.decision`) - a draft that has not moved has nothing to amend. The tick
   * is the other half: unticking (or ticking) it with the composition otherwise untouched is
   * still a change to what was decided, so it alone must be enough to unlock Save.
   */
  const alreadyConfirmed = covered && !needsReason && !suspectedChanged;
  // A quantity box holding something unreadable (bad input) is never read as 0: it is told on
  // the box and Save waits for it. A cleared box stays "none".
  const badQuantities =
    draft.reserve.some((row) => quantityError(row.qty)) ||
    draft.borrow.some((row) => quantityError(row.qty)) ||
    Boolean(quantityError(draft.buy_qty));
  const canSave =
    blockers.length === 0 && (!needsReason || reason.trim().length > 0) && !badQuantities;
  const fromStockMinor =
    balance.timelyMinor + balance.reserveMinor + balance.borrowMinor;
  /**
   * WHOLLY bought, which is what the switch means. STATE (B-1, fix round 7), not derived from
   * the numbers - deriving it used to flip the switch ON the instant `fromStockMinor` hit zero,
   * which clearing a reserve box (or typing 0 into it) does on its own now that D7's
   * `editComposition` recomputes `buy_qty` on every keystroke. The switch turning itself on
   * mid-edit unmounted the Reserve section and the Add-location button under the planner, and
   * turning it back OFF afterwards took the "nothing was captured" branch below and wrote
   * zeroes over rows nobody asked to zero.
   *
   * Seeded from the opening draft - a line that opens already wholly bought (its stock rows
   * already at zero) starts on the switch, the same line `setBuying`'s OFF transition
   * documents - and changed by `setBuying` itself, or by the BOARD-DECIDE-RESEED effect below
   * when the decision changes under an untouched panel (the same seed, off the re-seeded
   * draft). Never derived from the numbers mid-edit.
   */
  const [buying, setBuyingState] = React.useState(
    () => toMinor(draft.buy_qty) > 0 && fromStockMinor === 0,
  );
  /**
   * BUY-FIRST-PANEL (owner, 14 Oct 2026): "I want to key in the BUY quantity first". Buy
   * follows the remainder (D7) until the planner types it; from then on it is PINNED and no
   * stock-side edit changes it - the hint says what is left instead ("Unassigned N" /
   * "Over by N"), and the existing sum blocker keeps Save locked until it is 0. Cleared by the
   * whole-line switch going OFF and by a re-seed from the decision.
   */
  const [buyPinned, setBuyPinned] = React.useState(false);

  /**
   * Everything that stops the Save EXCEPT the balance, which the hint beside the summary
   * already states in a few words. Printing both would say the same refusal twice.
   */
  const otherBlockers = balance.balanced
    ? blockers
    : blockers.filter((blocker) => !blocker.includes('the components are'));

  const edit = (next: DraftLine) => {
    setDraft(next);
    setDirty(true);
  };

  /**
   * D7 (captain, 3 Sep): Buy FOLLOWS the remainder until it is typed (BUY-FIRST-PANEL pins it,
   * `buyPinned` above; a pinned Buy is never overwritten here).
   *
   * "BRW 62 · Buy 73" on SO419208 line 3 (open 135) used to leave `buy_qty` frozen at 73 the
   * instant the BRW reserve was edited down to 60 - the line read short by 2 and Save stayed
   * blocked, because nothing that touches Reserve or Borrow ever recomputed it. Every input
   * that changes the STOCK side of the composition writes through here instead of `edit()`
   * directly: `buy_qty = max(open_qty - reserve - borrow - timely, 0)`, the same arithmetic
   * `lineBalance` already does, read with Buy zeroed out of the total.
   *
   * `setBuying`'s own transitions do NOT call this - they compute their own value for each
   * direction (see there). Deriving here too would undo the OFF transition on a line that
   * opened already wholly bought (a frozen decision, or an all-Buy suggestion): its stock
   * rows are already at zero, and deriving off zero rows hands the whole line straight back
   * to Buy, so the switch could never actually be turned off.
   */
  const editComposition = (next: DraftLine, pinned: boolean = buyPinned) => {
    if (pinned) {
      edit(next);
      return;
    }
    const stockOnly = lineBalance({ ...next, buy_qty: '0' });
    edit({
      ...next,
      buy_qty: fromMinor(Math.max(stockOnly.openMinor - stockOnly.totalMinor, 0)),
    });
  };
  /** The planner's own Buy: pinned from the first keystroke. */
  const typeBuy = (value: string) => {
    setBuyPinned(true);
    edit({ ...draft, buy_qty: value });
  };
  /** What the parts leave uncovered, in minor units; 0 when they add up or run over. */
  const unassignedMinor = Math.max(-balance.differenceMinor, 0);
  const restToBuy = () => {
    setBuyPinned(true);
    edit({ ...draft, buy_qty: fromMinor(toMinor(draft.buy_qty) + unassignedMinor) });
  };
  const restToReserve = () => {
    const [first, ...others] = draft.reserve;
    if (!first) return;
    editComposition({
      ...draft,
      reserve: [{ ...first, qty: fromMinor(toMinor(first.qty) + unassignedMinor) }, ...others],
    });
  };
  const setBorrow = (borrow: DraftBorrow[]) => editComposition({ ...draft, borrow });
  // One or several borrow rows appended in ONE change: a merged bin's split arrives as
  // several (BORROW-LOCATION-ONLY R2), and appending them one by one off the same `draft`
  // would keep only the last.
  const addBorrows = (
    parts: { candidate: BorrowCandidate; qty: string }[],
    borrowReason: string,
  ) =>
    setBorrow([
      ...draft.borrow,
      ...parts.map(({ candidate, qty }, index) => ({
        key: `borrow-${candidate.warehouse_id}-${draft.borrow.length + index}`,
        source: candidate.source,
        warehouse_code: candidate.warehouse_code,
        warehouse_id: candidate.warehouse_id,
        donor_project_ref: candidate.donor_project_ref,
        donor_project_id: candidate.donor_project_id,
        qty,
        reason: borrowReason,
        donor_impact: candidate.donor_impact,
        donor_core_line_id: candidate.donor_core_line_id,
        donor_so_number: candidate.donor_so_number,
        donor_line_no: candidate.donor_line_no,
        donor_agent_code: candidate.donor_agent_code,
        same_agent: candidate.same_agent,
        donor_required_date: candidate.donor_required_date,
      })),
    ]);

  /**
   * The switch means WHOLLY bought, not "no mix allowed" - a stock-and-Buy mix is a legal
   * hand composition on this panel (8 Sep 2026 ruling) that simply leaves the switch off; it
   * needs a reason, which `amendNeedsReason` already requires because the engine never
   * proposes a mix, so any mix here already differs from the frozen or suggested baseline.
   *
   * Switching Buy ON zeroes the stock side, and switching it OFF puts back exactly what was
   * there: the quantities, the donors and their reasons. The rows alone are not enough - a
   * planner who typed a 9, a 15 and two sentences and then tried the Buy switch got a form of
   * zeroes back, which is a composition they never wrote and the worst possible answer to
   * "what happens if I press this". A TYPED Buy comes back too, still pinned (crew ruling (a),
   * BUY-FIRST-PANEL, 14 Oct 2026): the switch is a detour, and a typed number is never
   * silently changed.
   */
  const stockBefore = React.useRef<
    | (Pick<DraftLine, 'reserve' | 'borrow' | 'timely_spo_qty' | 'buy_qty'> & {
        buyPinned: boolean;
      })
    | null
  >(null);
  const setBuying = (next: boolean) => {
    setBuyingState(next);
    if (next) {
      stockBefore.current = {
        reserve: draft.reserve,
        borrow: draft.borrow,
        timely_spo_qty: draft.timely_spo_qty,
        buy_qty: draft.buy_qty,
        buyPinned,
      };
      // ON is a Buy the planner chose: the whole line, pinned.
      setBuyPinned(true);
      edit({
        ...draft,
        timely_spo_qty: '0',
        // The rows stay, at zero: they are where the locations are named.
        reserve: draft.reserve.map((row) => ({ ...row, qty: '0' })),
        borrow: [],
        buy_qty: draft.open_qty,
      });
      return;
    }
    const held = stockBefore.current;
    stockBefore.current = null;
    if (held) {
      const { buy_qty: heldBuy, buyPinned: heldPinned, ...stock } = held;
      setBuyPinned(heldPinned);
      if (heldPinned) {
        edit({ ...draft, ...stock, buy_qty: heldBuy });
        return;
      }
      // D7: the remainder of whatever gets restored, not the flat 0 this used to force - that
      // used to drop the BRW-62/Buy-73 shape's own Buy to nothing the moment the switch was
      // tried and put back, when nothing about the stock side had actually changed.
      editComposition({ ...draft, ...stock }, false);
      return;
    }
    setBuyPinned(false);
    // Nothing was captured: the switch was never turned ON in this session, so the line opened
    // already wholly bought and its stock rows are already at zero. Buy explicitly to 0 - not
    // derived, or it would hand the whole line straight back - and let the now-empty rows be
    // typed into.
    edit({ ...draft, buy_qty: '0' });
  };

  /**
   * BOARD-DECIDE-RESEED (owner, 7 Oct 2026, SO424856 lines 16 / 32): the draft, the Reason
   * box, the tick, the lock and the Buy switch seed from `decision` at MOUNT, and nothing
   * re-read it. A panel left open while the planner picked a way from the Decide dropdown
   * (`BoardDecideControl` writes `draft[key]` through `FulfilmentBoardPanel.decide`, never
   * through this panel) kept the OLD Reserve rows under a pill already reading the new
   * decision, and Save posted those stale rows - "The components add up to X and the line is
   * open for Y" from the server.
   *
   * So it re-seeds exactly as a fresh mount would, when the decision changes under it, UNLESS
   * the planner has typed into this panel (`dirty`): their work is not overwritten; the pill
   * beside the row already shows the new decision, and closing and reopening the row loads
   * it. Read through a ref so the effect keys on the decision alone - keying on `dirty` too
   * would fire this again the moment a save cleared it.
   *
   * This panel's OWN `save()` / `reject()` never trips it: `FulfilmentBoardPanel.decide`
   * writes `draft[key]` synchronously, before its await, so the render that carries the new
   * decision still has `dirty` true and is skipped; and an untouched approving Save re-seeds
   * to the suggestion either way (review round 1: an own-write guard here was unreachable).
   *
   * The N3 effect above is the same idea for `savedOnce` / `rejectedOnce`; this one is for
   * what the inputs hold.
   */
  const dirtyRef = React.useRef(dirty);
  dirtyRef.current = dirty;
  const seededFrom = React.useRef<BoardDecision | null>(decision);
  React.useEffect(() => {
    if (decision === seededFrom.current) return;
    seededFrom.current = decision;
    if (dirtyRef.current) return;
    const next = draftFor(contribution, decision);
    const stock = lineBalance({ ...next, buy_qty: '0' });
    setDraft(next);
    const reseeded = seedReason(decision) || seedReason(contribution.decision) || '';
    reasonSeed.current = reseeded;
    setReason(reseeded);
    setSuspected(
      decision
        ? Boolean(decision.suspected_system_issue)
        : Boolean(contribution.decision?.suspected_system_issue),
    );
    setLocked(Boolean(contribution.covered) && !decision);
    stockBefore.current = null;
    setBuyingState(toMinor(next.buy_qty) > 0 && stock.totalMinor === 0);
    setBuyPinned(false);
  }, [decision, contribution]);

  /**
   * ONE PRESS, TWO VERDICTS, decided by what the inputs hold rather than by which button was
   * chosen: the engine's composition is an approval, anything else is an amendment.
   *
   * The approval re-seeds from `suggestionDraftFrom`, never `draftFor`: on a covered line the
   * panel opens on what was DECIDED, so resetting to that put the frozen numbers back and
   * called it the suggestion - SO404352 line 22 stayed at 8 / 16 under a pill reading
   * Approved. The reason goes with it, because an approval overrides nothing.
   *
   * D11: the approval carries the suggested COMPOSITION too - reserve, borrow, buy_qty,
   * timely_spo_qty - not only the verdict. `saveLineDraft` writes this object verbatim to
   * `so_supply_decision_drafts.decision`, and `_saved_components` (`sales_order_service.py`)
   * reads it back for the sales order page's own "Decided" column; a bare
   * `{verdict: 'approved'}` read there as no components at all, so an accepted suggestion
   * showed Saved beside a blank Decided. `confirmLinesFor` (`fulfilmentBoard.ts`) already
   * solves the identical problem at Confirm time through the same pair
   * (`decisionFromAmendDraft(suggestionDraftFrom(contribution), '')`), so this is the same
   * derivation one step earlier - a draft and its own confirmation cannot disagree about what
   * an approval actually composed.
   *
   * NEVER on a COVERED line (R1): the server refuses an `approved` verdict there outright,
   * so a covered line always takes the ELSE branch below regardless of `approving` - an
   * unlocked, re-typed suggestion on a confirmed line is still an amendment that happens to
   * match the engine's numbers, not an approval.
   *
   * BOARD-CONFIRM-LEFT-OUT (measured cause 1): the approving branch used to post
   * `decisionFromAmendDraft(suggestionDraftFrom(contribution), '')` verbatim, which drops the
   * `buy_reason` a planner just typed for a discontinued Buy and any reason typed on a
   * suggested borrow row - `matchesSuggestion` rightly compares quantities only, so typing a
   * reason alone never stopped this from reading as an approval, and the reason never reached
   * the server. `suggestionWithReasons` carries them across, matched by warehouse + donor
   * (the same key `matchesSuggestion` uses) so a reason typed on the row it belongs to is the
   * one that travels with it.
   */
  const save = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    try {
      let ok: boolean | void;
      const approvingNow = approving && !covered;
      // PR 1622 (owner Q1, review B1 x2): the planner's OWN words on a line that buys, sent as
      // their own field (`cs_note`) and carried to purchasing's new order inquiry row. Never
      // what the box was merely seeded with (it can be the engine's borrow sentence), so
      // neither `amend_reason` nor `buy_reason` - both may hold that seed - is what travels.
      const typed = reason.trim();
      const csNote =
        toMinor(draft.buy_qty) > 0 &&
        typed !== '' &&
        (typed !== reasonSeed.current.trim() || typed === (decision?.cs_note ?? '').trim())
          ? typed
          : undefined;
      if (approvingNow) {
        ok = await onDecide({
          ...suggestionWithReasons(contribution, {
            // D3 (S2): the ONE box, not a per-field input any more - `foldedDraft` is the same
            // fan-out Save's amending branch below uses. AC-28: `reason` travels on the
            // approving branch too, not only the amending `else` below.
            reason,
            buy_reason: foldedDraft.buy_reason,
            borrow: foldedDraft.borrow,
          }),
          cs_note: csNote,
          suspected_system_issue: suspected,
        });
      } else {
        ok = await onDecide({
          ...decisionFromAmendDraft(foldedDraft, reason),
          cs_note: csNote,
          // THE BOOLEAN, never `|| undefined`: `false` is the planner's answer that the numbers
          // are fine, and dropping the key let the frozen `true` behind it read as current.
          suspected_system_issue: suspected,
        });
      }
      // S2 (code review round 3): the check state answers the click, but only for a click that
      // actually landed - `onDecide` returning `false` (a rejected write) must not show a
      // check the server never earned. `undefined` (a caller with nothing to report) reads as
      // success, the same as before this fix.
      //
      // B1 (fix round 5): every "clean" state change - dirty, locked, and the approving branch's
      // reseed to the suggestion - waits for that same guard. Setting them before the `await`
      // meant a REJECTED second save still rendered the button as Saved and disabled: `dirty`
      // was already false and `savedOnce` was already true from the first, successful save, so
      // `saved = savedOnce && !dirty` read true over an edit the server never wrote.
      if (ok === false) return;
      setDirty(false);
      setLocked(false);
      if (approvingNow) {
        // D3 (S2): the reason box is NOT cleared here any more (AC-28) - it is the one place
        // `buy_reason` and every borrow reason now live, `foldReasonIntoDraft` reads it fresh
        // on every render, and clearing it the instant its own save landed would wipe the
        // reason off screen while it is still what was just saved.
        setDraft(suggestionDraftFrom(contribution));
      }
      // S4/AC-4.1: the button answers the click itself, within the interaction, before the
      // pill's own "Saved" and the toast even have to be looked at - and it keeps answering
      // until the line is edited again (D4).
      setSavedOnce(true);
      // B1 (fix round 1): a Save on a line whose LAST landed verb was Reject must clear the
      // stale "Rejected" reading - the two are mutually exclusive, and only one press ever
      // sets the other back to false.
      setRejectedOnce(false);
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  };

  /**
   * Mirrors `save()` above: the write lands before the button answers it, and a write the
   * server refused (`ok === false`) leaves "Reject" exactly as a planner pressed it rather
   * than claiming a rejection that never happened.
   */
  const reject = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    try {
      const ok = await onDecide({
        verdict: 'rejected',
        reason: reason.trim(),
        suspected_system_issue: suspected,
      });
      if (ok === false) return;
      setDirty(false);
      setRejectedOnce(true);
      // B1 (fix round 1): the mirror of the same guard on `save()` above.
      setSavedOnce(false);
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  };

  // A saved cancel, until the composition is edited: then it is an ordinary amend.
  const cancelSaved = decision?.verdict === 'cancelled' && !dirty;
  const summary = cancelSaved
    ? 'Transfer to Cancel SO'
    : amendSummary(
        decisionFromAmendDraft(draft, reason),
        contribution.fulfilment_location,
      );

  // Shared between the bare Save button and its tooltip-wrapped, disabled twin below, so the
  // two never drift apart on what the button says.
  const saveButtonLabel = saved ? (
    <>
      <CheckCircle2 className="size-4" aria-hidden />
      Saved
    </>
  ) : (
    <>
      <Check className="size-4" aria-hidden />
      Save decision
    </>
  );

  // Shared between the covered and uncovered Reject buttons (fix round 3, nit): the two
  // were twenty-line twins differing only in whether a Tooltip wraps them, which is exactly
  // the shape a shared element with a conditional wrapper is for - `disabled` and the pending
  // guard have to move together on either branch, and duplicating them invited a drift.
  const rejectButton = (
    <Button
      type="button"
      size="sm"
      variant="outline"
      // Disabled ON the landed rejection too (mirrors Save/D4): there is nothing left to
      // reject, and a live button under "Rejected" invites a second write. `pending` (fix
      // round 1, S4): the write this same click just started is still on the wire.
      disabled={rejected || pending || reason.trim().length === 0}
      onClick={reject}
    >
      {rejected ? (
        <>
          <CheckCircle2 className="size-4" aria-hidden />
          Rejected
        </>
      ) : (
        <>
          <X className="size-4" aria-hidden />
          Reject
        </>
      )}
    </Button>
  );

  /**
   * A LINE THAT CANNOT BE DECIDED HERE STATES SO, AND OFFERS NOTHING.
   *
   * Its sales order names no fulfilment location, so there is no warehouse to reserve at and
   * the confirmation leaves the line out entirely (`lineFor` returns null for it). An
   * editable panel over that was a trap: the composition could be typed, the pill would read
   * Amended, and the press would silently post nothing for it. The row still opens - the
   * figures are worth reading, and a row that refused to open reads as a broken row - but the
   * only thing it can say is why, and where the fix is.
   */
  if (contribution.unplannable) {
    return (
      <div
        data-testid={`line-decision-${contribution.key}`}
        className="border-t bg-muted/30 px-4 py-3 sm:px-5"
      >
        <div>
          <div className="space-y-1">
            <p className="text-2xs uppercase tracking-wide text-muted-foreground">
              Decision
            </p>
            <p
              data-testid={`line-decision-blocked-${contribution.key}`}
              className="break-words text-sm text-destructive"
            >
              This sales order states no fulfilment location, so this line
              cannot be decided here. Set the location on the sales order, then
              plan it again.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      data-testid={`line-decision-${contribution.key}`}
      className="border-t bg-muted/30 px-4 py-3 sm:px-5"
    >
      {/* WHAT THE LADDER OFFERED, above the editor that amends it (R36, AC-S3-14).
          The same table the trail popover renders, and above rather than beside on purpose:
          it is six columns of dates, and squeezed into the right-hand column it would wrap at
          1280px, let alone at 375. It is what the planner reads BEFORE typing, so it reads
          first. Display only - taking a different option is Amend, in the inputs below. */}
      {(contribution.options?.length ?? 0) > 0 && (
        // Closed by default (owner, 22 Sep 2026): six columns of dates left open on every
        // row wasted the space a planner opened this panel to compose the decision in.
        <Collapsible open={optionsOpen} onOpenChange={setOptionsOpen} className="mb-3">
          <CollapsibleTrigger asChild>
            <button
              type="button"
              data-testid={`line-options-toggle-${contribution.key}`}
              // Reads as a control (fix round 1, S3), the same interactive classes the
              // in-repo `CollapsibleTrigger` precedents carry (`PlanSection`,
              // `TagSizeControl`, `ProductAttachmentsTab`). `min-h-8`, not
              // `COARSE_HIT_TARGET_CLASS` (fix round 2): every use of that class lives inside
              // a `components/ui/*` primitive, none in a feature file - a bare button here
              // does not reach for a primitive-internal class.
              className="-mx-1 flex min-h-8 cursor-pointer items-center gap-1 rounded px-1 py-0.5 text-2xs uppercase tracking-wide text-muted-foreground hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            >
              <ChevronRight
                className={cn(
                  'size-3.5 shrink-0 transition-transform motion-reduce:transition-none',
                  optionsOpen && 'rotate-90',
                )}
                aria-hidden
              />
              Options
            </button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="pt-1">
              <BoardLadderOptionsTable
                options={contribution.options ?? []}
                contributionKey={contribution.key}
                buyOrigin={contribution.buy_origin}
              />
            </div>
          </CollapsibleContent>
        </Collapsible>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        {/* THE COMPOSITION. The row above already states the outstanding quantity, so the
            editor carries only what the planner decides. Every section renders whatever the
            answer is: a line with no warehouse says so where its Reserve rows would be, and a
            section that disappears reads as a screen that has not finished loading. */}
        <div className="space-y-3">
          <Block label="Reserve">
            {buying ? (
              <Muted>The whole line is being bought.</Muted>
            ) : (
              <div className="space-y-2">
                {draft.reserve.length === 0 ? (
                  <Muted>The sales order states no warehouse for this line.</Muted>
                ) : (
                  draft.reserve.map((row, index) => {
                    const available = availableAt(
                      locations,
                      row.warehouse_id,
                      row.location,
                    );
                    // OI-CLOSED-LINE (owner, 12 Oct 2026): only the part not yet delivered
                    // needs free stock, and a shortfall there is a warning, never a block -
                    // Confirm records it and tells purchasing. The row is never removed.
                    const over =
                      available !== null &&
                      toMinor(row.qty) - (reserveCredits[index] ?? 0) > toMinor(available);
                    return (
                      <div
                        key={row.key}
                        className="flex flex-wrap items-center gap-2"
                      >
                        <NumberInput
                          min="0"
                          step="any"
                          value={row.qty}
                          error={quantityError(row.qty)}
                          disabled={locked}
                          aria-label={`Reserve at ${row.location ?? 'the fulfilment location'}`}
                          onChange={(event) => {
                            const next = [...draft.reserve];
                            next[index] = { ...row, qty: event.target.value };
                            editComposition({ ...draft, reserve: next });
                          }}
                          className="h-8 w-24 tabular-nums"
                        />
                        <span className="text-sm">
                          {row.location ?? 'Location not set'}
                        </span>
                        {/* The SERVER's figure for this location, beside the box it bounds. */}
                        {available !== null && (
                          <span
                            data-testid={`line-reserve-available-${row.key}`}
                            className={cn(
                              'text-sm tabular-nums',
                              over ? 'text-warning' : 'text-muted-foreground',
                            )}
                          >
                            {over
                              ? `Only ${available} available here`
                              : `${available} available`}
                          </span>
                        )}
                      </div>
                    );
                  })
                )}

                {locked ? null : reserveCandidates.length === 0 ? (
                  <Muted>No other location holds free stock of this item.</Muted>
                ) : (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setAddingReserve(true)}
                  >
                    <Plus className="size-4" aria-hidden />
                    Add location
                  </Button>
                )}
              </div>
            )}
          </Block>

          <Block label="Borrow">
            <div className="space-y-3">
              {buying ? (
                <Muted>The whole line is being bought.</Muted>
              ) : draft.borrow.length === 0 ? (
                <Muted>Nothing is borrowed on this line.</Muted>
              ) : (
                draft.borrow.map((row, index) => (
                  <div key={row.key} className="space-y-1.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <NumberInput
                        min="0"
                        step="any"
                        value={row.qty}
                        error={quantityError(row.qty)}
                        disabled={locked}
                        aria-label={`Borrow from ${row.donor_project_ref ?? row.warehouse_code}`}
                        onChange={(event) => {
                          const next = [...draft.borrow];
                          next[index] = { ...row, qty: event.target.value };
                          setBorrow(next);
                        }}
                        className="h-8 w-24 tabular-nums"
                      />
                      <span className="min-w-0 truncate text-sm">
                        {row.donor_project_ref
                          ? `${row.donor_project_ref} at ${row.warehouse_code}`
                          : row.warehouse_code}
                      </span>
                      {locked ? null : (
                        <Button
                          type="button"
                          mode="icon"
                          variant="dim"
                          aria-label={`Remove the borrow from ${row.donor_project_ref ?? row.warehouse_code}`}
                          onClick={() =>
                            setBorrow(
                              draft.borrow.filter(
                                (entry) => entry.key !== row.key,
                              ),
                            )
                          }
                        >
                          <Trash2 />
                        </Button>
                      )}
                    </div>
                  </div>
                ))
              )}

              {buying || locked ? null : candidates.length === 0 ? (
                <Muted>
                  No other location or project holds free stock of this item.
                </Muted>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setAdding(true)}
                >
                  <Plus className="size-4" aria-hidden />
                  Add a borrow
                </Button>
              )}
            </div>
          </Block>

          <Block label="Buy">
            <div className="space-y-2">
              {/* BUY-FIRST-PANEL: typed like a Reserve row. While the whole line is being
                  bought the switch states the quantity, so the box steps aside. */}
              {!buying && (
                <div className="flex flex-wrap items-center gap-2">
                  <NumberInput
                    min="0"
                    step="any"
                    value={draft.buy_qty}
                    error={quantityError(draft.buy_qty)}
                    disabled={locked}
                    aria-label="Buy quantity"
                    data-testid={`line-buy-qty-${contribution.key}`}
                    onChange={(event) => typeBuy(event.target.value)}
                    className="h-8 w-24 tabular-nums"
                  />
                </div>
              )}
              {/* Under the box, where Reserve and Borrow keep their own secondary action (owner
                  hand test, 14 Oct 2026: a switch squeezed beside the box read as clutter). The
                  whole-line switch is drawn as the same outline button as "Add location",
                  pressed while on; it keeps the switch role, so it still reads as on or off.
                  No manual Order back (owner, 14 Oct 2026: "borrow = order back"): a Borrow
                  already reads ORDER BACK to purchasing, so a Buy is always a plain ORDER. */}
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  role="switch"
                  aria-checked={buying}
                  aria-label="Buy the whole line"
                  selected={buying}
                  disabled={locked}
                  onClick={() => setBuying(!buying)}
                >
                  {buying ? (
                    <Check className="size-4" aria-hidden />
                  ) : (
                    <ShoppingCart className="size-4" aria-hidden />
                  )}
                  {buying ? `Buy the whole ${draft.open_qty}` : 'Buy the whole line'}
                </Button>
              </div>
            </div>
          </Block>
        </div>

        {/* WHAT WOULD BE CONFIRMED, and the two verbs. */}
        <div className="space-y-3">
          <div>
            <p className="text-2xs uppercase tracking-wide text-muted-foreground">
              Decision
            </p>
            <p
              data-testid={`line-decision-summary-${contribution.key}`}
              className="break-words text-sm tabular-nums"
            >
              {summary}
            </p>
            {/* A few words, and only while the composition does not add up (R9). The two
                helpers settle what is unassigned in one click; a borrow needs a donor and a
                reason, so there is no Rest to Borrow (BUY-FIRST-PANEL). */}
            {balance.balanced || cancelSaved ? null : (
              <div className="flex flex-wrap items-center gap-2">
                <p
                  data-testid={`line-decision-hint-${contribution.key}`}
                  className="text-sm text-destructive tabular-nums"
                >
                  {balance.differenceMinor > 0
                    ? `Over by ${fromMinor(balance.differenceMinor)}`
                    : `Unassigned ${fromMinor(unassignedMinor)}`}
                </p>
                {unassignedMinor > 0 && !locked && !buying && (
                  <>
                    <Button type="button" size="sm" variant="outline" onClick={restToBuy}>
                      Rest to Buy
                    </Button>
                    {draft.reserve.length > 0 && (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={restToReserve}
                      >
                        Rest to Reserve
                      </Button>
                    )}
                  </>
                )}
              </div>
            )}
            {otherBlockers.length > 0 && (
              <ul className="mt-1 space-y-0.5">
                {otherBlockers.map((blocker) => (
                  <li
                    key={blocker}
                    className="break-words text-sm text-destructive"
                  >
                    {blocker}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="space-y-1">
            <label
              className="flex items-center gap-1.5 text-2xs uppercase tracking-wide text-muted-foreground"
              htmlFor={`line-reason-${contribution.key}`}
            >
              Reason
              {needsReason ? (
                <span className="text-destructive"> *</span>
              ) : null}
              {/* D3 (S2): a discontinued Buy no longer needs a reason (the gate is gone, S1) -
                  the badge only says what the box is silent about; the box itself carries no
                  asterisk for this cause alone. */}
              {draft.is_discontinued && toMinor(draft.buy_qty) > 0 && (
                <Badge variant="warning" appearance="light" size="sm">
                  Discontinued
                </Badge>
              )}
            </label>
            <Textarea
              id={`line-reason-${contribution.key}`}
              rows={2}
              value={reason}
              disabled={locked}
              placeholder="In your own words"
              onChange={(event) => {
                setReason(event.target.value);
                setDirty(true);
              }}
            />
          </div>

          {/* The planner's other answer (R10): the numbers themselves look wrong. It rides on
              the decision so the flag reaches the same record the composition does. */}
          <label
            className="flex items-start gap-2 text-sm"
            htmlFor={`line-suspect-${contribution.key}`}
          >
            <Checkbox
              id={`line-suspect-${contribution.key}`}
              checked={suspected}
              disabled={locked}
              onCheckedChange={(value) => {
                setSuspected(value === true);
                setDirty(true);
              }}
              className="mt-0.5"
            />
            <span>
              This might be a system problem, flag it for investigation
            </span>
          </label>

          {locked ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setLocked(false)}
            >
              <Pencil className="size-4" aria-hidden />
              Amend
            </Button>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              {covered ? (
                // Wrapped for the LIFE of a covered line, not only while `alreadyConfirmed`
                // is true: that flips as the draft moves (a tick, a composition edit), and
                // swapping the wrapped Button for a bare one on that same flip would unmount
                // and remount the button - the element a planner (or a test) is mid-focus or
                // mid-click on. `disabled` and whether the tooltip carries the sentence are
                // what move; the trigger itself does not.
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span
                      tabIndex={0}
                      className="inline-flex"
                      data-testid={`save-decision-trigger-${contribution.key}`}
                    >
                      <Button
                        type="button"
                        size="sm"
                        // `pending` (fix round 2): this Save is LIVE once Amend has unlocked
                        // the row, so it needs the same in-flight guard the plain Save has -
                        // a double-click here fired two `onDecide` calls.
                        disabled={
                          saved || pending || alreadyConfirmed || badQuantities || (!approving && !canSave)
                        }
                        onClick={save}
                      >
                        {saveButtonLabel}
                      </Button>
                    </span>
                  </TooltipTrigger>
                  {alreadyConfirmed && (
                    // 375px (review round 2): the sentence alone is wider than the viewport,
                    // so it needs its own max-width and Radix's own collision padding rather
                    // than the primitive's ordinary width - every OTHER tooltip on the board
                    // stays at that default.
                    <TooltipContent
                      className="max-w-[min(20rem,calc(100vw-2rem))] text-pretty"
                      collisionPadding={16}
                    >
                      {CONFIRMED_LINE_TITLE}
                    </TooltipContent>
                  )}
                </Tooltip>
              ) : (
                <Button
                  type="button"
                  size="sm"
                  // Disabled ON the saved state too (D4): there is nothing left to save, and
                  // a live button under the word "Saved" invites a second write of the same
                  // row. `pending` (fix round 1, S4): the write this same click just started
                  // is still on the wire.
                  disabled={saved || pending || badQuantities || (!approving && !canSave)}
                  onClick={save}
                >
                  {saveButtonLabel}
                </Button>
              )}
              {activelyCovered ? (
                // R3(b) (`PLAN-board-reject-on-confirmed-line.md`, 22 Sep 2026): Reject on an
                // ACTIVELY covered line is no longer dead weight - it follows the UNCOVERED
                // branch's own rule (disabled only while the reason is blank), because it now
                // stages a withdrawal Confirm carries out. `CONFIRMED_LINE_TITLE` stays on
                // Save alone (R2 of #989 still holds there); this tooltip only ever carries
                // the "say why" sentence, and only while blank, so hovering an ENABLED Reject
                // shows nothing (AC-F2/AC-F3). The wrapped span stays for the life of the
                // covered line (AC-F4) - only `disabled` and whether the tooltip has content
                // move.
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span
                      tabIndex={0}
                      className="inline-flex"
                      data-testid={`reject-decision-trigger-${contribution.key}`}
                    >
                      {rejectButton}
                    </span>
                  </TooltipTrigger>
                  {reason.trim().length === 0 && (
                    <TooltipContent
                      className="max-w-[min(20rem,calc(100vw-2rem))] text-pretty"
                      collisionPadding={16}
                    >
                      Say why this line is being refused first.
                    </TooltipContent>
                  )}
                </Tooltip>
              ) : covered ? (
                // N1 (fix round): a covered-but-not-ACTIVELY-covered line is the
                // inquiry-only kind (#875, no `SOSupplyDecision` to withdraw from) -
                // Confirm has no seam for it, so Reject keeps the ORIGINAL #989 shape:
                // disabled outright, with `CONFIRMED_LINE_TITLE`, same as Save.
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span
                      tabIndex={0}
                      className="inline-flex"
                      data-testid={`reject-decision-trigger-${contribution.key}`}
                    >
                      <Button type="button" size="sm" variant="outline" disabled>
                        <X className="size-4" aria-hidden />
                        Reject
                      </Button>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent
                    className="max-w-[min(20rem,calc(100vw-2rem))] text-pretty"
                    collisionPadding={16}
                  >
                    {CONFIRMED_LINE_TITLE}
                  </TooltipContent>
                </Tooltip>
              ) : (
                // No `title` here (fix round 2, nit): the Button primitive's
                // `disabled:pointer-events-none` means a `title` on a disabled button never
                // reaches a real hover (see the comment on `CONFIRMED_LINE_TITLE` above), so
                // it was dead - no tooltip added either.
                rejectButton
              )}
            </div>
          )}
        </div>
      </div>

      {adding && (
        <BorrowAddDialog
          lineNo={contribution.line_no}
          itemCode={contribution.item_code}
          lineId={contribution.project_line_id}
          candidates={candidates}
          onDone={() => setAdding(false)}
          onAdd={(candidate: BorrowCandidate, qty, borrowReason) =>
            addBorrows([{ candidate, qty }], borrowReason)
          }
          onAddMany={addBorrows}
        />
      )}

      {addingReserve && (
        <ReserveAddDialog
          lineNo={contribution.line_no}
          itemCode={contribution.item_code}
          locations={reserveCandidates}
          // S-1 (fix round 7): NOT `open - total` while Buy follows the remainder - `total`
          // then includes D7's derived Buy, so on an already-composed line (BRW 62 + Buy 73) it
          // read 0 and the dialog fell back to the location's WHOLE free stock
          // (`ReserveAddDialog.openingQty`). A PINNED Buy (BUY-FIRST-PANEL) is the planner's
          // own number, so what is left for stock is the open quantity minus everything.
          openRemainder={fromMinor(
            Math.max(
              balance.openMinor -
                (buyPinned ? balance.totalMinor : balance.totalMinor - balance.buyMinor),
              0,
            ),
          )}
          onDone={() => setAddingReserve(false)}
          onAdd={(location, qty) =>
            editComposition({
              ...draft,
              reserve: [
                ...draft.reserve,
                {
                  key: `reserve-${location.warehouse_id}-${draft.reserve.length}`,
                  location: location.location,
                  warehouse_id: location.warehouse_id ?? '',
                  qty,
                  reason: '',
                },
              ],
            })
          }
        />
      )}
    </div>
  );
}

/**
 * The draft this panel opens on: the engine's proposal, with the planner's own amendment
 * laid over it when they have already made one on this board.
 *
 * `amendDraftFrom` is the one seeder (it knows the frozen decision, the own-location row and
 * the fallbacks), so this only replaces the QUANTITIES a saved amendment states. Without the
 * overlay, collapsing an amended row and opening it again showed the engine's numbers under
 * a pill reading Amended.
 */
function draftFor(
  contribution: BoardContribution,
  decision: BoardDecision | null,
): DraftLine {
  // No manual Order back on this panel (owner, 14 Oct 2026: borrow = order back): a frozen or
  // saved decision that carried one opens without it, so the next Save never re-sends a flag
  // the planner can no longer see.
  const base = {
    ...amendDraftFrom(contribution),
    order_back: false,
    cited_document: '',
  };
  // A saved Transfer to Cancel SO carries no composition: open on zeros, not the suggestion.
  if (decision?.verdict === 'cancelled') {
    return {
      ...base,
      reserve: base.reserve.map((row) => ({ ...row, qty: '0' })),
      borrow: [],
      timely_spo_qty: '0',
      buy_qty: '0',
    };
  }
  // An `approved` decision is NOT overlaid even though it carries rows (D11): an approval
  // means "the engine's composition", and `confirmLinesFor` (`fulfilmentBoard.ts`) commits
  // the CURRENT proposal for it, not the rows stored at the time - so the panel opens on the
  // same current proposal, or the two would disagree on a line whose suggestion moved after
  // it was approved (BOARD-DECIDE-RESEED review round 1).
  if (!decision || decision.verdict !== 'amended' || !decision.reserve)
    return base;

  const byWarehouse = new Map(
    decision.reserve.map((row) => [row.warehouse_id, row.qty]),
  );
  const reserve = base.reserve.map((row) => ({
    ...row,
    qty: byWarehouse.get(row.warehouse_id) ?? '0',
  }));
  for (const row of decision.reserve) {
    if (reserve.some((seeded) => seeded.warehouse_id === row.warehouse_id))
      continue;
    reserve.push({
      key: `reserve-${row.location ?? row.warehouse_id}`,
      location: row.location ?? null,
      warehouse_id: row.warehouse_id,
      qty: row.qty,
      reason: '',
    });
  }

  return {
    ...base,
    reserve,
    borrow: (decision.borrow ?? []).map((row, index) => ({
      key: `borrow-${row.warehouse_id}-${index}`,
      source: row.source,
      warehouse_code: row.warehouse_code ?? '',
      warehouse_id: row.warehouse_id,
      donor_project_ref: row.donor_project_ref ?? null,
      donor_project_id: row.donor_project_id ?? null,
      qty: row.qty,
      reason: row.reason,
      // A donor's position is a fact about NOW; an amendment held in the draft does not
      // carry one, and printing zeroes as "0 free" would say the donor is empty.
      donor_impact: {
        free_before: '0',
        free_after_full_borrow: '0',
        committed_qty: '0',
      },
      donor_core_line_id: row.donor_core_line_id ?? null,
      donor_so_number: row.donor_so_number ?? null,
      donor_line_no: row.donor_line_no ?? null,
      donor_agent_code: row.donor_agent_code ?? null,
      same_agent: row.same_agent ?? false,
      donor_required_date: row.donor_required_date ?? null,
    })),
    timely_spo_qty: decision.timely_spo_qty ?? base.timely_spo_qty,
    buy_qty: decision.buy_qty ?? '0',
    buy_reason: decision.buy_reason ?? '',
  };
}

/**
 * What a location has available, as the SERVER stated it for this cell. Matched by id first
 * and by code as the fallback, the same order every other lookup on this board uses.
 */
function availableAt(
  locations: BoardCellLocation[],
  warehouseId: string,
  code?: string | null,
): string | null {
  const found =
    locations.find(
      (row) => row.warehouse_id && row.warehouse_id === warehouseId,
    ) ?? (code ? locations.find((row) => row.location === code) : undefined);
  // NOT `available_qty` (B2, code review round 3): it is AutoCount's own SIGNED whole-book
  // figure - negative at a location like MWH-IB (-15514) - so it flagged the ENGINE'S OWN
  // suggestions as oversold. `qty_free_remaining` (falling back to `qty_free` before any
  // proposal has drawn it down) is the figure `reserveCandidates` and this dialog's own
  // `ReserveAddDialog.openingQty` already offer on, so the echo agrees with what a planner
  // was shown when they picked the location. The server's own guard
  // (`_check_reserve_against_on_hand`, on hand minus confirmed holds) stays the authority at
  // Confirm; this is only the echo shown while typing.
  return found?.qty_free_remaining ?? found?.qty_free ?? null;
}

/** One editable section, labelled the way the sheet labels it, so the two read the same. */
function Block({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <p className="mb-1 text-2xs uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <div className="min-w-0">{children}</div>
    </section>
  );
}

/** Block, not inline: two stated absences in one section must not run into one sentence. */
function Muted({ children }: { children: React.ReactNode }) {
  return (
    <span className="block text-sm text-muted-foreground">{children}</span>
  );
}
