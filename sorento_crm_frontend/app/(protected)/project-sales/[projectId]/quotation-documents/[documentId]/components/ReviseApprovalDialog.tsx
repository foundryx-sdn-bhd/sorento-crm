'use client';

import * as React from 'react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

/**
 * Editing a quotation that is pending or approved. It reopens as a draft, and the reason goes
 * to the project's feed (and to the approvers when it was still waiting on them).
 */
export function ReviseApprovalDialog({
  open,
  onOpenChange,
  isRevising,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  isRevising: boolean;
  onConfirm: (reason: string) => Promise<void>;
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
      <DialogContent className="max-h-[92dvh] w-full max-w-md overflow-hidden">
        <DialogHeader>
          <DialogTitle>Revise this quotation</DialogTitle>
        </DialogHeader>
        <DialogBody className="max-h-[65dvh] space-y-2 overflow-y-auto">
          <Label htmlFor="quotation-revise-reason">What changed, and why?</Label>
          <Textarea
            id="quotation-revise-reason"
            rows={4}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </DialogBody>
        <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!reason.trim() || isRevising}
            onClick={() => void onConfirm(reason.trim())}
          >
            Revise
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
