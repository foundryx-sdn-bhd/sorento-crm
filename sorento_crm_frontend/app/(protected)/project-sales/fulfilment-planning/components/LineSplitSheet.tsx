'use client';

import * as React from 'react';
import { useDraggable, useDroppable } from '@dnd-kit/core';
import { Check, CirclePlus, GripVertical, Plus, X } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { DatePicker, parseDateInput } from '@/components/ui/date-picker';
import { Input } from '@/components/ui/input';
import {
  COARSE_HIT_TARGET_CLASS,
  PRESSED_CLASS,
} from '@/components/ui/primitive-classes';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/lib/utils';
import {
  changeOf,
  formatSplitDate,
  gapOf,
  groupKeyOf,
  lineShort,
  readsRemain,
  splitsOf,
  type SplitDraft,
  type SplitLine,
} from '../lib/lineSplitModel';
import { ChangePill, QtyInput, SitsOnSelect } from './lineSplitParts';

/**
 * OI-LINE-SPLIT v4: the one split sheet (UAC A2 to A12, F1; mock v4
 * `documentation/mockups/OI-LINE-SPLIT/index.html`). Every open line of the SO in one table,
 * one `<tbody>` per line (its drop target), a split's rows stacked under its line with the
 * line's own cells merged across them. The dialog owns the state, the drag context and Save;
 * this file only draws and reports edits.
 */

/** What a grip or a "N left" chip carries into a drag. */
export type DragSource =
  | { kind: 'split'; lineId: string; splitId: string }
  | { kind: 'line'; lineId: string; splitId: string }
  | { kind: 'rest'; lineId: string };

export const DROP_NEW = 'drop:new';
export const DROP_CANCEL = 'drop:cancel';
export const lineDropId = (lineId: string) => `line:${lineId}`;

/** The id the focus helpers find a split's date input by. */
export const dateInputId = (splitId: string) => `split-date-${splitId}`;

/** A line AutoCount added in the pending change: nothing of its own to split (base 0). */
export function isAddedLine(line: SplitLine): boolean {
  return !line.baseQty && line.splits.length === 0;
}

export interface SheetDrag {
  source: DragSource;
  groupKey: string;
}

export interface SheetHandlers {
  /** A REMAIN cell clicked or typed on (`typed` = the key, put into the cell). */
  open: (line: SplitLine, cell: 'qty' | 'date', typed?: string) => void;
  qty: (line: SplitLine, splitId: string, qty: number) => void;
  date: (line: SplitLine, splitId: string, iso: string | null) => void;
  sitsOn: (line: SplitLine, splitId: string, value: string) => void;
  remark: (line: SplitLine, splitId: string, remark: string) => void;
  add: (line: SplitLine) => void;
  remove: (line: SplitLine, splitId: string) => void;
  fieldBlur: () => void;
}

type DataAttributes = { [key: `data-${string}`]: string | undefined };

const DESKTOP_ONLY = 'max-md:hidden';
const GRIP_STICKY = 'max-md:sticky max-md:left-0';
const ITEM_STICKY = 'max-md:sticky max-md:left-[28px]';

/** Fixed widths except REMARK, which takes the rest (the sheet fits 1240px, the 1280 page). */
const COLUMNS: Array<{
  key: string;
  label: string;
  width?: number;
  className?: string;
  change?: boolean;
  aria?: string;
}> = [
  { key: 'grip', label: '', width: 28, className: GRIP_STICKY, aria: 'Drag' },
  { key: 'line', label: 'Line', width: 48, className: DESKTOP_ONLY },
  { key: 'soDate', label: 'SO date', width: 80, className: DESKTOP_ONLY },
  { key: 'soNo', label: 'S/O no', width: 84, className: DESKTOP_ONLY },
  { key: 'item', label: 'Item code', width: 112, className: ITEM_STICKY },
  { key: 'location', label: 'Location', width: 72, className: DESKTOP_ONLY },
  { key: 'qty', label: 'Qty', width: 56 },
  { key: 'date', label: 'Delivery date', width: 104 },
  { key: 'changeQty', label: 'Change quantity', width: 80, change: true },
  { key: 'changeDate', label: 'Change date', width: 200, change: true },
  { key: 'sitsOn', label: 'AutoCount line', width: 144, change: true },
  { key: 'remark', label: 'Remark', change: true },
  { key: 'balance', label: 'Balance', width: 92 },
  { key: 'remove', label: '', width: 34, aria: 'Remove' },
];

const CELL =
  'h-9 border-b border-e border-border bg-background px-1.5 py-1 text-center align-middle text-xs';
const BASE_CELL = cn(CELL, 'text-foreground');

function isoToDate(iso: string | null): Date | undefined {
  if (!iso) return undefined;
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function dateToIso(date: Date): string {
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${mm}-${dd}`;
}

/** A grip or the "N left" chip: one @dnd-kit draggable (pointer, touch long-press, keyboard). */
function DragHandle({
  id,
  source,
  label,
  disabled,
  className,
  children,
  ...rest
}: {
  id: string;
  source: DragSource;
  label: string;
  disabled?: boolean;
  className?: string;
  children?: React.ReactNode;
} & DataAttributes) {
  const { attributes, listeners, setNodeRef } = useDraggable({
    id,
    data: source,
    disabled,
  });
  return (
    <button
      type="button"
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      {...rest}
      aria-label={label}
      data-drag-handle=""
      className={cn(
        'touch-none select-none focus-visible:outline-2 focus-visible:outline-primary',
        COARSE_HIT_TARGET_CLASS,
        PRESSED_CLASS,
        disabled ? 'cursor-default opacity-40' : 'cursor-grab',
        className,
      )}
    >
      {children ?? <GripVertical aria-hidden className="size-3.5" />}
    </button>
  );
}

/** One line block: a `<tbody>` drop target (UAC A8). */
function DropBody({
  id,
  enabled,
  children,
  ...rest
}: {
  id: string;
  enabled: boolean;
  children: React.ReactNode;
} & React.HTMLAttributes<HTMLTableSectionElement> &
  DataAttributes) {
  const { setNodeRef } = useDroppable({ id, disabled: !enabled });
  return (
    <tbody
      ref={setNodeRef}
      {...rest}
      data-drop-id={id}
      data-drop-target={enabled ? 'true' : undefined}
    >
      {children}
    </tbody>
  );
}

function DateCell({
  split,
  label,
  onDate,
  onBlur,
}: {
  split: SplitDraft;
  label: string;
  onDate: (iso: string | null) => void;
  onBlur: () => void;
}) {
  // One Date per ISO day: a new object each render would reset what is being typed.
  const value = React.useMemo(
    () => isoToDate(split.deliveryDate),
    [split.deliveryDate],
  );
  const cellRef = React.useRef<HTMLDivElement | null>(null);
  const latest = React.useRef({ onDate, onBlur });
  latest.current = { onDate, onBlur };
  React.useEffect(() => {
    const cell = cellRef.current;
    if (!cell) return undefined;
    const onFocusOut = (event: FocusEvent) => {
      const input = event.target as HTMLInputElement;
      // A typed date that is not a day leaves the split with no date: "date to fill".
      if (
        input.tagName === 'INPUT' &&
        input.value.trim() &&
        !parseDateInput(input.value)
      )
        latest.current.onDate(null);
      latest.current.onBlur();
    };
    cell.addEventListener('focusout', onFocusOut);
    return () => cell.removeEventListener('focusout', onFocusOut);
  }, []);
  return (
    <div ref={cellRef} data-nav-col="date" className="min-w-0 flex-1">
      <DatePicker
        id={dateInputId(split.id)}
        ariaLabel={label}
        value={value}
        onChange={(date) => onDate(date ? dateToIso(date) : null)}
        className="min-w-0 gap-1"
        inputClassName="h-7 min-w-0 px-2 text-xs"
      />
    </div>
  );
}

const RemainButton = ({
  label,
  cell,
  line,
  handlers,
}: {
  label: string;
  cell: 'qty' | 'date';
  line: SplitLine;
  handlers: SheetHandlers;
}) => (
  <button
    type="button"
    aria-label={label}
    data-nav-col={cell}
    data-remain={cell}
    onClick={() => handlers.open(line, cell)}
    onKeyDown={(event) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key.length !== 1) return;
      if (!/\d/.test(event.key) && !(cell === 'date' && event.key === '/'))
        return;
      event.preventDefault();
      event.stopPropagation();
      handlers.open(line, cell, event.key);
    }}
    className={cn(
      'h-7 w-full cursor-text rounded-md border border-dashed border-transparent text-2xs font-semibold tracking-wide text-muted-foreground hover:border-input hover:text-foreground focus-visible:outline-2 focus-visible:outline-primary',
      COARSE_HIT_TARGET_CLASS,
      PRESSED_CLASS,
    )}
  >
    REMAIN
  </button>
);

interface LineBlockProps {
  line: SplitLine;
  /** Lines of the same item and location, sorted (the AUTOCOUNT LINE options). */
  groupLines: SplitLine[];
  incoming: string;
  soNumber: string;
  soDate: string;
  /** More than one location on the SO: the phone shows it under the item code. */
  multiLocation: boolean;
  open: boolean;
  itemTop: boolean;
  flash: boolean;
  dimmed: boolean;
  target: boolean;
  source: boolean;
  over: boolean;
  handlers: SheetHandlers;
}

const LineBlock = React.memo(function LineBlock({
  line,
  groupLines,
  incoming,
  soNumber,
  soDate,
  multiLocation,
  open,
  itemTop,
  flash,
  dimmed,
  target,
  source,
  over,
  handlers,
}: LineBlockProps) {
  const no = line.lineNo ?? '';
  const added = isAddedLine(line);
  const remain = !added && !open && readsRemain(line);
  const splits = added ? [] : splitsOf(line);
  const rows = remain || added ? 1 : Math.max(1, splits.length);
  const span = rows > 1 ? rows : undefined;
  const gap = gapOf(line);
  const top = itemTop ? 'border-t-2 border-t-muted-foreground/40' : '';
  const baseCell = cn(BASE_CELL, top, flash && 'jump-flash');

  const baseCells = (
    <>
      <TableCell
        rowSpan={span}
        className={cn(baseCell, DESKTOP_ONLY, 'tabular-nums')}
      >
        {no}
      </TableCell>
      <TableCell rowSpan={span} className={cn(baseCell, DESKTOP_ONLY)}>
        {soDate}
      </TableCell>
      <TableCell rowSpan={span} className={cn(baseCell, DESKTOP_ONLY)}>
        {soNumber}
      </TableCell>
      <TableCell
        rowSpan={span}
        className={cn(baseCell, ITEM_STICKY, 'max-md:z-[1]')}
      >
        <span className="font-semibold">{line.itemCode}</span>
        <span className="block text-2xs text-muted-foreground md:hidden">
          {multiLocation
            ? `${lineShort(line)} · ${line.location}`
            : lineShort(line)}
        </span>
      </TableCell>
      <TableCell rowSpan={span} className={cn(baseCell, DESKTOP_ONLY)}>
        {line.location}
      </TableCell>
      <TableCell rowSpan={span} className={cn(baseCell, 'tabular-nums')}>
        {added ? 0 : line.baseQty}
      </TableCell>
      <TableCell rowSpan={span} className={baseCell}>
        <span>{added ? '-' : formatSplitDate(line.baseDate)}</span>
        {line.pendingChange && !added ? (
          <span className="block text-2xs leading-tight text-warning">
            AutoCount now{' '}
            <span className="tabular-nums">
              {`${line.nowQty} · ${formatSplitDate(line.nowDate)}`}
            </span>
          </span>
        ) : null}
        {line.stale ? (
          <Badge
            variant="warning"
            appearance="light"
            size="sm"
            className="mt-0.5"
          >
            AutoCount changed
          </Badge>
        ) : null}
        {incoming
          ? incoming.split('\n').map((text) => (
              <span
                key={text}
                className="block text-2xs leading-tight text-info"
              >
                {text}
              </span>
            ))
          : null}
      </TableCell>
    </>
  );

  const addButton = added ? null : (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="h-6 px-1.5"
      aria-label={`Add a split to line ${no}`}
      onClick={() => handlers.add(line)}
    >
      <Plus aria-hidden />
      Split
    </Button>
  );

  const balance = (
    <TableCell
      rowSpan={span}
      data-testid={`balance-${no}`}
      className={cn(baseCell)}
    >
      {added ? null : (
        <div className="flex flex-col items-center gap-1">
          {remain ? null : gap === 0 ? (
            <span className="inline-flex items-center gap-0.5 font-semibold tabular-nums text-success">
              0
              <Check aria-hidden className="size-3.5" />
            </span>
          ) : gap > 0 ? (
            <DragHandle
              id={`drag-rest:${line.lineId}`}
              source={{ kind: 'rest', lineId: line.lineId }}
              label={`Drag the ${gap} left of line ${no}`}
              className="inline-flex items-center gap-0.5 whitespace-nowrap rounded-full border border-warning/40 bg-warning/10 py-px ps-1 pe-2 font-semibold tabular-nums text-warning"
            >
              <GripVertical aria-hidden className="size-3" />
              {`${gap} left`}
            </DragHandle>
          ) : (
            <span className="whitespace-nowrap font-semibold tabular-nums text-destructive">
              {`${-gap} over`}
            </span>
          )}
          {addButton}
        </div>
      )}
    </TableCell>
  );

  const gripCell = cn(CELL, top, GRIP_STICKY, 'max-md:z-[1] px-0');
  const changeCell = cn(CELL, top);

  let body: React.ReactNode;
  if (added) {
    body = (
      <TableRow className="border-0 hover:bg-transparent">
        <TableCell className={gripCell} />
        {baseCells}
        <TableCell colSpan={2} className={changeCell}>
          <span className="text-2xs italic text-muted-foreground">
            {`Added in AutoCount: ${line.nowQty} · ${formatSplitDate(line.nowDate)}`}
          </span>
        </TableCell>
        <TableCell className={changeCell}>
          <span className="text-muted-foreground">-</span>
        </TableCell>
        <TableCell className={changeCell} />
        {balance}
        <TableCell className={changeCell} />
      </TableRow>
    );
  } else if (remain) {
    const only = splits[0];
    body = (
      <TableRow className="border-0 hover:bg-transparent">
        <TableCell className={gripCell}>
          <DragHandle
            id={`drag-line:${line.lineId}`}
            source={{
              kind: 'line',
              lineId: line.lineId,
              splitId: only?.id ?? '',
            }}
            label={`Drag line ${no}`}
            disabled={!only?.qty}
            className="inline-flex h-7 w-5 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
          />
        </TableCell>
        {baseCells}
        <TableCell className={changeCell}>
          <RemainButton
            label={`Split line ${no} quantity`}
            cell="qty"
            line={line}
            handlers={handlers}
          />
        </TableCell>
        <TableCell className={changeCell}>
          <RemainButton
            label={`Split line ${no} date`}
            cell="date"
            line={line}
            handlers={handlers}
          />
        </TableCell>
        <TableCell className={changeCell}>
          <span className="text-muted-foreground">-</span>
        </TableCell>
        <TableCell className={changeCell}>
          <Input
            variant="sm"
            data-nav-col="remark"
            aria-label={`Remark, line ${no}`}
            value={only?.remark ?? ''}
            title={only?.remark || undefined}
            onChange={(event) =>
              only && handlers.remark(line, only.id, event.target.value)
            }
            onBlur={handlers.fieldBlur}
          />
        </TableCell>
        {balance}
        <TableCell className={changeCell} />
      </TableRow>
    );
  } else {
    body = splits.map((split, index) => {
      const n = index + 1;
      const first = index === 0;
      const cell = cn(CELL, first && top);
      const word = changeOf(split, line);
      return (
        <TableRow
          key={split.id}
          data-split-id={split.id}
          className="border-0 hover:bg-transparent"
        >
          <TableCell className={cn(cell, GRIP_STICKY, 'max-md:z-[1] px-0')}>
            <DragHandle
              id={`drag-split:${split.id}`}
              source={{ kind: 'split', lineId: line.lineId, splitId: split.id }}
              label={`Drag split ${n} of line ${no}`}
              disabled={!split.qty}
              data-grip-split={split.id}
              className="inline-flex h-7 w-5 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
            />
          </TableCell>
          {first ? baseCells : null}
          <TableCell className={cell}>
            <QtyInput
              variant="sm"
              data-nav-col="qty"
              data-qty-split={split.id}
              aria-label={`Quantity, line ${no} split ${n}`}
              value={split.qty}
              onValue={(qty) => handlers.qty(line, split.id, qty)}
              onBlur={handlers.fieldBlur}
              className="text-center font-semibold tabular-nums"
            />
          </TableCell>
          <TableCell className={cell}>
            {split.isCancel ? (
              <ChangePill word="CANCEL" />
            ) : (
              <div className="flex items-center gap-1">
                <DateCell
                  split={split}
                  label={`Date, line ${no} split ${n}`}
                  onDate={(iso) => handlers.date(line, split.id, iso)}
                  onBlur={handlers.fieldBlur}
                />
                {split.deliveryDate ? <ChangePill word={word} /> : null}
              </div>
            )}
          </TableCell>
          <TableCell className={cell}>
            <div data-nav-col="line" className="min-w-0">
              <SitsOnSelect
                split={split}
                origin={line}
                groupLines={groupLines}
                label={`AutoCount line, line ${no} split ${n}`}
                onChange={(value) => handlers.sitsOn(line, split.id, value)}
              />
            </div>
          </TableCell>
          <TableCell className={cell}>
            <Input
              variant="sm"
              data-nav-col="remark"
              aria-label={`Remark, line ${no} split ${n}`}
              value={split.remark}
              title={split.remark || undefined}
              onChange={(event) =>
                handlers.remark(line, split.id, event.target.value)
              }
              onBlur={handlers.fieldBlur}
            />
          </TableCell>
          {first ? balance : null}
          <TableCell className={cn(cell, 'px-0')}>
            <Button
              type="button"
              variant="ghost"
              mode="icon"
              size="sm"
              className="size-7 text-muted-foreground"
              aria-label={`Remove split ${n} of line ${no}`}
              title="Remove split"
              onClick={() => handlers.remove(line, split.id)}
            >
              <X aria-hidden />
            </Button>
          </TableCell>
        </TableRow>
      );
    });
  }

  return (
    <DropBody
      id={lineDropId(line.lineId)}
      enabled={target}
      data-testid={`split-line-${line.lineNo ?? line.lineId}`}
      data-line-id={line.lineId}
      data-dimmed={dimmed ? 'true' : undefined}
      data-over={over ? 'true' : undefined}
      className={cn(
        dimmed && 'opacity-40',
        source && '[&>tr>td]:bg-muted/60',
        over && '[&>tr>td]:bg-primary/10',
      )}
    >
      {body}
    </DropBody>
  );
});

function DropRow({
  id,
  testId,
  over,
  destructive,
  children,
}: {
  id: string;
  testId: string;
  over: boolean;
  destructive?: boolean;
  children: React.ReactNode;
}) {
  return (
    <DropBody
      id={id}
      enabled
      data-testid={testId}
      data-over={over ? 'true' : undefined}
      className={cn(over && '[&>tr>td]:bg-primary/10')}
    >
      <TableRow className="border-0 hover:bg-transparent">
        <TableCell
          colSpan={COLUMNS.length}
          className={cn(
            'h-9 border-b border-dashed border-muted-foreground/50 bg-muted/30 ps-3.5 text-start text-xs',
            destructive ? 'text-destructive' : 'text-muted-foreground',
          )}
        >
          {children}
        </TableCell>
      </TableRow>
    </DropBody>
  );
}

/** Focus a sheet cell: the input or button it is, or holds; a typed value is selected. */
export function focusCell(cell: Element | null): boolean {
  if (!cell) return false;
  const el = (
    cell.matches('input, button')
      ? cell
      : cell.querySelector('input, button, [role="combobox"]')
  ) as HTMLElement | null;
  if (!el) return false;
  el.focus();
  if (el instanceof HTMLInputElement) el.select();
  return document.activeElement === el;
}

/** `+236 from line 480` for every other line's split sitting on each line. */
function incomingByLine(lines: SplitLine[]): Map<string, string> {
  const out = new Map<string, string[]>();
  for (const origin of lines) {
    for (const split of origin.splits) {
      if (split.isCancel || !split.qty || !split.sitsOnLineId) continue;
      if (split.sitsOnLineId === origin.lineId) continue;
      const list = out.get(split.sitsOnLineId) ?? [];
      list.push(`+${split.qty} from ${lineShort(origin)}`);
      out.set(split.sitsOnLineId, list);
    }
  }
  return new Map([...out].map(([key, list]) => [key, list.join('\n')]));
}

export function LineSplitSheet({
  lines,
  allLines,
  soNumber,
  soDate,
  openLineIds,
  drag,
  overTarget,
  flashLineId,
  handlers,
}: {
  /** The lines on screen (search and filter applied), in sheet order. */
  lines: SplitLine[];
  /** Every line, in sheet order (AUTOCOUNT LINE options, "+N from line M"). */
  allLines: SplitLine[];
  soNumber: string;
  soDate: string;
  openLineIds: ReadonlySet<string>;
  drag: SheetDrag | null;
  overTarget: string | null;
  flashLineId: string | null;
  handlers: SheetHandlers;
}) {
  // A group's array is reused while its lines are the same objects, so typing in one line
  // re-renders that line's group only.
  const groupCache = React.useRef(new Map<string, SplitLine[]>());
  const groups = React.useMemo(() => {
    const map = new Map<string, SplitLine[]>();
    for (const line of allLines) {
      const key = groupKeyOf(line);
      map.set(key, [...(map.get(key) ?? []), line]);
    }
    for (const [key, list] of map) {
      const cached = groupCache.current.get(key);
      if (
        cached &&
        cached.length === list.length &&
        cached.every((line, index) => line === list[index])
      )
        map.set(key, cached);
    }
    groupCache.current = map;
    return map;
  }, [allLines]);
  const lineById = React.useMemo(
    () => new Map(allLines.map((line) => [line.lineId, line])),
    [allLines],
  );

  /**
   * Excel keys in the cells (UAC A5, A10): Ctrl/Cmd+Enter adds a split under the line,
   * Ctrl/Cmd+Backspace removes the row; Enter or Down goes to the same column one row down,
   * Shift+Enter or Up one row up. A grip or chip is the drag's own (Space picks up).
   */
  const onKeyDown = (event: React.KeyboardEvent<HTMLTableElement>) => {
    if (drag) return;
    const target = event.target as HTMLElement;
    if (target.closest('[data-drag-handle]')) return;
    const lineId = target.closest<HTMLElement>('tbody[data-line-id]')?.dataset
      .lineId;
    const line = lineId ? lineById.get(lineId) : undefined;
    if (!line) return;
    const splitId =
      target.closest<HTMLElement>('tr[data-split-id]')?.dataset.splitId;
    const mod = event.metaKey || event.ctrlKey;
    if (mod && event.key === 'Enter') {
      if (isAddedLine(line)) return;
      event.preventDefault();
      handlers.add(line);
      return;
    }
    if (mod && event.key === 'Backspace') {
      if (!splitId) return;
      event.preventDefault();
      handlers.remove(line, splitId);
      return;
    }
    if (mod || event.altKey) return;
    const here = target.closest<HTMLElement>('[data-nav-col]');
    const col = here?.dataset.navCol;
    if (!here || !col) return;
    const down =
      (event.key === 'Enter' && !event.shiftKey) || event.key === 'ArrowDown';
    const up =
      (event.key === 'Enter' && event.shiftKey) || event.key === 'ArrowUp';
    if (!down && !up) return;
    // Enter presses a button: a REMAIN cell opens, the AUTOCOUNT LINE list opens.
    if (event.key === 'Enter' && target.tagName === 'BUTTON') return;
    const table = event.currentTarget;
    const cells = Array.from(
      table.querySelectorAll<HTMLElement>(`[data-nav-col="${col}"]`),
    );
    const next = cells[cells.indexOf(here) + (down ? 1 : -1)];
    if (!next) return;
    event.preventDefault();
    focusCell(next);
  };
  const incoming = React.useMemo(() => incomingByLine(allLines), [allLines]);
  const multiLocation = React.useMemo(
    () => new Set(allLines.map((line) => line.location)).size > 1,
    [allLines],
  );
  const lastOfDragGroup = drag
    ? lines.reduce(
        (last, line, index) =>
          groupKeyOf(line) === drag.groupKey ? index : last,
        -1,
      )
    : -1;

  return (
    <Table
      aria-label="Split delivery sheet"
      data-split-sheet=""
      onKeyDown={onKeyDown}
      className="min-w-[1240px] table-fixed border-separate border-spacing-0 text-xs max-md:min-w-[1000px]"
    >
      <TableHeader className="[&_tr]:border-0">
        <TableRow className="border-0 hover:bg-transparent">
          {COLUMNS.map((column) => (
            <TableHead
              key={column.key}
              aria-label={column.aria}
              style={{ width: column.width }}
              className={cn(
                'sticky top-0 z-[2] h-auto border-b border-e border-border px-1.5 py-2 text-center text-2xs font-semibold uppercase leading-tight tracking-wide whitespace-normal text-foreground/70',
                column.change ? 'bg-muted' : 'bg-background',
                column.className,
                column.key === 'grip' || column.key === 'item'
                  ? 'max-md:z-[3]'
                  : null,
              )}
            >
              {column.label}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      {lines.map((line, index) => {
        const key = groupKeyOf(line);
        const prev = lines[index - 1];
        const inDrag = drag !== null;
        const sameGroup = inDrag && drag.groupKey === key;
        const isSource = inDrag && drag.source.lineId === line.lineId;
        return (
          <React.Fragment key={line.lineId}>
            <LineBlock
              line={line}
              groupLines={groups.get(key) ?? [line]}
              incoming={incoming.get(line.lineId) ?? ''}
              soNumber={soNumber}
              soDate={soDate}
              multiLocation={multiLocation}
              open={openLineIds.has(line.lineId)}
              itemTop={!prev || prev.itemCode !== line.itemCode}
              flash={flashLineId === line.lineId}
              dimmed={inDrag && !sameGroup}
              target={sameGroup && !isSource}
              source={isSource}
              over={overTarget === lineDropId(line.lineId)}
              handlers={handlers}
            />
            {index === lastOfDragGroup ? (
              <>
                <DropRow
                  id={DROP_NEW}
                  testId="drop-new-date"
                  over={overTarget === DROP_NEW}
                >
                  <CirclePlus
                    aria-hidden
                    className="me-1.5 inline size-3.5 align-[-2px]"
                  />
                  New date
                </DropRow>
                <DropRow
                  id={DROP_CANCEL}
                  testId="drop-cancel"
                  over={overTarget === DROP_CANCEL}
                  destructive
                >
                  Cancel this qty
                </DropRow>
              </>
            ) : null}
          </React.Fragment>
        );
      })}
      {lines.length === 0 ? (
        <TableBody>
          <TableRow className="border-0 hover:bg-transparent">
            <TableCell
              colSpan={COLUMNS.length}
              className="py-10 text-center text-sm text-muted-foreground"
            >
              No lines match
            </TableCell>
          </TableRow>
        </TableBody>
      ) : null}
    </Table>
  );
}
