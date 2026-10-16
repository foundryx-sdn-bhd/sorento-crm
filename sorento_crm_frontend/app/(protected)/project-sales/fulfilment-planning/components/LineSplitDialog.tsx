'use client';

import * as React from 'react';
import { createPortal } from 'react-dom';
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { toast } from '@/lib/toast';
import { useLineSplits } from '../../_shared/hooks/useLineSplits';
import {
  addSplit,
  backToOwn,
  datesOf,
  dropEmpty,
  formatSplitDate,
  fromPayload,
  gapOf,
  groupKeyOf,
  groupsOf,
  isSplit,
  lineLabel,
  lineShort,
  moveSplit,
  setDate,
  setPair,
  setQty,
  setRemark,
  splitInTwo,
  splitsOf,
  toPayload,
  type SplitDraft,
  type SplitLine,
} from '../lib/lineSplitModel';
import {
  CANCEL_COL,
  LineSplitGrid,
  NEW_DATE_COL,
  NEW_LINE_ROW,
  OPEN_ALL_GROUPS_UP_TO,
  SplitCardBody,
  placedOf,
  type CardData,
  type CardHandlers,
  type CellData,
  type Placed,
} from './LineSplitGrid';
import { LineSplitMoveBox, type MoveTarget } from './LineSplitMoveBox';
import { LineSplitPhone } from './LineSplitPhone';
import { LineSplitZoom, type SplitRowHandlers } from './LineSplitZoom';
import {
  SITS_ON_CANCEL,
  SITS_ON_NEW,
  type SplitActions,
} from './lineSplitParts';

interface Draft {
  lines: SplitLine[];
  history: SplitLine[][];
}

const HISTORY_LIMIT = 80;

/** Below Tailwind's `md`: the phone layout (UAC F1). */
function belowMdNow(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    Boolean(window.matchMedia('(max-width: 767px)').matches)
  );
}

function useBelowMd(): boolean {
  // Read at first render, so a phone never paints the desktop grid first.
  const [below, setBelow] = React.useState(belowMdNow);
  React.useEffect(() => {
    if (
      typeof window === 'undefined' ||
      typeof window.matchMedia !== 'function'
    )
      return;
    const query = window.matchMedia('(max-width: 767px)');
    const update = () => setBelow(Boolean(query.matches));
    update();
    query.addEventListener?.('change', update);
    return () => query.removeEventListener?.('change', update);
  }, []);
  return below;
}

function isTyping(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  return Boolean(element && /^(INPUT|SELECT|TEXTAREA)$/.test(element.tagName));
}

function focusCard(splitId: string) {
  window.requestAnimationFrame(() => {
    document
      .querySelector<HTMLElement>(`[data-split-card="${splitId}"]`)
      ?.focus({ preventScroll: true });
  });
}

/**
 * OI-LINE-SPLIT: the whole-SO split view (UAC A, B, F; mock v2
 * `documentation/mockups/OI-LINE-SPLIT/index.html`). Rows are the order's open lines grouped
 * by item and location, columns their dates then New date and Cancel; each card is one split,
 * dragged (pointer, touch long-press, keyboard) between cells of its own group. The zoom pane
 * edits one line. Save sends every line at once and is blocked while a line does not add up.
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
  const phone = useBelowMd();
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const draftRef = React.useRef<Draft | null>(null);
  const initialRef = React.useRef('');
  const [selectedLineId, setSelectedLineId] = React.useState<string | null>(
    null,
  );
  const [selectedSplitId, setSelectedSplitId] = React.useState<string | null>(
    null,
  );
  const [zoomOpen, setZoomOpen] = React.useState(true);
  const [moveTarget, setMoveTarget] = React.useState<MoveTarget | null>(null);
  const [active, setActive] = React.useState<CardData | null>(null);
  const activeRef = React.useRef<CardData | null>(null);
  // The focused line's group leads; a big order opens it alone (`OPEN_ALL_GROUPS_UP_TO`).
  const [leadKey, setLeadKey] = React.useState<string | null>(null);
  const [openKeys, setOpenKeys] = React.useState<ReadonlySet<string>>(
    new Set(),
  );

  const commit = React.useCallback((next: Draft | null) => {
    draftRef.current = next;
    setDraft(next);
  }, []);

  React.useEffect(() => {
    if (!open) {
      commit(null);
      setMoveTarget(null);
      setActive(null);
      activeRef.current = null;
      return;
    }
    if (query.data && !query.isFetching && !draftRef.current) {
      const lines = fromPayload(query.data);
      initialRef.current = JSON.stringify(toPayload(lines));
      commit({ lines, history: [] });
      const focus =
        focusLineId && lines.some((line) => line.lineId === focusLineId)
          ? focusLineId
          : null;
      const lead = lines.find(
        (line) => line.lineId === (focus ?? lines[0]?.lineId),
      );
      const leadGroup = lead ? groupKeyOf(lead) : null;
      setSelectedLineId(lead?.lineId ?? null);
      setSelectedSplitId(null);
      setZoomOpen(true);
      setLeadKey(leadGroup);
      setOpenKeys(
        new Set(
          lines.length <= OPEN_ALL_GROUPS_UP_TO
            ? lines.map(groupKeyOf)
            : leadGroup
              ? [leadGroup]
              : [],
        ),
      );
    }
  }, [open, query.data, query.isFetching, focusLineId, commit]);

  const lines = draft?.lines ?? null;
  const groups = React.useMemo(() => {
    if (!lines) return [];
    const all = groupsOf(lines);
    const lead = all.find((group) => group.key === leadKey);
    return lead ? [lead, ...all.filter((group) => group !== lead)] : all;
  }, [lines, leadKey]);
  const byId = React.useCallback(
    (lineId: string | null | undefined) =>
      lines?.find((line) => line.lineId === lineId),
    [lines],
  );
  /** The latest lines, for callbacks that must stay stable across edits. */
  const liveLine = React.useCallback(
    (lineId: string | null | undefined) =>
      draftRef.current?.lines.find((line) => line.lineId === lineId),
    [],
  );

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

  // Cmd/Ctrl+Z anywhere in the view but a text box (UAC A7).
  React.useEffect(() => {
    if (!open) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === 'z' &&
        !isTyping(event.target)
      ) {
        event.preventDefault();
        undo();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, undo]);

  /** One change, one Undo step; `message` toasts with an Undo action. */
  const typingRef = React.useRef<string | null>(null);
  const apply = React.useCallback(
    (
      edit: (lines: SplitLine[]) => SplitLine[],
      message?: string,
      typingKey?: string,
    ) => {
      const current = draftRef.current;
      if (!current) return false;
      const next = edit(current.lines);
      if (next === current.lines) return false;
      // Typing in one field is ONE Undo step until it loses focus (UAC A7): the first
      // keystroke records the step, the rest of the edit rides on it.
      const sameEdit =
        typingKey !== undefined && typingRef.current === typingKey;
      typingRef.current = typingKey ?? null;
      commit({
        lines: next,
        history: sameEdit
          ? current.history
          : [...current.history, current.lines].slice(-HISTORY_LIMIT),
      });
      if (message)
        toast(message, { action: { label: 'Undo', onClick: () => undo() } });
      return true;
    },
    [commit, undo],
  );

  const whereText = React.useCallback(
    (lineId: string, col: string, on: string | null) => {
      if (col === CANCEL_COL) return 'Cancel';
      const date = formatSplitDate(col);
      if (on === lineId) return date;
      return `${date}, ${on ? lineShort(liveLine(on)) : 'new line'}`;
    },
    [liveLine],
  );

  /** Move `qty` of a split to a cell (`row` is the line it sits on, or the New line row). */
  const moveTo = React.useCallback(
    (
      lineId: string,
      splitId: string,
      qty: number,
      col: string,
      row: string,
    ) => {
      const cancel = col === CANCEL_COL;
      const on = cancel ? lineId : row === NEW_LINE_ROW ? null : row;
      const moved = apply(
        (current) =>
          moveSplit(current, lineId, splitId, qty, {
            date: cancel ? 'cancel' : col,
            sitsOnLineId: on,
          }),
        `${qty} to ${whereText(lineId, col, on)}`,
      );
      if (moved) focusCard(splitId);
    },
    [apply, whereText],
  );

  const openMoveBox = React.useCallback(
    (
      lineId: string,
      splitId: string,
      row: string,
      col: string,
      at?: { x: number; y: number },
    ) => {
      const line = liveLine(lineId);
      const split = line
        ? splitsOf(line).find((candidate) => candidate.id === splitId)
        : undefined;
      if (!split || !split.qty) return;
      let point = at;
      if (!point) {
        const rect = document
          .querySelector<HTMLElement>(`[data-split-card="${splitId}"]`)
          ?.getBoundingClientRect();
        point = rect
          ? { x: rect.right + 6, y: rect.top }
          : { x: window.innerWidth / 2, y: window.innerHeight / 3 };
      }
      const on =
        col === CANCEL_COL ? lineId : row === NEW_LINE_ROW ? null : row;
      setMoveTarget({
        lineId,
        splitId,
        row,
        col,
        x: point.x,
        y: point.y,
        max: split.qty,
        lineText:
          col === CANCEL_COL || on === lineId
            ? null
            : on
              ? lineShort(liveLine(on))
              : 'new line',
      });
    },
    [liveLine],
  );

  const rowOf = (origin: SplitLine, split: SplitDraft) =>
    split.isCancel ? origin.lineId : (split.sitsOnLineId ?? NEW_LINE_ROW);

  const actionsFor = React.useCallback(
    (origin: SplitLine, split: SplitDraft): SplitActions => ({
      splitInTwo: () => {
        if (split.qty < 2) return;
        const first = Math.ceil(split.qty / 2);
        apply(
          (current) => splitInTwo(current, origin.lineId, split.id),
          `Split into ${first} + ${split.qty - first}`,
        );
      },
      newDate: () =>
        openMoveBox(
          origin.lineId,
          split.id,
          rowOf(origin, split),
          NEW_DATE_COL,
        ),
      cancel: () =>
        moveTo(origin.lineId, split.id, split.qty, CANCEL_COL, origin.lineId),
      backToOwn: () => {
        if (
          !split.isCancel &&
          split.deliveryDate === origin.baseDate &&
          split.sitsOnLineId === origin.lineId
        ) {
          toast('Already on its own line and date');
          return;
        }
        apply(
          (current) => backToOwn(current, origin.lineId, split.id),
          'Back to its own line and date',
        );
      },
      sitsOn: (value: string) => {
        if (value === SITS_ON_CANCEL) {
          moveTo(origin.lineId, split.id, split.qty, CANCEL_COL, origin.lineId);
          return;
        }
        const date = split.isCancel ? origin.baseDate : split.deliveryDate;
        if (!date) return;
        moveTo(
          origin.lineId,
          split.id,
          split.qty,
          date,
          value === SITS_ON_NEW ? NEW_LINE_ROW : value,
        );
      },
    }),
    [apply, moveTo, openMoveBox],
  );

  const zoomTo = React.useCallback(
    (lineId: string) => {
      setSelectedLineId(lineId);
      setZoomOpen(true);
      // Zooming a line of a closed group opens that group (alone, on a big order).
      const line = liveLine(lineId);
      if (!line) return;
      const key = groupKeyOf(line);
      setOpenKeys((current) => {
        if (current.has(key)) return current;
        const big =
          (draftRef.current?.lines.length ?? 0) > OPEN_ALL_GROUPS_UP_TO;
        return new Set(big ? [key] : [...current, key]);
      });
    },
    [liveLine],
  );

  const onToggleGroup = React.useCallback((key: string) => {
    setOpenKeys((current) => {
      if (current.has(key)) {
        const next = new Set(current);
        next.delete(key);
        return next;
      }
      const big = (draftRef.current?.lines.length ?? 0) > OPEN_ALL_GROUPS_UP_TO;
      return new Set(big ? [key] : [...current, key]);
    });
  }, []);

  const onCardKey = React.useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>, placed: Placed) => {
      if (activeRef.current || event.metaKey || event.ctrlKey || event.altKey)
        return;
      const { split, origin, row, col } = placed;
      // The card's own group, read live: a card only ever moves inside it.
      const key = groupKeyOf(origin);
      const group = groupsOf(draftRef.current?.lines ?? []).find(
        (candidate) => candidate.key === key,
      );
      if (!group) return;
      const amount = event.shiftKey ? Math.min(10, split.qty) : split.qty;
      const cols = [...datesOf(group), CANCEL_COL];
      const rows = [...group.lines.map((line) => line.lineId), NEW_LINE_ROW];
      switch (event.key) {
        case 'ArrowLeft':
        case 'ArrowRight': {
          event.preventDefault();
          const to =
            cols[cols.indexOf(col) + (event.key === 'ArrowLeft' ? -1 : 1)];
          if (!to) {
            if (event.key === 'ArrowRight')
              openMoveBox(origin.lineId, split.id, row, NEW_DATE_COL);
            return;
          }
          moveTo(
            origin.lineId,
            split.id,
            amount,
            to,
            to === CANCEL_COL ? origin.lineId : row,
          );
          return;
        }
        case 'ArrowUp':
        case 'ArrowDown': {
          event.preventDefault();
          if (col === CANCEL_COL) return;
          const target =
            rows[rows.indexOf(row) + (event.key === 'ArrowUp' ? -1 : 1)];
          if (!target) return;
          moveTo(origin.lineId, split.id, amount, col, target);
          return;
        }
        case 'n':
        case 'N':
          event.preventDefault();
          openMoveBox(origin.lineId, split.id, row, NEW_DATE_COL);
          return;
        case 's':
        case 'S':
          event.preventDefault();
          actionsFor(origin, split).splitInTwo();
          return;
        case 'c':
        case 'C':
          event.preventDefault();
          actionsFor(origin, split).cancel();
          return;
        case 'Delete':
        case 'Backspace':
          event.preventDefault();
          actionsFor(origin, split).backToOwn();
          return;
        case 'Enter':
          event.preventDefault();
          zoomTo(origin.lineId);
          setSelectedSplitId(split.id);
          window.setTimeout(() => {
            const input = document.querySelector<HTMLInputElement>(
              `[data-split-qty="${split.id}"]`,
            );
            input?.focus();
            input?.select();
          }, 0);
          return;
        default:
      }
    },
    [actionsFor, moveTo, openMoveBox, zoomTo],
  );

  const onPickCard = React.useCallback(
    (placed: Placed) => {
      zoomTo(placed.origin.lineId);
      setSelectedSplitId(placed.split.id);
    },
    [zoomTo],
  );
  const cardHandlers = React.useMemo<CardHandlers>(
    () => ({ onKey: onCardKey, onPick: onPickCard }),
    [onCardKey, onPickCard],
  );

  const removeEmpty = React.useCallback(
    (line: SplitLine) => {
      window.setTimeout(() => {
        if (apply((current) => dropEmpty(current, line.lineId) ?? current)) {
          toast('Empty split removed', {
            action: { label: 'Undo', onClick: () => undo() },
          });
        }
      }, 0);
    },
    [apply, undo],
  );

  const handlers: SplitRowHandlers = {
    actionsFor,
    onQty: (line, split, qty) =>
      apply(
        (current) => setQty(current, line.lineId, split.id, qty),
        undefined,
        `qty:${split.id}`,
      ),
    onQtyBlur: (line) => {
      typingRef.current = null;
      removeEmpty(line);
    },
    onFieldBlur: () => {
      typingRef.current = null;
    },
    onDate: (line, split, date) =>
      apply((current) => setDate(current, line.lineId, split.id, date)),
    onRemark: (line, split, remark) =>
      apply(
        (current) => setRemark(current, line.lineId, split.id, remark),
        undefined,
        `remark:${split.id}`,
      ),
    onAdd: (line) => {
      let id = '';
      apply((current) => {
        const added = addSplit(current, line.lineId);
        id = added.id;
        return added.lines;
      });
      window.setTimeout(() => {
        document
          .querySelector<HTMLInputElement>(`[data-split-qty="${id}"]`)
          ?.focus();
      }, 0);
    },
    onPairStart: () => {
      const current = draftRef.current;
      if (current)
        commit({
          lines: current.lines,
          history: [...current.history, current.lines].slice(-HISTORY_LIMIT),
        });
    },
    onPair: (line, firstId, secondId, firstQty) => {
      const current = draftRef.current;
      if (!current) return;
      const next = setPair(
        current.lines,
        line.lineId,
        firstId,
        secondId,
        firstQty,
      );
      if (next !== current.lines)
        commit({ lines: next, history: current.history });
    },
    onPairEnd: (line) => {
      const current = draftRef.current;
      if (!current) return;
      const next = dropEmpty(current.lines, line.lineId);
      if (next) {
        commit({ lines: next, history: current.history });
        toast('Empty split removed', {
          action: { label: 'Undo', onClick: () => undo() },
        });
      }
    },
  };

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
    }),
  );

  const onDragStart = (event: DragStartEvent) => {
    activeRef.current =
      (event.active.data.current as CardData | undefined) ?? null;
    setActive(activeRef.current);
  };
  const onDragEnd = (event: DragEndEvent) => {
    const from = event.active.data.current as CardData | undefined;
    const to = event.over?.data.current as CellData | undefined;
    activeRef.current = null;
    setActive(null);
    if (!from || !to || !event.over) return;
    if (
      to.groupKey !== from.groupKey ||
      (to.row === from.row && to.col === from.col)
    )
      return;
    const rect = event.over.rect;
    openMoveBox(from.lineId, from.splitId, to.row, to.col, {
      x: rect.left + 8,
      y: rect.top + 8,
    });
  };

  const activePlaced = React.useMemo(() => {
    if (!active || !lines) return null;
    const group = groups.find((candidate) => candidate.key === active.groupKey);
    return group
      ? (placedOf(group).find((entry) => entry.split.id === active.splitId) ??
          null)
      : null;
  }, [active, lines, groups]);

  const gaps = lines ? lines.filter((line) => gapOf(line) !== 0) : [];
  const splitCount = lines ? lines.filter(isSplit).length : 0;
  const dirty =
    Boolean(lines) &&
    JSON.stringify(toPayload(lines as SplitLine[])) !== initialRef.current;

  const onSave = () => {
    const current = draftRef.current;
    if (!current || gaps.length > 0) return;
    let cleaned = current.lines;
    for (const line of current.lines)
      cleaned = dropEmpty(cleaned, line.lineId) ?? cleaned;
    if (cleaned !== current.lines)
      commit({ lines: cleaned, history: [...current.history, current.lines] });
    save.mutate(toPayload(cleaned), { onSuccess: () => onOpenChange(false) });
  };

  // The phone's line chips in group order, the focused line's group first.
  const phoneLines = React.useMemo(
    () => groups.flatMap((group) => group.lines),
    [groups],
  );
  const selected = byId(selectedLineId) ?? lines?.[0] ?? null;
  const selectedGroup = selected
    ? groups.find((group) => group.lines.includes(selected))
    : undefined;
  const position = selected && lines ? lines.indexOf(selected) + 1 : 0;
  const closeMoveBox = React.useCallback(() => setMoveTarget(null), []);

  const gapText = (line: SplitLine) => {
    const gap = gapOf(line);
    return gap > 0 ? `Gap ${gap}` : `Over ${-gap}`;
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) return;
        // Escape and the X never throw edits away; the footer's Cancel does (UAC B5).
        if (dirty) {
          toast('Unsaved changes: Save or Cancel');
          return;
        }
        onOpenChange(false);
      }}
    >
      <DialogContent
        variant="fullscreen"
        className="gap-0 overflow-hidden p-0 max-md:inset-0 max-md:rounded-none"
      >
        <div
          data-testid="line-split-dialog"
          className="flex h-full min-h-0 flex-col"
        >
          <DialogHeader className="mb-0 flex-row items-center justify-between gap-3 border-b border-border px-4.5 py-3 pe-12 text-start">
            <div className="min-w-0">
              <DialogTitle className="truncate">
                {`Split delivery · ${query.data?.so_number ?? ''}`}
              </DialogTitle>
              <DialogDescription className="truncate text-xs">
                {[query.data?.customer, query.data?.project]
                  .filter(Boolean)
                  .join(' · ')}
              </DialogDescription>
            </div>
            {!phone && !zoomOpen && lines ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setZoomOpen(true)}
              >
                Zoom line
              </Button>
            ) : null}
          </DialogHeader>

          <div className="flex min-h-0 flex-1">
            {query.isError && !lines ? (
              <div
                role="alert"
                className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-sm"
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
              <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
                Loading…
              </div>
            ) : phone ? (
              selected && selectedGroup ? (
                <div className="min-w-0 flex-1 overflow-auto p-4">
                  <LineSplitPhone
                    lines={phoneLines}
                    line={selected}
                    groupLines={selectedGroup.lines}
                    onPick={zoomTo}
                    handlers={handlers}
                  />
                </div>
              ) : null
            ) : (
              <DndContext
                sensors={sensors}
                onDragStart={onDragStart}
                onDragEnd={onDragEnd}
                onDragCancel={() => {
                  activeRef.current = null;
                  setActive(null);
                }}
              >
                <div className="min-w-0 flex-1 overflow-auto p-3.5">
                  <LineSplitGrid
                    groups={groups}
                    lines={lines}
                    openKeys={openKeys}
                    selectedLineId={
                      zoomOpen ? (selected?.lineId ?? null) : null
                    }
                    selectedSplitId={selectedSplitId}
                    handlers={cardHandlers}
                    actionsFor={actionsFor}
                    onToggle={onToggleGroup}
                    onZoom={zoomTo}
                  />
                </div>
                {zoomOpen && selected && selectedGroup ? (
                  <div className="w-[350px] shrink-0 overflow-auto border-l border-border p-3.5">
                    <LineSplitZoom
                      line={selected}
                      groupLines={selectedGroup.lines}
                      position={position}
                      count={lines.length}
                      onPrev={() =>
                        position > 1 &&
                        setSelectedLineId(lines[position - 2].lineId)
                      }
                      onNext={() =>
                        position < lines.length &&
                        setSelectedLineId(lines[position].lineId)
                      }
                      onClose={() => setZoomOpen(false)}
                      handlers={handlers}
                    />
                  </div>
                ) : null}
                {typeof document !== 'undefined'
                  ? createPortal(
                      <DragOverlay dropAnimation={null}>
                        {activePlaced ? (
                          <SplitCardBody
                            placed={activePlaced}
                            className="w-24 scale-105 shadow-lg"
                          />
                        ) : null}
                      </DragOverlay>,
                      document.body,
                    )
                  : null}
              </DndContext>
            )}
          </div>

          <div
            data-testid="line-split-footer"
            className="flex flex-wrap items-center justify-between gap-2.5 border-t border-border px-4.5 py-2.5"
          >
            <div className="min-w-0 text-xs">
              {gaps.length > 0 ? (
                <span className="font-medium text-destructive">
                  {phone
                    ? `${gaps.length} line${gaps.length === 1 ? ' does' : 's do'} not add up: ${gaps.map(gapText).join(', ')}`
                    : gaps
                        .map((line) => `${lineLabel(line)}: ${gapText(line)}`)
                        .join('; ')}
                </span>
              ) : lines ? (
                <span className="text-muted-foreground">
                  {`${splitCount} line${splitCount === 1 ? '' : 's'} split · every line adds up`}
                </span>
              ) : null}
            </div>
            <div className="flex items-center gap-2 max-md:w-full">
              {dirty ? (
                <span className="text-xs text-muted-foreground">Unsaved</span>
              ) : null}
              <Button
                type="button"
                variant="outline"
                className="max-md:flex-1"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                className="max-md:flex-1"
                disabled={!lines || gaps.length > 0 || save.isPending}
                onClick={onSave}
              >
                Save
              </Button>
            </div>
          </div>
        </div>

        <LineSplitMoveBox
          target={moveTarget}
          onClose={closeMoveBox}
          onMove={(target, qty, date) =>
            moveTo(target.lineId, target.splitId, qty, date, target.row)
          }
        />
      </DialogContent>
    </Dialog>
  );
}

export default LineSplitDialog;
