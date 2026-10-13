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
 * Decline, with a reason.
 *
 * Free text rather than a picker: "not my patch" and "already quoted by Ali under
 * another name" are both real answers and neither belongs in a fixed lookup. The reason
 * is mandatory because a decline with no reason is indistinguishable from silence, which
 * is the failure this handshake exists to remove.
 */
export function DeclineLeadDialog({
  leadCode,
  submitting,
  onDone,
  onConfirm,
}: {
  leadCode: string;
  submitting?: boolean;
  onDone: () => void;
  onConfirm: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const pending = busy || Boolean(submitting);

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent
        aria-describedby={undefined}
        className="max-h-[92vh] w-full max-w-md overflow-hidden"
      >
        <DialogHeader>
          <DialogTitle>Decline {leadCode}</DialogTitle>
        </DialogHeader>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (!reason.trim()) return;
            setBusy(true);
            try {
              await onConfirm(reason.trim());
              onDone();
            } finally {
              setBusy(false);
            }
          }}
        >
          <DialogBody className="max-h-[60vh] space-y-4 overflow-y-auto">
            <div className="space-y-1.5">
              <Label htmlFor="decline-lead-reason">
                Reason <span className="text-destructive">*</span>
              </Label>
              <Textarea
                id="decline-lead-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={3}
                placeholder="e.g. Outside my area, Johor team covers Nusajaya"
              />
            </div>
          </DialogBody>

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" variant="destructive" disabled={!reason.trim() || pending}>
              Decline
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
