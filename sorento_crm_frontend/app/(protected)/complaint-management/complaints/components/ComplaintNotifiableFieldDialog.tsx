'use client';

import { useEffect, useState } from 'react';
import { Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';

export type ComplaintNotifiableFieldKind = 'root_cause' | 'resolution';

const UNSET = '__unset__';

const COPY: Record<
  ComplaintNotifiableFieldKind,
  { title: string; label: string; placeholder: string }
> = {
  root_cause: {
    title: 'Edit root cause',
    label: 'Root cause',
    placeholder: 'Select root cause',
  },
  resolution: {
    title: 'Edit resolution',
    label: 'Resolution',
    placeholder: 'Select resolution',
  },
};

interface ComplaintNotifiableFieldDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  kind: ComplaintNotifiableFieldKind;
  /** Current FK value on the complaint (null when unset). */
  value: string | null | undefined;
  options: { id: string; name: string }[];
  loadError?: unknown;
  onRetry?: () => void;
  /** Save the picked id on the complaint. */
  onUpdate: (id: string | null) => Promise<unknown>;
  /** Save then send the Respond.io update message to the contact. */
  onUpdateAndReply: (id: string) => Promise<unknown>;
  /** False when the complaint has no linked Respond.io conversation. */
  canReply: boolean;
  isPending?: boolean;
}

/**
 * Edit-then-notify dialog for the complaint root cause / resolution, mirroring
 * the "Edit technical team response" flow: Cancel / Update / Update & Reply.
 * Update saves the record only; Update & Reply saves then tells the contact
 * what the root cause / resolution is.
 */
export default function ComplaintNotifiableFieldDialog({
  open,
  onOpenChange,
  kind,
  value,
  options,
  loadError,
  onRetry,
  onUpdate,
  onUpdateAndReply,
  canReply,
  isPending = false,
}: ComplaintNotifiableFieldDialogProps) {
  const copy = COPY[kind];
  const [selected, setSelected] = useState<string>(value ?? UNSET);
  const [submitting, setSubmitting] = useState<null | 'update' | 'reply'>(null);

  // Re-seed from the record every time the dialog opens so a cancelled edit
  // never leaks into the next one.
  useEffect(() => {
    if (open) {
      setSelected(value ?? UNSET);
      setSubmitting(null);
    }
  }, [open, value]);

  const busy = isPending || submitting !== null;
  const pickedId = selected === UNSET ? null : selected;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{copy.title}</DialogTitle>
          <DialogDescription>
            Update saves the record only. Update &amp; Reply saves your pick and tells the
            contact what the {copy.label.toLowerCase()} is.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor={`complaint-${kind}-select`}>{copy.label}</Label>
          <SearchableSelect
            id={`complaint-${kind}-select`}
            value={selected}
            onChange={(v) => setSelected(v)}
            loadError={loadError}
            onRetry={onRetry}
            options={[
              { value: UNSET, label: ' - None - ' },
              ...options.map((opt) => ({ value: opt.id, label: opt.name })),
            ]}
            placeholder={copy.placeholder}
            disabled={busy}
          />
        </div>
        <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="outline"
            disabled={busy}
            onClick={async () => {
              setSubmitting('update');
              try {
                await onUpdate(pickedId);
                onOpenChange(false);
              } catch {
                // toast from mutation
              } finally {
                setSubmitting(null);
              }
            }}
          >
            {submitting === 'update' ? 'Updating…' : 'Update'}
          </Button>
          {canReply && (
            <Button
              variant="primary"
              // Nothing to tell the contact when the field is cleared.
              disabled={busy || !pickedId}
              onClick={async () => {
                if (!pickedId) return;
                setSubmitting('reply');
                try {
                  await onUpdateAndReply(pickedId);
                  onOpenChange(false);
                } catch {
                  // toast from mutation
                } finally {
                  setSubmitting(null);
                }
              }}
            >
              <Send className="size-4 mr-1" />
              {submitting === 'reply' ? 'Sending…' : 'Update & Reply'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
