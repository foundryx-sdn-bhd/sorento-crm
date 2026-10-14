'use client';

/**
 * What changed under a tag, and the two things a person can do about it
 * (r9 S5/D18).
 *
 * Old on the left, new on the right, one row per field, because the question
 * being asked is "is this new value the one you want printed" and that cannot
 * be answered without seeing both. Keep current and Update tag are equally
 * legitimate answers - a price that moved after the salesperson approved the
 * proof is often exactly the thing NOT to print - so neither is styled as the
 * safe one.
 */

import { useState } from 'react';
import { Check, RotateCcw } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type {
  LineDataChange,
  TagDataChangeSet,
} from '@/lib/dealer-kit/product-data-changes';
import { distinctName } from '@/lib/product-display';

interface ProductDataReviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The tag under review. Null closes the dialog. */
  changeSet: TagDataChangeSet | null;
  onDecide: (action: 'update' | 'keep') => Promise<void>;
}

export default function ProductDataReviewDialog({
  open,
  onOpenChange,
  changeSet,
  onDecide,
}: ProductDataReviewDialogProps) {
  const [busy, setBusy] = useState<'update' | 'keep' | null>(null);

  const decide = async (action: 'update' | 'keep') => {
    setBusy(action);
    try {
      await onDecide(action);
      onOpenChange(false);
    } catch {
      // The decision did not happen, so the question has not been answered:
      // the dialog stays open with both buttons live. The caller owns the
      // message - closing here would report a change that was refused.
    } finally {
      setBusy(null);
    }
  };

  if (!open || !changeSet) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[95vw] sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Product data changed</DialogTitle>
          <DialogDescription>
            {/* The tag's label rides with the code because a line can print
                several tags and only one of them is under review here. Both
                are what a reader is shown everywhere else; neither is an id. */}
            {changeSet.code}
            {changeSet.tag_label ? ` ${changeSet.tag_label}` : ''}
            {distinctName(changeSet.code, changeSet.name) ? ` / ${changeSet.name}` : ''}
          </DialogDescription>
        </DialogHeader>

        <ProductDataChangeRows changes={changeSet.changes} />

        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            variant="outline"
            disabled={busy !== null}
            onClick={() => void decide('keep')}
          >
            <RotateCcw className="size-4 mr-1" />
            {busy === 'keep' ? 'Keeping...' : 'Keep current'}
          </Button>
          <Button disabled={busy !== null} onClick={() => void decide('update')}>
            <Check className="size-4 mr-1" />
            {busy === 'update' ? 'Updating...' : 'Update tag'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * The old -> new rows, one per field. Shared with the r10 S8 "updated"
 * dialog (`ProductDataUpdatedDialog`), which shows the same diff after the
 * fact - the reader is asked the same question either way, only the answers
 * on offer differ.
 */
export function ProductDataChangeRows({
  changes,
  oldHeading = 'On the tag',
  newHeading = 'Now in the product',
}: {
  changes: LineDataChange[];
  oldHeading?: string;
  newHeading?: string;
}) {
  return (
    <div className="max-h-[60dvh] overflow-y-auto" data-testid="product-data-review-rows">
      {changes.map((change) => (
        <div
          key={change.field}
          className="grid grid-cols-1 gap-2 border-b py-3 last:border-b-0 sm:grid-cols-[9rem_1fr_1fr] sm:gap-3"
        >
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {change.label}
          </span>
          <div className="min-w-0">
            <p className="text-2xs uppercase tracking-wide text-muted-foreground">{oldHeading}</p>
            <ChangeValue
              value={change.old}
              imageUrl={change.old_image_url}
              isImage={change.field.startsWith('image:')}
              muted
            />
          </div>
          <div className="min-w-0">
            <p className="text-2xs uppercase tracking-wide text-muted-foreground">{newHeading}</p>
            <ChangeValue
              value={change.new}
              imageUrl={change.new_image_url}
              isImage={change.field.startsWith('image:')}
            />
            {change.note && <p className="mt-0.5 text-xs text-amber-700">{change.note}</p>}
          </div>
        </div>
      ))}
    </div>
  );
}

function ChangeValue({
  value,
  imageUrl,
  isImage,
  muted,
}: {
  value: string | null;
  imageUrl?: string | null;
  /**
   * A PHOTO row, decided by the field name (`image:<attachment id>`) and not
   * by whether a URL came with it. The response model declares both URL keys,
   * so FastAPI serialises them as `null` on every row - a price row included -
   * and `imageUrl !== undefined` was therefore true for all of them: every
   * text row took the image branch and asked the reader to choose between
   * "No photo" and "No photo".
   */
  isImage?: boolean;
  muted?: boolean;
}) {
  if (isImage) {
    return imageUrl ? (
      <img
        src={imageUrl}
        alt={value ?? 'Product photo'}
        className="mt-1 size-16 rounded border object-contain"
      />
    ) : (
      <p className="mt-1 text-sm text-muted-foreground">No photo</p>
    );
  }
  return (
    <p
      className={`mt-1 whitespace-pre-wrap break-words text-sm ${muted ? 'text-muted-foreground line-through' : ''}`}
    >
      {value === null || value === '' ? '-' : value}
    </p>
  );
}
