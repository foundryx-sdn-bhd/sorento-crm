'use client';

import * as React from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useDraggable, useDroppable } from '@dnd-kit/core';
import { Badge } from '@/components/ui/badge';
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuTrigger,
} from '@/components/ui/context-menu';
import { cn } from '@/lib/utils';
import { OrderInquiryVerbPill } from '../../_shared/components/OrderInquiryVerbPill';
import {
  changeOf,
  datesOf,
  formatSplitDate,
  gapOf,
  isSplit,
  leftOf,
  lineLabel,
  lineShort,
  planOf,
  splitsOf,
  type SplitDraft,
  type SplitGroup,
  type SplitLine,
} from '../lib/lineSplitModel';
import { SplitMenuItems, type SplitActions } from './lineSplitParts';

/** The row of a group holding splits that sit on a new line (not in AutoCount yet). */
export const NEW_LINE_ROW = '__new_line__';
export const NEW_DATE_COL = 'new';
export const CANCEL_COL = 'cancel';

/**
 * Up to this many lines, every group is open at once. A bigger order opens only the
 * focused line's group (one at a time): each open cell is a drop target, and a 255-line
 * order with every group open froze the drag (review round 1).
 */
export const OPEN_ALL_GROUPS_UP_TO = 20;

/** What a dragged card and a drop cell carry. */
export interface CardData {
  lineId: string;
  splitId: string;
  groupKey: string;
  row: string;
  col: string;
}

export interface CellData {
  groupKey: string;
  row: string;
  col: string;
}

export interface Placed {
  split: SplitDraft;
  origin: SplitLine;
  row: string;
  col: string;
}

/** Every split of every line in the group, at the cell it sits in. */
export function placedOf(group: SplitGroup): Placed[] {
  const ids = new Set(group.lines.map((line) => line.lineId));
  const out: Placed[] = [];
  for (const origin of group.lines) {
    for (const split of splitsOf(origin)) {
      const row = split.isCancel
        ? origin.lineId
        : split.sitsOnLineId && ids.has(split.sitsOnLineId)
          ? split.sitsOnLineId
          : NEW_LINE_ROW;
      const col = split.isCancel
        ? CANCEL_COL
        : (split.deliveryDate ?? NEW_DATE_COL);
      out.push({ split, origin, row, col });
    }
  }
  return out;
}

const WORD_BORDER: Record<string, string> = {
  REMAIN: 'border-l-muted-foreground/50',
  DELAY: 'border-l-amber-500',
  ADVANCE: 'border-l-sky-500',
  CANCEL: 'border-l-destructive bg-destructive/5',
};

export const SplitCardBody = React.memo(function SplitCardBody({
  placed,
  rowLine,
  className,
}: {
  placed: Placed;
  rowLine?: SplitLine;
  className?: string;
}) {
  const { split, origin, row } = placed;
  const word = changeOf(split, origin);
  const autoCountMoved =
    rowLine?.pendingChange &&
    !split.isCancel &&
    split.deliveryDate &&
    rowLine.nowDate &&
    rowLine.nowDate !== split.deliveryDate;
  return (
    <div
      className={cn(
        'rounded-md border border-l-[3px] bg-background px-1.5 py-1 text-2xs leading-tight shadow-xs',
        WORD_BORDER[word],
        className,
      )}
    >
      <div className="text-sm font-semibold tabular-nums">{split.qty}</div>
      {origin.lineId !== row ? (
        <div
          className="truncate text-muted-foreground"
          title={`from ${lineLabel(origin)}`}
        >
          {`from ${lineShort(origin)}`}
        </div>
      ) : null}
      {autoCountMoved ? (
        <div className="truncate text-amber-700">
          {`AutoCount ${formatSplitDate(rowLine.nowDate)}`}
        </div>
      ) : null}
      {split.remark ? (
        <div className="truncate text-muted-foreground" title={split.remark}>
          {split.remark}
        </div>
      ) : null}
    </div>
  );
});

/** A line whose saved splits AutoCount has since moved on from (they no longer act). */
export function AutoCountChangedBadge() {
  return (
    <Badge variant="warning" appearance="light" size="sm">
      AutoCount changed
    </Badge>
  );
}

/** Stable handlers the cells call with ids, so a cell re-renders only when its data moves. */
export interface CardHandlers {
  onKey: (event: React.KeyboardEvent<HTMLDivElement>, placed: Placed) => void;
  onPick: (placed: Placed) => void;
}

const SplitCard = React.memo(
  function SplitCard({
    placed,
    groupKey,
    rowLine,
    selected,
    handlers,
  }: {
    placed: Placed;
    groupKey: string;
    rowLine?: SplitLine;
    selected: boolean;
    handlers: CardHandlers;
  }) {
    const { split, origin, row, col } = placed;
    const data: CardData = {
      lineId: origin.lineId,
      splitId: split.id,
      groupKey,
      row,
      col,
    };
    const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
      id: split.id,
      data,
    });
    const word = changeOf(split, origin);
    const label = `${split.qty} ${word === 'CANCEL' ? 'CANCEL BALANCE' : word}${
      split.deliveryDate ? ` on ${formatSplitDate(split.deliveryDate)}` : ''
    } from ${lineLabel(origin)}`;
    return (
      <div
        ref={setNodeRef}
        {...attributes}
        {...listeners}
        role="button"
        tabIndex={0}
        aria-roledescription="draggable split"
        aria-label={label}
        data-testid={`line-split-card-${split.id}`}
        data-split-card={split.id}
        data-split-origin={origin.lineId}
        onClick={() => handlers.onPick(placed)}
        onKeyDown={(event) => {
          handlers.onKey(event, placed);
          if (!event.defaultPrevented) listeners?.onKeyDown?.(event);
        }}
        className={cn(
          'cursor-grab touch-none rounded-md outline-none select-none focus-visible:ring-2 focus-visible:ring-primary',
          selected && 'ring-2 ring-primary',
          isDragging && 'opacity-35',
        )}
      >
        <SplitCardBody placed={placed} rowLine={rowLine} />
      </div>
    );
  },
  (a, b) =>
    samePlaced(a.placed, b.placed) &&
    a.groupKey === b.groupKey &&
    a.rowLine === b.rowLine &&
    a.selected === b.selected &&
    a.handlers === b.handlers,
);

function samePlaced(a: Placed, b: Placed): boolean {
  return (
    a.split === b.split &&
    a.origin === b.origin &&
    a.row === b.row &&
    a.col === b.col
  );
}

const DropCell = React.memo(
  function DropCell({
    groupKey,
    row,
    col,
    original,
    entries,
    rowLine,
    selectedSplitId,
    handlers,
  }: {
    groupKey: string;
    row: string;
    col: string;
    original: boolean;
    entries: Placed[];
    rowLine?: SplitLine;
    selectedSplitId: string | null;
    handlers: CardHandlers;
  }) {
    const disabled = row === NEW_LINE_ROW && col === CANCEL_COL;
    const data: CellData = { groupKey, row, col };
    const { setNodeRef, isOver, active } = useDroppable({
      id: `${groupKey}::${row}::${col}`,
      data,
      disabled,
    });
    // A card only moves inside its own group: another group's cell never lights up.
    const sameGroup =
      (active?.data.current as CardData | undefined)?.groupKey === groupKey;
    return (
      <div
        ref={setNodeRef}
        data-testid={`line-split-cell-${row}-${col}`}
        title={original ? 'Original date of this line' : undefined}
        className={cn(
          'flex min-h-13 flex-col gap-1 border-r border-b border-border p-1',
          original && 'bg-muted/40',
          col === NEW_DATE_COL && 'bg-muted/20',
          col === CANCEL_COL && 'bg-destructive/5',
          isOver &&
            sameGroup &&
            !disabled &&
            'bg-primary/10 ring-2 ring-primary ring-inset',
        )}
      >
        <CellCards
          entries={entries}
          groupKey={groupKey}
          rowLine={rowLine}
          selectedSplitId={selectedSplitId}
          handlers={handlers}
        />
      </div>
    );
  },
  (a, b) =>
    a.groupKey === b.groupKey &&
    a.row === b.row &&
    a.col === b.col &&
    a.original === b.original &&
    a.rowLine === b.rowLine &&
    a.selectedSplitId === b.selectedSplitId &&
    a.handlers === b.handlers &&
    a.entries.length === b.entries.length &&
    a.entries.every((entry, index) => samePlaced(entry, b.entries[index])),
);

/** The cards of one cell, memoised apart from the cell so a drag's `isOver` repaint of the
 * cell does not repaint its cards. */
const CellCards = React.memo(function CellCards({
  entries,
  groupKey,
  rowLine,
  selectedSplitId,
  handlers,
}: {
  entries: Placed[];
  groupKey: string;
  rowLine?: SplitLine;
  selectedSplitId: string | null;
  handlers: CardHandlers;
}) {
  return (
    <>
      {entries.map((entry) => (
        <SplitCard
          key={entry.split.id}
          placed={entry}
          groupKey={groupKey}
          rowLine={rowLine}
          selected={selectedSplitId === entry.split.id}
          handlers={handlers}
        />
      ))}
    </>
  );
});

const RowHeader = React.memo(function RowHeader({
  line,
  plan,
  left,
  movedOut,
  movedIn,
  selected,
  onZoom,
}: {
  line: SplitLine;
  plan: number;
  left: number;
  movedOut: string;
  movedIn: string;
  selected: boolean;
  onZoom: (lineId: string) => void;
}) {
  const gap = gapOf(line);
  return (
    <div
      role="button"
      tabIndex={0}
      data-testid={`line-split-row-${line.lineId}`}
      onClick={() => onZoom(line.lineId)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onZoom(line.lineId);
        }
      }}
      className={cn(
        'flex min-w-0 cursor-pointer flex-col gap-0.5 border-r border-b border-border px-2 py-1.5 text-xs outline-none focus-visible:ring-2 focus-visible:ring-primary',
        selected && 'bg-primary/5 shadow-[inset_3px_0_0_var(--color-primary)]',
      )}
    >
      <div className="font-semibold whitespace-nowrap tabular-nums">
        {lineLabel(line)}
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {isSplit(line) ? (
          <Badge variant="secondary" appearance="light" size="sm">
            {`Split ${splitsOf(line).length}`}
          </Badge>
        ) : null}
        {line.stale ? <AutoCountChangedBadge /> : null}
        {gap > 0 ? (
          <span className="text-2xs font-semibold text-destructive">{`Gap ${gap}`}</span>
        ) : gap < 0 ? (
          <span className="text-2xs font-semibold text-destructive">{`Over ${-gap}`}</span>
        ) : null}
        {line.pendingChange ? (
          left === 0 ? (
            <Badge variant="success" appearance="light" size="sm">
              Explained
            </Badge>
          ) : left > 0 ? (
            <span title={`ORDER +${left}`}>
              <OrderInquiryVerbPill verb="ORDER" />
              <span className="ms-1 text-2xs font-medium tabular-nums">{`+${left}`}</span>
            </span>
          ) : (
            <span title={`CANCEL BALANCE ${-left}`}>
              <OrderInquiryVerbPill verb="CANCEL_BALANCE" />
              <span className="ms-1 text-2xs font-medium tabular-nums">
                {-left}
              </span>
            </span>
          )
        ) : null}
      </div>
      {movedOut ? (
        <div className="truncate text-2xs text-muted-foreground">
          {movedOut}
        </div>
      ) : null}
      {movedIn ? (
        <div className="truncate text-2xs text-muted-foreground">{movedIn}</div>
      ) : null}
      <div className="truncate text-2xs text-muted-foreground">
        {`Plan ${plan} · AutoCount ${line.nowQty}${
          line.pendingChange && line.nowDate !== line.baseDate && line.nowDate
            ? ` on ${formatSplitDate(line.nowDate)}`
            : ''
        }`}
      </div>
    </div>
  );
});

/** The open group: its date grid of drop cells and cards. */
const GroupGrid = React.memo(function GroupGrid({
  group,
  lines,
  selectedLineId,
  selectedSplitId,
  handlers,
  onZoom,
}: {
  group: SplitGroup;
  lines: SplitLine[];
  selectedLineId: string | null;
  selectedSplitId: string | null;
  handlers: CardHandlers;
  onZoom: (lineId: string) => void;
}) {
  const cols = React.useMemo(
    () => [...datesOf(group), NEW_DATE_COL, CANCEL_COL],
    [group],
  );
  const placed = React.useMemo(() => placedOf(group), [group]);
  const byCell = React.useMemo(() => {
    const map = new Map<string, Placed[]>();
    for (const entry of placed) {
      const key = `${entry.row}::${entry.col}`;
      const list = map.get(key);
      if (list) list.push(entry);
      else map.set(key, [entry]);
    }
    return map;
  }, [placed]);
  const rows = [...group.lines.map((line) => line.lineId), NEW_LINE_ROW];
  const template = `230px ${cols
    .map((col) =>
      col === NEW_DATE_COL || col === CANCEL_COL ? '92px' : '96px',
    )
    .join(' ')}`;
  const byId = (id: string | null) =>
    lines.find((candidate) => candidate.lineId === id);
  return (
    <div
      className="grid min-w-max overflow-hidden rounded-lg border-t border-l border-border"
      style={{ gridTemplateColumns: template }}
    >
      <div className="border-r border-b border-border bg-muted/40 px-2 py-1.5 text-xs font-medium text-muted-foreground">
        SO line
      </div>
      {cols.map((col) => (
        <div
          key={col}
          className={cn(
            'flex items-center justify-center border-r border-b border-border bg-muted/40 px-1 py-1.5 text-xs font-medium whitespace-nowrap text-muted-foreground',
            col === CANCEL_COL && 'text-destructive',
          )}
        >
          {col === NEW_DATE_COL
            ? 'New date'
            : col === CANCEL_COL
              ? 'Cancel'
              : formatSplitDate(col)}
        </div>
      ))}
      {rows.map((row) => {
        const rowLine = group.lines.find((line) => line.lineId === row);
        let header: React.ReactNode;
        if (rowLine) {
          const out = splitsOf(rowLine).filter(
            (split) => !split.isCancel && split.sitsOnLineId !== rowLine.lineId,
          );
          const into = lines.flatMap((origin) =>
            origin.lineId === rowLine.lineId
              ? []
              : splitsOf(origin)
                  .filter(
                    (split) =>
                      !split.isCancel && split.sitsOnLineId === rowLine.lineId,
                  )
                  .map((split) => `${split.qty} from ${lineShort(origin)}`),
          );
          header = (
            <RowHeader
              line={rowLine}
              plan={planOf(lines, rowLine.lineId)}
              left={leftOf(lines, rowLine.lineId)}
              movedOut={
                out.length
                  ? `→ ${out
                      .map(
                        (split) =>
                          `${split.qty} to ${
                            split.sitsOnLineId
                              ? lineShort(byId(split.sitsOnLineId))
                              : 'new line'
                          }`,
                      )
                      .join(', ')}`
                  : ''
              }
              movedIn={into.length ? `← ${into.join(', ')}` : ''}
              selected={selectedLineId === rowLine.lineId}
              onZoom={onZoom}
            />
          );
        } else {
          header = (
            <div
              data-testid={`line-split-new-line-${group.key}`}
              className="flex flex-col justify-center border-r border-b border-border px-2 py-1.5 text-xs"
            >
              <span className="font-medium text-muted-foreground italic">
                New line
              </span>
              <span className="text-2xs text-muted-foreground">
                not in AutoCount yet
              </span>
            </div>
          );
        }
        return (
          <React.Fragment key={row}>
            {header}
            {cols.map((col) => (
              <DropCell
                key={col}
                groupKey={group.key}
                row={row}
                col={col}
                original={Boolean(rowLine && rowLine.baseDate === col)}
                entries={byCell.get(`${row}::${col}`) ?? EMPTY}
                rowLine={rowLine}
                selectedSplitId={selectedSplitId}
                handlers={handlers}
              />
            ))}
          </React.Fragment>
        );
      })}
    </div>
  );
});

const EMPTY: Placed[] = [];

/** One group's header: item, location, line count, Split / Gap badges; toggles the grid. */
function GroupHeader({
  group,
  open,
  onToggle,
}: {
  group: SplitGroup;
  open: boolean;
  onToggle: (key: string) => void;
}) {
  const split = group.lines.filter(isSplit).length;
  const gaps = group.lines.filter((line) => gapOf(line) !== 0).length;
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <button
      type="button"
      data-testid={`line-split-group-${group.itemCode}-${group.location}`}
      aria-expanded={open}
      onClick={() => onToggle(group.key)}
      className="mb-1.5 flex w-full items-center gap-2 rounded-md px-1 py-1 text-left text-sm font-semibold hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none"
    >
      <Chevron className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      {group.itemCode}
      <Badge variant="outline" size="sm">
        {group.location}
      </Badge>
      <span className="text-xs font-normal text-muted-foreground">
        {`${group.lines.length} line${group.lines.length === 1 ? '' : 's'}`}
      </span>
      {split > 0 ? (
        <Badge variant="secondary" appearance="light" size="sm">
          {`Split ${split}`}
        </Badge>
      ) : null}
      {gaps > 0 ? (
        <span className="text-2xs font-semibold text-destructive">
          {`Gap ${gaps} line${gaps === 1 ? '' : 's'}`}
        </span>
      ) : null}
    </button>
  );
}

export function LineSplitGrid({
  groups,
  lines,
  openKeys,
  selectedLineId,
  selectedSplitId,
  handlers,
  actionsFor,
  onToggle,
  onZoom,
}: {
  /** Already in display order (the focused line's group first). */
  groups: SplitGroup[];
  lines: SplitLine[];
  /** The groups drawn as a grid; the others show their header only. */
  openKeys: ReadonlySet<string>;
  selectedLineId: string | null;
  selectedSplitId: string | null;
  handlers: CardHandlers;
  actionsFor: (origin: SplitLine, split: SplitDraft) => SplitActions;
  onToggle: (key: string) => void;
  onZoom: (lineId: string) => void;
}) {
  // ONE right-click menu for every card (review round 1): it opens for the card under the
  // pointer, or the focused card on Shift+F10, and does nothing anywhere else.
  const [menuFor, setMenuFor] = React.useState<{
    lineId: string;
    splitId: string;
  } | null>(null);
  const menuOrigin = menuFor
    ? lines.find((line) => line.lineId === menuFor.lineId)
    : undefined;
  const menuSplit = menuOrigin
    ? splitsOf(menuOrigin).find((split) => split.id === menuFor?.splitId)
    : undefined;
  const menuGroup = menuOrigin
    ? groups.find((group) => group.lines.includes(menuOrigin))
    : undefined;
  return (
    <ContextMenu onOpenChange={(open) => !open && setMenuFor(null)}>
      <ContextMenuTrigger
        asChild
        onContextMenu={(event) => {
          const card = (event.target as HTMLElement).closest<HTMLElement>(
            '[data-split-card]',
          );
          if (!card) {
            // No card here: no menu.
            event.preventDefault();
            return;
          }
          setMenuFor({
            lineId: card.dataset.splitOrigin ?? '',
            splitId: card.dataset.splitCard ?? '',
          });
        }}
      >
        <div className="space-y-3">
          {groups.map((group) => {
            const open = openKeys.has(group.key);
            return (
              <div key={group.key}>
                <GroupHeader group={group} open={open} onToggle={onToggle} />
                {open ? (
                  <GroupGrid
                    group={group}
                    lines={lines}
                    selectedLineId={selectedLineId}
                    selectedSplitId={selectedSplitId}
                    handlers={handlers}
                    onZoom={onZoom}
                  />
                ) : null}
              </div>
            );
          })}
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent className="min-w-60">
        {menuOrigin && menuSplit && menuGroup ? (
          <SplitMenuItems
            kind="context"
            split={menuSplit}
            origin={menuOrigin}
            groupLines={menuGroup.lines}
            actions={actionsFor(menuOrigin, menuSplit)}
          />
        ) : null}
      </ContextMenuContent>
    </ContextMenu>
  );
}
