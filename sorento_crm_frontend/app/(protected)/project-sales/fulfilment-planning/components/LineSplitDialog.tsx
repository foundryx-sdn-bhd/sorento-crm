'use client';

import * as React from 'react';
import { createPortal } from 'react-dom';
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MeasuringStrategy,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
  pointerWithin,
  rectIntersection,
  type CollisionDetection,
  type DragEndEvent,
  type DragOverEvent,
  type DragStartEvent,
  type KeyboardCoordinateGetter,
} from '@dnd-kit/core';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Switch } from '@/components/ui/switch';
import { toast } from '@/lib/toast';
import { useLineSplits } from '../../_shared/hooks/useLineSplits';
import {
  addSplit,
  clearDate,
  dropEmpty,
  formatSplitDate,
  fromPayload,
  gapOf,
  groupKeyOf,
  lineShort,
  moveSplit,
  placeRest,
  readsRemain,
  removeSplit,
  setDate,
  setQty,
  setRemark,
  setSitsOn,
  sortLines,
  splitsOf,
  targetDateOf,
  toPayload,
  type MoveTarget,
  type SplitLine,
} from '../lib/lineSplitModel';
import {
  DROP_CANCEL,
  DROP_NEW,
  LineSplitSheet,
  dateInputId,
  focusCell,
  lineDropId,
  type DragSource,
  type SheetDrag,
  type SheetHandlers,
} from './LineSplitSheet';
import { SITS_ON_CANCEL, SITS_ON_NEW } from './lineSplitParts';

interface Draft {
  lines: SplitLine[];
  history: SplitLine[][];
}

const HISTORY_LIMIT = 80;
const FLASH_MS = 2000;

function isTyping(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  return Boolean(element && /^(INPUT|SELECT|TEXTAREA)$/.test(element.tagName));
}

/**
 * The block under the POINTER wins; only with the pointer over no block does the dragged
 * handle's own rectangle decide. Matching the handle's rectangle alone dropped one row low
 * near the bottom of the scrolling sheet (real browser).
 */
const splitCollision: CollisionDetection = (args) => {
  const hits = pointerWithin(args);
  return hits.length > 0 ? hits : rectIntersection(args);
};

/** Blocks are re-measured during a drag: the sheet scrolls (autoscroll) under it. */
const SPLIT_MEASURING = { droppable: { strategy: MeasuringStrategy.Always } };

/** Autoscroll only from a thin edge band, and slowly, so a drop near the edge lands where
 * it was aimed instead of the sheet still scrolling under it (browser pass). */
const SPLIT_AUTOSCROLL = { threshold: { x: 0.08, y: 0.08 }, acceleration: 4 };

/** Where focus goes once the sheet has drawn the change. */
type FocusRequest =
  | { kind: 'qty'; splitId: string; select?: boolean; caretEnd?: boolean }
  | { kind: 'date'; splitId: string; typed?: string }
  | { kind: 'grip'; splitId: string }
  | { kind: 'line'; lineId: string };

const attr = (value: string) => value.replace(/["\\]/g, '\\$&');

function lineBody(lineId: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(
    `tbody[data-line-id="${attr(lineId)}"]`,
  );
}

/**
 * A key typed on a REMAIN date cell lands in the date input it turns into. The input's
 * value is set the way typing sets it, so the date field holds it as its own draft.
 */
function typeInto(input: HTMLInputElement, text: string) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    'value',
  )?.set;
  setter?.call(input, text);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function runFocus(request: FocusRequest): boolean {
  if (request.kind === 'line') {
    const body = lineBody(request.lineId);
    if (!body) return false;
    body.scrollIntoView({ block: 'center' });
    return focusCell(body.querySelector('[data-nav-col]'));
  }
  if (request.kind === 'grip') {
    const grip = document.querySelector<HTMLElement>(
      `[data-grip-split="${attr(request.splitId)}"]`,
    );
    if (!grip) return false;
    grip.focus();
    return true;
  }
  if (request.kind === 'date') {
    const input = document.getElementById(
      dateInputId(request.splitId),
    ) as HTMLInputElement | null;
    if (!input) return false;
    input.focus();
    if (request.typed !== undefined) typeInto(input, request.typed);
    else input.select();
    return true;
  }
  const input = document.querySelector<HTMLInputElement>(
    `[data-qty-split="${attr(request.splitId)}"]`,
  );
  if (!input) return false;
  input.focus();
  if (request.select) input.select();
  else if (request.caretEnd)
    input.setSelectionRange(input.value.length, input.value.length);
  return true;
}

/** The split that took the moved qty: a new one, one that grew, else the one dragged. */
function landedSplitId(
  before: SplitLine,
  after: SplitLine | undefined,
  fallback: string | null,
): string | null {
  if (!after) return fallback;
  const prior = new Map(splitsOf(before).map((split) => [split.id, split]));
  const grown =
    splitsOf(after).find((split) => !prior.has(split.id)) ??
    splitsOf(after).find(
      (split) => split.qty > (prior.get(split.id)?.qty ?? 0),
    );
  return grown?.id ?? fallback;
}

function dragQty(source: DragSource, line: SplitLine): number {
  if (source.kind === 'rest') return Math.max(0, gapOf(line));
  return splitsOf(line).find((split) => split.id === source.splitId)?.qty ?? 0;
}

/** `20/11/2026 · line 608`, `New date`, `Cancel this qty` (the overlay's target, UAC A8). */
function targetLabel(
  overId: string | null,
  lines: SplitLine[],
  cancelWord = 'Cancel this qty',
): string | null {
  if (!overId) return null;
  if (overId === DROP_NEW) return 'New date';
  if (overId === DROP_CANCEL) return cancelWord;
  const to = lines.find((line) => lineDropId(line.lineId) === overId);
  if (!to) return null;
  const date = formatSplitDate(targetDateOf(to));
  return date ? `${date} · ${lineShort(to)}` : lineShort(to);
}

/**
 * OI-LINE-SPLIT v4: the split sheet page (UAC A, B, F; mock v4
 * `documentation/mockups/OI-LINE-SPLIT/index.html`). Every open line of the SO in one sheet
 * (`LineSplitSheet`); a split row, a REMAIN line or a line's "N left" drags (pointer, touch
 * long-press, keyboard) onto another line of its item and location, a new date or Cancel.
 * Save sends every line at once and waits while a line does not add up or lacks a date.
 */
export function LineSplitDialog({
  open,
  psoId,
  focusLineId,
  onOpenChange,
}: {
  open: boolean;
  psoId: string;
  focusLineId?: string;
  onOpenChange: (open: boolean) => void;
}) {
  const { query, save } = useLineSplits(psoId, open);
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const draftRef = React.useRef<Draft | null>(null);
  const initialRef = React.useRef('');
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  // The page's own node, as state: Radix mounts the portalled content after the first
  // render, so an effect keyed on it attaches once it exists.
  const [container, setContainer] = React.useState<HTMLDivElement | null>(null);
  const containerCallback = React.useCallback((node: HTMLDivElement | null) => {
    containerRef.current = node;
    setContainer(node);
  }, []);
  const [search, setSearch] = React.useState('');
  const [splitOnly, setSplitOnly] = React.useState(false);
  const [openLineIds, setOpenLineIds] = React.useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [flashLineId, setFlashLineId] = React.useState<string | null>(null);
  const [drag, setDrag] = React.useState<SheetDrag | null>(null);
  const dragRef = React.useRef<(SheetDrag & { keyboard: boolean }) | null>(
    null,
  );
  const [overTarget, setOverTarget] = React.useState<string | null>(null);
  const overRef = React.useRef<string | null>(null);
  const keyboardIndexRef = React.useRef(-1);
  // dnd-kit's own live region is kept out of the reading order (it would read internal
  // ids); the status line says what is moving where instead.
  const [liveHost, setLiveHost] = React.useState<HTMLDivElement | null>(null);
  const switchId = React.useId();

  const commit = React.useCallback((next: Draft | null) => {
    draftRef.current = next;
    setDraft(next);
  }, []);

  // ---- focus after a change: applied once the sheet has drawn it
  const focusRef = React.useRef<FocusRequest | null>(null);
  const scrollRef = React.useRef<string | null>(null);
  const requestFocus = React.useCallback((request: FocusRequest) => {
    focusRef.current = request;
    let tries = 0;
    const retry = () => {
      if (focusRef.current !== request) return;
      if (runFocus(request) || (tries += 1) > 10) {
        if (focusRef.current === request) focusRef.current = null;
        return;
      }
      window.requestAnimationFrame(retry);
    };
    window.requestAnimationFrame(retry);
  }, []);
  React.useLayoutEffect(() => {
    const request = focusRef.current;
    if (request && runFocus(request)) focusRef.current = null;
    const scrollTo = scrollRef.current;
    if (scrollTo) {
      const body = lineBody(scrollTo);
      if (body) {
        body.scrollIntoView({ block: 'center' });
        scrollRef.current = null;
      }
    }
  });

  React.useEffect(() => {
    if (!flashLineId) return undefined;
    const timer = window.setTimeout(() => setFlashLineId(null), FLASH_MS);
    return () => window.clearTimeout(timer);
  }, [flashLineId]);

  const endDrag = React.useCallback(() => {
    dragRef.current = null;
    overRef.current = null;
    keyboardIndexRef.current = -1;
    setDrag(null);
    setOverTarget(null);
  }, []);

  React.useEffect(() => {
    if (!open) {
      commit(null);
      endDrag();
      setOpenLineIds(new Set());
      setSearch('');
      setSplitOnly(false);
      return;
    }
    if (query.data && !query.isFetching && !draftRef.current) {
      const lines = fromPayload(query.data);
      initialRef.current = JSON.stringify(toPayload(lines));
      commit({ lines, history: [] });
      // Opened from a board line: that line is scrolled to and flashed (UAC A1).
      if (focusLineId && lines.some((line) => line.lineId === focusLineId)) {
        scrollRef.current = focusLineId;
        setFlashLineId(focusLineId);
      }
    }
  }, [open, query.data, query.isFetching, focusLineId, commit, endDrag]);

  const lines = draft?.lines ?? null;
  const sorted = React.useMemo(() => (lines ? sortLines(lines) : []), [lines]);
  const visible = React.useMemo(() => {
    const needle = search.trim().toLowerCase();
    return sorted.filter(
      (line) =>
        (!needle || line.itemCode.toLowerCase().includes(needle)) &&
        (!splitOnly || !readsRemain(line) || openLineIds.has(line.lineId)),
    );
  }, [sorted, search, splitOnly, openLineIds]);

  // ---- history: one change, one Undo step
  const undo = React.useCallback(() => {
    const current = draftRef.current;
    if (!current || current.history.length === 0) {
      toast('Nothing to undo');
      return;
    }
    commit({
      lines: current.history[current.history.length - 1],
      history: current.history.slice(0, -1),
    });
  }, [commit]);

  // Cmd/Ctrl+Z: anywhere in the sheet, and anywhere else but a text box (UAC A10).
  React.useEffect(() => {
    if (!open) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.shiftKey) return;
      if (event.key.toLowerCase() !== 'z' || dragRef.current) return;
      const target = event.target as HTMLElement | null;
      const inSheet = Boolean(target?.closest?.('[data-split-sheet]'));
      if (isTyping(target) && !inSheet) return;
      event.preventDefault();
      undo();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, undo]);

  const typingRef = React.useRef<string | null>(null);
  /** Apply an edit as one Undo step. Typing in one field is ONE step until it loses focus. */
  const apply = React.useCallback(
    (edit: (lines: SplitLine[]) => SplitLine[], typingKey?: string) => {
      const current = draftRef.current;
      if (!current) return null;
      const next = edit(current.lines);
      if (next === current.lines) return null;
      const sameEdit =
        typingKey !== undefined && typingRef.current === typingKey;
      typingRef.current = typingKey ?? null;
      commit({
        lines: next,
        history: sameEdit
          ? current.history
          : [...current.history, current.lines].slice(-HISTORY_LIMIT),
      });
      return { before: current.lines, after: next };
    },
    [commit],
  );

  /** Focus left a line: its empty splits go, and a line equal to its base reads REMAIN. */
  const focusedLineRef = React.useRef<string | null>(null);
  const leaveLine = React.useCallback(
    (lineId: string) => {
      typingRef.current = null;
      setOpenLineIds((current) => {
        if (!current.has(lineId)) return current;
        const next = new Set(current);
        next.delete(lineId);
        return next;
      });
      const current = draftRef.current;
      if (!current) return;
      const next = dropEmpty(current.lines, lineId);
      if (next)
        commit({
          lines: next,
          history: [...current.history, current.lines].slice(-HISTORY_LIMIT),
        });
    },
    [commit],
  );
  // A DOM listener, so focus in a list or calendar opened from a cell (portalled out of
  // the page) never reaches it: that focus is still in its line.
  React.useEffect(() => {
    if (!container) return undefined;
    const onFocusIn = (event: FocusEvent) => {
      const target = event.target as HTMLElement;
      const lineId =
        target.closest<HTMLElement>('tbody[data-line-id]')?.dataset.lineId ??
        null;
      const previous = focusedLineRef.current;
      focusedLineRef.current = lineId;
      if (previous && previous !== lineId) leaveLine(previous);
    };
    container.addEventListener('focusin', onFocusIn);
    return () => container.removeEventListener('focusin', onFocusIn);
  }, [container, leaveLine]);

  const liveLine = React.useCallback(
    (lineId: string) =>
      draftRef.current?.lines.find((line) => line.lineId === lineId),
    [],
  );

  const handlers = React.useMemo<SheetHandlers>(
    () => ({
      open: (line, cell, typed) => {
        const current = liveLine(line.lineId) ?? line;
        const split = splitsOf(current)[0];
        if (!split) return;
        setOpenLineIds((ids) => new Set(ids).add(line.lineId));
        if (cell === 'qty') {
          if (typed)
            apply(
              (all) => setQty(all, line.lineId, split.id, Number(typed)),
              `qty:${split.id}`,
            );
          requestFocus({
            kind: 'qty',
            splitId: split.id,
            select: !typed,
            caretEnd: Boolean(typed),
          });
        } else {
          requestFocus({ kind: 'date', splitId: split.id, typed });
        }
      },
      qty: (line, splitId, qty) => {
        apply(
          (all) => setQty(all, line.lineId, splitId, qty),
          `qty:${splitId}`,
        );
      },
      date: (line, splitId, iso) => {
        apply((all) =>
          iso
            ? setDate(all, line.lineId, splitId, iso)
            : clearDate(all, line.lineId, splitId),
        );
      },
      sitsOn: (line, splitId, value) => {
        apply((all) =>
          setSitsOn(
            all,
            line.lineId,
            splitId,
            value === SITS_ON_CANCEL
              ? 'cancel'
              : value === SITS_ON_NEW
                ? null
                : value,
          ),
        );
      },
      remark: (line, splitId, remark) => {
        apply(
          (all) => setRemark(all, line.lineId, splitId, remark),
          `remark:${splitId}`,
        );
      },
      add: (line) => {
        let id = '';
        const done = apply((all) => {
          const added = addSplit(all, line.lineId);
          id = added.id;
          return added.lines;
        });
        if (done) requestFocus({ kind: 'qty', splitId: id, select: true });
      },
      remove: (line, splitId) => {
        if (apply((all) => removeSplit(all, line.lineId, splitId)))
          requestFocus({ kind: 'line', lineId: line.lineId });
      },
      fieldBlur: () => {
        typingRef.current = null;
      },
    }),
    [apply, liveLine, requestFocus],
  );

  // ---- drag and drop (UAC A8, A9)
  const stepKeyboardTarget = React.useCallback((dir: 1 | -1) => {
    const table = containerRef.current?.querySelector('[data-split-sheet]');
    const targets = Array.from(
      table?.querySelectorAll<HTMLElement>('tbody[data-drop-target="true"]') ??
        [],
    );
    if (targets.length === 0) return null;
    const index =
      dir > 0
        ? Math.min(targets.length - 1, keyboardIndexRef.current + 1)
        : Math.max(0, keyboardIndexRef.current - 1);
    keyboardIndexRef.current = index;
    const el = targets[index];
    const id = el.dataset.dropId ?? null;
    overRef.current = id;
    setOverTarget(id);
    return el;
  }, []);
  const stepRef = React.useRef(stepKeyboardTarget);
  stepRef.current = stepKeyboardTarget;

  /**
   * Up / Down step through the item's other lines in order, then New date, then Cancel
   * (UAC A10). The order comes from the sheet, never from measured positions; the overlay
   * only follows the target when the browser has laid it out.
   */
  const keyboardCoordinates = React.useCallback<KeyboardCoordinateGetter>(
    (event, { currentCoordinates }) => {
      if (event.code !== 'ArrowDown' && event.code !== 'ArrowUp')
        return undefined;
      const el = stepRef.current(event.code === 'ArrowDown' ? 1 : -1);
      const rect = el?.getBoundingClientRect();
      if (!rect || !rect.height) return currentCoordinates;
      return {
        x: currentCoordinates.x,
        y: rect.top + Math.min(rect.height / 2, 18) - 14,
      };
    },
    [],
  );

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 5 } }),
    useSensor(TouchSensor, {
      activationConstraint: { delay: 200, tolerance: 8 },
    }),
    useSensor(KeyboardSensor, {
      keyboardCodes: {
        start: ['Space'],
        cancel: ['Escape'],
        end: ['Space', 'Enter'],
      },
      coordinateGetter: keyboardCoordinates,
    }),
  );

  const onDragStart = (event: DragStartEvent) => {
    const source = event.active.data.current as DragSource | undefined;
    const line = source ? liveLine(source.lineId) : undefined;
    if (!source || !line) return;
    const keyboard =
      typeof (event.activatorEvent as KeyboardEvent | null)?.key === 'string';
    const next = { source, groupKey: groupKeyOf(line) };
    dragRef.current = { ...next, keyboard };
    overRef.current = null;
    keyboardIndexRef.current = -1;
    setDrag(next);
    setOverTarget(null);
  };
  const onDragOver = (event: DragOverEvent) => {
    if (!dragRef.current || dragRef.current.keyboard) return;
    const id = event.over ? String(event.over.id) : null;
    overRef.current = id;
    setOverTarget(id);
  };

  const drop = (source: DragSource, overId: string) => {
    const current = draftRef.current;
    const line = current?.lines.find((c) => c.lineId === source.lineId);
    if (!current || !line) return;
    let target: MoveTarget;
    if (overId === DROP_NEW) target = { date: '', sitsOnLineId: null };
    else if (overId === DROP_CANCEL)
      target = { date: 'cancel', sitsOnLineId: null };
    else {
      const to = current.lines.find((c) => lineDropId(c.lineId) === overId);
      if (
        !to ||
        to.lineId === line.lineId ||
        groupKeyOf(to) !== groupKeyOf(line)
      )
        return;
      target = { date: targetDateOf(to) ?? '', sitsOnLineId: to.lineId };
    }
    const qty = dragQty(source, line);
    if (qty <= 0) return;
    const done = apply((all) =>
      source.kind === 'rest'
        ? placeRest(all, line.lineId, target)
        : moveSplit(all, line.lineId, source.splitId, qty, target),
    );
    if (!done) return;
    const where = targetLabel(overId, current.lines, 'Cancel');
    toast(`Moved ${qty} to ${where}`, {
      action: {
        label: 'Undo',
        onClick: () => {
          const now = draftRef.current;
          if (!now) return;
          commit(
            now.lines === done.after
              ? { lines: done.before, history: now.history.slice(0, -1) }
              : {
                  lines: done.before,
                  history: [...now.history, now.lines].slice(-HISTORY_LIMIT),
                },
          );
        },
      },
    });
    const landed = landedSplitId(
      line,
      done.after.find((c) => c.lineId === line.lineId),
      source.kind === 'rest' ? null : source.splitId,
    );
    if (!landed) return;
    // A new date is typed next (UAC A9); otherwise the keyboard stays on the moved row.
    requestFocus(
      target.date === ''
        ? { kind: 'date', splitId: landed }
        : { kind: 'grip', splitId: landed },
    );
  };

  const onDragEnd = (event: DragEndEvent) => {
    const active = dragRef.current;
    const overId = active?.keyboard
      ? overRef.current
      : event.over
        ? String(event.over.id)
        : null;
    endDrag();
    if (active && overId) drop(active.source, overId);
  };

  // ---- the header and status line
  const problems = React.useMemo(
    () =>
      sorted.flatMap((line) => {
        const gap = gapOf(line);
        const undated =
          !readsRemain(line) &&
          line.splits.some(
            (split) => !split.isCancel && split.qty > 0 && !split.deliveryDate,
          );
        if (gap === 0 && !undated) return [];
        const what =
          gap > 0 ? `${gap} left` : gap < 0 ? `${-gap} over` : 'date to fill';
        return [
          {
            lineId: line.lineId,
            text: `${line.itemCode} ${lineShort(line)}: ${what}`,
          },
        ];
      }),
    [sorted],
  );
  const splitCount = sorted.filter((line) => !readsRemain(line)).length;
  const products = new Set(sorted.map((line) => line.itemCode)).size;
  const dirty =
    Boolean(lines) &&
    JSON.stringify(toPayload(lines as SplitLine[])) !== initialRef.current;

  const show = (lineId: string) => {
    setSearch('');
    setSplitOnly(false);
    setFlashLineId(lineId);
    requestFocus({ kind: 'line', lineId });
  };

  const requestClose = () => {
    // Escape, Close and the scrim never throw edits away; Cancel does (UAC B5).
    if (dirty) {
      toast('Unsaved changes: Save or Cancel');
      return;
    }
    onOpenChange(false);
  };

  const onSave = () => {
    const current = draftRef.current;
    if (!current || problems.length > 0) return;
    let cleaned = current.lines;
    for (const line of current.lines)
      cleaned = dropEmpty(cleaned, line.lineId) ?? cleaned;
    if (cleaned !== current.lines)
      commit({ lines: cleaned, history: [...current.history, current.lines] });
    save.mutate(toPayload(cleaned), { onSuccess: () => onOpenChange(false) });
  };

  const soDate = formatSplitDate(query.data?.so_date ?? null);
  const subtitle = [query.data?.so_number, soDate, query.data?.customer]
    .filter(Boolean)
    .join(' · ');
  const locations = [...new Set(sorted.map((line) => line.location))]
    .filter(Boolean)
    .join(', ');

  const dragLine = drag ? liveLine(drag.source.lineId) : undefined;
  const dragAmount = drag && dragLine ? dragQty(drag.source, dragLine) : 0;
  const dragTarget = lines ? targetLabel(overTarget, lines) : null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) requestClose();
      }}
    >
      <DialogContent
        variant="fullscreen"
        showCloseButton={false}
        className="gap-0 overflow-hidden p-0 max-md:inset-0 max-md:rounded-none"
        // Escape during a drag puts the drag back; it never closes the page (UAC A10).
        onEscapeKeyDown={(event) => {
          if (dragRef.current) event.preventDefault();
        }}
      >
        <div
          ref={containerCallback}
          data-testid="line-split-dialog"
          className="flex h-full min-h-0 flex-col"
        >
          <DialogHeader className="mb-0 flex flex-col gap-3 space-y-0 border-b border-border px-4 py-3 text-start sm:text-start lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0">
              <DialogTitle className="truncate">Split delivery</DialogTitle>
              <DialogDescription className="truncate text-xs">
                {subtitle}
              </DialogDescription>
              {locations ? (
                <p className="truncate text-xs text-muted-foreground md:hidden">
                  {locations}
                </p>
              ) : null}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <ListSearchInput
                value={search}
                onChange={setSearch}
                placeholder="Search item code"
                className="w-full sm:w-56"
              />
              <div className="flex items-center gap-2 text-xs">
                <Switch
                  id={switchId}
                  size="sm"
                  checked={splitOnly}
                  onCheckedChange={setSplitOnly}
                />
                <label
                  htmlFor={switchId}
                  className="cursor-pointer select-none"
                >
                  Split lines only
                </label>
              </div>
              <div className="ms-auto flex items-center gap-2">
                <Button type="button" variant="ghost" onClick={requestClose}>
                  Close
                </Button>
                {dirty ? (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => onOpenChange(false)}
                  >
                    Cancel
                  </Button>
                ) : null}
                <Button
                  type="button"
                  disabled={!lines || problems.length > 0 || save.isPending}
                  onClick={onSave}
                >
                  Save
                </Button>
              </div>
            </div>
          </DialogHeader>

          <div
            role="status"
            aria-live="polite"
            className="flex min-h-8 flex-wrap items-center gap-x-3.5 gap-y-1 border-b border-border px-4 py-1.5 text-xs"
          >
            {lines ? (
              <span className="text-muted-foreground">
                {`${sorted.length} line${sorted.length === 1 ? '' : 's'} · ${products} product${products === 1 ? '' : 's'} · ${splitCount} split`}
              </span>
            ) : null}
            {problems.map((problem) => (
              <span
                key={problem.lineId}
                className="inline-flex items-center gap-1.5"
              >
                <span className="font-medium text-destructive">
                  {problem.text}
                </span>
                <Button
                  type="button"
                  variant="link"
                  size="sm"
                  className="h-auto text-xs"
                  onClick={() => show(problem.lineId)}
                >
                  Show
                </Button>
              </span>
            ))}
            {drag && dragLine ? (
              <span className="sr-only">
                {`Moving ${dragAmount} of ${dragLine.itemCode} from ${lineShort(dragLine)}${dragTarget ? ` → ${dragTarget}` : ''}`}
              </span>
            ) : null}
          </div>

          <div className="min-h-0 flex-1 [&>[data-slot=table-wrapper]]:h-full">
            {query.isError && !lines ? (
              <div
                role="alert"
                className="flex h-full flex-col items-center justify-center gap-3 p-6 text-sm"
              >
                <span className="text-destructive">
                  {(query.error as Error | null)?.message ||
                    'Failed to load the splits'}
                </span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void query.refetch()}
                >
                  Retry
                </Button>
              </div>
            ) : !lines ? (
              <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
                Loading…
              </div>
            ) : (
              <DndContext
                sensors={sensors}
                collisionDetection={splitCollision}
                measuring={SPLIT_MEASURING}
                autoScroll={SPLIT_AUTOSCROLL}
                accessibility={{
                  container: liveHost ?? undefined,
                  restoreFocus: false,
                }}
                onDragStart={onDragStart}
                onDragOver={onDragOver}
                onDragEnd={onDragEnd}
                onDragCancel={endDrag}
              >
                <LineSplitSheet
                  lines={visible}
                  allLines={sorted}
                  soNumber={query.data?.so_number ?? ''}
                  soDate={soDate}
                  openLineIds={openLineIds}
                  drag={drag}
                  overTarget={overTarget}
                  flashLineId={flashLineId}
                  handlers={handlers}
                />
                {typeof document !== 'undefined'
                  ? createPortal(
                      <DragOverlay dropAnimation={null}>
                        {drag && dragLine ? (
                          <div className="pointer-events-none w-max whitespace-nowrap rounded-lg border border-s-[3px] border-border border-s-primary bg-background px-2.5 py-1.5 text-xs shadow-lg">
                            <b className="tabular-nums">{dragAmount}</b>
                            {` · ${dragLine.itemCode} `}
                            <span className="text-muted-foreground">
                              {`from ${lineShort(dragLine)}`}
                            </span>
                            {dragTarget ? (
                              <span
                                className={
                                  overTarget === DROP_CANCEL
                                    ? 'block text-destructive'
                                    : 'block text-primary'
                                }
                              >
                                {`→ ${dragTarget}`}
                              </span>
                            ) : null}
                          </div>
                        ) : null}
                      </DragOverlay>,
                      document.body,
                    )
                  : null}
              </DndContext>
            )}
          </div>
          <div hidden ref={setLiveHost} />
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default LineSplitDialog;
