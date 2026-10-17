/**
 * The line balance CS is editing against, and everything that stops a Confirm.
 *
 * Pure on purpose: the sheet reads these on every keystroke, and the same rules are
 * rechecked by the server against authoritative facts at confirmation (plan 3.1 step 5).
 * The browser copy exists to say WHICH line is wrong before the round trip, never to be
 * the authority - a line that passes here can still fail there, and the failure names the
 * line by number and item code.
 *
 * Quantities travel as decimal strings (a float round trip loses the tail of a quantity
 * the customer signed for). Comparison is done in minor units at four decimal places,
 * which is the widest UOM precision the plan allows, so `10.1 + 20.2 === 30.3` reads as
 * balanced rather than as 0.0000000000000004 over.
 */
import { numberFieldError } from '@/lib/number-field';
import type {
  BorrowSource,
  ConfirmLine,
  SupplyLine,
  BorrowDonorImpact,
} from '../types/fulfilmentPlanning.types';

/**
 * One whole unit, in the minor units every comparison here is done in. EXPORTED because
 * ladder v8's pool share has to floor to a WHOLE unit (`poolShare.ts`, R-K: "47 free reads
 * 23"), and a second copy of this number is how two files come to disagree about precision.
 */
export const QTY_SCALE = 10_000;
const SCALE = QTY_SCALE;

/** A quantity that is not a number at all counts as 0, so a half-typed input never NaNs. */
export function toMinor(qty: string | number | null | undefined): number {
  const value = typeof qty === 'number' ? qty : Number.parseFloat(String(qty ?? '').trim());
  if (!Number.isFinite(value)) return 0;
  return Math.round(value * SCALE);
}

/** The sentence for a quantity box holding text that is not a number; none for blank or valid. */
export function quantityError(qty: string | number | null | undefined): string | undefined {
  if (qty === null || qty === undefined || String(qty).trim() === '') return undefined;
  return numberFieldError(qty);
}

export function fromMinor(minor: number): string {
  const value = minor / SCALE;
  // Trailing zeroes off: "40" reads as a quantity, "40.0000" reads as a machine.
  return String(Number(value.toFixed(4)));
}

/** What CS is holding for one line before it is sent. Quantities stay as typed. */
export interface DraftBorrow {
  /** Local key so two borrows from the same warehouse can both exist while being typed. */
  key: string;
  source: BorrowSource;
  warehouse_code: string;
  warehouse_id: string;
  donor_project_ref?: string | null;
  donor_project_id?: string | null;
  qty: string;
  reason: string;
  donor_impact: BorrowDonorImpact;
  /**
   * Ladder v2 group borrow (`PLAN-demo-followups-19aug-ladder-v2.md` section E.4): the
   * donor's own sales-order line. Present, this row is checked against that line's live
   * committed quantity at confirm, not against free stock.
   */
  donor_core_line_id?: string | null;
  donor_so_number?: string | null;
  donor_line_no?: number | null;
  donor_agent_code?: string | null;
  same_agent?: boolean;
  donor_required_date?: string | null;
  /**
   * Ladder v7.1 step 3 (S4): the incoming DOCUMENT this borrow comes off - addressed by
   * `supply_key` (never rendered) and named by `supply_document`. Present, the Confirm
   * checks it against the document's own open balance rather than against free stock at a
   * bin, and moves the placement link onto the asking line.
   */
  supply_key?: string | null;
  supply_document?: string | null;
  arrival_date?: string | null;
}

export interface DraftReserve {
  key: string;
  /** Warehouse code, shown. */
  location?: string | null;
  warehouse_id: string;
  qty: string;
  reason: string;
}

export interface DraftLine {
  project_line_id: string;
  line_no: number;
  item_code?: string | null;
  open_qty: string;
  timely_spo_qty: string;
  reserve: DraftReserve[];
  borrow: DraftBorrow[];
  buy_qty: string;
  buy_reason: string;
  is_discontinued: boolean;
  /** This Buy is an order back against a document already on its way (part 2 4b). */
  order_back: boolean;
  /** The document CS cited for it, if any. Tried first by the auto-link walk. */
  cited_document: string;
}

/** The proposal as CS first sees it: the engine's components, unedited. */
export function draftFromLine(line: SupplyLine): DraftLine {
  const timely = line.components
    .filter((component) => component.kind === 'timely_spo')
    .reduce((total, component) => total + toMinor(component.qty), 0);
  const buy = line.components.find((component) => component.kind === 'buy');
  return {
    project_line_id: line.project_line_id,
    line_no: line.line_no,
    item_code: line.item_code,
    open_qty: line.open_qty,
    timely_spo_qty: fromMinor(timely),
    reserve: line.components
      .filter((component) => component.kind === 'reserve')
      .map((component, index) => ({
        key: `reserve-${line.project_line_id}-${index}`,
        location: component.source_location,
        warehouse_id: component.source_warehouse_id ?? '',
        qty: component.qty,
        reason: component.reason,
      })),
    borrow: line.components
      .filter((component) => component.kind === 'borrow')
      .map((component, index) => ({
        key: `borrow-${line.project_line_id}-${index}`,
        source: component.donor_project_ref ? 'other_project' : 'other_location',
        warehouse_code: component.source_location ?? '',
        warehouse_id: component.source_warehouse_id ?? '',
        donor_project_ref: component.donor_project_ref,
        donor_project_id: component.donor_project_id,
        qty: component.qty,
        /**
         * THE ENGINE'S OWN SENTENCE, until a person writes their own over it.
         *
         * A borrow needs a reason before the order can be confirmed (AC-B09,
         * `lineBlockers`), and an engine-proposed borrow arrives with `cs_reason` null -
         * nobody has typed anything yet. Seeding empty therefore disabled Confirm Project SO
         * on a line nobody disagreed with, until the planner retyped the sentence the engine
         * had already written. The board has always seeded `source.reason` here
         * (`boardAmend.draftFromSources`), and the two surfaces compose on this same draft:
         * one of them refusing what the other accepts is the bug this closes.
         */
        reason: component.cs_reason ?? component.reason ?? '',
        donor_impact: {
          free_before: '0',
          free_after_full_borrow: '0',
          committed_qty: '0',
        },
        // A group_borrow the ladder auto-proposed (rung === 'group_borrow') already
        // names its donor SO line; carried through so re-confirming the proposal as it
        // stands still checks against that line's live commitment, not free stock.
        donor_core_line_id: component.donor_core_line_id,
        donor_so_number: component.donor_so_number,
        donor_line_no: component.donor_line_no,
        donor_agent_code: component.donor_agent_code,
        same_agent: component.same_agent,
        // The donor's own delivery date: the order-back's urgency, and the month the debt
        // lands in. Dropped here it was lost from the posted borrow the moment an engine
        // proposal went through this draft - the board carries it for the same reason.
        donor_required_date: component.donor_required_date ?? null,
        // LADDER v7.1 STEP 3 (S4): the DOCUMENT this borrow comes off, carried verbatim so a
        // proposal approved as it stands still moves the placement the engine named. Dropped
        // here, the Confirm would re-check the quantity against free stock at a bin holding a
        // container that has not landed, and refuse the engine's own answer.
        supply_key: component.supply_key ?? null,
        supply_document: component.supply_document ?? null,
        arrival_date: component.arrival_date ?? null,
      })),
    buy_qty: buy?.qty ?? '0',
    buy_reason: buy?.cs_reason ?? '',
    is_discontinued: line.is_discontinued,
    // A fresh proposal is never an order back: the engine proposes a purchase, and only a
    // person can say the quantity is owed against something already on its way.
    order_back: false,
    cited_document: '',
  };
}

export interface LineBalance {
  openMinor: number;
  totalMinor: number;
  /** Composed minus open. Positive is over the need, negative is short of it. */
  differenceMinor: number;
  balanced: boolean;
  reserveMinor: number;
  borrowMinor: number;
  buyMinor: number;
  timelyMinor: number;
}

export function lineBalance(draft: DraftLine): LineBalance {
  const timelyMinor = toMinor(draft.timely_spo_qty);
  const reserveMinor = draft.reserve.reduce((total, row) => total + toMinor(row.qty), 0);
  const borrowMinor = draft.borrow.reduce((total, row) => total + toMinor(row.qty), 0);
  const buyMinor = toMinor(draft.buy_qty);
  const totalMinor = timelyMinor + reserveMinor + borrowMinor + buyMinor;
  const openMinor = toMinor(draft.open_qty);
  return {
    openMinor,
    totalMinor,
    differenceMinor: totalMinor - openMinor,
    balanced: totalMinor === openMinor,
    reserveMinor,
    borrowMinor,
    buyMinor,
    timelyMinor,
  };
}

/**
 * What each SITE POOL may lend THIS line, and the one net over all of them (D5).
 *
 * The whole-line rule has exactly one carve-out (R-C): a site pool keeps a share back for
 * dealers and lends the rest, so a line bigger than that share is legitimately "BRW 62 +
 * Buy 73" - which is what the engine itself proposes inside the immediate window. The
 * server's confirm admits it (`ProjectSupplyService._is_pool_share_split`); without these
 * figures the client refused the engine's own suggestion before it could be saved.
 *
 * Both numbers are the SERVER's, read off the cell's own site pool rows
 * (`BoardCellLocation.available_for_project` and `net` - see `poolShareLimitsOf`), never
 * recomputed here: the allowance is what the walk obeyed, and a second arithmetic would be a
 * second opinion about it.
 *
 * Absent (the per-order SHEET, which has no cell to read) means no carve-out at all, which
 * is the rule as it stood: a screen that cannot check the allowance must not assume it.
 */
export interface PoolShareLimits {
  /** Warehouse id -> that pool's `available_for_project`. A bin absent from it is not a pool. */
  allowanceByWarehouseId: Record<string, string | null | undefined>;
  /** The five site pools' NET, which bounds the pools TOGETHER (R-D). */
  net: string | null | undefined;
}

/**
 * The pool-share carve-out, judged (D5). `null` when this composition is not one, and the
 * sentence that refuses it when it is one and overdraws.
 *
 * Deliberately NARROWER than "some of it came from a pool": every from-stock unit has to be
 * a reserve at a pool that STATES an allowance, because a borrow, a timely SPO or an own-bin
 * reserve beside a Buy is the mix the rule exists to refuse.
 *
 * ONE difference from the server, stated rather than hidden: the server also requires the
 * line to be inside `immediate_window_days`, a figure the board does not send. A far-dated
 * line composed this way by hand is therefore allowed here and refused at confirm, with the
 * server's own message - the engine never proposes one, so the only way to reach it is to
 * type it.
 */
function poolShareSplit(
  draft: DraftLine,
  limits: PoolShareLimits,
  subject: string,
  balance: LineBalance,
): { allowed: boolean; blocker?: string } {
  const whole =
    `Take the whole ${fromMinor(balance.openMinor)} from stock, or buy the whole ` +
    `${fromMinor(balance.openMinor)}.`;
  if (balance.borrowMinor > 0 || balance.timelyMinor > 0) return { allowed: false };
  const rows = draft.reserve.filter((row) => toMinor(row.qty) > 0);
  if (rows.length === 0) return { allowed: false };
  const perPool = new Map<string, { location: string; minor: number; allowance: number }>();
  for (const row of rows) {
    const stated = limits.allowanceByWarehouseId[row.warehouse_id];
    if (stated === null || stated === undefined) return { allowed: false };
    const seen = perPool.get(row.warehouse_id);
    perPool.set(row.warehouse_id, {
      location: row.location || seen?.location || 'this pool',
      minor: (seen?.minor ?? 0) + toMinor(row.qty),
      allowance: toMinor(stated),
    });
  }
  for (const pool of perPool.values()) {
    if (pool.minor > pool.allowance) {
      return {
        allowed: false,
        blocker:
          `${subject}: ${pool.location} can spare ${fromMinor(pool.allowance)} for this ` +
          `line, and ${fromMinor(pool.minor)} was asked for. ${whole}`,
      };
    }
  }
  const pooled = [...perPool.values()].reduce((total, pool) => total + pool.minor, 0);
  const net = Math.max(toMinor(limits.net ?? '0'), 0);
  if (pooled > net) {
    return {
      allowed: false,
      blocker:
        `${subject}: the site pools net ${fromMinor(net)} between them, and ` +
        `${fromMinor(pooled)} was asked for. ${whole}`,
    };
  }
  return { allowed: true };
}

/**
 * Everything that stops this line being confirmed, in the order CS can act on it. One
 * sentence each, naming the line, because the Confirm button is at the foot of a sheet
 * that may hold ten lines and "something is wrong" is not an instruction.
 *
 * `limits` is the site pools' own allowances when the caller has them (the BOARD does; the
 * sheet does not) - see `PoolShareLimits`.
 *
 * `options.mixAllowed` (the captain, 8 Sep 2026): the whole-line rule (AC-L5) stays for the
 * ENGINE's proposals and for the per-order SHEET, where a mix is never a deliberate manual
 * decision. The fulfilment-planning BOARD passes `mixAllowed: true` on the strength of its
 * own `reason` field, not of this function: the panel seeds `reason` from the frozen
 * decision's own `amend_reason` at mount (`BoardLineDecisionPanel`), and `amendNeedsReason`
 * only asks whether the draft has moved SINCE THEN, so an unchanged frozen mix re-saves on
 * the reason it already carried, not a freshly typed one. The server's own gate reads that
 * same field off the confirm line, not a client-only flag.
 */
export function lineBlockers(
  draft: DraftLine,
  limits?: PoolShareLimits,
  options?: { mixAllowed?: boolean },
): string[] {
  const blockers: string[] = [];
  const subject = `Line ${draft.line_no}${draft.item_code ? `, ${draft.item_code}` : ''}`;
  const balance = lineBalance(draft);

  const typed = [
    draft.timely_spo_qty,
    draft.buy_qty,
    ...draft.reserve.map((row) => row.qty),
    ...draft.borrow.map((row) => row.qty),
  ];
  if (typed.some((qty) => quantityError(qty) !== undefined)) {
    // Bad input is never read as 0: the line is refused until the box holds a number.
    blockers.push(`${subject}: a quantity is not a number.`);
    return blockers;
  }

  if (
    balance.reserveMinor < 0 ||
    balance.borrowMinor < 0 ||
    balance.buyMinor < 0 ||
    draft.reserve.some((row) => toMinor(row.qty) < 0) ||
    draft.borrow.some((row) => toMinor(row.qty) < 0)
  ) {
    blockers.push(`${subject}: a quantity is below zero.`);
  }

  if (!balance.balanced) {
    const over = balance.differenceMinor > 0;
    blockers.push(
      `${subject}: the components are ${over ? 'over' : 'short of'} the open quantity by ${fromMinor(
        Math.abs(balance.differenceMinor),
      )}.`,
    );
  }

  // The whole-line rule, AC-L5 (the captain, 25 August 2026): "a line is either wholly
  // covered from stock (own group, pools, borrow, incoming in any mix) or wholly Buy".
  // Said only on a line that already ADDS UP: one refusal at a time, and a line short of
  // its open quantity has not finished stating a composition to judge.
  //
  // LIFTED when the caller states `mixAllowed` (the captain, 8 Sep 2026): the sheet never
  // sets it, so this still fires there exactly as before; the board sets it because a board
  // amendment that mixes stock and Buy is a legal hand composition once it carries a reason,
  // and `amendNeedsReason` already makes that reason mandatory before Save unblocks.
  const fromStockMinor = balance.timelyMinor + balance.reserveMinor + balance.borrowMinor;
  if (!options?.mixAllowed && balance.balanced && fromStockMinor > 0 && balance.buyMinor > 0) {
    // R-C's one carve-out first (D5): a site pool's share beside a Buy is a composition the
    // engine MAKES, so refusing it here refused the suggestion on screen.
    const split = limits
      ? poolShareSplit(draft, limits, subject, balance)
      : { allowed: false, blocker: undefined };
    if (split.blocker) {
      blockers.push(split.blocker);
    } else if (!split.allowed) {
      blockers.push(
        `${subject}: a line is either met wholly from stock or wholly bought. This one ` +
          `mixes ${fromMinor(fromStockMinor)} from stock with a Buy of ${fromMinor(
            balance.buyMinor,
          )}.`,
      );
    }
  }

  for (const row of draft.borrow) {
    if (toMinor(row.qty) > 0 && !row.reason.trim()) {
      const donor = row.donor_project_ref || row.warehouse_code || 'the donor';
      blockers.push(`${subject}: the borrow from ${donor} needs a reason.`);
    }
  }

  return blockers;
}

export function draftBlockers(drafts: DraftLine[]): string[] {
  return drafts.flatMap((draft) => lineBlockers(draft));
}

/** The confirm payload. Zero-quantity components are dropped: they decide nothing. */
export function confirmLineFromDraft(draft: DraftLine): ConfirmLine {
  return {
    project_line_id: draft.project_line_id,
    timely_spo_qty: fromMinor(toMinor(draft.timely_spo_qty)),
    reserve: draft.reserve
      .filter((row) => toMinor(row.qty) > 0)
      .map((row) => ({ warehouse_id: row.warehouse_id, qty: fromMinor(toMinor(row.qty)) })),
    borrow: draft.borrow
      .filter((row) => toMinor(row.qty) > 0)
      .map((row) => ({
        source: row.source,
        warehouse_id: row.warehouse_id,
        donor_project_id: row.donor_project_id ?? null,
        qty: fromMinor(toMinor(row.qty)),
        reason: row.reason.trim(),
        donor_core_line_id: row.donor_core_line_id ?? null,
        donor_so_number: row.donor_so_number ?? null,
        donor_line_no: row.donor_line_no ?? null,
        donor_agent_code: row.donor_agent_code ?? null,
        same_agent: row.same_agent ?? false,
        donor_required_date: row.donor_required_date ?? null,
        // LADDER v7.1 STEP 3 (S4): the DOCUMENT this borrow comes off, carried verbatim so a
        // proposal approved as it stands still moves the placement the engine named. Dropped
        // here, the Confirm would re-check the quantity against free stock at a bin holding a
        // container that has not landed, and refuse the engine's own answer.
        supply_key: row.supply_key ?? null,
        supply_document: row.supply_document ?? null,
        arrival_date: row.arrival_date ?? null,
      })),
    buy_qty: fromMinor(toMinor(draft.buy_qty)),
    buy_reason: draft.buy_reason.trim() ? draft.buy_reason.trim() : null,
  };
}
