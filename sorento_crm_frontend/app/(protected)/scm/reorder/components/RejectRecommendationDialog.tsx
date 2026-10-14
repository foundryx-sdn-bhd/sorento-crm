'use client';

import { useEffect, useState } from 'react';
import { LoaderCircle } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import type { RejectPayload } from '../types/decisions.types';
import type { ReorderRecommendation } from '../types/reorder.types';
import { codeWithName } from '@/lib/product-display';

/**
 * M4-D8 - Reject a recommendation. Destructive confirm (AlertDialog) with a
 * REQUIRED reason. The reason is captured raw and enters the feedback pipeline;
 * classification into a `reason_code` lands in Slice C - nothing is inferred here.
 */
export function RejectRecommendationDialog({
  rec,
  open,
  onOpenChange,
  onSubmit,
  isSubmitting,
}: {
  rec: ReorderRecommendation | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: RejectPayload) => void;
  isSubmitting: boolean;
}) {
  const [reason, setReason] = useState('');
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (open) {
      setReason('');
      setTouched(false);
    }
  }, [open]);

  const submit = () => {
    setTouched(true);
    if (!reason.trim()) return;
    onSubmit({ reason_text: reason.trim() });
  };

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Reject this recommendation?</AlertDialogTitle>
          <AlertDialogDescription>
            {rec ? (
              <>
                {codeWithName(rec.sku, rec.product_name)} will be dismissed and won&apos;t draft a purchase
                order. This can&apos;t be undone.
              </>
            ) : null}
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-1.5">
          <Label className="block">
            Reason <span className="text-destructive">*</span>
          </Label>
          <Textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why reject? e.g. discontinued, overstocked elsewhere, demand spike was one-off…"
            rows={3}
            autoFocus
          />
          {touched && !reason.trim() ? (
            <p className="text-2xs text-destructive">A reason is required to reject.</p>
          ) : null}
        </div>

        <AlertDialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={isSubmitting}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : null}
            Reject recommendation
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
