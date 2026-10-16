'use client';

import { useState } from 'react';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Info,
  Layers,
  PackageOpen,
  Tag,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';
import { EM_DASH, fmtDoc, fmtInt } from '../../lib/format';
import { splitDispositionRows, type M8DispositionAction, type M8DispositionRow } from '../lib/planRow';
import { distinctName } from '@/lib/product-display';

/**
 * SCM M8 - read-only Stock allocation list (M8-C12 / M8-F18; internal key stays
 * `disposition`). The list is dominated by "Hold" rows (overstock just above the
 * days-cover ceiling) that need no action, so the main table shows ONLY actionable
 * rows - Discontinue (dead stock) and Promote/reallocate. "Hold" rows are FYI: they
 * are demoted (not dropped) into a muted, collapsed "No action needed (N)" section
 * below and excluded from the headline Stock-allocation count (done in the tile).
 * These lines are NOT budgeted, so this is a plain read-only table (no drag, no
 * inline decisions). The inter-warehouse transfer suggestion is a SEPARATE feature
 * (M9, to grill), not built here. Phase 2 serves the rows from the same snapshot as
 * the buy rows.
 */

const ACTION_META: Record<
  M8DispositionAction,
  { label: string; variant: 'destructive' | 'info' | 'warning'; icon: typeof Tag }
> = {
  discontinue: { label: 'Discontinue', variant: 'destructive', icon: AlertTriangle },
  promo: { label: 'Promote', variant: 'info', icon: Tag },
  hold: { label: 'Hold', variant: 'warning', icon: Layers },
};

function ActionBadge({ action }: { action: M8DispositionAction }) {
  const meta = ACTION_META[action];
  return (
    <Badge variant={meta.variant} appearance="light" size="md">
      <meta.icon className="size-3" />
      {meta.label}
    </Badge>
  );
}

/** Plain-language meaning of each disposition action (the "what and why" the detail
 *  dialog explains, so a reviewer understands Promote vs Discontinue vs Hold). */
const ACTION_EXPLAINER: Record<M8DispositionAction, { what: string; why: string }> = {
  promo: {
    what: 'Promote / reallocate this stock.',
    why: 'This location holds far more of this product than its demand needs, so cover runs well beyond the healthy ceiling. Move it to a location that needs it, or push it through a promotion, to sell the excess down before it ages, rather than buying more elsewhere.',
  },
  discontinue: {
    what: 'Discontinue - stop reordering and clear it.',
    why: 'This product has had little to no movement for a long time, so the standing quantity is effectively dead stock. Stop replenishing it and work the remaining units down (clearance / write-off) instead of tying up cash and space.',
  },
  hold: {
    what: 'Hold - no action needed.',
    why: 'Cover sits just above the healthy ceiling. It is worth watching but does not warrant reallocating or discontinuing yet, so it is monitored only.',
  },
};

/** Detail dialog for a Stock-allocation line (M8-F / issue A) - mirrors the Buy detail:
 *  what the action means, and the frozen numbers behind it (excess qty, cover, warehouse,
 *  engine reason). Read-only; these lines are not budgeted and carry no decision. */
function DispositionDetailDialog({
  row,
  onClose,
}: {
  row: M8DispositionRow | null;
  onClose: () => void;
}) {
  const meta = row ? ACTION_META[row.action] : null;
  const explain = row ? ACTION_EXPLAINER[row.action] : null;
  return (
    <Dialog open={!!row} onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>How this recommendation was reached</DialogTitle>
        </DialogHeader>
        {row && meta && explain ? (
          <DialogBody className="space-y-4">
            <div className="flex items-center gap-2">
              <ActionBadge action={row.action} />
              <span className="font-semibold">{row.sku}</span>
              {distinctName(row.sku, row.product_name) ? (
                <>
                  <span className="text-sm text-muted-foreground">{EM_DASH}</span>
                  <span className="truncate text-sm text-muted-foreground" title={row.product_name}>
                    {row.product_name}
                  </span>
                </>
              ) : null}
            </div>

            <div className="rounded-lg border bg-muted/30 p-3">
              <div className="text-sm font-semibold">{explain.what}</div>
              <p className="mt-1 text-sm leading-relaxed text-muted-foreground">{explain.why}</p>
            </div>

            <div className="grid grid-cols-2 gap-3 text-sm">
              <Fact label="Excess quantity" value={fmtInt(row.qty)} hint="units above target at this location" />
              <Fact
                label="Runway"
                value={row.days_cover === null ? EM_DASH : fmtDoc(row.days_cover, false)}
                hint="how long the stock lasts at current demand"
              />
              <Fact label="Warehouse" value={row.warehouse_code || EM_DASH} hint={row.warehouse_name} />
              <Fact label="Action" value={meta.label} hint="flagged by the daily engine" />
            </div>

            {row.reason ? (
              <div className="rounded-lg border bg-card p-3">
                <div className="text-2xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Engine reason
                </div>
                <p className="mt-1 text-sm text-muted-foreground">{row.reason}</p>
              </div>
            ) : null}
          </DialogBody>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

/** One label/value fact in the disposition detail grid. */
function Fact({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-2xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="truncate font-medium tabular-nums" title={value}>
        {value}
      </div>
      {hint ? (
        <div className="truncate text-2xs text-muted-foreground" title={hint}>
          {hint}
        </div>
      ) : null}
    </div>
  );
}

/** The allocation table body - reused for the actionable list and the FYI hold list.
 *  Rows are clickable to open the detail dialog (M8-F / issue A). */
function DispositionTable({
  rows,
  muted = false,
  onSelect,
}: {
  rows: M8DispositionRow[];
  muted?: boolean;
  onSelect: (row: M8DispositionRow) => void;
}) {
  return (
    <ScrollArea>
      <table className="w-full min-w-[720px] text-sm">
        <thead className="bg-muted/40">
          <tr className="text-2xs text-muted-foreground">
            <th className="px-3 py-2 text-left font-medium">SKU</th>
            <th className="px-3 py-2 text-left font-medium">Action</th>
            <th className="px-3 py-2 text-right font-medium">Qty</th>
            <th className="px-3 py-2 text-left font-medium">Warehouse</th>
            <th className="px-3 py-2 text-right font-medium">Runway</th>
            <th className="px-3 py-2 text-left font-medium">Reason</th>
          </tr>
        </thead>
        <tbody className={cn(muted && 'text-muted-foreground')}>
          {rows.map((r) => (
            <tr
              key={r.id}
              onClick={() => onSelect(r)}
              title={`View why ${r.sku} is flagged`}
              className="cursor-pointer border-t border-border/60 align-top transition-colors hover:bg-muted/30"
            >
              <td className="px-3 py-2">
                <div className={cn('font-medium', muted && 'font-normal')}>{r.sku}</div>
                {distinctName(r.sku, r.product_name) ? (
                  <div className="truncate text-xs text-muted-foreground" title={r.product_name}>
                    {r.product_name}
                  </div>
                ) : null}
              </td>
              <td className="px-3 py-2">
                <ActionBadge action={r.action} />
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtInt(r.qty)}</td>
              <td className="px-3 py-2">
                <span title={r.warehouse_name}>
                  <span className={cn('font-medium', muted && 'font-normal')}>{r.warehouse_code}</span>{' '}
                  <span className="text-xs text-muted-foreground">{r.warehouse_name}</span>
                </span>
              </td>
              <td className="px-3 py-2 text-right tabular-nums">
                {r.days_cover === null ? EM_DASH : fmtDoc(r.days_cover, false)}
              </td>
              <td className="px-3 py-2 text-muted-foreground">
                <span className="line-clamp-2" title={r.reason}>
                  {r.reason}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ScrollBar orientation="horizontal" />
    </ScrollArea>
  );
}

export function DispositionResultsGrid({ rows }: { rows: M8DispositionRow[] }) {
  const { actionable, hold } = splitDispositionRows(rows);
  const [showHold, setShowHold] = useState(false);
  const [detailRow, setDetailRow] = useState<M8DispositionRow | null>(null);

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2 rounded-lg border border-border bg-muted/30 p-3 text-sm text-muted-foreground">
        <Info className="mt-0.5 size-4 shrink-0" />
        <span>
          Read-only. Lines the engine flags to act on now - discontinue dead stock or promote
          overstock. Click a line to see why it is flagged. Hold lines (just above the cover
          ceiling) need no action and sit below. Not budgeted; use the Buy view for the cash plan.
        </span>
      </div>

      <DispositionDetailDialog row={detailRow} onClose={() => setDetailRow(null)} />

      <Card className="overflow-hidden p-0">
        {actionable.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-12 text-center">
            <span className="flex size-11 items-center justify-center rounded-full bg-muted">
              <PackageOpen className="size-5 text-muted-foreground" />
            </span>
            <div className="text-sm font-medium">No stock-allocation actions needed today</div>
            {hold.length > 0 ? (
              <div className="text-xs text-muted-foreground">
                {fmtInt(hold.length)} hold {hold.length === 1 ? 'line' : 'lines'} are being monitored
                below.
              </div>
            ) : null}
          </div>
        ) : (
          <DispositionTable rows={actionable} onSelect={setDetailRow} />
        )}
      </Card>

      {hold.length > 0 ? (
        <div className="space-y-2">
          <button
            type="button"
            onClick={() => setShowHold((v) => !v)}
            aria-expanded={showHold}
            className="flex w-full items-center gap-2 rounded-lg border border-dashed border-border bg-muted/20 px-3 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-muted/40"
          >
            {showHold ? (
              <ChevronDown className="size-4 shrink-0" />
            ) : (
              <ChevronRight className="size-4 shrink-0" />
            )}
            <span className="font-medium">No action needed ({fmtInt(hold.length)})</span>
            <span className="text-xs">Overstock above the cover ceiling - monitoring only</span>
          </button>
          {showHold ? (
            <Card className="overflow-hidden p-0 opacity-80">
              <DispositionTable rows={hold} muted onSelect={setDetailRow} />
            </Card>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
