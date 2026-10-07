import { formatDateInMalaysia } from '@/lib/helpers';
import { isInquiryBuyRow } from './orderInquiryWorklist';
import type { OrderInquiryWorklistRow } from '../types/orderInquiry.types';

/**
 * `PLAN-oi-no-double-count-25sep.md` S0 (issue #1248): the OI detail Lines tab reads like
 * the sales order's own Lines grid - ONE row per sales order line, whatever the split
 * (owner ruling 26 Sep 2026, G5). Pure and client-side over the rows the tab already
 * loads; no row is written or dropped, only grouped.
 *
 * A row is LIVE while `state != 'cancelled'` and it is not `redirected_to_pool`; every
 * other row of the line is history, read behind the line's one History icon (G1, G6).
 *
 * S2 (AC-ND-20): the rows are the Lines tab's one `include_history` fetch, cancelled rows
 * included. A cancelled row is history on its line; a line only renders when at least one
 * of its rows is not cancelled, the same rule the header list's Lines count reads (G10).
 */

/** The four quantity columns (G4) plus how the line reads. */
export interface OrderInquiryLine {
  /** Stable grid row id: `sol:<so_line_id>`, `line:<core_line_id>`, `so:<so_number>:<line_no>`
   * or `row:<id>`. */
  key: string;
  lineNo: number | null;
  /** Every row of the line the Lines fetch returned: live, used and cancelled. */
  rows: OrderInquiryWorklistRow[];
  liveRows: OrderInquiryWorklistRow[];
  historyRows: OrderInquiryWorklistRow[];
  /** Used rows still waiting on Confirm (`isWaitingUsedRow`), read in History only. */
  waitingUsedRows: OrderInquiryWorklistRow[];
  /** Addresses the line's reserve actions, Raised via, Delivery date, Supplier, Location
   * and product: the reserve-bearing live buy row, else the most urgent live buy row,
   * else the first live row, else the first row. */
  primary: OrderInquiryWorklistRow;
  /** The row whose verb is the line's Instruction (CANCEL_BALANCE > CHANGE_SO > DELAY >
   * ADVANCE > the primary row's own verb). */
  instructionRow: OrderInquiryWorklistRow;
  /** Most urgent live state, or `line_cancelled` / `nothing_to_buy` / `to_confirm`. */
  state: string;
  lineCancelled: boolean;
  /** Greyed on the grid: a cancelled line (G7) or one with nothing left to buy (O2). */
  muted: boolean;
  /** The sales order line's own Qty (`so_line_qty`); null when the line names no sales
   * order line (L17). */
  soQty: number | null;
  requested: number;
  taken: number;
  remaining: number;
  bundled: number;
}

/** What the grid itself renders: the primary row's fields plus its line. A plain row
 * (no `line`) is read as a line of one, see `lineOf`. */
export type OrderInquiryLineRow = OrderInquiryWorklistRow & { line?: OrderInquiryLine };

const STATE_URGENCY = ['raised', 'partly_linked', 'placed', 'actioned'];
const RESERVE_URGENCY = ['requested', 'reserved', 'declined'];
const INSTRUCTION_URGENCY = ['CANCEL_BALANCE', 'CHANGE_SO', 'DELAY', 'ADVANCE'];

function rank(list: string[], value: string | null | undefined): number {
  const index = value ? list.indexOf(value) : -1;
  return index === -1 ? list.length : index;
}

function num(value: string | null | undefined): number {
  return Number(value ?? '0') || 0;
}

export function isLiveInquiryRow(row: OrderInquiryWorklistRow): boolean {
  return row.state !== 'cancelled' && !row.redirected_to_pool;
}

/**
 * PR #1266 review S2 (G6 rationale: the header never sits Outstanding on a row nobody can
 * see or confirm): a used row waits on Confirm while it is not cancelled and its ack is
 * awaiting or changed. ONE rule, shared by the header's `lines_to_confirm`, the line's
 * State (To confirm) and the server's Confirm sweep (`_acknowledge_used_rows_of`).
 */
export function isWaitingUsedRow(row: OrderInquiryWorklistRow): boolean {
  return (
    Boolean(row.redirected_to_pool) &&
    row.state !== 'cancelled' &&
    (row.ack_state === 'awaiting' || row.ack_state === 'changed')
  );
}

/**
 * Which sales order line a row belongs to: its mirror line `so_line_id`, the key the
 * header's Lines count reads too (G10). It is one-to-one with the core line and, unlike
 * `core_line_id` / `line_no` (both off the core line join), is there before AutoCount
 * reconciles the line (review S1). A row served without it falls back to the core line,
 * then SO number + line no; a row that names none is a line of its own.
 */
export function foldKeyOf(row: OrderInquiryWorklistRow): string {
  if (row.so_line_id) return `sol:${row.so_line_id}`;
  if (row.core_line_id) return `line:${row.core_line_id}`;
  if (row.line_no != null) return `so:${row.so_number ?? ''}:${row.line_no}`;
  return `row:${row.id}`;
}

function pickPrimary(live: OrderInquiryWorklistRow[], all: OrderInquiryWorklistRow[]) {
  const buy = live.filter((row) => isInquiryBuyRow(row.verb));
  const byReserve = [...buy].sort(
    (a, b) => rank(RESERVE_URGENCY, a.reserve_state) - rank(RESERVE_URGENCY, b.reserve_state),
  );
  if (byReserve[0]?.reserve_state && RESERVE_URGENCY.includes(byReserve[0].reserve_state)) {
    return byReserve[0];
  }
  const byState = [...buy].sort(
    (a, b) => rank(STATE_URGENCY, a.state) - rank(STATE_URGENCY, b.state),
  );
  return byState[0] ?? live[0] ?? all[0];
}

function buildLine(key: string, rows: OrderInquiryWorklistRow[]): OrderInquiryLine {
  const liveRows = rows.filter(isLiveInquiryRow);
  const historyRows = rows.filter((row) => !isLiveInquiryRow(row));
  const waitingUsedRows = rows.filter(isWaitingUsedRow);
  const liveBuy = liveRows.filter((row) => isInquiryBuyRow(row.verb));
  const primary = pickPrimary(liveRows, rows);
  const lineCancelled = rows.some((row) => row.line_cancelled);

  const asked = liveBuy.reduce((total, row) => total + num(row.qty), 0);
  const taken = liveBuy.reduce(
    (total, row) => total + num(row.linked_qty) + num(row.reserved_qty),
    0,
  );
  const bundled = liveBuy.reduce((total, row) => total + num(row.bundled_qty), 0);
  // G7: a cancelled line's quantity is called off, not owed.
  const requested = lineCancelled ? 0 : asked;
  const remaining = lineCancelled ? 0 : Math.max(requested - taken - bundled, 0);

  const instructionRow =
    [...liveRows]
      .filter((row) => INSTRUCTION_URGENCY.includes(row.verb))
      .sort((a, b) => rank(INSTRUCTION_URGENCY, a.verb) - rank(INSTRUCTION_URGENCY, b.verb))[0] ??
    primary;

  let state: string;
  if (lineCancelled) state = 'line_cancelled';
  // AC-ND-7 (review B1): a live buy row CS amended after purchasing took it on waits on
  // purchasing again, and the approved mockup reads that line "To confirm". Only an
  // explicit `changed`: every fresh row is born `awaiting`, and AC-ND-8's fresh 4 still
  // reads To buy. Review S2: a waiting used row makes it To confirm too, whether the
  // line's live rows are already confirmed or it has none at all.
  else if (liveBuy.some((row) => row.ack_state === 'changed') || waitingUsedRows.length > 0) {
    state = 'to_confirm';
  } else if (liveRows.length === 0) state = 'nothing_to_buy';
  else state = pickPrimary(
    // The most urgent STATE, whatever row the reserve pill reads.
    liveBuy.map((row) => ({ ...row, reserve_state: null })),
    liveRows,
  ).state;

  // AC-ND-4 (G4): the number the sales order's own Lines grid shows for the line.
  const soLineQty = rows.find((row) => row.so_line_qty != null)?.so_line_qty;
  const soQty = soLineQty == null ? null : num(soLineQty);

  return {
    key,
    // `so_line_no` reads the mirror's No. when AutoCount has not reconciled the line yet.
    lineNo: primary.so_line_no ?? primary.line_no ?? null,
    rows,
    liveRows,
    historyRows,
    waitingUsedRows,
    primary,
    instructionRow,
    state,
    lineCancelled,
    // O2's grey "Nothing to buy" row, unless a used row on it still waits on Confirm.
    muted: lineCancelled || (liveRows.length === 0 && waitingUsedRows.length === 0),
    soQty,
    requested,
    taken,
    remaining,
    bundled: lineCancelled ? 0 : bundled,
  };
}

/** Groups rows into lines, sorted by sales order line No.; lines with no No. sort last, in
 * the order their rows arrived. */
export function foldInquiryLines(rows: OrderInquiryWorklistRow[]): OrderInquiryLine[] {
  const groups = new Map<string, OrderInquiryWorklistRow[]>();
  for (const row of rows) {
    const key = foldKeyOf(row);
    const group = groups.get(key);
    if (group) group.push(row);
    else groups.set(key, [row]);
  }
  const lines = [...groups.entries()]
    .filter(([, group]) => group.some((row) => row.state !== 'cancelled'))
    .map(([key, group]) => buildLine(key, group));
  return lines
    .map((line, index) => ({ line, index }))
    .sort((a, b) => {
      const an = a.line.lineNo ?? Number.POSITIVE_INFINITY;
      const bn = b.line.lineNo ?? Number.POSITIVE_INFINITY;
      return an === bn ? a.index - b.index : an - bn;
    })
    .map(({ line }) => line);
}

/** The grid's data: the primary row's own fields, so every shared cell renderer reads it
 * unchanged, plus the line. */
export function toLineRows(lines: OrderInquiryLine[]): OrderInquiryLineRow[] {
  return lines.map((line) => ({ ...line.primary, line }));
}

export function lineOf(row: OrderInquiryLineRow): OrderInquiryLine {
  return row.line ?? buildLine(foldKeyOf(row), [row]);
}

/**
 * The rows a tick on the line selects (AC-ND-12): its live rows, or, on a line with none,
 * its waiting used rows, so a line whose only waiting row is used can still be ticked and
 * confirmed (review S2). Never a cancelled row, and never a used row beside live ones:
 * Link, Unlink and Reject work on live rows only (AC-ND-27).
 */
export function tickRowsOf(line: OrderInquiryLine): OrderInquiryWorklistRow[] {
  return line.liveRows.length > 0 ? line.liveRows : line.waitingUsedRows;
}

/** Footer totals (AC-ND-17): cancelled lines excluded. Remaining is the sum of the line
 * Remaining cells (review S3): an over-covered line's surplus nets nothing off another
 * line, so the footer always tallies with the column above it. */
export function lineFooterTotals(lines: OrderInquiryLine[]) {
  const counted = lines.filter((line) => !line.lineCancelled);
  const soQty = counted.reduce((total, line) => total + (line.soQty ?? 0), 0);
  const requested = counted.reduce((total, line) => total + line.requested, 0);
  const taken = counted.reduce((total, line) => total + line.taken, 0);
  const remaining = counted.reduce((total, line) => total + line.remaining, 0);
  return { soQty, requested, taken, remaining };
}

export type LineHistoryWhat =
  | 'Now'
  | 'Before'
  | 'Used'
  | 'Superseded'
  | 'Re-raised'
  | 'Cancelled'
  | 'Cancel balance'
  | 'Line cancelled';

export interface LineHistoryEntry {
  row: OrderInquiryWorklistRow;
  what: LineHistoryWhat;
  why: string;
  /** A Before entry is a synthetic row: these override what the grid would read off `row`. */
  id?: string;
  when?: string | null;
  qty?: string | null;
  deliveryDate?: string | null;
  noDocuments?: boolean;
}

/**
 * Review S1: the backend stamps a carried row (the same need moved under a new revision,
 * plan L9) and a plain supersede alike, "Superseded by revision N". What tells the carry
 * apart is that a later row of the same line asks for the same qty again.
 */
function whatOf(row: OrderInquiryWorklistRow, lineRows: OrderInquiryWorklistRow[]): LineHistoryWhat {
  if (row.line_cancelled) return 'Line cancelled';
  if (row.redirected_to_pool) return 'Used';
  if (row.state === 'cancelled') {
    if (row.verb === 'CANCEL_BALANCE') return 'Cancel balance';
    if (!/superseded/i.test(row.note ?? '')) return 'Cancelled';
    const raisedAgain = lineRows.some(
      (other) =>
        other.id !== row.id &&
        num(other.qty) === num(row.qty) &&
        (other.raised_at ?? '') > (row.raised_at ?? ''),
    );
    return raisedAgain ? 'Re-raised' : 'Superseded';
  }
  return 'Now';
}

function newestFirst(a: OrderInquiryWorklistRow, b: OrderInquiryWorklistRow) {
  return (b.raised_at ?? '').localeCompare(a.raised_at ?? '');
}

/**
 * The History dialog's Rows tab (AC-ND-15): the line's live rows first as Now, then every
 * retired row (used and cancelled, all already on the line since S2), newest first. A Now
 * row with a `previous_qty` reads "Was <n>." before its note (G1). SO-LINE-CHANGE-HISTORY
 * F2: each Now row carrying a previous quantity or delivery date also gets a Before entry
 * (its own qty and delivery date), listed after every Now row and before the retired rows.
 */
export function lineHistoryEntries(line: OrderInquiryLine): LineHistoryEntry[] {
  const toEntry = (row: OrderInquiryWorklistRow): LineHistoryEntry => {
    const what = whatOf(row, line.rows);
    const note = row.note ?? '';
    const was = what === 'Now' && row.previous_qty != null ? `Was ${num(row.previous_qty)}.` : '';
    return { row, what, why: [was, note].filter(Boolean).join(' ') };
  };
  const now = line.liveRows.filter((row) => !row.line_cancelled);
  const retired = line.rows.filter((row) => !now.includes(row)).sort(newestFirst);
  const before = now
    .filter((row) => row.previous_qty != null || row.previous_delivery_date != null)
    .map((row): LineHistoryEntry => {
      const qty = row.previous_qty != null ? `${num(row.previous_qty)}` : null;
      const on = row.previous_delivery_date
        ? ` on ${formatDateInMalaysia(row.previous_delivery_date)}`
        : '';
      return {
        row,
        what: 'Before',
        id: `${row.id}:before`,
        when: row.changed_at ?? row.raised_at ?? null,
        qty: row.previous_qty ?? null,
        deliveryDate: row.previous_delivery_date ?? null,
        noDocuments: true,
        why: `Was ${qty ?? '-'}${on}, before ${row.inquiry_no} changed it`,
      };
    });
  return [...now.map(toEntry), ...before, ...retired.map(toEntry)];
}

/** W1 (owner hand test, 26 Sep 2026): how far purchasing has confirmed the line. */
export type LineConfirmation =
  | { kind: 'none' }
  | { kind: 'confirmed'; by: string | null; at: string | null }
  | { kind: 'partly'; confirmed: number; total: number };

/**
 * W1 (PR #1266, owner, 26 Sep: "after we click confirm, at the line level can't really see
 * it is confirmed"): the same fold rule as the rest of the line - its live rows only, so a
 * cancelled or used row never holds the mark back. A rejected row has nothing left to
 * confirm and is left out of n of m; a line nobody has confirmed any of reads none (its
 * State already says what waits). A cancelled SO line is marked like any other (review
 * SF1): its rows wait on Confirm too (G7), and its State reads "Line cancelled" either way.
 */
export function lineConfirmationOf(line: OrderInquiryLine): LineConfirmation {
  const counted = line.liveRows.filter((row) => row.ack_state !== 'rejected');
  const confirmed = counted.filter((row) => row.ack_state === 'acknowledged');
  if (confirmed.length === 0) return { kind: 'none' };
  if (confirmed.length < counted.length) {
    return { kind: 'partly', confirmed: confirmed.length, total: counted.length };
  }
  const latest = [...confirmed].sort((a, b) =>
    (b.acknowledged_at ?? '').localeCompare(a.acknowledged_at ?? ''),
  )[0];
  return {
    kind: 'confirmed',
    by: latest.acknowledged_by_name ?? null,
    at: latest.acknowledged_at ?? null,
  };
}

/**
 * Review S4 (AC-ND-14): the live row that carries the line's reserve history, whichever
 * row is the primary - `pickPrimary` puts a `requested` row ahead of a `reserved` one, so
 * gating the Reserve tab on the primary alone would hide it.
 */
export function reserveHistoryRowOf(line: OrderInquiryLine): OrderInquiryWorklistRow | null {
  return (
    line.liveRows.find(
      (row) => row.reserve_state === 'reserved' || row.reserve_state === 'declined',
    ) ?? null
  );
}
