'use client';

import * as React from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useAssignableUsers } from '../../_shared/hooks/useLeadAcceptance';

/**
 * Hand a lead to a salesperson.
 *
 * Assignment is not ownership, so the copy says awaiting acceptance rather than
 * assigned: the lead stays nobody's until they accept it.
 */
export function AssignLeadDialog({
  leadCode,
  currentOwnerName,
  submitting,
  onDone,
  onConfirm,
}: {
  leadCode: string;
  currentOwnerName?: string | null;
  submitting?: boolean;
  onDone: () => void;
  onConfirm: (ownerUserId: string, note: string | null) => Promise<void>;
}) {
  const users = useAssignableUsers();
  const [ownerUserId, setOwnerUserId] = React.useState('');
  const [note, setNote] = React.useState('');
  const [busy, setBusy] = React.useState(false);

  const options = (users.data ?? []).map((user) => ({
    value: user.id,
    label: user.name || 'Unnamed user',
  }));
  const pending = busy || Boolean(submitting);

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-h-[92vh] w-full max-w-md overflow-hidden">
        <DialogHeader>
          <DialogTitle>Assign {leadCode}</DialogTitle>
          <DialogDescription>
            {currentOwnerName
              ? `Currently with ${currentOwnerName}.`
              : 'Nobody holds this lead yet.'}
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (!ownerUserId) return;
            setBusy(true);
            try {
              await onConfirm(ownerUserId, note.trim() || null);
              onDone();
            } finally {
              setBusy(false);
            }
          }}
        >
          <DialogBody className="max-h-[60vh] space-y-4 overflow-y-auto">
            <div className="space-y-1.5">
              <Label htmlFor="assign-lead-owner">
                Salesperson <span className="text-destructive">*</span>
              </Label>
              <SearchableSelect
                id="assign-lead-owner"
                value={ownerUserId}
                onChange={setOwnerUserId}
                loadError={users.error}
                onRetry={() => void users.refetch()}
                options={options}
                disabled={users.isLoading}
                placeholder={users.isLoading ? 'Loading people' : 'Search people'}
                emptyMessage="No match"
              />
            </div>
            {/* Carried in the assignee's notification only. It is NOT stored on the
                lead, so the copy must not promise it will be there later. */}
            <div className="space-y-1.5">
              <Label htmlFor="assign-lead-note">Message to them</Label>
              <Textarea
                id="assign-lead-note"
                value={note}
                onChange={(event) => setNote(event.target.value)}
                rows={3}
                placeholder="Goes out with their notification"
              />
            </div>
          </DialogBody>

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" disabled={!ownerUserId || pending}>
              Assign
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
