'use client';

import * as React from 'react';
import { ChevronLeft, ChevronRight, Plus, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuTrigger,
} from '@/components/ui/context-menu';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import {
  changeOf,
  formatSplitDate,
  gapOf,
  lineLabel,
  splitsOf,
  type SplitDraft,
  type SplitLine,
} from '../lib/lineSplitModel';
import {
  ChangePill,
  QtyInput,
  SitsOnSelect,
  SplitMenuButton,
  SplitMenuItems,
  type SplitActions,
} from './lineSplitParts';

const SEGMENT: Record<string, string> = {
  REMAIN: 'bg-muted text-foreground',
  DELAY: 'bg-amber-100 text-amber-800',
  ADVANCE: 'bg-sky-100 text-sky-800',
  CANCEL: 'bg-destructive/15 text-destructive',
};

/** Splits by date, cancels last. */
export function byDate(a: SplitDraft, b: SplitDraft): number {
  const x = a.isCancel ? '9999' : (a.deliveryDate ?? '');
  const y = b.isCancel ? '9999' : (b.deliveryDate ?? '');
  return x < y ? -1 : x > y ? 1 : 0;
}

/**
 * One segment per split (by date) and a gap segment while the splits fall short; the dark
 * dividers between two splits move qty between them (pointer, touch, or Left/Right, Shift 10).
 */
export function SplitBar({
  line,
  compact = false,
  onPairStart,
  onPair,
  onPairEnd,
}: {
  line: SplitLine;
  compact?: boolean;
  /** A divider drag begins (one Undo step for the whole drag). */
  onPairStart: () => void;
  onPair: (firstId: string, secondId: string, firstQty: number) => void;
  onPairEnd: () => void;
}) {
  const barRef = React.useRef<HTMLDivElement>(null);
  const splits = splitsOf(line).slice().sort(byDate);
  const gap = gapOf(line);
  const total = splits.reduce((sum, split) => sum + (split.qty || 0), 0);
  const scale = Math.max(line.baseQty, total) || 1;
  let cumulative = 0;
  const dividers: { left: number; first: SplitDraft; second: SplitDraft }[] =
    [];
  splits.forEach((split, index) => {
    cumulative += ((split.qty || 0) / scale) * 100;
    if (index < splits.length - 1)
      dividers.push({
        left: cumulative,
        first: split,
        second: splits[index + 1],
      });
  });

  const startDrag = (
    event: React.PointerEvent,
    first: SplitDraft,
    second: SplitDraft,
  ) => {
    event.preventDefault();
    const width = barRef.current?.getBoundingClientRect().width || 1;
    const x0 = event.clientX;
    const firstQty = first.qty;
    onPairStart();
    const move = (moveEvent: PointerEvent) => {
      onPair(
        first.id,
        second.id,
        firstQty + Math.round(((moveEvent.clientX - x0) / width) * scale),
      );
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      onPairEnd();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  return (
    <div
      ref={barRef}
      className="relative my-1.5 flex h-8.5 touch-none rounded-md bg-muted select-none"
    >
      {splits.map((split) => {
        const width = ((split.qty || 0) / scale) * 100;
        const word = changeOf(split, line);
        const text =
          width > (compact ? 16 : 11)
            ? `${split.qty}${width > (compact ? 32 : 24) && split.deliveryDate ? ` · ${formatSplitDate(split.deliveryDate)}` : ''}`
            : width > 4
              ? String(split.qty)
              : '';
        return (
          <div
            key={split.id}
            className={cn(
              'flex h-full items-center justify-center overflow-hidden border-r-2 border-background text-2xs font-medium whitespace-nowrap first:rounded-l-md last:rounded-r-md last:border-r-0',
              SEGMENT[word],
            )}
            style={{ width: `${width}%` }}
            title={`${split.qty} ${word === 'CANCEL' ? 'CANCEL BALANCE' : word} ${formatSplitDate(split.deliveryDate)}`}
          >
            {text}
          </div>
        );
      })}
      {gap > 0 ? (
        <div
          className="flex h-full items-center justify-center overflow-hidden rounded-r-md border border-dashed border-destructive/40 bg-destructive/10 text-2xs font-medium text-destructive"
          style={{ width: `${(gap / scale) * 100}%` }}
        >
          {`Gap ${gap}`}
        </div>
      ) : null}
      {dividers.map(({ left, first, second }) => (
        <div
          key={`${first.id}-${second.id}`}
          role="separator"
          aria-orientation="vertical"
          aria-label="Move qty between splits"
          aria-valuenow={first.qty}
          tabIndex={0}
          onPointerDown={(event) => startDrag(event, first, second)}
          onKeyDown={(event) => {
            if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
            event.preventDefault();
            const step =
              (event.shiftKey ? 10 : 1) * (event.key === 'ArrowLeft' ? -1 : 1);
            onPairStart();
            onPair(first.id, second.id, first.qty + step);
            onPairEnd();
          }}
          className="group absolute -top-1 -bottom-1 z-10 flex w-3.5 -translate-x-1/2 cursor-ew-resize items-center justify-center rounded outline-none focus-visible:ring-2 focus-visible:ring-primary"
          style={{ left: `${left}%` }}
        >
          <span className="h-full w-1 rounded-sm bg-foreground/75 group-hover:bg-primary" />
        </div>
      ))}
    </div>
  );
}

export function SplitSum({ line }: { line: SplitLine }) {
  const gap = gapOf(line);
  const total = splitsOf(line).reduce(
    (sum, split) => sum + (split.qty || 0),
    0,
  );
  return (
    <div className="mb-2 flex flex-wrap gap-3 text-xs">
      <span className="text-muted-foreground">
        Line <b className="text-foreground tabular-nums">{line.baseQty}</b>
      </span>
      <span className="text-muted-foreground">
        Split <b className="text-foreground tabular-nums">{total}</b>
      </span>
      {gap === 0 ? (
        <span className="font-medium text-emerald-700">Adds up</span>
      ) : (
        <span className="font-semibold text-destructive">
          {gap > 0 ? `Gap ${gap}` : `Over ${-gap}`}
        </span>
      )}
    </div>
  );
}

export interface SplitRowHandlers {
  actionsFor: (line: SplitLine, split: SplitDraft) => SplitActions;
  onQty: (line: SplitLine, split: SplitDraft, qty: number) => void;
  onQtyBlur: (line: SplitLine) => void;
  onDate: (line: SplitLine, split: SplitDraft, date: string) => void;
  onRemark: (line: SplitLine, split: SplitDraft, remark: string) => void;
  onAdd: (line: SplitLine) => void;
  onPairStart: () => void;
  onPair: (
    line: SplitLine,
    firstId: string,
    secondId: string,
    firstQty: number,
  ) => void;
  onPairEnd: (line: SplitLine) => void;
}

/** The zoom pane: one line, its bar and its splits table (UAC A8). */
export function LineSplitZoom({
  line,
  groupLines,
  position,
  count,
  onPrev,
  onNext,
  onClose,
  handlers,
}: {
  line: SplitLine;
  groupLines: SplitLine[];
  position: number;
  count: number;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  handlers: SplitRowHandlers;
}) {
  const splits = splitsOf(line).slice().sort(byDate);
  return (
    <div data-testid="line-split-zoom" className="space-y-1">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold tabular-nums">
            {lineLabel(line)}
          </div>
          <div className="truncate text-xs text-muted-foreground">
            {`${line.itemCode} · ${line.location}${
              line.pendingChange
                ? ` · AutoCount now ${line.nowQty} on ${formatSplitDate(line.nowDate)}`
                : ''
            }`}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <Button
            type="button"
            variant="ghost"
            mode="icon"
            size="sm"
            aria-label="Previous line"
            disabled={position <= 1}
            onClick={onPrev}
          >
            <ChevronLeft aria-hidden />
          </Button>
          <span className="text-xs whitespace-nowrap text-muted-foreground tabular-nums">{`${position} of ${count}`}</span>
          <Button
            type="button"
            variant="ghost"
            mode="icon"
            size="sm"
            aria-label="Next line"
            disabled={position >= count}
            onClick={onNext}
          >
            <ChevronRight aria-hidden />
          </Button>
          <Button
            type="button"
            variant="ghost"
            mode="icon"
            size="sm"
            aria-label="Close zoom"
            onClick={onClose}
          >
            <X aria-hidden />
          </Button>
        </div>
      </div>
      <SplitBar
        line={line}
        onPairStart={handlers.onPairStart}
        onPair={(first, second, qty) =>
          handlers.onPair(line, first, second, qty)
        }
        onPairEnd={() => handlers.onPairEnd(line)}
      />
      <SplitSum line={line} />
      <table className="w-full table-fixed border-collapse text-xs">
        <colgroup>
          <col style={{ width: 64 }} />
          <col style={{ width: 128 }} />
          <col />
        </colgroup>
        <thead>
          <tr className="text-left text-muted-foreground">
            <th className="border-b border-border px-1 py-1.5 text-right font-medium">
              Qty
            </th>
            <th className="border-b border-border px-1 py-1.5 font-medium">
              Date
            </th>
            <th className="border-b border-border px-1 py-1.5 font-medium">
              Sits on / remark
            </th>
          </tr>
        </thead>
        <tbody>
          {splits.map((split) => {
            const actions = handlers.actionsFor(line, split);
            return (
              <ContextMenu key={split.id}>
                <ContextMenuTrigger asChild>
                  <tr className="align-top hover:bg-muted/30">
                    <td className="border-b border-border px-1 py-1.5">
                      <QtyInput
                        splitId={split.id}
                        value={split.qty}
                        onValue={(qty) => handlers.onQty(line, split, qty)}
                        onBlur={() => handlers.onQtyBlur(line)}
                        className="text-right tabular-nums"
                      />
                    </td>
                    <td className="border-b border-border px-1 py-1.5">
                      {split.isCancel ? (
                        <div className="flex h-7 items-center">
                          <ChangePill word="CANCEL" />
                        </div>
                      ) : (
                        <>
                          <Input
                            type="date"
                            variant="sm"
                            aria-label="Date"
                            value={split.deliveryDate ?? ''}
                            onChange={(event) =>
                              handlers.onDate(line, split, event.target.value)
                            }
                          />
                          <div className="mt-1">
                            <ChangePill word={changeOf(split, line)} />
                          </div>
                        </>
                      )}
                    </td>
                    <td className="border-b border-border px-1 py-1.5">
                      <div className="flex min-w-0 items-center gap-1">
                        <SitsOnSelect
                          split={split}
                          origin={line}
                          groupLines={groupLines}
                          onChange={actions.sitsOn}
                        />
                        <SplitMenuButton
                          split={split}
                          origin={line}
                          groupLines={groupLines}
                          actions={actions}
                        />
                      </div>
                      <Input
                        variant="sm"
                        className="mt-1"
                        aria-label="Remark"
                        placeholder="Remark"
                        value={split.remark}
                        onChange={(event) =>
                          handlers.onRemark(line, split, event.target.value)
                        }
                      />
                    </td>
                  </tr>
                </ContextMenuTrigger>
                <ContextMenuContent className="min-w-60">
                  <SplitMenuItems
                    kind="context"
                    split={split}
                    origin={line}
                    groupLines={groupLines}
                    actions={actions}
                  />
                </ContextMenuContent>
              </ContextMenu>
            );
          })}
        </tbody>
      </table>
      <div className="pt-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => handlers.onAdd(line)}
        >
          <Plus aria-hidden />
          Add split
        </Button>
      </div>
    </div>
  );
}
