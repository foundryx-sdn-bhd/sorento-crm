/**
 * OI-LINE-SPLIT: the pure model behind the whole-SO split view (UAC A5, A9, B1, B4).
 *
 * A line with no stored splits reads as ONE implicit split: its base qty on its own line and
 * base date. Every change returns new lines (the input is never touched), so the view can
 * keep a history for Undo. A split keeps its OWN line (the line it was split from) and
 * records the line it SITS ON, which only staff ever pick.
 */
import type {
  LineSplitLine,
  LineSplitsPayload,
  LineSplitsSaveBody,
} from '../../_shared/types/lineSplit.types';

export interface SplitDraft {
  id: string;
  qty: number;
  /** ISO date; `null` for a cancel. */
  deliveryDate: string | null;
  isCancel: boolean;
  /** The line this split sits on; `null` = a new line not in AutoCount yet. */
  sitsOnLineId: string | null;
  remark: string;
}

export interface SplitLine {
  lineId: string;
  lineNo: number | null;
  itemCode: string;
  location: string;
  baseQty: number;
  baseDate: string | null;
  nowQty: number;
  nowDate: string | null;
  pendingChange: boolean;
  deliveredQty?: number;
  /** Stored splits; empty = not split (one implicit split, `splitsOf`). */
  splits: SplitDraft[];
}

/** Where a split goes: a date (or `'cancel'`) and the line it sits on. */
export interface MoveTarget {
  date: string | 'cancel';
  sitsOnLineId: string | null;
}

export type ChangeWord = 'REMAIN' | 'DELAY' | 'ADVANCE' | 'CANCEL';

/**
 * A client-only id for a split made in this view (the server never sees it), derived from
 * the line and the splits it already has: the same input always gives the same id, so a
 * function called twice (React StrictMode) answers the same both times.
 */
export function nextSplitId(
  lineId: string,
  splits: readonly SplitDraft[],
): string {
  const taken = new Set(splits.map((split) => split.id));
  let index = splits.length + 1;
  while (taken.has(`${lineId}:${index}`)) index += 1;
  return `${lineId}:${index}`;
}

/** The id of a line's implicit split (its base, unsplit). */
export function implicitSplitId(lineId: string): string {
  return `own:${lineId}`;
}

export function splitsOf(line: SplitLine): SplitDraft[] {
  if (line.splits.length > 0) return line.splits;
  return [
    {
      id: implicitSplitId(line.lineId),
      qty: line.baseQty,
      deliveryDate: line.baseDate,
      isCancel: false,
      sitsOnLineId: line.lineId,
      remark: '',
    },
  ];
}

/** Not split: no stored splits, or one plain split equal to the base on its own line and date. */
export function isSplit(line: SplitLine): boolean {
  const splits = line.splits;
  if (splits.length === 0) return false;
  if (splits.length > 1) return true;
  const only = splits[0];
  return !(
    !only.isCancel &&
    only.qty === line.baseQty &&
    only.deliveryDate === line.baseDate &&
    only.sitsOnLineId === line.lineId &&
    !only.remark.trim()
  );
}

/** Base minus the sum of the splits: positive is a gap, negative is over. */
export function gapOf(line: SplitLine): number {
  return (
    line.baseQty -
    splitsOf(line).reduce((sum, split) => sum + (split.qty || 0), 0)
  );
}

/** What is planned ON a line: every non-cancel split of any line that sits on it. */
export function planOf(lines: SplitLine[], lineId: string): number {
  let total = 0;
  for (const line of lines) {
    for (const split of splitsOf(line)) {
      if (!split.isCancel && split.sitsOnLineId === lineId)
        total += split.qty || 0;
    }
  }
  return total;
}

/** AutoCount's qty on the line less what is planned on it (UAC A4). */
export function leftOf(lines: SplitLine[], lineId: string): number {
  const line = lines.find((candidate) => candidate.lineId === lineId);
  return (line?.nowQty ?? 0) - planOf(lines, lineId);
}

/** A split's change word against its own line's base date (UAC A9). */
export function changeOf(split: SplitDraft, line: SplitLine): ChangeWord {
  if (split.isCancel) return 'CANCEL';
  if (
    !split.deliveryDate ||
    !line.baseDate ||
    split.deliveryDate === line.baseDate
  )
    return 'REMAIN';
  return split.deliveryDate > line.baseDate ? 'DELAY' : 'ADVANCE';
}

/** Copy of `lines` with `lineId`'s splits materialised and handed to `edit` to change. */
function editLine(
  lines: SplitLine[],
  lineId: string,
  edit: (splits: SplitDraft[], line: SplitLine) => SplitDraft[] | null,
): SplitLine[] {
  const line = lines.find((candidate) => candidate.lineId === lineId);
  if (!line) return lines;
  const splits = splitsOf(line).map((split) => ({ ...split }));
  const next = edit(splits, line);
  if (!next) return lines;
  return lines.map((candidate) =>
    candidate.lineId === lineId ? { ...candidate, splits: next } : candidate,
  );
}

/**
 * Move `qty` of a split to `target`. All of it moves the split; part of it makes a new split
 * for the moved qty. Landing where a split of the same line already sits (same date, same
 * sits-on line) merges the two. A cancel has no date and sits on its own line.
 */
export function moveSplit(
  lines: SplitLine[],
  lineId: string,
  splitId: string,
  qty: number,
  target: MoveTarget,
): SplitLine[] {
  return editLine(lines, lineId, (splits) => {
    const split = splits.find((candidate) => candidate.id === splitId);
    if (!split) return null;
    const moving = Math.min(Math.floor(qty) || 0, split.qty);
    if (moving <= 0) return null;
    const isCancel = target.date === 'cancel';
    const date = isCancel ? null : (target.date as string);
    const on = isCancel ? lineId : target.sitsOnLineId;
    const existing = splits.find(
      (candidate) =>
        candidate !== split &&
        candidate.isCancel === isCancel &&
        candidate.deliveryDate === date &&
        candidate.sitsOnLineId === on,
    );
    if (moving >= split.qty) {
      if (existing) {
        existing.qty += split.qty;
        existing.remark = [existing.remark, split.remark]
          .filter(Boolean)
          .join('; ');
        return splits.filter((candidate) => candidate !== split);
      }
      split.deliveryDate = date;
      split.isCancel = isCancel;
      split.sitsOnLineId = on;
      return splits;
    }
    split.qty -= moving;
    if (existing) existing.qty += moving;
    else
      splits.push({
        id: nextSplitId(lineId, splits),
        qty: moving,
        deliveryDate: date,
        isCancel,
        sitsOnLineId: on,
        remark: '',
      });
    return splits;
  });
}

/** Halve a split in place, the first half rounded up. */
export function splitInTwo(
  lines: SplitLine[],
  lineId: string,
  splitId: string,
): SplitLine[] {
  return editLine(lines, lineId, (splits) => {
    const index = splits.findIndex((candidate) => candidate.id === splitId);
    if (index < 0 || splits[index].qty < 2) return null;
    const split = splits[index];
    const first = Math.ceil(split.qty / 2);
    const second = split.qty - first;
    split.qty = first;
    splits.splice(index + 1, 0, {
      ...split,
      id: nextSplitId(lineId, splits),
      qty: second,
      remark: '',
    });
    return splits;
  });
}

/** A split back on its own line and original date, merging with what is there. */
export function backToOwn(
  lines: SplitLine[],
  lineId: string,
  splitId: string,
): SplitLine[] {
  const line = lines.find((candidate) => candidate.lineId === lineId);
  const split = line
    ? splitsOf(line).find((candidate) => candidate.id === splitId)
    : undefined;
  if (!line || !split) return lines;
  const date = line.baseDate ?? split.deliveryDate;
  if (!date) return lines;
  return moveSplit(lines, lineId, splitId, split.qty, {
    date,
    sitsOnLineId: lineId,
  });
}

/** A typed qty (a zero split stays until `dropEmpty`). */
export function setQty(
  lines: SplitLine[],
  lineId: string,
  splitId: string,
  qty: number,
): SplitLine[] {
  return editLine(lines, lineId, (splits) => {
    const split = splits.find((candidate) => candidate.id === splitId);
    if (!split) return null;
    split.qty = Math.max(0, Math.floor(qty) || 0);
    return splits;
  });
}

export function setDate(
  lines: SplitLine[],
  lineId: string,
  splitId: string,
  date: string,
): SplitLine[] {
  if (!date) return lines;
  return editLine(lines, lineId, (splits) => {
    const split = splits.find((candidate) => candidate.id === splitId);
    if (!split || split.isCancel) return null;
    split.deliveryDate = date;
    return splits;
  });
}

/** Sits on another line, a new line (`null`) or `'cancel'`; leaving Cancel takes the base date. */
export function setSitsOn(
  lines: SplitLine[],
  lineId: string,
  splitId: string,
  on: string | null | 'cancel',
): SplitLine[] {
  return editLine(lines, lineId, (splits, line) => {
    const split = splits.find((candidate) => candidate.id === splitId);
    if (!split) return null;
    if (on === 'cancel') {
      split.isCancel = true;
      split.deliveryDate = null;
      split.sitsOnLineId = lineId;
      return splits;
    }
    if (split.isCancel) {
      split.isCancel = false;
      split.deliveryDate = line.baseDate;
    }
    split.sitsOnLineId = on;
    return splits;
  });
}

export function setRemark(
  lines: SplitLine[],
  lineId: string,
  splitId: string,
  remark: string,
): SplitLine[] {
  return editLine(lines, lineId, (splits) => {
    const split = splits.find((candidate) => candidate.id === splitId);
    if (!split) return null;
    split.remark = remark;
    return splits;
  });
}

/** Two neighbouring splits share their total; `firstQty` is the first one's new qty. */
export function setPair(
  lines: SplitLine[],
  lineId: string,
  firstId: string,
  secondId: string,
  firstQty: number,
): SplitLine[] {
  return editLine(lines, lineId, (splits) => {
    const first = splits.find((candidate) => candidate.id === firstId);
    const second = splits.find((candidate) => candidate.id === secondId);
    if (!first || !second) return null;
    const total = first.qty + second.qty;
    const next = Math.max(0, Math.min(total, Math.round(firstQty)));
    if (next === first.qty) return null;
    first.qty = next;
    second.qty = total - next;
    return splits;
  });
}

/** A new empty split on the line's own line and base date (UAC A8 "+ Add split"). */
export function addSplit(
  lines: SplitLine[],
  lineId: string,
): { lines: SplitLine[]; id: string } {
  const line = lines.find((candidate) => candidate.lineId === lineId);
  const id = nextSplitId(lineId, line ? splitsOf(line) : []);
  return {
    id,
    lines: editLine(lines, lineId, (splits, current) => [
      ...splits,
      {
        id,
        qty: 0,
        deliveryDate: current.baseDate,
        isCancel: false,
        sitsOnLineId: lineId,
        remark: '',
      },
    ]),
  };
}

/** Zero-qty splits removed (UAC B1, on blur and on Save). `null` when there were none. */
export function dropEmpty(
  lines: SplitLine[],
  lineId: string,
): SplitLine[] | null {
  const line = lines.find((candidate) => candidate.lineId === lineId);
  if (!line || !line.splits.some((split) => !split.qty)) return null;
  return lines.map((candidate) =>
    candidate.lineId === lineId
      ? {
          ...candidate,
          splits: candidate.splits.filter((split) => split.qty > 0),
        }
      : candidate,
  );
}

/** The Save body: every line, `[]` for a line that is not split, zero splits dropped. */
export function toPayload(lines: SplitLine[]): LineSplitsSaveBody {
  return {
    lines: lines.map((line) => {
      const clean = {
        ...line,
        splits: line.splits.filter((split) => split.qty > 0),
      };
      return {
        line_id: line.lineId,
        splits: isSplit(clean)
          ? clean.splits.map((split) => ({
              qty: split.qty,
              delivery_date: split.isCancel ? null : split.deliveryDate,
              is_cancel: split.isCancel,
              sits_on_line_id: split.isCancel
                ? line.lineId
                : split.sitsOnLineId,
              remark: split.remark,
            }))
          : [],
      };
    }),
  };
}

export function fromPayload(payload: LineSplitsPayload): SplitLine[] {
  return payload.lines.map((line: LineSplitLine) => ({
    lineId: line.line_id,
    lineNo: line.line_no,
    itemCode: line.item_code,
    location: line.location,
    baseQty: Number(line.base_qty) || 0,
    baseDate: line.base_date,
    nowQty: Number(line.now_qty) || 0,
    nowDate: line.now_date,
    pendingChange: Boolean(line.pending_change),
    deliveredQty: Number(line.delivered_qty) || 0,
    splits: line.splits.map((split) => ({
      id: split.id,
      qty: Number(split.qty) || 0,
      deliveryDate: split.is_cancel ? null : split.delivery_date,
      isCancel: split.is_cancel,
      sitsOnLineId: split.is_cancel ? line.line_id : split.sits_on_line_id,
      remark: split.remark ?? '',
    })),
  }));
}

/** Lines grouped by item code and location, in the order the server listed them. */
export interface SplitGroup {
  key: string;
  itemCode: string;
  location: string;
  lines: SplitLine[];
}

/** The key of the group a line belongs to (item code and location). */
export function groupKeyOf(line: SplitLine): string {
  return `${line.itemCode}|${line.location}`;
}

export function groupsOf(lines: SplitLine[]): SplitGroup[] {
  const groups: SplitGroup[] = [];
  for (const line of lines) {
    const key = groupKeyOf(line);
    let group = groups.find((candidate) => candidate.key === key);
    if (!group) {
      group = {
        key,
        itemCode: line.itemCode,
        location: line.location,
        lines: [],
      };
      groups.push(group);
    }
    group.lines.push(line);
  }
  return groups;
}

/** The group's date columns: every line's base date and every split date, ascending. */
export function datesOf(group: SplitGroup): string[] {
  const dates = new Set<string>();
  for (const line of group.lines) {
    if (line.baseDate) dates.add(line.baseDate);
    for (const split of splitsOf(line))
      if (!split.isCancel && split.deliveryDate) dates.add(split.deliveryDate);
  }
  return [...dates].sort();
}

/** `20/10/2026`. */
export function formatSplitDate(iso: string | null | undefined): string {
  if (!iso) return '';
  return `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)}`;
}

/** `Line 20 · 417 on 20/10/2026`, or the qty and date alone when AutoCount gave no number. */
export function lineLabel(line: SplitLine): string {
  const base = `${line.baseQty} on ${formatSplitDate(line.baseDate)}`;
  return line.lineNo != null ? `Line ${line.lineNo} · ${base}` : base;
}

/** `line 20`, or `the 417 line` when AutoCount gave no number. */
export function lineShort(line: SplitLine | undefined): string {
  if (!line) return 'new line';
  return line.lineNo != null
    ? `line ${line.lineNo}`
    : `the ${line.baseQty} line`;
}
