'use client';

import * as React from 'react';
import { Minus, Plus, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import {
  changeOf,
  gapOf,
  lineLabel,
  splitsOf,
  type SplitLine,
} from '../lib/lineSplitModel';
import {
  ChangePill,
  QtyInput,
  SitsOnSelect,
  SplitMenuButton,
} from './lineSplitParts';
import {
  SplitBar,
  SplitSum,
  byDate,
  type SplitRowHandlers,
} from './LineSplitZoom';

const CARD_BORDER: Record<string, string> = {
  REMAIN: 'border-l-muted-foreground/50',
  DELAY: 'border-l-amber-500',
  ADVANCE: 'border-l-sky-500',
  CANCEL: 'border-l-destructive',
};

/** Phone (below md, UAC F1): one line at a time, each split a card. */
export function LineSplitPhone({
  lines,
  line,
  groupLines,
  onPick,
  handlers,
}: {
  lines: SplitLine[];
  line: SplitLine;
  groupLines: SplitLine[];
  onPick: (lineId: string) => void;
  handlers: SplitRowHandlers;
}) {
  const splits = splitsOf(line).slice().sort(byDate);
  return (
    <div className="space-y-3">
      <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1">
        {lines.map((candidate) => (
          <Button
            key={candidate.lineId}
            type="button"
            size="sm"
            variant={candidate.lineId === line.lineId ? 'primary' : 'outline'}
            className="shrink-0 rounded-full"
            onClick={() => onPick(candidate.lineId)}
          >
            {`${candidate.itemCode} ${candidate.baseQty}`}
            {gapOf(candidate) !== 0 ? (
              <TriangleAlert aria-label="Does not add up" />
            ) : null}
          </Button>
        ))}
      </div>
      <div>
        <div className="text-sm font-semibold tabular-nums">
          {lineLabel(line)}
        </div>
        <SplitBar
          line={line}
          compact
          onPairStart={handlers.onPairStart}
          onPair={(first, second, qty) =>
            handlers.onPair(line, first, second, qty)
          }
          onPairEnd={() => handlers.onPairEnd(line)}
        />
        <SplitSum line={line} />
      </div>
      {splits.map((split) => {
        const word = changeOf(split, line);
        const actions = handlers.actionsFor(line, split);
        return (
          <div
            key={split.id}
            className={cn(
              'space-y-2 rounded-lg border border-l-[3px] border-border p-2.5',
              CARD_BORDER[word],
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center overflow-hidden rounded-md border border-input">
                <Button
                  type="button"
                  variant="ghost"
                  mode="icon"
                  aria-label="Less"
                  onClick={() =>
                    handlers.onQty(line, split, Math.max(0, split.qty - 1))
                  }
                >
                  <Minus aria-hidden />
                </Button>
                <QtyInput
                  splitId={split.id}
                  value={split.qty}
                  onValue={(qty) => handlers.onQty(line, split, qty)}
                  onBlur={() => handlers.onQtyBlur(line)}
                  className="w-16 rounded-none border-y-0 text-center font-semibold tabular-nums"
                />
                <Button
                  type="button"
                  variant="ghost"
                  mode="icon"
                  aria-label="More"
                  onClick={() => handlers.onQty(line, split, split.qty + 1)}
                >
                  <Plus aria-hidden />
                </Button>
              </div>
              <ChangePill word={word} />
              <SplitMenuButton
                split={split}
                origin={line}
                groupLines={groupLines}
                actions={actions}
              />
            </div>
            {split.isCancel ? null : (
              <Input
                type="date"
                aria-label="Date"
                value={split.deliveryDate ?? ''}
                onChange={(event) =>
                  handlers.onDate(line, split, event.target.value)
                }
              />
            )}
            <SitsOnSelect
              split={split}
              origin={line}
              groupLines={groupLines}
              onChange={actions.sitsOn}
            />
            <Input
              aria-label="Remark"
              placeholder="Remark"
              value={split.remark}
              onChange={(event) =>
                handlers.onRemark(line, split, event.target.value)
              }
              onBlur={handlers.onFieldBlur}
            />
          </div>
        );
      })}
      <Button
        type="button"
        variant="outline"
        className="w-full"
        onClick={() => handlers.onAdd(line)}
      >
        <Plus aria-hidden />
        Add split
      </Button>
    </div>
  );
}
