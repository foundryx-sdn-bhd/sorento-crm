'use client';

import * as React from 'react';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { NumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import { numberFieldError } from '@/lib/number-field';
import { fromMinor, toMinor } from '../../_shared/lib/supplyComposition';
import { availableForProjectOf } from '../../_shared/lib/poolShare';
import type {
  BoardCellLocation,
  BoardLocationWhere,
} from '../../_shared/types/fulfilmentPlanning.types';

const LOCATION_COL = 'w-[160px] min-w-[160px] max-w-[160px]';
const NUMBER_COL = 'w-[130px] min-w-[130px] max-w-[130px]';

/**
 * Adding a reserve location by hand (S3, R-G): "any location with free stock, the site
 * pool included, can be added to Reserve by hand; the server's on-hand check stays the
 * guard."
 *
 * The candidate list is the board cell's OWN stock rows (`BoardCellLocation[]`), already on
 * screen for this cell - no second fetch. R-A asks the site pool before own locations for
 * every product, so it is not filtered out here either; it is sorted first, the same order
 * the walk itself asks in.
 *
 * NOT a DataGrid, the same carve-out `BorrowAddDialog` documents: a small fixed table inside
 * a dialog, with no column config, sort, resize or pagination to earn.
 */
/** A quantity to add must be a number above zero. */
function qtyError(raw: string) {
  return numberFieldError(raw, { min: 0, minExclusive: true });
}

export function ReserveAddDialog({
  lineNo,
  itemCode,
  locations,
  openRemainder,
  onDone,
  onAdd,
}: {
  lineNo: number;
  itemCode?: string | null;
  /** Already filtered to what is left to add (free stock, not already on the Reserve list). */
  locations: BoardCellLocation[];
  /** What the line still has to cover, so the box opens on a sane quantity. */
  openRemainder: string;
  onDone: () => void;
  onAdd: (location: BoardCellLocation, qty: string) => void;
}) {
  const candidates = React.useMemo(
    () =>
      [...locations].sort((a, b) => {
        const rank = whereRank(a.where) - whereRank(b.where);
        if (rank !== 0) return rank;
        return freeMinorOf(b) - freeMinorOf(a);
      }),
    [locations],
  );

  const first = candidates[0];
  const [selectedKey, setSelectedKey] = React.useState(first ? locationKey(first) : '');
  const [qty, setQty] = React.useState(openingQty(first, openRemainder));

  const selected =
    candidates.find((location) => locationKey(location) === selectedKey) ?? first;
  const quantityMessage = qtyError(qty);
  const [showErrors, setShowErrors] = React.useState(false);

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-h-[92vh] w-full max-w-3xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>Add a reserve location for line {lineNo}</DialogTitle>
          <DialogDescription>{itemCode ?? 'This item'}</DialogDescription>
        </DialogHeader>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (!selected) return;
            if (quantityMessage) {
              setShowErrors(true);
              return;
            }
            onAdd(selected, qty.trim());
            onDone();
          }}
        >
          <DialogBody className="max-h-[60vh] space-y-4 overflow-y-auto">
            <fieldset className="space-y-2">
              <legend className="mb-1.5 text-sm font-medium">Location</legend>
              {candidates.length === 0 ? (
                // Rendered rather than hidden, per the CRUD standard: the caller already
                // decided whether to open this dialog, but a stale render (a location's
                // free stock moved to zero between the click and the paint) still deserves
                // an answer rather than an empty table.
                <div
                  data-testid="reserve-location-empty"
                  className="rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground"
                >
                  No location holds free stock of this item
                </div>
              ) : (
                <div
                  data-testid="reserve-location-table"
                  className="max-h-[40vh] w-full overflow-x-auto overflow-y-auto overscroll-x-contain rounded-lg border border-border"
                >
                  <table className="w-max border-separate border-spacing-0 text-xs">
                    <thead>
                      <tr>
                        <th scope="col" className={cn(LOCATION_COL, HEAD_CELL)}>
                          Location
                        </th>
                        <th scope="col" className={cn(NUMBER_COL, HEAD_CELL, 'text-end')}>
                          Free
                        </th>
                        <th scope="col" className={cn(NUMBER_COL, HEAD_CELL, 'text-end')}>
                          Available for project
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {candidates.map((location) => {
                        const key = locationKey(location);
                        // No UUID in the UI: a location with no name reads "Unknown" rather
                        // than the warehouse id it has no other label for.
                        const code = location.location ?? 'Unknown';
                        const chosen = key === selectedKey;
                        const free = location.qty_free_remaining ?? location.qty_free ?? null;
                        // S2 (fix round 5): outside a site pool, Available for Project IS
                        // Available (D2, captain 3 Sep) - the same accessor
                        // `CellStockTable`'s own column reads, so a group row here does not
                        // print "Not stated" over a figure the cell it was added from
                        // already states.
                        const availableForProject = availableForProjectOf(location);
                        return (
                          <tr key={key} data-testid={`reserve-location-${code}`}>
                            <td className={cn(LOCATION_COL, BODY_CELL)}>
                              <label
                                htmlFor={`reserve-${lineNo}-${key}`}
                                className="flex cursor-pointer items-start gap-2"
                              >
                                <input
                                  id={`reserve-${lineNo}-${key}`}
                                  type="radio"
                                  name={`reserve-source-${lineNo}`}
                                  className="mt-0.5"
                                  checked={chosen}
                                  onChange={() => {
                                    setSelectedKey(key);
                                    setQty(openingQty(location, openRemainder));
                                  }}
                                />
                                <span className="min-w-0">
                                  <span className="block truncate font-medium" title={code}>
                                    {code}
                                  </span>
                                  <span className="block truncate text-muted-foreground">
                                    {whereLabel(location.where)}
                                  </span>
                                </span>
                              </label>
                            </td>
                            <td className={cn(NUMBER_COL, BODY_CELL)}>
                              <span
                                data-testid={`reserve-cell-free-${code}`}
                                className="block truncate text-end tabular-nums"
                                title={free ?? 'Not stated'}
                              >
                                {free ?? 'Not stated'}
                              </span>
                            </td>
                            <td className={cn(NUMBER_COL, BODY_CELL)}>
                              <span
                                data-testid={`reserve-cell-available-for-project-${code}`}
                                className={cn(
                                  'block truncate text-end tabular-nums',
                                  availableForProject === null ||
                                    availableForProject === undefined
                                    ? 'text-muted-foreground'
                                    : undefined,
                                )}
                                title={availableForProject ?? 'Not stated'}
                              >
                                {availableForProject ?? 'Not stated'}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </fieldset>

            <div className="space-y-1.5">
              <Label htmlFor={`reserve-qty-${lineNo}`}>Quantity</Label>
              <NumberInput
                id={`reserve-qty-${lineNo}`}
                min="0"
                step="any"
                value={qty}
                error={showErrors ? quantityMessage : undefined}
                onChange={(event) => setQty(event.target.value)}
                className="h-9 w-40 tabular-nums"
              />
            </div>
          </DialogBody>

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" disabled={!selected}>
              Add the location
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

const HEAD_CELL =
  'sticky top-0 z-10 border-b border-e border-border bg-muted px-2 py-1.5 text-start align-bottom font-medium';
const BODY_CELL = 'border-b border-e border-border px-2 py-1.5 align-middle';

function freeMinorOf(location: BoardCellLocation): number {
  return toMinor(location.qty_free_remaining ?? location.qty_free ?? '0');
}

/** Site pool first (R-A: the pool is asked before own locations, for every product), then
 * own, group, other group - the same order the walk itself asks in. */
function whereRank(where: BoardLocationWhere | undefined): number {
  switch (where) {
    case 'site_pool':
      return 0;
    case 'own':
      return 1;
    case 'group':
      return 2;
    case 'other_group':
      return 3;
    default:
      return 1; // `where` defaults to `own` when the server omits it.
  }
}

function whereLabel(where: BoardLocationWhere | undefined): string {
  switch (where) {
    case 'site_pool':
      return 'Site pool';
    case 'group':
      return 'Group';
    case 'other_group':
      return 'Other group';
    case 'own':
    default:
      return 'Own';
  }
}

function locationKey(location: BoardCellLocation): string {
  return location.warehouse_id ?? location.location ?? '';
}

/** The line's own remainder, capped at what this location can give - the same opening rule
 * `BorrowAddDialog` uses for its own candidates. */
function openingQty(
  location: BoardCellLocation | undefined,
  openRemainder: string,
): string {
  if (!location) return '';
  const free = freeMinorOf(location);
  const remainder = toMinor(openRemainder);
  if (remainder <= 0) return fromMinor(free);
  return fromMinor(Math.min(remainder, free));
}
