'use client';

import * as React from 'react';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useDisqualifyReasons } from '../../../_shared/hooks/useProjects';
import type { ProjectLead } from '../../../_shared/types/project.types';

/**
 * Disqualify with a reason from the configured lookup (AC-O6).
 *
 * A picker rather than a text box, because the reasons are the report: "not interested"
 * typed nine different ways is nine buckets and no insight. When an admin has
 * configured none, this says so and points at the fix instead of accepting free text
 * the server will refuse anyway.
 */
export function DisqualifyLeadDialog({
  lead,
  onDone,
  onConfirm,
}: {
  lead: ProjectLead;
  onDone: () => void;
  onConfirm: (reason: string) => Promise<void>;
}) {
  const reasons = useDisqualifyReasons();
  const [reason, setReason] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);

  const options = reasons.data ?? [];
  const unconfigured = !reasons.isLoading && options.length === 0;

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent
        aria-describedby={undefined}
        className="max-h-[92vh] w-full max-w-md overflow-hidden"
      >
        <DialogHeader>
          <DialogTitle>Disqualify {lead.lead_code}</DialogTitle>
        </DialogHeader>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            setSubmitting(true);
            try {
              await onConfirm(reason);
              onDone();
            } finally {
              setSubmitting(false);
            }
          }}
        >
          <DialogBody className="max-h-[60vh] space-y-4 overflow-y-auto">
            {unconfigured ? (
              <p className="rounded-md border border-dashed border-border px-3 py-2.5 text-sm text-muted-foreground">
                No disqualification reasons are configured yet. An administrator adds
                them to the <code>project_lead_disqualify_reason</code> lookup set, which
                is what the conversion report groups by.
              </p>
            ) : (
              <div className="space-y-1.5">
                <Label htmlFor="disqualify-reason">
                  Reason <span className="text-destructive">*</span>
                </Label>
                <SearchableSelect
                  id="disqualify-reason"
                  value={reason}
                  onChange={setReason}
                  options={options.map((option) => ({
                    value: option.value,
                    label: option.label,
                  }))}
                  placeholder="Select a reason"
                />
              </div>
            )}
          </DialogBody>

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" disabled={!reason || unconfigured || submitting}>
              Disqualify
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
