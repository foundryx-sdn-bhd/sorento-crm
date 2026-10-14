'use client';

/**
 * What an auto-update changed under a tag, after the fact (r10 S8).
 *
 * In `designing` / `changes_requested` the pin moves by itself the moment
 * master data changes, so the question is no longer "do you want this" but
 * "you have seen it": Dismiss clears the indicator and keeps the new data,
 * Roll back restores the "Before product update" version the auto-update
 * wrote first. Neither is styled as the safe one - a price that moved is as
 * often right as wrong.
 */

import { useState } from 'react';
import { Check, Undo2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { LineDataChange } from '@/lib/dealer-kit/product-data-changes';
import { ProductDataChangeRows } from './ProductDataReviewDialog';
import { distinctName } from '@/lib/product-display';

interface ProductDataUpdatedDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The tag's code and "1a" label, never an id. */
  code: string;
  tagLabel: string;
  name?: string;
  changes: LineDataChange[];
  /** The before-version to restore; null hides Roll back (nothing to go back to). */
  version: number | null;
  onDismiss: () => Promise<void>;
  onRollBack: (version: number) => Promise<void>;
}

export default function ProductDataUpdatedDialog({
  open,
  onOpenChange,
  code,
  tagLabel,
  name,
  changes,
  version,
  onDismiss,
  onRollBack,
}: ProductDataUpdatedDialogProps) {
  const [busy, setBusy] = useState<'dismiss' | 'rollback' | null>(null);

  const run = async (action: 'dismiss' | 'rollback') => {
    setBusy(action);
    try {
      if (action === 'dismiss') await onDismiss();
      else if (version != null) await onRollBack(version);
      onOpenChange(false);
    } catch {
      // The action was refused: the dialog stays open with both buttons live.
      // The caller owns the message.
    } finally {
      setBusy(null);
    }
  };

  if (!open) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[95vw] sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Product data updated</DialogTitle>
          <DialogDescription>
            {code}
            {tagLabel ? ` ${tagLabel}` : ''}
            {distinctName(code, name) ? ` / ${name}` : ''}
          </DialogDescription>
        </DialogHeader>

        {changes.length > 0 ? (
          <ProductDataChangeRows changes={changes} oldHeading="Was" newHeading="Now" />
        ) : (
          <p className="py-3 text-sm text-muted-foreground">
            The tag was updated to the current product data.
          </p>
        )}

        <DialogFooter className="gap-2 sm:gap-2">
          {version != null && (
            <Button
              variant="outline"
              disabled={busy !== null}
              onClick={() => void run('rollback')}
            >
              <Undo2 className="size-4 mr-1" />
              {busy === 'rollback' ? 'Rolling back...' : 'Roll back'}
            </Button>
          )}
          <Button disabled={busy !== null} onClick={() => void run('dismiss')}>
            <Check className="size-4 mr-1" />
            {busy === 'dismiss' ? 'Dismissing...' : 'Dismiss'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
