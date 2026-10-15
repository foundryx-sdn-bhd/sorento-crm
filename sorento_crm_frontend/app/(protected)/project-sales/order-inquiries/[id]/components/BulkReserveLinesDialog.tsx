'use client';

import * as React from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SearchableSelect, type SearchableSelectOption } from '@/components/common/SearchableSelect';
import { bareLocationCode } from '../../../_shared/lib/orderInquiryReserve';
import {
  clampReservedQty,
  reserveCeiling,
  reserveLocationOptions,
  reserveReasonNeeded,
} from './ReserveLineForm';

/**
 * OI-BULK-RESERVE (AC-BR-10, owner hand test 16 Oct): Decide > Reserve... opens ONE popup with
 * the pen's own Edit reserve fields for every counted line - Location, Reserved (prefilled at
 * min(requested, available at the location), never above the request) and a Reason whenever
 * Reserved is short of the request. Same rules as `ReserveLineForm` (its exported helpers).
 * Stage hands every line back at once; nothing is posted here.
 *
 * The caller mounts it only once every line's pool options have loaded, so the prefill reads
 * real availability (the same rule `ReserveLineForm` follows, 6e.4 S2).
 */
export interface BulkReserveLine {
  rowId: string;
  itemCode: string | null;
  requested: number;
  locationOptions: SearchableSelectOption[];
  availableQtyByLocation: Record<string, number>;
  defaultLocationId: string | null;
}

export interface BulkReserveStage {
  rowId: string;
  /** Absent when no location is chosen: the server defaults it. */
  warehouseId: string | null;
  locationLabel: string | null;
  qty: number;
  reason: string | null;
}

interface Draft {
  location: string;
  qty: number;
  reason: string;
}

export function BulkReserveLinesDialog({
  lines,
  onOpenChange,
  onStage,
}: {
  lines: BulkReserveLine[];
  onOpenChange: (open: boolean) => void;
  onStage: (entries: BulkReserveStage[]) => void;
}) {
  const [drafts, setDrafts] = React.useState<Record<string, Draft>>(() =>
    Object.fromEntries(
      lines.map((line) => {
        const location = line.defaultLocationId ?? '';
        return [
          line.rowId,
          {
            location,
            qty: reserveCeiling(line.requested, line.availableQtyByLocation, location),
            reason: '',
          },
        ];
      }),
    ),
  );

  function update(rowId: string, patch: Partial<Draft>) {
    setDrafts((prev) => ({ ...prev, [rowId]: { ...prev[rowId], ...patch } }));
  }

  const noun = `${lines.length} line${lines.length === 1 ? '' : 's'}`;
  const canStage = lines.every((line) => {
    const draft = drafts[line.rowId];
    return !reserveReasonNeeded(draft.qty, line.requested) || draft.reason.trim().length > 0;
  });

  function handleStage() {
    onStage(
      lines.map((line) => {
        const draft = drafts[line.rowId];
        const option = line.locationOptions.find((candidate) => candidate.value === draft.location);
        return {
          rowId: line.rowId,
          warehouseId: draft.location || null,
          locationLabel: option ? bareLocationCode(option.label) : null,
          qty: draft.qty,
          reason: reserveReasonNeeded(draft.qty, line.requested) ? draft.reason.trim() : null,
        };
      }),
    );
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{`Reserve ${noun}`}</DialogTitle>
        </DialogHeader>
        <DialogBody className="max-h-[60vh] space-y-3 overflow-y-auto">
          {lines.map((line) => {
            const draft = drafts[line.rowId];
            const idBase = `bulk-reserve-${line.rowId}`;
            const showReason = reserveReasonNeeded(draft.qty, line.requested);
            return (
              <fieldset
                key={line.rowId}
                aria-labelledby={`${idBase}-legend`}
                className="space-y-2 rounded-lg border border-border p-3"
              >
                <div className="flex min-w-0 items-baseline justify-between gap-2">
                  <span id={`${idBase}-legend`} className="min-w-0 truncate text-sm font-medium">
                    {line.itemCode}
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                    {`Requested ${line.requested}`}
                  </span>
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <div className="min-w-0 space-y-1">
                    <Label htmlFor={`${idBase}-location`}>Location</Label>
                    <SearchableSelect
                      id={`${idBase}-location`}
                      value={draft.location}
                      onChange={(value) =>
                        update(line.rowId, {
                          location: value,
                          qty: reserveCeiling(line.requested, line.availableQtyByLocation, value),
                        })
                      }
                      options={reserveLocationOptions(line.locationOptions, line.availableQtyByLocation)}
                    />
                  </div>
                  <div className="min-w-0 space-y-1">
                    <Label htmlFor={`${idBase}-qty`}>Reserved</Label>
                    <Input
                      id={`${idBase}-qty`}
                      type="number"
                      min={0}
                      max={line.requested}
                      step="any"
                      value={draft.qty}
                      onChange={(event) =>
                        update(line.rowId, { qty: clampReservedQty(event.target.value, line.requested) })
                      }
                    />
                  </div>
                </div>
                {showReason ? (
                  <div className="space-y-1">
                    <Label htmlFor={`${idBase}-reason`}>Reason</Label>
                    <Input
                      id={`${idBase}-reason`}
                      value={draft.reason}
                      onChange={(event) => update(line.rowId, { reason: event.target.value })}
                    />
                  </div>
                ) : null}
              </fieldset>
            );
          })}
        </DialogBody>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" onClick={handleStage} disabled={!canStage}>
            {`Stage ${noun}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default BulkReserveLinesDialog;
