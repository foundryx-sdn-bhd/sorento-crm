'use client';

import * as React from 'react';
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

export function SplitCardBody({
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
        <div className="truncate text-amber-700">{`AutoCount ${formatSplitDate(rowLine.nowDate)}`}</div>
      ) : null}
      {split.remark ? (
        <div className="truncate text-muted-foreground" title={split.remark}>
          {split.remark}
        </div>
      ) : null}
    </div>
  );
}

function SplitCard({
  placed,
  groupKey,
  groupLines,
  rowLine,
  selected,
  actions,
  onKey,
  onPick,
}: {
  placed: Placed;
  groupKey: string;
  groupLines: SplitLine[];
  rowLine?: SplitLine;
  selected: boolean;
  actions: SplitActions;
  onKey: (event: React.KeyboardEvent<HTMLDivElement>, placed: Placed) => void;
  onPick: (placed: Placed) => void;
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
    <ContextMenu>
      <ContextMenuTrigger asChild>
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
          onClick={() => onPick(placed)}
          onKeyDown={(event) => {
            onKey(event, placed);
            if (!event.defaultPrevented) listeners?.onKeyDown?.(event);
          }}
          className={cn(
            'cursor-grab touch-none select-none rounded-md outline-none focus-visible:ring-2 focus-visible:ring-primary',
            selected && 'ring-2 ring-primary',
            isDragging && 'opacity-35',
          )}
        >
          <SplitCardBody placed={placed} rowLine={rowLine} />
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent className="min-w-60">
        <SplitMenuItems
          kind="context"
          split={split}
          origin={origin}
          groupLines={groupLines}
          actions={actions}
        />
      </ContextMenuContent>
    </ContextMenu>
  );
}

function DropCell({
  groupKey,
  row,
  col,
  activeGroupKey,
  original,
  children,
}: {
  groupKey: string;
  row: string;
  col: string;
  activeGroupKey: string | null;
  original: boolean;
  children: React.ReactNode;
}) {
  const disabled = row === NEW_LINE_ROW && col === CANCEL_COL;
  const data: CellData = { groupKey, row, col };
  const { setNodeRef, isOver } = useDroppable({
    id: `${groupKey}::${row}::${col}`,
    data,
    disabled:
      disabled || (activeGroupKey !== null && activeGroupKey !== groupKey),
  });
  return (
    <div
      ref={setNodeRef}
      title={original ? 'Original date of this line' : undefined}
      className={cn(
        'flex min-h-13 flex-col gap-1 border-r border-b border-border p-1',
        original && 'bg-muted/40',
        col === NEW_DATE_COL && 'bg-muted/20',
        col === CANCEL_COL && 'bg-destructive/5',
        isOver && !disabled && 'bg-primary/10 ring-2 ring-inset ring-primary',
      )}
    >
      {children}
    </div>
  );
}

function RowHeader({
  line,
  lines,
  selected,
  onZoom,
}: {
  line: SplitLine;
  lines: SplitLine[];
  selected: boolean;
  onZoom: () => void;
}) {
  const gap = gapOf(line);
  const plan = planOf(lines, line.lineId);
  const movedOut = splitsOf(line).filter(
    (split) => !split.isCancel && split.sitsOnLineId !== line.lineId,
  );
  const movedIn = lines.flatMap((origin) =>
    origin.lineId === line.lineId
      ? []
      : splitsOf(origin)
          .filter(
            (split) => !split.isCancel && split.sitsOnLineId === line.lineId,
          )
          .map((split) => ({ split, origin })),
  );
  const left = leftOf(lines, line.lineId);
  const byId = (id: string | null) =>
    lines.find((candidate) => candidate.lineId === id);
  return (
    <div
      role="button"
      tabIndex={0}
      data-testid={`line-split-row-${line.lineId}`}
      onClick={onZoom}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onZoom();
        }
      }}
      className={cn(
        'flex min-w-0 cursor-pointer flex-col gap-0.5 border-r border-b border-border px-2 py-1.5 text-xs outline-none focus-visible:ring-2 focus-visible:ring-primary',
        selected && 'bg-primary/5 shadow-[inset_3px_0_0_var(--color-primary)]',
      )}
    >
      <div className="font-semibold tabular-nums whitespace-nowrap">
        {lineLabel(line)}
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {isSplit(line) ? (
          <Badge variant="secondary" appearance="light" size="sm">
            {`Split ${splitsOf(line).length}`}
          </Badge>
        ) : null}
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
      {movedOut.length > 0 ? (
        <div className="truncate text-2xs text-muted-foreground">
          {`→ ${movedOut
            .map(
              (split) =>
                `${split.qty} to ${split.sitsOnLineId ? lineShort(byId(split.sitsOnLineId)) : 'new line'}`,
            )
            .join(', ')}`}
        </div>
      ) : null}
      {movedIn.length > 0 ? (
        <div className="truncate text-2xs text-muted-foreground">
          {`← ${movedIn.map(({ split, origin }) => `${split.qty} from ${lineShort(origin)}`).join(', ')}`}
        </div>
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
}

export function LineSplitGrid({
  groups,
  lines,
  selectedLineId,
  selectedSplitId,
  activeGroupKey,
  actionsFor,
  onKey,
  onPick,
  onZoom,
}: {
  groups: SplitGroup[];
  lines: SplitLine[];
  selectedLineId: string | null;
  selectedSplitId: string | null;
  /** The group of the card being dragged: other groups are not drop targets. */
  activeGroupKey: string | null;
  actionsFor: (placed: Placed) => SplitActions;
  onKey: (
    event: React.KeyboardEvent<HTMLDivElement>,
    placed: Placed,
    group: SplitGroup,
  ) => void;
  onPick: (placed: Placed) => void;
  onZoom: (lineId: string) => void;
}) {
  return (
    <div className="space-y-5">
      {groups.map((group) => {
        const cols = [...datesOf(group), NEW_DATE_COL, CANCEL_COL];
        const placed = placedOf(group);
        const rows = [...group.lines.map((line) => line.lineId), NEW_LINE_ROW];
        const template = `230px ${cols.map((col) => (col === NEW_DATE_COL || col === CANCEL_COL ? '92px' : '96px')).join(' ')}`;
        return (
          <div key={group.key}>
            <div className="mb-1.5 flex items-center gap-2 text-sm font-semibold">
              {group.itemCode}
              <Badge variant="outline" size="sm">
                {group.location}
              </Badge>
              <span className="text-xs font-normal text-muted-foreground">
                {`${group.lines.length} line${group.lines.length === 1 ? '' : 's'}`}
              </span>
            </div>
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
                return (
                  <React.Fragment key={row}>
                    {rowLine ? (
                      <RowHeader
                        line={rowLine}
                        lines={lines}
                        selected={selectedLineId === rowLine.lineId}
                        onZoom={() => onZoom(rowLine.lineId)}
                      />
                    ) : (
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
                    )}
                    {cols.map((col) => (
                      <DropCell
                        key={col}
                        groupKey={group.key}
                        row={row}
                        col={col}
                        activeGroupKey={activeGroupKey}
                        original={Boolean(rowLine && rowLine.baseDate === col)}
                      >
                        {placed
                          .filter(
                            (entry) => entry.row === row && entry.col === col,
                          )
                          .map((entry) => (
                            <SplitCard
                              key={entry.split.id}
                              placed={entry}
                              groupKey={group.key}
                              groupLines={group.lines}
                              rowLine={rowLine}
                              selected={selectedSplitId === entry.split.id}
                              actions={actionsFor(entry)}
                              onKey={(event, target) =>
                                onKey(event, target, group)
                              }
                              onPick={onPick}
                            />
                          ))}
                      </DropCell>
                    ))}
                  </React.Fragment>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
