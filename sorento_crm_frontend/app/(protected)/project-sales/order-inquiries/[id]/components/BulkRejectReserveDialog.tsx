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
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';

/**
 * OI-BULK-RESERVE (AC-BR-04/09): "Reject N lines", one required Reason. Rejecting is a
 * Reserve 0 + reason on each line, the same answer the pen's Edit reserve gives at 0
 * (`ReserveLineForm.tsx` 125-126). Used by the strip's Decide > Reject... and by the
 * line's own x (N = 1).
 */
export function BulkRejectReserveDialog({
  open,
  count,
  onOpenChange,
  onConfirm,
}: {
  open: boolean;
  count: number;
  onOpenChange: (open: boolean) => void;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = React.useState('');
  const noun = `${count} line${count === 1 ? '' : 's'}`;
  const trimmed = reason.trim();

  // A fresh reason every time the dialog opens; the previous lines' reason never carries.
  React.useEffect(() => {
    if (open) setReason('');
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{`Reject ${noun}`}</DialogTitle>
        </DialogHeader>
        <DialogBody className="space-y-1.5">
          <Label htmlFor="bulk-reject-reserve-reason">
            Reason<span className="text-destructive"> *</span>
          </Label>
          <Textarea
            id="bulk-reject-reserve-reason"
            rows={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </DialogBody>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" disabled={!trimmed} onClick={() => onConfirm(trimmed)}>
            {`Reject ${noun}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default BulkRejectReserveDialog;
