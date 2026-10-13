/**
 * ONE vocabulary for where supply comes from, used by the board grid, the board list, the cell
 * popover and the SO detail (PLAN-scm-cs-planning-uat.md section 2).
 *
 * WHAT DECIDES THE WORDS IS THE ENGINE'S `rung`, NEVER A WAREHOUSE CODE. This file was
 * `boardSuggestion.ts` and it split own/shared on the SITE PREFIX: a pool draw from `BRW` on a
 * line fulfilled from `BRW-BB` shared the prefix, so the shared pool read as "Use own location".
 * The captain, on the live cell: "Use own location, 71 from BRW reads wrong. Own location is the
 * line's -BB location. BRW is the SHARED pool." The prefix is a coincidence of naming; the rung
 * is the rule that actually fired, and it is the only input here.
 *
 * | Rung                              | Label                      | Colour  |
 * | --------------------------------- | -------------------------- | ------- |
 * | `group_take` (a Reserve)          | Use own location           | emerald |
 * | `group_take` (a `timely_spo`)     | Use incoming               | violet  |
 * | `order_borrow`                    | Borrow from another order  | amber   |
 * | `pool` (borrow half)              | Borrow from another order  | amber   |
 * | `supply_borrow`                   | Borrow incoming            | amber   |
 * | `cross_group_borrow`              | Borrow                     | amber   |
 * | `group_borrow`                    | Borrow from another order  | amber   |
 * | `pool` (free draw)                | Use BRW                    | sky     |
 * | `buy`                             | Buy                        | rose    |
 * | `incoming`                        | Use incoming               | violet  |
 *
 * `cross_group_borrow`, `group_borrow` and the `incoming` RUNG are FROZEN-SNAPSHOT rungs
 * under ladder v7.1: the engine writes none of them any more, and the board still renders
 * decisions that carry them.
 *
 * STEP 1 DRAWS TWO KINDS OF STOCK AND THEY ARE NOT THE SAME PROMISE. The engine composes the
 * group's floor stock as a Reserve and the group's share that is still on the water as a
 * `timely_spo` (`front_planning_engine._draw_group` / `_draw_other_groups`: `kind=TIMELY_SPO
 * if water else RESERVE`, both under `rung=group_take`), because a hold cannot be written
 * against goods a picker cannot walk to. Reading the rung alone printed both under "Use own
 * location", so a promise resting on a ship read exactly like one resting on a floor. The
 * COMPONENT KIND is what tells them apart, and it is consulted for that one rung.
 *
 * The rung strings are `app/services/scm/front_planning_engine.py`'s own constants
 * (`RUNG_POOL`, `RUNG_GROUP_TAKE`, ...), spelled here exactly as the engine spells them.
 *
 * A RESERVE THAT CARRIES NO RUNG IS READ ON THE OWNERSHIP GROUP, never on the site and never
 * on the exact code. Verified against the live board on 25 Aug: every source and every decision
 * row of a COVERED line arrives with `rung: null` (the board rebuilds a frozen composition
 * without it), so this fallback is what SO324132 rev 1 is actually rendered by. See
 * `fallbackRung`. A BORROW that carries no rung is read as a borrow instead - the group
 * reading would call a same-group donor "Use own location", and a borrow is never that.
 *
 * INCOMING IS A LIVE STEP AGAIN under ladder v7.1. It was history under v5 (the engine
 * proposed no such component, because an SPO sat inside the ownership group's net), and v7.1's
 * date-aware step 1 composes the group's water as `timely_spo` once more - so the kind counts
 * today's free incoming AND the frozen `timely_spo` of decisions taken under v3 and v4. It
 * stays its own rather than being folded into Buy: a snapshot is evidence of what was
 * promised, and adding it to Buy would report a past promise as a purchase nobody made.
 */

import { fromMinor, toMinor } from './supplyComposition';
import type { PillTone } from '@/components/common/PillOverflow';
import type {
  BoardCell,
  BoardContribution,
  BoardDecision,
  BoardLineDecision,
  BoardLineOrderInquiry,
} from '../types/fulfilmentPlanning.types';

/**
 * The ladder writing today's proposals, mirroring
 * `app.services.project_supply_service.LADDER_VERSION`.
 *
 * Its one job is to tell a FROZEN suggestion apart from a live one: a snapshot stamped with
 * anything else - or with nothing, from before the stamp existed - was composed under a rule
 * that no longer runs, and the screen labels it as history rather than passing it off as
 * today's answer (AC-V8).
 */
export const LADDER_VERSION = 'v8';

export type SupplyKind =
  | 'buy'
  | 'shared'
  | 'own'
  | 'borrow_order'
  | 'borrow_incoming'
  | 'borrow_other'
  | 'incoming';

/** What a person reads. The whole product says supply in these seven words and no others. */
export const LABELS: Record<SupplyKind, string> = {
  buy: 'Buy',
  shared: 'Use BRW',
  own: 'Use own location',
  borrow_order: 'Borrow from another order',
  borrow_incoming: 'Borrow incoming',
  // Owner, 9 Oct 2026: "just called it borrow cause it is not just other location, own
  // location also can borrow". The order borrow (switch ON) keeps its own longer words.
  borrow_other: 'Borrow',
  // "Use", because this half of step 1 owes nobody anything: the group's own incoming
  // document, taken free. "Incoming supply" said what it was and not what is being done
  // with it, which is the one thing that tells it from `borrow_incoming`.
  incoming: 'Use incoming',
};

/**
 * The same seven, short enough for a grid cell and a summary line ("Shared 71 · Buy 0").
 *
 * A cell is 150px wide; "Borrow from another order 71" does not fit in it, and truncating the
 * label would leave two kinds reading the same. The long form is the legend's and the card's.
 */
export const SHORT_LABELS: Record<SupplyKind, string> = {
  buy: 'Buy',
  shared: 'BRW',
  own: 'Own',
  borrow_order: 'Borrow (order)',
  borrow_incoming: 'Borrow (incoming)',
  borrow_other: 'Borrow',
  incoming: 'Incoming',
};

/**
 * Tailwind class tokens per kind: the paint for a bar segment or a legend swatch, and the
 * matching text colour for the word beside it.
 *
 * All THREE borrow kinds are amber, as the vocabulary table says. They are told apart by their
 * label, not by their shade: a borrow is the same decision made against different stock, and
 * what it costs - an order-back at the donor's date - does not change with the stock.
 */
export const COLOURS: Record<SupplyKind, { bar: string; text: string }> = {
  buy: { bar: 'bg-rose-500', text: 'text-rose-700' },
  shared: { bar: 'bg-sky-500', text: 'text-sky-700' },
  own: { bar: 'bg-emerald-500', text: 'text-emerald-700' },
  borrow_order: { bar: 'bg-amber-500', text: 'text-amber-700' },
  borrow_incoming: { bar: 'bg-amber-500', text: 'text-amber-700' },
  borrow_other: { bar: 'bg-amber-500', text: 'text-amber-700' },
  incoming: { bar: 'bg-violet-500', text: 'text-violet-700' },
};

/**
 * The SAME seven kinds, read onto `PillOverflow`'s own six-tone vocabulary (S3b, R-J). One
 * mapping, here, so the Sourced-from cell, the Stock tab's Taken cell and the board grid
 * strip cannot each invent their own answer to "what colour is a borrow".
 */
export const PILL_TONE: Record<SupplyKind, PillTone> = {
  buy: 'buy',
  shared: 'pool',
  own: 'own',
  borrow_order: 'borrow',
  borrow_incoming: 'borrow',
  borrow_other: 'borrow',
  incoming: 'spo',
};

/**
 * The fixed reading order: LADDER V7.1'S OWN WALK, left = the first consideration, right = the
 * last resort (the captain, 30 Aug 2026). The strip, the bar and the composition cards all read
 * it, so the screen asks the engine's questions in the order the engine asks them:
 *
 * 1. `own` - step 1 off the group's FLOOR stock.
 * 2. `incoming` - step 1 off the group's WATER, free and owed to nobody. The same step, split
 *    out because a promise resting on a ship is not the same promise as one on a floor.
 * 3. `borrow_order` - step 2, a later order's ON HAND, and the pool's borrow half (step 4b),
 *    which is the same debt against a different pile.
 * 4. `borrow_incoming` - step 3, the DOCUMENT a later order is waiting on. Reads 0 until S4
 *    lands the candidates, and is rendered anyway: a step nobody can see is a step nobody
 *    knows was asked.
 * 5. `borrow_other` - RETIRED by v7.1 (`RUNG_CROSS_GROUP_BORROW`: "the constant survives for
 *    reading frozen snapshots"). Nothing composed today is this kind; a decision frozen under
 *    an older ladder is, and it sits with the borrows because that is what it is.
 * 6. `shared` - step 4a, the site pool. LAST BEFORE BUY under v7.1: it used to sit second,
 *    which read as the pool being reached for before anybody's own order was.
 * 7. `buy` - the answer when every question above was answered no.
 *
 * It used to lead with Buy, which put the answer before the questions.
 *
 * What keeps a card or a bar comparable between two cells is that each kind is always in the
 * same place, whether it is 300 or absent.
 */
export const ORDER: SupplyKind[] = [
  'own',
  'incoming',
  'borrow_order',
  'borrow_incoming',
  'borrow_other',
  'shared',
  'buy',
];

/**
 * One piece of supply, in the shape both wire types already have.
 *
 * Structural rather than a union of `BoardSource | SupplyComponent`, because the two name the
 * warehouse differently (`location` / `source_location`) and are otherwise the same three
 * facts. Anything with a kind, a rung and a quantity can be described.
 */
export interface SupplyPart {
  kind?: string | null;
  rung?: string | null;
  qty: string;
  location?: string | null;
  /** `SupplyComponent`'s own spelling of `location`. */
  source_location?: string | null;
  /** The sales order a borrow takes FROM, when one is named. Tells the two borrows apart. */
  donor_so_number?: string | null;
}

/** The warehouse a part names, whichever of the two wire spellings it arrived under. */
export function locationOf(part: SupplyPart): string | null {
  return part.location ?? part.source_location ?? null;
}

/**
 * The ownership-group suffix a warehouse code carries: `BRW-BB` -> `BB`.
 *
 * THE SUFFIX AFTER THE FIRST HYPHEN, upper-cased, which is the backend's own rule
 * (`app/services/scm/sales_agent_service.group_of_warehouse_code`) spelled the same way here so
 * the two cannot drift. A plain site code (`BRW`, `MWH`, `DC1`, `WH3`, `RSW`) has no hyphen and
 * therefore no group: it is a POOL, not anyone's ownership group.
 *
 * Exported as `groupOfWarehouseCode` too (`PLAN-oi-request-cs-reserve.md` 3.9): the OI stock
 * grid needs the identical rule to decide `group=` vs `warehouse_id=` on `stock-detail`, and a
 * second copy of six lines of hyphen-splitting is how the two come to disagree.
 */
function groupOf(code: string | null | undefined): string | null {
  if (!code) return null;
  const text = String(code).trim();
  const cut = text.indexOf('-');
  if (cut < 0) return null;
  return (
    text
      .slice(cut + 1)
      .trim()
      .toUpperCase() || null
  );
}

/** Public name for `groupOf`, for a caller outside this module (`OrderInquiryStockGrid`). */
export function groupOfWarehouseCode(code: string | null | undefined): string | null {
  return groupOf(code);
}

/**
 * The rung a component with none of its own would have been produced by.
 *
 * A COVERED line's sources and every decision reserve row arrive with `rung: null` - verified
 * against the live board on 25 Aug: SO324132 rev 1's CWCY605 sends three reserve sources at
 * DC1-BB / MWH-BB / WH3-BB, all `rung: null`, because `_apply_frozen` rebuilds a frozen
 * composition without carrying the rung the engine froze. An uncovered line on the same board
 * carries `pool` / `buy` / `cross_group_borrow` correctly.
 *
 * So the fallback has to reproduce the ladder's own reading, and it is the OWNERSHIP GROUP that
 * does it, never the site. For a `BRW-BB` line, `DC1-BB` is the agent's OWN group at another
 * site - rung 2, "Use own location" - while `BRW` is the shared pool at this line's own site.
 * Comparing the exact code called DC1-BB somebody else's stock; comparing the site prefix called
 * BRW the line's own. The group suffix is the only comparison that reads both correctly.
 */
function fallbackRung(
  location: string | null,
  ownLocation: string | null | undefined,
): string {
  const group = groupOf(location);
  // No hyphen at all: a site pool (BRW / MWH / DC1 / WH3 / RSW), shared by every group there.
  if (!group) return 'pool';
  if (group === groupOf(ownLocation)) return 'group_take';
  // A warehouse in somebody ELSE's ownership group. Not the agent's to take, so it is a borrow.
  return 'cross_group_borrow';
}

/**
 * Owner rule, 12 Oct 2026 (`PLAN-plan-four-options-12oct.md`, Q2 + Q4(a)): every borrow READS
 * "Borrow", the line detail included. The planner no longer names a donor order or borrows a
 * later order's SPO, and an older decision that did still keeps its donor in its data; only
 * the word shown folds. The backend twin is `ladder_steps.ORDER_BORROW_NAMES_DONOR`;
 * `shownApart` on restores "Borrow from another order" and "Borrow incoming" as their own
 * kinds (tests that pin those words switch it on).
 */
export const borrowKinds = { shownApart: false };

export function rowOf(
  part: SupplyPart,
  ownLocation?: string | null,
): SupplyKind | null {
  const kind = rowOfLadder(part, ownLocation);
  if (
    !borrowKinds.shownApart &&
    (kind === 'borrow_order' || kind === 'borrow_incoming')
  ) {
    return 'borrow_other';
  }
  return kind;
}

/**
 * Which kind this piece of supply is. The rung decides; the code never does.
 *
 * `kind` is consulted in two places and no others. FIRST, where there is no rung to consult:
 * Buy and incoming carry their own kind, and a component frozen before ladder v2 - or drafted
 * in the Amend dialog, which sends no rung at all - carries none (`SupplyComponent.rung` is
 * optional for exactly that reason). SECOND, on `group_take`, the one rung that composes two
 * kinds: floor stock as a Reserve, water as a `timely_spo`. That is not the code deciding -
 * the engine stamped the kind, and the two are different promises.
 *
 * Two different fallbacks, because a reserve and a borrow are not the same question:
 *
 * * an unrunged RESERVE is read on the OWNERSHIP GROUP (`fallbackRung`): the agent's own group
 *   at any site is "Use own location", a plain site code is the shared pool, and anybody
 *   else's group is stock this line has to borrow;
 * * an unrunged BORROW is a BORROW, whatever the location says. Reading it on the group would
 *   call a borrow from a donor at the line's own `-BB` location "Use own location", which is
 *   the one thing a borrow is not: the quantity belongs to another order and it raises an
 *   order-back. It reads `borrow_order` when the donor sales order is named and
 *   `borrow_other` when it is not, which is exactly what those two words distinguish.
 *
 * `rowOf` is this, with the four-options borrow fold applied on top.
 */
function rowOfLadder(
  part: SupplyPart,
  ownLocation?: string | null,
): SupplyKind | null {
  // A line whose sales order names no location was never walked down the ladder at all, so it
  // proposes nothing rather than proposing a buy nobody decided.
  if (part.kind === 'unplannable') return null;
  // A borrow with no rung - every draft the Amend dialog's BorrowAddDialog produces - never
  // falls through to the group reading below. See the note above.
  if (!part.rung && part.kind === 'borrow') {
    return part.donor_so_number ? 'borrow_order' : 'borrow_other';
  }
  const rung =
    part.rung ??
    (part.kind === 'reserve'
      ? fallbackRung(locationOf(part), ownLocation)
      : null);
  switch (rung) {
    case 'buy':
      return 'buy';
    case 'incoming':
      return 'incoming';
    case 'pool':
      // LADDER V7.1 step 4 has TWO halves and they are different decisions. The free pile
      // is a draw nobody is owed back ("Use BRW"); step 4b takes a LATER POOL ORDER's on
      // hand and raises an ORDER_BACK against it (R34), which is a Borrow and has to read
      // as one - it rendered as the free "Use BRW" label, so the debt was invisible on
      // every composition surface. The kind tells them apart; the borrow half names a donor
      // only with "Borrow from another order" on, and is a location borrow otherwise.
      if (part.kind !== 'borrow') return 'shared';
      return part.donor_so_number ? 'borrow_order' : 'borrow_other';
    case 'group_take':
      // ONE RUNG, TWO PROMISES (the captain, 30 Aug 2026). Step 1 draws the group's floor
      // stock as a Reserve and its share still on the water as a `timely_spo`, both under
      // this rung; reading the rung alone printed a container at sea as "Use own location".
      // Neither half is a borrow - free stock owes nobody a thing, wherever it is.
      return part.kind === 'timely_spo' ? 'incoming' : 'own';
    // Step 2: ON HAND held by a LATER order. The pool's borrow half lands here too (see the
    // `pool` case above) - the same debt, against a different pile. With "Borrow from another
    // order" off the engine names no donor, and the same stock is a LOCATION's borrow
    // (BORROW-LOCATION-ONLY D3); the frozen `group_borrow` reads the same way. A part whose
    // kind is already a word (`cellSupply`'s summed segments) keeps it.
    case 'order_borrow':
    case 'group_borrow':
      return part.kind === 'borrow' && !part.donor_so_number
        ? 'borrow_other'
        : 'borrow_order';
    // Step 3 (S4): the DOCUMENT a later order is waiting on. Its own word, because what is
    // being taken is not stock anybody can pick - the donor waits longer for a ship, and the
    // asker waits for that same ship. Folding it into step 2 hid a whole rung of the walk.
    case 'supply_borrow':
      return 'borrow_incoming';
    case 'cross_group_borrow':
      return 'borrow_other';
    default:
      break;
  }
  if (part.kind === 'buy') return 'buy';
  if (part.kind === 'timely_spo') return 'incoming';
  return null;
}

/** One kind's share of a composition. The unit the bar and the legend are drawn from. */
export interface SupplySegment {
  kind: SupplyKind;
  qty: string;
}

/** The same, with the locations it was drawn from and how much came from each. */
export interface SupplyPlace {
  location: string;
  qty: string;
}

/**
 * A composition reduced to its kinds, in `ORDER`, keeping only the kinds with a quantity.
 *
 * A row of zero was never what made a card readable between cells - the fixed order is - and on
 * a real cell three of six kinds read 0, so the one line that said what to do sat inside five
 * lines of nothing and had to be found each time.
 */
export function segmentsOf(
  parts: SupplyPart[],
  ownLocation?: string | null,
): SupplySegment[] {
  const minor = new Map<SupplyKind, number>();
  for (const part of parts) {
    const kind = rowOf(part, ownLocation);
    if (!kind) continue;
    minor.set(kind, (minor.get(kind) ?? 0) + toMinor(part.qty));
  }
  return ORDER.filter((kind) => (minor.get(kind) ?? 0) !== 0).map((kind) => ({
    kind,
    qty: fromMinor(minor.get(kind) as number),
  }));
}

/**
 * The largest segment: what a cell says in one phrase when it has room for one phrase.
 *
 * Ties break on `ORDER`, so two equal halves always name the same one of the two rather than
 * whichever the map happened to yield first.
 */
export function dominant(segments: SupplySegment[]): SupplySegment | null {
  let best: SupplySegment | null = null;
  for (const segment of segments) {
    if (!best || toMinor(segment.qty) > toMinor(best.qty)) best = segment;
  }
  return best;
}

/** "Shared 71". The dominant kind in the fewest words that still name it. */
export function dominantText(segments: SupplySegment[]): string {
  const best = dominant(segments);
  return best ? `${SHORT_LABELS[best.kind]} ${best.qty}` : '';
}

/**
 * A composition as one compact line: "Shared 71 (BRW) · Buy 12".
 *
 * Only the kinds with a quantity, for the reason `segmentsOf` gives, and each kind names the
 * locations it drew on so "Shared 71" cannot be read as "shared from nowhere in particular".
 * Empty composition returns an empty string; the caller says what nothing means on its screen.
 */
export function describe(
  parts: SupplyPart[],
  ownLocation?: string | null,
): string {
  const places = placesOf(parts, ownLocation);
  return segmentsOf(parts, ownLocation)
    .map((segment) => {
      const where = (places.get(segment.kind) ?? [])
        .map((place) => place.location)
        .join(', ');
      return where
        ? `${SHORT_LABELS[segment.kind]} ${segment.qty} (${where})`
        : `${SHORT_LABELS[segment.kind]} ${segment.qty}`;
    })
    .join(' · ');
}

/** Per kind, how much came from each named location, in the order the ladder took them. */
function placesOf(
  parts: SupplyPart[],
  ownLocation?: string | null,
): Map<SupplyKind, SupplyPlace[]> {
  const byKind = new Map<SupplyKind, Map<string, number>>();
  for (const part of parts) {
    const kind = rowOf(part, ownLocation);
    if (!kind) continue;
    const at = locationOf(part);
    if (!at) continue;
    const seen = byKind.get(kind) ?? new Map<string, number>();
    seen.set(at, (seen.get(at) ?? 0) + toMinor(part.qty));
    byKind.set(kind, seen);
  }
  const out = new Map<SupplyKind, SupplyPlace[]>();
  for (const [kind, seen] of byKind) {
    out.set(
      kind,
      [...seen].map(([location, qty]) => ({ location, qty: fromMinor(qty) })),
    );
  }
  return out;
}

/**
 * What the ladder proposes for a whole cell, said as quantities by kind of source.
 *
 * The dialog opens on a decision, so the decision leads: the kinds with a quantity, in `ORDER`.
 */
export interface SuggestionRow {
  key: SupplyKind;
  label: string;
  /** The quantity, summed across every line of the cell. */
  qty: string;
  /**
   * Where it came from AND HOW MUCH FROM EACH ("454 from DC1-BB, 267 from MWH-BB").
   *
   * A bare list of codes was the whole of it, so a row reading "932 from DC1-BB, MWH-BB,
   * WH3-BB" left the planner to guess the split - and the split is the instruction: it is
   * three separate movements of stock, and somebody has to key each of them.
   */
  places: SupplyPlace[];
  /**
   * The engine's own sentence for this row, when every source on it gives the SAME one.
   *
   * It is what makes a Buy readable: "delivery date beyond the lead time window; stock kept
   * for nearer orders" and "nothing free at any location" are the same quantity for opposite
   * reasons, and the card is where the planner decides between them. Two different sentences
   * on one row is a per-line fact, and the table below the card is where per-line facts live,
   * so the row says nothing rather than picking one of them.
   */
  note?: string;
}

/** "454 from DC1-BB, 267 from MWH-BB, 211 from WH3-BB", or the bare total when Buy. */
export function rowText(row: SuggestionRow): string {
  if (row.places.length === 0) return row.qty;
  return row.places
    .map((place) => `${place.qty} from ${place.location}`)
    .join(', ');
}

/**
 * The cell's rows for ONE side of the question, given a reader that turns a contributing
 * line into its parts. `suggestionBreakdown` and `decisionBreakdown` are the two readers;
 * the aggregation below is identical for both, and having it once is what keeps the
 * Suggestion and Decision cards the same shape rather than two shapes that resemble one
 * another.
 *
 * Resolved PER CONTRIBUTION, because a cell spans lines whose own locations need not agree:
 * `DC1-BB` is the agent's own group on a `BRW-BB` line and somebody else's on a `BRW-IB` one.
 * Each part is re-stamped with the rung its own line resolved it at, so everything below can
 * aggregate the flat list without needing to know which line each part came from.
 */
function breakdown(
  cell: Pick<BoardCell, 'contributions'>,
  partsOf: (contribution: BoardContribution) => SupplyPart[] | null,
): SuggestionRow[] {
  return partsBreakdown(
    cell.contributions.map((contribution) => ({
      parts: partsOf(contribution) ?? [],
      ownLocation: contribution.fulfilment_location,
    })),
  );
}

/**
 * The same aggregation, given the parts directly rather than a cell.
 *
 * A planning change's Was / Now table reads a line's HELD composition and its fresh proposal
 * (`boardChangeAnnotations.ts`, AC-P3-3), neither of which is a board cell - and saying "Use
 * own location 40 from BRW-BB" in a second place would be a second vocabulary the day either
 * changed. One entry per line, because the rung a part resolves at depends on THAT line's own
 * location.
 */
export function partsBreakdown(
  entries: { parts: SupplyPart[]; ownLocation?: string | null }[],
): SuggestionRow[] {
  const parts: SupplyPart[] = [];
  const why = new Map<SupplyKind, Set<string>>();
  const lentBy = new Map<SupplyKind, Set<string>>();
  for (const entry of entries) {
    const ownGroup = groupOf(entry.ownLocation);
    for (const source of entry.parts) {
      const kind = rowOf(source, entry.ownLocation);
      if (!kind) continue;
      const at = source.location ?? source.source_location ?? null;
      parts.push({
        kind: source.kind,
        rung: rungFor(kind),
        qty: source.qty,
        location: at,
        // A borrow's rung says "from another order" only beside a named donor (D3).
        donor_so_number: source.donor_so_number ?? null,
      });
      // WHOSE STOCK IT IS (R-M, 3 Sep 2026). Resolved here, per entry, because it is the
      // CONTRIBUTING LINE's own group that decides it - `DC1-BB` is somebody else's stock
      // on a `BRW-IB` line and the line's own on a `BRW-BB` one - and the aggregation
      // below has no line left to ask.
      const from = groupOf(at);
      if (ownGroup && from && from !== ownGroup) {
        const lenders = lentBy.get(kind) ?? new Set<string>();
        lenders.add(from);
        lentBy.set(kind, lenders);
      }
      const reason = (source as { reason?: string | null }).reason;
      if (!reason) continue;
      const seen = why.get(kind) ?? new Set<string>();
      seen.add(reason);
      why.set(kind, seen);
    }
  }

  const places = placesOf(parts);
  return segmentsOf(parts).map((segment) => {
    const reasons = [...(why.get(segment.kind) ?? [])];
    return {
      key: segment.kind,
      label: labelOf(segment.kind, lentBy.get(segment.kind)),
      qty: segment.qty,
      places: places.get(segment.kind) ?? [],
      ...(reasons.length === 1 ? { note: reasons[0] } : {}),
    };
  });
}

/**
 * The card's own words for a row, naming the LENDING GROUP where step 1 drew on one (R-M).
 *
 * The board's options row has said "Use IB group stock" since S4
 * (`front_planning_engine._use_step_label`), and the Suggestion card beside it went on
 * saying "Use own location" for the same composition - the captain's production cell was a
 * `BRW-BB` line proposed off `BRW-IB` reading as the line's own stock on one surface and as
 * IB's on the other. This is that label, spelled exactly as the engine spells it, so the two
 * surfaces cannot drift again.
 *
 * Only the two STEP 1 kinds take it: `own` is the group's floor and `incoming` is its water,
 * and they are the only rows a group other than the line's own can appear on. Everything
 * else keeps its fixed word.
 */
function labelOf(kind: SupplyKind, lenders?: Set<string>): string {
  if (!lenders || lenders.size === 0) return LABELS[kind];
  if (kind !== 'own' && kind !== 'incoming') return LABELS[kind];
  const named = [...lenders]
    .sort()
    .map((group) => `${group} group`)
    .join(' and ');
  return kind === 'incoming' ? `Use incoming from ${named}` : `Use ${named} stock`;
}

/**
 * The physical movements a DECISION implies, as one line: "454 DC1-BB -> BRW-BB · 267
 * MWH-BB -> BRW-BB" (`PLAN-scm-cs-planning-uat.md` section E).
 *
 * Stock decided from anywhere other than the line's own location has to be carried there
 * before anything can be delivered, and until the transfer entity existed nothing on the
 * board said so. This is the same set of rows the backend writes at confirm, derived here
 * from the decision the planner is looking at - so the card says what Approve will ask for
 * BEFORE it is pressed, with no second call.
 *
 * Same-location components contribute nothing (that stock is already where it has to be),
 * and neither do Buy or Incoming: one is not held anywhere yet and the other arrives at the
 * line's own location on somebody else's document. Aggregated per (from, to) pair, because
 * two lines drawing from one warehouse to one location is ONE movement to key.
 */
export function movesOf(
  cell: Pick<BoardCell, 'contributions'>,
  draft: Record<string, BoardDecision>,
): { from: string; to: string; qty: string }[] {
  const byPair = new Map<string, number>();
  for (const contribution of cell.contributions) {
    const own = contribution.fulfilment_location ?? null;
    if (!own) continue;
    for (const part of contributionDecision(
      contribution,
      draft[contribution.key] ?? null,
    ) ?? []) {
      if (part.kind !== 'reserve' && part.kind !== 'borrow') continue;
      // `locationOf`, not a second hand-rolled `location ?? source_location`: the two wire
      // shapes spell the warehouse differently and one reader for both is the whole reason
      // that helper exists.
      const from = locationOf(part);
      if (!from || from === own) continue;
      const key = `${from}\u0000${own}`;
      byPair.set(key, (byPair.get(key) ?? 0) + toMinor(part.qty));
    }
  }
  return [...byPair].map(([key, qty]) => {
    const [from, to] = key.split('\u0000');
    return { from, to, qty: fromMinor(qty) };
  });
}

/** "454 DC1-BB -> BRW-BB · 267 MWH-BB -> BRW-BB", or "" when nothing has to move. */
export function movesText(
  moves: { from: string; to: string; qty: string }[],
): string {
  return moves
    .map((move) => `${move.qty} ${move.from} -> ${move.to}`)
    .join(' · ');
}

/**
 * What the LADDER proposes for a whole cell.
 *
 * The proposal, never the decision: on a covered line `sources` is the frozen composition
 * rebuilt, so reading it here printed every decided cell's decision as its own suggestion
 * and an amendment looked like it had changed nothing.
 */
export function suggestionBreakdown(
  cell: Pick<BoardCell, 'contributions'>,
): SuggestionRow[] {
  return breakdown(cell, (contribution) =>
    contributionSuggestion(contribution),
  );
}

/**
 * What was DECIDED for a whole cell, in the SAME shape (AC-D3): same rows, same words, same
 * per-location quantities, so the two cards are read side by side rather than compared.
 *
 * Empty while nothing is decided - the card is not rendered then, because a Decision card of
 * nothing says a decision was taken to do nothing.
 */
export function decisionBreakdown(
  cell: Pick<BoardCell, 'contributions'>,
  draft: Record<string, BoardDecision>,
): SuggestionRow[] {
  return breakdown(cell, (contribution) =>
    contributionDecision(contribution, draft[contribution.key] ?? null),
  );
}

/**
 * A confirmed or drafted decision, read as supply parts.
 *
 * A DECISION CARRIES NO RUNG on its reserve rows (`BoardReserveComponent` is a warehouse and a
 * quantity), so the rung is looked up from the line's own `sources` by warehouse - which is
 * where an amendment's reserve rows come from in the first place: `BoardAmendDialog` only edits
 * the quantities of the rows the proposal named. A row with no match at all falls back to an
 * EXACT code comparison with the line's own location, which is a different test from the site
 * prefix that caused this file to be rewritten: `BRW-BB` is literally the line's warehouse,
 * `BRW` is literally not.
 *
 * Borrow rows carry their rung when the server froze them; a drafted borrow names its donor
 * sales order instead, and a borrow that names one IS the borrow-from-another-order kind.
 */
export function decisionParts(
  contribution: BoardContribution,
  decision: BoardLineDecision | BoardDecision,
): SupplyPart[] {
  /** The two decision shapes' reserve and borrow rows, read on what they have in common. */
  type ReserveRow = {
    warehouse_id?: string | null;
    location?: string | null;
    qty: string;
    rung?: string | null;
  };
  type BorrowRow = {
    qty: string;
    location?: string | null;
    warehouse_code?: string | null;
    rung?: string | null;
    donor_so_number?: string | null;
  };

  const rungByLocation = new Map<string, string>();
  const rungByWarehouse = new Map<string, string>();
  for (const source of contribution.sources) {
    if (!source.rung) continue;
    if (source.location) rungByLocation.set(source.location, source.rung);
    if (source.warehouse_id)
      rungByWarehouse.set(source.warehouse_id, source.rung);
  }
  const parts: SupplyPart[] = [];

  // THE WATER KEEPS THE QUESTION THAT DREW IT, AND THE KIND IT WAS DRAWN AS. A confirmation
  // records the SPO share per document location with its rung (`group_take`), so the decided
  // side is read by exactly the pair the suggestion beside it was read by - and under v7.1
  // that pair lands both on "Use incoming". Hard-coding the rung here filed one line under
  // two kinds and put an amber "these differ" dot on both cards.
  const incomingRows = (decision as BoardLineDecision).incoming;
  if (incomingRows && incomingRows.length) {
    for (const row of incomingRows) {
      if (toMinor(row.qty) === 0) continue;
      parts.push({
        kind: 'timely_spo',
        rung: row.rung ?? 'incoming',
        qty: row.qty,
        location: row.location ?? null,
      });
    }
  } else {
    // A DRAFT (the Amend dialog posts a scalar) or a revision frozen before the split was
    // recorded. The line's own sources are the only place the rung can be read from, and on
    // a covered line they are the frozen composition itself.
    const incoming = decision.timely_spo_qty;
    if (incoming !== undefined && toMinor(incoming) !== 0) {
      const water = contribution.sources.find(
        (source) => source.kind === 'timely_spo',
      );
      parts.push({
        kind: 'timely_spo',
        rung: water?.rung ?? 'incoming',
        qty: incoming,
        location: water?.location ?? null,
      });
    }
  }

  for (const row of (decision.reserve ?? []) as ReserveRow[]) {
    // A rung the line's own sources already state beats any reading of the code. When there is
    // none - which is EVERY covered line, since the board rebuilds a frozen composition without
    // it - the row is left unrunged and `rowOf` resolves it on the ownership group.
    const rung =
      // The rung the SERVER froze with the row, when it has one. A frozen decision now
      // carries it on every kind, so a covered line no longer relies on the group reading
      // below - which was right, but was a second opinion about a question the engine had
      // already answered.
      row.rung ??
      (row.warehouse_id ? rungByWarehouse.get(row.warehouse_id) : undefined) ??
      (row.location ? rungByLocation.get(row.location) : undefined) ??
      null;
    parts.push({
      kind: 'reserve',
      rung,
      qty: row.qty,
      location: row.location ?? null,
    });
  }

  for (const row of (decision.borrow ?? []) as BorrowRow[]) {
    // A borrow that names a donor sales order IS the borrow-from-another-order kind, whatever
    // warehouse it sits in. Without one, `rowOf` resolves it on the group like a reserve.
    const rung = row.rung ?? (row.donor_so_number ? 'group_borrow' : null);
    parts.push({
      kind: 'borrow',
      rung,
      qty: row.qty,
      location: row.location ?? row.warehouse_code ?? null,
      donor_so_number: row.donor_so_number ?? null,
    });
  }

  const buy = decision.buy_qty;
  if (buy !== undefined && toMinor(buy) !== 0) {
    parts.push({ kind: 'buy', rung: 'buy', qty: buy });
  }

  return parts;
}

/**
 * What the ENGINE suggested for one line, as supply parts - never what was decided.
 *
 * `proposed` when the server recorded one: the live ladder on an undecided line, the
 * composition frozen at confirm on a covered one. `null` on a covered line whose revision
 * predates the frozen proposal, which the screen says as "Not recorded" rather than as
 * "nothing was suggested".
 *
 * The fallback to `sources` is for an UNCOVERED line only, and it is a safety net rather
 * than the design: on a covered line `sources` is the DECISION rebuilt from the snapshot, so
 * reading it as the suggestion would print every decided line as its own suggestion and no
 * amendment would ever appear to have changed anything.
 */
export function contributionSuggestion(
  contribution: Pick<BoardContribution, 'proposed' | 'sources' | 'covered'>,
): SupplyPart[] | null {
  if (contribution.proposed) return contribution.proposed.components;
  return contribution.covered ? null : contribution.sources;
}

/**
 * What was DECIDED for one line, as supply parts, or `null` while nothing is.
 *
 * The draft leads, exactly as `contributionSupply` has it: a line amended in this session is
 * decided even though nothing has been posted, and a rejection decides no supply at all.
 */
export function contributionDecision(
  contribution: BoardContribution,
  drafted?: BoardDecision | null,
): SupplyPart[] | null {
  if (drafted) {
    if (drafted.verdict === 'rejected') return null;
    const composed =
      drafted.reserve || drafted.borrow || drafted.buy_qty !== undefined;
    // An APPROVAL composes nothing of its own: it takes the proposal as it stands, so what
    // was decided is what was suggested.
    return composed
      ? decisionParts(contribution, drafted)
      : (contributionSuggestion(contribution) ?? contribution.sources);
  }
  if (contribution.covered && contribution.decision) {
    return decisionParts(contribution, contribution.decision);
  }
  return null;
}

/**
 * The ORDER INQUIRY that decides this line, when that is what decides it - OR, since
 * the 17 Sep review round, that merely has something to SAY about it even when it does
 * not decide it.
 *
 * The 14 September 2026 ruling: a line carrying a live inquiry row from the migrated book is
 * decided on the buying side, so the board holds it read-only and proposes nothing for it. It
 * has no composition - purchasing was told about it before any board existed - so `covered` is
 * true while `decision` is null, and the composition slot would otherwise read "Not decided"
 * over a line somebody has already been told to buy.
 *
 * AC-RL-06 amended (17 Sep review round): `!contribution.covered` alone must not null the
 * whole branch out any more - the `received`/`used` word is a fact about the BOOK's own
 * documents, not about whether the board has decided anything, and the owner wants it
 * readable on precisely the line CS is about to confirm a fresh Buy for (uncovered, a live
 * proposal sitting beside it). An uncovered line only surfaces here when its own inquiry has
 * something to say (`documents` or `redirected`) - a fresh, un-linked inquiry row stays
 * silent rather than printing a bare number with nothing behind it. `contribution.decision`
 * still nulls the branch: a DECIDED line's own composition already owns this slot.
 */
export function contributionInquiryDecision(
  contribution: Pick<BoardContribution, 'covered' | 'decision' | 'order_inquiry'>,
): BoardLineOrderInquiry | null {
  if (contribution.decision) return null;
  const inquiry = contribution.order_inquiry;
  if (!inquiry) return null;
  if (contribution.covered) return inquiry;
  const hasWordSignal = Boolean(inquiry.redirected) || (inquiry.documents ?? []).length > 0;
  return hasWordSignal ? inquiry : null;
}

/**
 * AC-RL-06 amended again (17 Sep review round, "beside the PRODUCT"): the SAME word the OI
 * worklist chip carries - `used` once the winning row was itself redirected (AC-RL-10),
 * `received` once every document behind it is fully received - read directly off the
 * inquiry's own `documents`/`redirected`, with NO gate on `covered`, `decision` or a local
 * draft. The Decided cell's own inquiry branch answers a different question (is there a
 * composition to print, or does an inquiry already decide this line) and stays gated by
 * `contributionInquiryDecision` above; this one is a plain fact about the book, true or not
 * on every line regardless of verdict.
 *
 * Widened to the WHOLE LADDER (`board-verdict-actions-chips-acceptance-criteria.md` AC-A1 to
 * AC-A7, owner finding 22 Sep on SO402757): the two words it used to answer with left a line
 * that has become an SPO, a line that has become a PO and a line still sitting on an open
 * inquiry all reading exactly the same as a line with no inquiry at all - silent. So the
 * ladder is `used` > `received` > `SPO` > `PO` > `OI`, most advanced state wins, and the
 * chip's own tooltip carries the numbers (R1: the chip is a word, the number is in the
 * title) - nothing else on the board has room for a document number beside a product code.
 *
 * ONE helper for both readings of the board (AC-A7): the list view's Product cell and the
 * cell breakdown's own, so the two cannot come to say different things about one line.
 */
export interface BoardOrderInquiryWord {
  /** The one word the chip prints. */
  word: 'received' | 'used' | 'SPO' | 'PO' | 'OI';
  /** What the chip's tooltip says: the document number(s), or the inquiry's own number. */
  title: string;
}

export function boardOrderInquiryWord(
  inquiry: BoardLineOrderInquiry | null | undefined,
): BoardOrderInquiryWord | null {
  if (!inquiry) return null;
  const documents = inquiry.documents ?? [];
  const numbersOf = (kinds: ('po' | 'spo')[]) =>
    documents
      .filter((document) => kinds.includes(document.kind))
      .map((document) => document.document)
      .filter((document): document is string => Boolean(document))
      .join(', ');

  // REDIRECTED FIRST (AC-A2/AC-A7, AC-RL-10 unchanged): `used` means the document that
  // landed was redirected off THIS line, so its goods are somebody else's now - reading
  // `received` over it would send a planner looking for stock that has already gone.
  if (inquiry.redirected) return { word: 'used', title: numbersOf(['po', 'spo']) };
  if (documents.length > 0 && documents.every((document) => document.received)) {
    return { word: 'received', title: numbersOf(['po', 'spo']) };
  }
  // An SPO is further along than a PO - purchasing has committed a supplier order for this
  // line - so it wins, and the title carries the SPO numbers ALONE: a PO number in an SPO
  // chip's tooltip would be a document the word does not describe.
  if (documents.some((document) => document.kind === 'spo')) {
    return { word: 'SPO', title: numbersOf(['spo']) };
  }
  if (documents.some((document) => document.kind === 'po')) {
    return { word: 'PO', title: numbersOf(['po']) };
  }
  // Nothing on a document yet. A CANCELLED inquiry with nothing behind it says nothing
  // (AC-A6): the row is not an instruction anybody holds. One that still carries a document
  // is answered by the branches above, because that document is a fact whatever the row's
  // own state has since become.
  if (inquiry.state === 'cancelled') return null;
  return { word: 'OI', title: inquiry.inquiry_no ?? 'Unnumbered inquiry' };
}

/**
 * What ONE contributing line's bar is drawn from, and whether it is settled.
 *
 * The DECISION when there is one - a confirmed active revision, or an amendment ticked into
 * this session's draft - and the engine's proposal otherwise. So a line the planner amends from
 * Buy to the shared pool flips rose to sky the moment the tick lands, and back when it is
 * cleared, with no second copy of the draft anywhere.
 *
 * A REJECTION decides no supply, so it leaves the proposal on screen and leaves it faded: there
 * is nothing settled to draw solid.
 */
export function contributionSupply(
  contribution: BoardContribution,
  drafted?: BoardDecision | null,
): { segments: SupplySegment[]; decided: boolean } {
  const { parts, decided } = contributionParts(contribution, drafted);
  return {
    segments: segmentsOf(parts, contribution.fulfilment_location),
    decided,
  };
}

/**
 * The SAME switch, one step earlier: the parts a line's bar is drawn from, before they are
 * summed by kind.
 *
 * The location table's Taken column needs the parts themselves (which warehouse, how much),
 * and reading the decision-or-proposal question a second time is how the bar and the table
 * come to disagree about a line the planner has just amended.
 */
export function contributionParts(
  contribution: BoardContribution,
  drafted?: BoardDecision | null,
): { parts: SupplyPart[]; decided: boolean } {
  if (drafted && drafted.verdict !== 'rejected') {
    const composed =
      drafted.reserve || drafted.borrow || drafted.buy_qty !== undefined;
    return {
      parts: composed
        ? decisionParts(contribution, drafted)
        : contribution.sources,
      decided: true,
    };
  }
  if (!drafted && contribution.covered && contribution.decision) {
    return {
      parts: decisionParts(contribution, contribution.decision),
      decided: true,
    };
  }
  return { parts: contribution.sources, decided: false };
}

/**
 * How much the cell draws FROM EACH LOCATION, summed over its contributing lines (AC-B3).
 *
 * The answer to "why not MWH", said by the row itself rather than by its absence: MWH was
 * listed, it held 12, and nothing was needed from it. A location not drawn on has no entry
 * here and the table reads 0 for it.
 *
 * The decision when there is one, the suggestion otherwise - `contributionParts`, which is the
 * same switch the cell's colour bar uses, so a line amended from Buy to the shared pool moves
 * its Taken the moment the tick lands.
 *
 * A BUY is not drawn from anywhere - it is not held yet - so it never appears here. Everything
 * else does, INCOMING INCLUDED (the water ruling, 27 August 2026): what question 1 hands over
 * off the water comes off the SPO qty of a row this very table lists, so leaving it out made
 * the table contradict the suggestion above it ("Use own location 10 from BRW-SMC" beside
 * "Taken 1"). Under the retired rung 1 the incoming genuinely was somebody else's document and
 * this exclusion was right; it stopped being right when the SPO moved inside the group's net.
 *
 * KEYED BY WAREHOUSE CODE, so a quantity only shows up if the server listed that warehouse as
 * a row. The group's siblings and every site pool are always listed, so a suggestion can never
 * fall through; a drafted Amend that hand-picks a cross-group donor CAN, because such a
 * location reaches the table only when a component already cited it. The Taken then sums to
 * less than the quantity needed, which is the honest reading: the table is not showing the row
 * that stock came off.
 */
export function takenByLocation(
  cell: Pick<BoardCell, 'contributions'>,
  draft: Record<string, BoardDecision>,
): Map<string, string> {
  const minor = new Map<string, number>();
  for (const contribution of cell.contributions) {
    const { parts } = contributionParts(
      contribution,
      draft[contribution.key] ?? null,
    );
    for (const part of parts) {
      if (part.kind === 'buy' || part.rung === 'buy') continue;
      // The RETIRED rung 1's incoming, which arrived on somebody else's document at the
      // line's own location: it took nothing off that row's pile, so a frozen v3/v4
      // component must not read as though it had. Question 1's water (rung `group_take`)
      // does come off a listed row's SPO qty, and is counted above.
      if (part.rung === 'incoming') continue;
      const at = locationOf(part);
      if (!at) continue;
      minor.set(at, (minor.get(at) ?? 0) + toMinor(part.qty));
    }
  }
  return new Map(
    [...minor].map(([location, qty]) => [location, fromMinor(qty)]),
  );
}

/**
 * `takenByLocation`'s own sibling, kept apart by KIND rather than summed (S3b, R-J): the Stock
 * tab's Taken cell used to print one number per location, and a location the cell draws on
 * from two kinds at once - a Reserve off the floor AND a borrow, say - read as one figure with
 * no way to tell which part is whose. Same loop, same exclusions (a Buy is not held yet; the
 * retired rung 1's incoming took nothing off this row's pile), the KIND dimension only kept
 * rather than collapsed.
 *
 * Ordered per `ORDER` (own first, Buy last - Buy never appears here, but the order still
 * reads the same way the strip and the bar do), so a location drawn from three kinds lists
 * them in the ladder's own reading order rather than in draw order.
 */
export function takenKindsByLocation(
  cell: Pick<BoardCell, 'contributions'>,
  draft: Record<string, BoardDecision>,
): Map<string, { kind: SupplyKind; qty: string }[]> {
  const byLocation = new Map<string, Map<SupplyKind, number>>();
  for (const contribution of cell.contributions) {
    const { parts } = contributionParts(
      contribution,
      draft[contribution.key] ?? null,
    );
    for (const part of parts) {
      if (part.kind === 'buy' || part.rung === 'buy') continue;
      if (part.rung === 'incoming') continue;
      const at = locationOf(part);
      if (!at) continue;
      const kind = rowOf(part, contribution.fulfilment_location);
      if (!kind) continue;
      const byKind = byLocation.get(at) ?? new Map<SupplyKind, number>();
      byKind.set(kind, (byKind.get(kind) ?? 0) + toMinor(part.qty));
      byLocation.set(at, byKind);
    }
  }
  const result = new Map<string, { kind: SupplyKind; qty: string }[]>();
  for (const [location, byKind] of byLocation) {
    result.set(
      location,
      ORDER.filter((kind) => byKind.has(kind)).map((kind) => ({
        kind,
        qty: fromMinor(byKind.get(kind) as number),
      })),
    );
  }
  return result;
}

/**
 * The whole cell's bar: every contributing line's supply, summed by kind.
 *
 * Solid only when EVERY contribution is settled. A cell half decided and half proposed is not
 * a decision, and drawing it solid would say it was.
 */
export function cellSupply(
  cell: Pick<BoardCell, 'contributions'>,
  draft: Record<string, BoardDecision>,
): { segments: SupplySegment[]; decided: boolean } {
  const parts: SupplyPart[] = [];
  let decided = cell.contributions.length > 0;
  for (const contribution of cell.contributions) {
    const supply = contributionSupply(
      contribution,
      draft[contribution.key] ?? null,
    );
    if (!supply.decided) decided = false;
    for (const segment of supply.segments) {
      parts.push({
        kind: segment.kind,
        rung: rungFor(segment.kind),
        qty: segment.qty,
      });
    }
  }
  return { segments: segmentsOf(parts), decided };
}

/** The rung a kind came from, so a summed segment can be re-read by `rowOf` unchanged. */
function rungFor(kind: SupplyKind): string {
  switch (kind) {
    case 'buy':
      return 'buy';
    case 'shared':
      return 'pool';
    case 'own':
      return 'group_take';
    case 'borrow_order':
      return 'group_borrow';
    case 'borrow_incoming':
      return 'supply_borrow';
    case 'borrow_other':
      return 'cross_group_borrow';
    case 'incoming':
      return 'incoming';
  }
}
