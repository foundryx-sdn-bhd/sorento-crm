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

/**
 * `PLAN-oi-request-cs-reserve.md` section 6e.2 (round 4, 24 Sep), AC-RS-85 (reserve
 * mode) / AC-RS-86 (amend mode). A small dialog whose ONLY job is to hand back a
 * STAGED decision through `onStage` - it never posts anything itself (the Lines grid's
 * own header `Reserve (N)` CTA does the one batched commit, `OrderInquiryDetail.tsx`
 * owns the staged map). Replaces `ReserveRowDialog`'s per-row `ReserveRowSection`.
 *
 * Reserve mode: Location is a `SearchableSelect` (options = the row's own pools with
 * availability), Reserved defaults to `min(requested, available at the chosen pool)`
 * and recomputes to that ceiling every time Location changes. Amend mode: Location is
 * locked read-only text (the row's own answered location never changes on an amend),
 * Reserved starts at the row's own net reserved.
 *
 * A reason is required whenever Reserved is short of the request (`qty < requested`), in
 * both modes (6e.4, S1): availability never waives it - "only 20 at DC1" is exactly the
 * explanation the requester reads in the mail.
 *
 * The caller mounts this only once the row's pool options have loaded (6e.4, S2), so
 * the reserve-mode prefill is read off real availability, never an empty map.
 */
/** OI-BULK-RESERVE (AC-BR-10): the form's reserve rules, shared with Decide > Reserve...
 * (`BulkReserveLinesDialog.tsx`) so one line and many lines can never disagree. */

/** What Reserved auto-sets to for a location: the request, capped at what that pool has
 * (0 when it reports nothing). No location (the server defaults it) is the request itself. */
export function reserveCeiling(
  requested: number,
  availableQtyByLocation: Record<string, number>,
  locationId: string,
): number {
  if (!locationId) return requested;
  const available = availableQtyByLocation[locationId];
  return Math.max(0, available != null ? Math.min(requested, available) : 0);
}

/** A typed Reserved, kept within 0..max. */
export function clampReservedQty(raw: string, max: number): number {
  const value = Number(raw);
  return Math.min(Math.max(Number.isFinite(value) ? value : 0, 0), max);
}

/** AC-RS-85 / 6e.4: a reason is required whenever Reserved is short of the request. */
export function reserveReasonNeeded(qty: number, requested: number): boolean {
  return qty < requested;
}

/** Location options with the availability as the description line, never in the label. */
export function reserveLocationOptions(
  locationOptions: SearchableSelectOption[],
  availableQtyByLocation: Record<string, number>,
): SearchableSelectOption[] {
  return locationOptions.map((option) => {
    const available = availableQtyByLocation[option.value];
    return {
      ...option,
      label: bareLocationCode(option.label),
      description: available != null ? `available ${available}` : option.description,
    };
  });
}

export interface ReserveLineFormStagePayload {
  /** Absent when no location is chosen: the server defaults it (6e.4, S8). */
  warehouse_id?: string;
  qty_reserved: number;
  reason: string | null;
}

export interface ReserveLineFormAmendPayload {
  qty_reserved: number;
  reason: string | null;
}

export interface ReserveLineFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  itemCode: string | null;
  mode: 'reserve' | 'amend';
  /** The request row's own `qty_requested` - the input's own ceiling either way. */
  requestedQty: string;
  /** Reserve mode only: the row's own pools, `available_qty` per warehouse id, and
   * which one Reserved defaults to. */
  locationOptions?: SearchableSelectOption[];
  availableQtyByLocation?: Record<string, number>;
  defaultLocationId?: string | null;
  /** Amend mode only: the answered location's own code (read-only text, never a
   * `SearchableSelect` - the location itself is locked on an amend), and the row's
   * own net reserved qty to prefill Reserved with. */
  lockedLocationLabel?: string;
  initialQty?: string;
  /** Amend mode: the input's ceiling - the line's net plus what it still has open
   * (6e.4, "Amend edits the LINE's net"). Defaults to `requestedQty`. */
  maxQty?: string;
  /** Amend mode: how many answered requests the line's `requestedQty` sums; above 1
   * the form states it. */
  answeredRequestCount?: number;
  onStage: (payload: ReserveLineFormStagePayload | ReserveLineFormAmendPayload) => void;
}

export function ReserveLineForm({
  open,
  onOpenChange,
  itemCode,
  mode,
  requestedQty,
  locationOptions = [],
  availableQtyByLocation = {},
  defaultLocationId = null,
  lockedLocationLabel,
  initialQty,
  maxQty,
  answeredRequestCount = 1,
  onStage,
}: ReserveLineFormProps) {
  const requested = Number(requestedQty || '0');
  const max = maxQty != null ? Number(maxQty) : requested;

  // What Reserved auto-sets to, on mount and on every Location switch: the request,
  // capped at what the chosen pool actually has (0 when that pool reports nothing). No
  // location at all (the server defaults it) prefills the request itself.
  const ceilingFor = React.useCallback(
    (locationId: string): number =>
      mode === 'amend' ? requested : reserveCeiling(requested, availableQtyByLocation, locationId),
    [mode, requested, availableQtyByLocation],
  );

  const [location, setLocation] = React.useState(() => defaultLocationId ?? '');
  const [qty, setQty] = React.useState(() =>
    mode === 'amend'
      ? Math.max(0, Number(initialQty || '0'))
      : Math.max(0, ceilingFor(defaultLocationId ?? '')),
  );
  const [reason, setReason] = React.useState('');

  function handleLocationChange(value: string) {
    setLocation(value);
    setQty(Math.max(0, ceilingFor(value)));
  }

  function handleQtyChange(event: React.ChangeEvent<HTMLInputElement>) {
    setQty(clampReservedQty(event.target.value, max));
  }

  // AC-RS-85 / 6e.4: Reason appears, and is required, whenever Reserved < Requested.
  const showReason = reserveReasonNeeded(qty, requested);
  const canStage = !showReason || reason.trim().length > 0;

  // AC-RS-85: "options = the row's pools with availability" - the availability reads
  // as its own DESCRIPTION line (the shared `SearchableSelect` primitive's own second
  // row), never baked into the primary label - `useReserveRowOptions`' own combined
  // string ("BRW  available 107") would otherwise print in full as the SELECTED
  // value's own trigger text (no UUID in the UI is the letter of the rule; a
  // duplicated "available N" clause on the CLOSED control is the same spirit of it).
  const displayLocationOptions = React.useMemo(
    () => reserveLocationOptions(locationOptions, availableQtyByLocation),
    [locationOptions, availableQtyByLocation],
  );

  function handleStage() {
    const cleanReason = showReason ? reason.trim() || null : null;
    if (mode === 'reserve') {
      onStage(
        location
          ? { warehouse_id: location, qty_reserved: qty, reason: cleanReason }
          : { qty_reserved: qty, reason: cleanReason },
      );
    } else {
      onStage({ qty_reserved: qty, reason: cleanReason });
    }
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{itemCode}</DialogTitle>
        </DialogHeader>
        <DialogBody className="space-y-3">
          {mode === 'reserve' ? (
            <div className="space-y-1">
              <Label htmlFor="reserve-line-location">Location</Label>
              <SearchableSelect
                id="reserve-line-location"
                value={location}
                onChange={handleLocationChange}
                options={displayLocationOptions}
              />
            </div>
          ) : (
            <div className="space-y-1">
              <Label>Location</Label>
              <div className="text-sm">{lockedLocationLabel || '-'}</div>
              {answeredRequestCount > 1 ? (
                <div className="text-xs text-muted-foreground">
                  Requested {requested} across {answeredRequestCount} requests
                </div>
              ) : null}
            </div>
          )}
          <div className="space-y-1">
            <Label htmlFor="reserve-line-qty">Reserved</Label>
            <Input
              id="reserve-line-qty"
              type="number"
              min={0}
              max={max}
              step="any"
              value={qty}
              onChange={handleQtyChange}
            />
          </div>
          {showReason ? (
            <div className="space-y-1">
              <Label htmlFor="reserve-line-reason">Reason</Label>
              <Input
                id="reserve-line-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </div>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" onClick={handleStage} disabled={!canStage}>
            Stage
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default ReserveLineForm;
