'use client';

import * as React from 'react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

/** The manager sends a pending quotation back. The reason is required. */
export function QuotationRejectDialog({
  open,
  onOpenChange,
  isDeciding,
  onReject,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  isDeciding: boolean;
  onReject: (reason: string) => Promise<void> | void;
}) {
  const [reason, setReason] = React.useState('');

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) setReason('');
      }}
    >
      <DialogContent className="max-h-[92vh] w-full max-w-md overflow-hidden">
        <DialogHeader>
          <DialogTitle>Send this back</DialogTitle>
          <DialogDescription>
            The salesperson sees your reason on the quotation and re-prices from there.
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            await onReject(reason.trim());
            onOpenChange(false);
            setReason('');
          }}
        >
          <DialogBody className="max-h-[65vh] space-y-2 overflow-y-auto">
            <Label htmlFor="quotation-reject-reason">
              Why <span className="text-destructive">*</span>
            </Label>
            <Textarea
              id="quotation-reject-reason"
              rows={4}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="e.g. the WC suite has to come back to at least RM 240"
            />
          </DialogBody>
          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!reason.trim() || isDeciding}>
              Send it back
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
