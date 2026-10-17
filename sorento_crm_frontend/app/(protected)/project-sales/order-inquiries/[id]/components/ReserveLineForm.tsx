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
import { NumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { SearchableSelect, type SearchableSelectOption } from '@/components/common/SearchableSelect';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { bareLocationCode } from '../../../_shared/lib/orderInquiryReserve';

/**
 * `PLAN-oi-request-cs-reserve.md` section 6e.2 (round 4, 24 Sep), AC-RS-85 (reserve
 * mode) / AC-RS-86 (amend mode). A small dialog whose ONLY job is to hand back a
 * STAGED decision through `onStage` - it never posts anything itself (the Lines grid's
 * own header `Reserve (N)` CTA does the one batched commit, `OrderInquiryDetail.tsx`
 * owns the staged map). Replaces `ReserveRowDialog`'s per-row `ReserveRowSection`.
 *
 * Reserve mode: Location is a `SearchableSelect` (options = the row's own pools, their
 * availability shown as information), Reserved defaults to the request. Owner, 16 Oct: never
 * capped or recomputed by CRM stock - AutoCount may already hold the reservation while CRM
 * stock lags - so any qty up to the request is allowed whatever the pool shows. Amend mode:
 * Location is
 * locked read-only text (the row's own answered location never changes on an amend),
 * Reserved starts at the row's own net reserved.
 *
 * A reason is required whenever Reserved is short of the request (`qty < requested`), in
 * both modes (6e.4, S1): availability never waives it - "only 20 at DC1" is exactly the
 * explanation the requester reads in the mail.
 *
 * The caller mounts this only once the row's pool options have loaded (6e.4, S2), so the
 * default location and the option list are real, never empty.
 *
 * Its button reads "Save" (owner 16 Oct: no "Stage"), Fulfilment Planning's own word for a
 * decision kept until Confirm (`BoardDecideControl.tsx` "Save N lines").
 */
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

  const [location, setLocation] = React.useState(() => defaultLocationId ?? '');
  // What the operator typed, kept as typed; the final value is checked on Save.
  const [qtyText, setQtyText] = React.useState(() =>
    String(mode === 'amend' ? Math.max(0, Number(initialQty || '0')) : Math.max(0, requested)),
  );
  const [reason, setReason] = React.useState('');
  const [showErrors, setShowErrors] = React.useState(false);

  const qtyError = numberFieldError(qtyText, { min: 0, max });
  const qty = parseNumberDraft(qtyText);

  // AC-RS-85 / 6e.4: Reason appears, and is required, whenever Reserved < Requested.
  const showReason = (qty ?? requested) < requested;
  const canStage = !showReason || reason.trim().length > 0;

  // AC-RS-85: "options = the row's pools with availability" - the availability reads
  // as its own DESCRIPTION line (the shared `SearchableSelect` primitive's own second
  // row), never baked into the primary label - `useReserveRowOptions`' own combined
  // string ("BRW  available 107") would otherwise print in full as the SELECTED
  // value's own trigger text (no UUID in the UI is the letter of the rule; a
  // duplicated "available N" clause on the CLOSED control is the same spirit of it).
  const displayLocationOptions = React.useMemo(
    () =>
      locationOptions.map((option) => {
        const available = availableQtyByLocation[option.value];
        return {
          ...option,
          label: bareLocationCode(option.label),
          description: available != null ? `available ${available}` : option.description,
        };
      }),
    [locationOptions, availableQtyByLocation],
  );

  function handleStage() {
    if (qtyError || qty === null) {
      setShowErrors(true);
      return;
    }
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
                onChange={setLocation}
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
            <NumberInput
              id="reserve-line-qty"
              min={0}
              max={max}
              step="any"
              value={qtyText}
              error={showErrors ? qtyError : undefined}
              onChange={(event) => setQtyText(event.target.value)}
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
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default ReserveLineForm;
