'use client';

import * as React from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import {
  Popover,
  PopoverContent,
  PopoverPortal,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { cn } from '@/lib/utils';
import { useReserveRowOptions } from '../../_shared/hooks/useReserveRowOptions';
import { useSetSnapshotDecision } from '../hooks/useStockDebtSnapshots';
import type {
  SnapshotDecision,
  SnapshotRow,
} from '../types/stockDebtSnapshot.types';

export const DECISION_LABEL: Record<SnapshotDecision, string> = {
  reserve: 'Reserve',
  buy: 'Buy',
  ignore: 'Ignore',
};

const CHOICES: SnapshotDecision[] = ['reserve', 'buy', 'ignore'];

/** The Decision cell's badge text: Reserve names its pool. */
export function decisionText(
  row: Pick<SnapshotRow, 'decision' | 'decision_warehouse_code'>,
): string {
  if (!row.decision) return 'Decide';
  return row.decision === 'reserve' && row.decision_warehouse_code
    ? `${DECISION_LABEL.reserve} ${row.decision_warehouse_code}`
    : DECISION_LABEL[row.decision];
}

/** AC-F4: the Decision cell and its popover. "Decide" when empty, else a badge; the popover
 *  sets Reserve / Buy / Ignore (From for Reserve only), a Note, or clears the decision. */
export function SnapshotDecisionPopover({
  snapshotId,
  row,
}: {
  snapshotId: string;
  row: SnapshotRow;
}) {
  const [open, setOpen] = React.useState(false);
  const [decision, setDecision] = React.useState<SnapshotDecision | null>(
    row.decision,
  );
  const [from, setFrom] = React.useState(row.decision_warehouse_id ?? '');
  const [note, setNote] = React.useState(row.decision_note ?? '');
  const save = useSetSnapshotDecision(snapshotId);

  // Opening starts from what is stored, so a cancelled edit never lingers.
  React.useEffect(() => {
    if (!open) return;
    setDecision(row.decision);
    setFrom(row.decision_warehouse_id ?? '');
    setNote(row.decision_note ?? '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const resolved = useReserveRowOptions(
    open
      ? [{ key: row.row_id, productId: row.product_id, location: null }]
      : [],
  );
  const pools = resolved[row.row_id]?.options ?? [];

  const submit = (next: {
    decision: SnapshotDecision | null;
    warehouse_id: string | null;
    note: string | null;
  }) =>
    save.mutate(
      { rowId: row.row_id, ...next },
      { onSuccess: () => setOpen(false) },
    );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="max-w-full rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          {row.decision ? (
            <Badge
              variant="secondary"
              appearance="light"
              className="max-w-full truncate px-2 py-0.5 text-xs"
            >
              {decisionText(row)}
            </Badge>
          ) : (
            <span className="text-sm text-primary hover:underline">Decide</span>
          )}
        </button>
      </PopoverTrigger>
      {/* Portalled out of the grid cell: a DataGrid cell carries `truncate` (nowrap, hidden
          overflow), which an inline popover inherits and which pushed the Note box and its
          label past the popover's padding. */}
      <PopoverPortal>
        <PopoverContent
          className="w-80 space-y-4 whitespace-normal"
          align="start"
        >
          <div className="flex gap-2" role="group" aria-label="Decision">
            {CHOICES.map((choice) => (
              <Button
                key={choice}
                type="button"
                size="sm"
                variant={decision === choice ? 'primary' : 'outline'}
                aria-pressed={decision === choice}
                className={cn('flex-1')}
                onClick={() => {
                  setDecision(choice);
                  if (choice !== 'reserve') setFrom('');
                }}
              >
                {DECISION_LABEL[choice]}
              </Button>
            ))}
          </div>
          {decision === 'reserve' && (
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">From</Label>
              <SearchableSelect
                aria-label="From"
                value={from}
                onChange={setFrom}
                options={pools}
                placeholder="Any pool"
                clearable
              />
            </div>
          )}
          <div className="space-y-1.5">
            <Label
              htmlFor={`snapshot-note-${row.row_id}`}
              className="text-xs text-muted-foreground"
            >
              Note
            </Label>
            <Textarea
              id={`snapshot-note-${row.row_id}`}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={3}
              maxLength={2000}
            />
          </div>
          {row.decision && row.decided_at && (
            <p className="text-xs text-muted-foreground">
              {row.decided_by_name ?? 'Someone'},{' '}
              {formatDateTimeInMalaysia(row.decided_at)}
            </p>
          )}
          <div className="flex items-center justify-between gap-2">
            {row.decision ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={save.isPending}
                onClick={() =>
                  submit({ decision: null, warehouse_id: null, note: null })
                }
              >
                Clear decision
              </Button>
            ) : (
              <span />
            )}
            <Button
              type="button"
              size="sm"
              disabled={!decision || save.isPending}
              onClick={() =>
                submit({
                  decision,
                  warehouse_id: decision === 'reserve' ? from || null : null,
                  note: note.trim() || null,
                })
              }
            >
              Save
            </Button>
          </div>
        </PopoverContent>
      </PopoverPortal>
    </Popover>
  );
}
