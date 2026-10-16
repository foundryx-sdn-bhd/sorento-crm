'use client';

import * as React from 'react';
import { createPortal } from 'react-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { formatSplitDate } from '../lib/lineSplitModel';

/** Where a dropped (or keyed) card asks to go: the "how many" box's own question. */
export interface MoveTarget {
  lineId: string;
  splitId: string;
  /** The line it would sit on, or the New line row. */
  row: string;
  /** A date column, `new` (asks for the date) or `cancel`. */
  col: string;
  x: number;
  y: number;
  /** All of the split, the default (UAC A5). */
  max: number;
  /** "Cancel", "20/11/2026" or the date box, then "· line 30" / "· new line". */
  lineText: string | null;
}

/**
 * The "how many" box (UAC A5, A6): qty (default all, Enter moves), a date when the target is
 * New date. Holds its own typing, so a keystroke here repaints this box and nothing else.
 */
export function LineSplitMoveBox({
  target,
  onClose,
  onMove,
}: {
  target: MoveTarget | null;
  onClose: () => void;
  onMove: (target: MoveTarget, qty: number, date: string) => void;
}) {
  return (
    <Popover open={Boolean(target)} onOpenChange={(next) => !next && onClose()}>
      {target && typeof document !== 'undefined'
        ? createPortal(
            <PopoverTrigger asChild>
              <span
                aria-hidden
                className="pointer-events-none fixed size-px"
                style={{ left: target.x, top: target.y }}
              />
            </PopoverTrigger>,
            document.body,
          )
        : null}
      <PopoverContent align="start" className="w-64 p-3" aria-label="Move qty">
        {target ? (
          <MoveForm
            key={`${target.splitId}|${target.row}|${target.col}|${target.x}|${target.y}`}
            target={target}
            onClose={onClose}
            onMove={onMove}
          />
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

function MoveForm({
  target,
  onClose,
  onMove,
}: {
  target: MoveTarget;
  onClose: () => void;
  onMove: (target: MoveTarget, qty: number, date: string) => void;
}) {
  const [qty, setQty] = React.useState(String(target.max));
  const [date, setDate] = React.useState('');

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const when = target.col === 'new' ? date : target.col;
    if (!when) return;
    const amount = Number(qty.replace(/[^0-9]/g, '')) || 0;
    onClose();
    if (amount > 0) onMove(target, amount, when);
  };

  return (
    <form onSubmit={submit} className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-1.5 text-sm font-semibold">
        <span>Move to</span>
        {target.col === 'new' ? (
          <Input
            type="date"
            variant="sm"
            aria-label="New date"
            autoFocus
            className="w-36"
            value={date}
            onChange={(event) => setDate(event.target.value)}
          />
        ) : (
          <span>
            {target.col === 'cancel' ? 'Cancel' : formatSplitDate(target.col)}
          </span>
        )}
        {target.lineText ? <span>{`· ${target.lineText}`}</span> : null}
      </div>
      <div className="flex items-center gap-2">
        <Input
          variant="sm"
          inputMode="decimal"
          aria-label="Qty to move"
          autoFocus={target.col !== 'new'}
          onFocus={(event) => event.target.select()}
          className="w-24 text-right tabular-nums"
          value={qty}
          onChange={(event) => setQty(event.target.value)}
        />
        <span className="text-xs text-muted-foreground">{`of ${target.max}`}</span>
      </div>
      <div className="flex justify-end gap-1.5">
        <Button type="button" variant="outline" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" size="sm">
          Move
        </Button>
      </div>
    </form>
  );
}
