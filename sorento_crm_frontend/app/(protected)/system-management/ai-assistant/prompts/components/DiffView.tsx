'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';
import { collapseUnchanged, diffHunks } from '../../lib/diffHunks';
import { diffStats, lineDiff } from '../../lib/lineDiff';

/**
 * Client-side unified line diff between two version templates (PLAN §9b Q2), with change
 * navigation (PLAN-prompt-dynamic-30sep R5b): "Change N of M", previous / next (buttons and
 * Alt+ArrowUp / Alt+ArrowDown), and "Changes only", which folds unchanged runs to 2 lines
 * of context with an expander.
 */
export function DiffView({
  a,
  b,
  aLabel,
  bLabel,
}: {
  a: string;
  b: string;
  aLabel: string;
  bLabel: string;
}) {
  const rows = useMemo(() => lineDiff(a, b), [a, b]);
  const stats = useMemo(() => diffStats(rows), [rows]);
  const hunks = useMemo(() => diffHunks(rows), [rows]);
  const [current, setCurrent] = useState(0);
  const [changesOnly, setChangesOnly] = useState(false);
  const [opened, setOpened] = useState<Set<number>>(new Set());
  const scrollRef = useRef<HTMLDivElement>(null);
  const total = hunks.length;
  // Typing in the draft re-diffs on every keystroke: keep the reader's place (clamped to the
  // changes that still exist) and never scroll for it (reviewer pass 2, should-fix 1).
  useEffect(() => {
    setCurrent((c) => (total === 0 ? 0 : Math.min(c, total - 1)));
  }, [total]);
  const hunk = total > 0 ? hunks[Math.min(current, total - 1)] : null;

  const [stepSeq, setStepSeq] = useState(0);
  const step = useCallback(
    (delta: number) => {
      if (total === 0) return;
      setCurrent((c) => (c + delta + total) % total);
      setStepSeq((n) => n + 1);
    },
    [total],
  );

  // Bring the current change into view on an explicit step only, by scrolling the diff pane
  // itself: `scrollIntoView` would scroll the page too.
  const hunkRef = useRef(hunk);
  hunkRef.current = hunk;
  useEffect(() => {
    const pane = scrollRef.current;
    const target = hunkRef.current;
    if (!pane || !target || stepSeq === 0) return;
    const el = pane.querySelector<HTMLElement>(`[data-row="${target.start}"]`);
    if (el) pane.scrollTop = Math.max(0, el.offsetTop - pane.clientHeight / 2);
  }, [stepSeq]);

  const items = useMemo(
    () => (changesOnly ? collapseUnchanged(rows, 2) : rows.map((_, index) => ({ kind: 'row' as const, index }))),
    [rows, changesOnly],
  );

  const renderRow = (index: number) => {
    const r = rows[index];
    const inCurrent = hunk != null && index >= hunk.start && index <= hunk.end;
    return (
      <div
        key={`r${index}`}
        data-row={index}
        data-testid={`diff-row-${r.op}`}
        data-current={inCurrent ? 'true' : undefined}
        className={cn(
          'flex gap-2 px-2',
          r.op === 'added' && 'bg-emerald-50 text-emerald-900',
          r.op === 'removed' && 'bg-red-50 text-red-900',
          inCurrent && 'outline outline-2 -outline-offset-2 outline-primary',
        )}
      >
        <span className="w-4 shrink-0 select-none text-muted-foreground">
          {r.op === 'added' ? '+' : r.op === 'removed' ? '−' : ' '}
        </span>
        <span className="whitespace-pre-wrap break-words">{r.text || ' '}</span>
      </div>
    );
  };

  return (
    <div
      className="space-y-2 outline-none"
      data-testid="diff-view"
      role="group"
      aria-label="Version diff"
      tabIndex={-1}
      onKeyDown={(e) => {
        if (!e.altKey) return;
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          step(1);
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          step(-1);
        }
      }}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-muted-foreground">
        <span>
          Comparing <span className="font-medium">{aLabel}</span> →{' '}
          <span className="font-medium">{bLabel}</span>
        </span>
        <span className="text-emerald-600" data-testid="diff-added-count">
          +{stats.added}
        </span>
        <span className="text-destructive" data-testid="diff-removed-count">
          −{stats.removed}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="min-w-24 font-medium tabular-nums" data-testid="diff-change-counter">
          {total === 0 ? 'No changes' : `Change ${current + 1} of ${total}`}
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => step(-1)}
          disabled={total === 0}
          data-testid="diff-prev"
          title="Previous change (Alt+ArrowUp)"
        >
          <ChevronUp className="size-4" /> Previous
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => step(1)}
          disabled={total === 0}
          data-testid="diff-next"
          title="Next change (Alt+ArrowDown)"
        >
          <ChevronDown className="size-4" /> Next
        </Button>
        <label className="flex cursor-pointer items-center gap-2">
          <Switch
            checked={changesOnly}
            onCheckedChange={(v) => setChangesOnly(v === true)}
            data-testid="diff-changes-only"
            aria-label="Changes only"
          />
          Changes only
        </label>
        <kbd className="hidden font-mono text-2xs text-muted-foreground sm:inline">Alt+↓ / Alt+↑</kbd>
      </div>
      <div ref={scrollRef} className="max-h-[480px] overflow-auto rounded-md border">
        <pre className="min-w-full text-xs leading-relaxed">
          {items.map((item) => {
            if (item.kind === 'row') return renderRow(item.index);
            if (opened.has(item.from)) {
              return Array.from({ length: item.count }, (_, k) => renderRow(item.from + k));
            }
            return (
              <button
                key={`g${item.from}`}
                type="button"
                data-testid="diff-gap"
                onClick={() => setOpened((s) => new Set(s).add(item.from))}
                className="block w-full bg-muted/40 px-2 py-0.5 text-left text-muted-foreground hover:bg-muted"
              >
                ... {item.count} unchanged lines
              </button>
            );
          })}
        </pre>
      </div>
    </div>
  );
}
