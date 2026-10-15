'use client';

/**
 * One product's photos, chosen one product at a time.
 *
 * **Why a dialog and not the page.** The list can be 11,390 products long, and
 * a wall of candidate thumbnails for every one of them is a page nobody can
 * scan: the codes are what somebody navigates by, and they were buried under
 * their own pictures. The list stays a list; the pictures appear when a row is
 * opened.
 *
 * **Why it walks.** Choosing photos is a sitting, not an errand. Closing the
 * dialog to open the next row costs two clicks per product and loses the
 * user's place, so the dialog moves through the SAME rows the list is showing,
 * in the same order, and a choice advances to the next one automatically.
 */

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { Check, ChevronLeft, ChevronRight, ImageOff } from 'lucide-react';

import type { BrochureImageRow } from '../../services/brochureImageService';
import { distinctName } from '@/lib/product-display';

export interface BrochureImageDialogProps {
  /** The rows the list is showing, in list order. */
  rows: BrochureImageRow[];
  /** Which of them is open, or null when the dialog is closed. */
  index: number | null;
  onIndexChange: (index: number | null) => void;
  onChoose: (productId: string, attachmentId: string, isChosen: boolean) => void;
}

export function BrochureImageDialog({
  rows,
  index,
  onIndexChange,
  onChoose,
}: BrochureImageDialogProps) {
  const open = index !== null && index >= 0 && index < rows.length;
  const row = open ? rows[index] : null;

  const go = (delta: number) => {
    if (index === null) return;
    const next = index + delta;
    // Clamped rather than wrapped: arriving back at the first product after the
    // last one reads as the list having reloaded, and the user starts again.
    if (next < 0 || next >= rows.length) return;
    onIndexChange(next);
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onIndexChange(null)}>
      <DialogContent className="max-w-3xl" data-dk-bi-dialog>
        <DialogHeader>
          <DialogTitle className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-mono">{row?.productCode}</span>
            {distinctName(row?.productCode, row?.productName) ? (
              <span className="min-w-0 truncate font-normal text-muted-foreground">
                {row?.productName}
              </span>
            ) : null}
            {row?.chosenAttachmentId && (
              <Badge variant="success" appearance="light" size="sm">
                chosen
              </Badge>
            )}
          </DialogTitle>
        </DialogHeader>

        {row && row.candidates.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-10 text-center">
            <ImageOff className="size-7 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              No photo is linked to this product yet. Attach one first.
            </p>
          </div>
        ) : (
          <div
            className="grid max-h-[60vh] grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-3 overflow-y-auto p-1"
            data-dk-bi-dialog-candidates
          >
            {row?.candidates.map((candidate) => {
              const isChosen = candidate.attachmentId === row.chosenAttachmentId;
              return (
                <button
                  key={candidate.attachmentId}
                  type="button"
                  onClick={() => onChoose(row.productId, candidate.attachmentId, isChosen)}
                  aria-pressed={isChosen}
                  data-dk-bi-candidate={candidate.filename}
                  className={`group flex flex-col gap-2 rounded-lg border p-2 text-start transition-[border-color,box-shadow] ${
                    isChosen
                      ? 'border-primary ring-2 ring-primary/25'
                      : 'border-border hover:border-primary/50'
                  }`}
                >
                  <div className="relative aspect-square overflow-hidden rounded-md bg-muted">
                    {candidate.url ? (
                      // A plain img, not next/image: the src is a signed URL on a
                      // storage host that changes per request, which the
                      // optimiser cannot cache anyway.
                      <img
                        src={candidate.url}
                        alt={candidate.filename}
                        className="size-full object-cover"
                      />
                    ) : (
                      <div className="flex size-full items-center justify-center text-muted-foreground">
                        <ImageOff className="size-5" />
                      </div>
                    )}
                    {isChosen && (
                      <span className="absolute end-1 top-1 rounded-full bg-primary p-1 text-primary-foreground">
                        <Check className="size-3" />
                      </span>
                    )}
                  </div>
                  {/* The filename is the only thing telling two thumbnails apart
                      when one of them is another product entirely, so it is
                      never hidden. */}
                  <span className="truncate text-xs" title={candidate.filename}>
                    {candidate.filename}
                  </span>
                  {candidate.accessLevels?.includes('dealer') && (
                    <span className="text-xs text-muted-foreground">dealer only</span>
                  )}
                </button>
              );
            })}
          </div>
        )}

        <DialogFooter className="sm:justify-between">
          <span className="text-xs text-muted-foreground">
            {index !== null ? `${index + 1} of ${rows.length} on this page` : ''}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => go(-1)}
              disabled={index === null || index <= 0}
            >
              <ChevronLeft className="size-4" />
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => go(1)}
              disabled={index === null || index >= rows.length - 1}
            >
              Next
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
