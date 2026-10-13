'use client';

import { Info } from 'lucide-react';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import {
  Popover,
  PopoverContent,
  PopoverPortal,
  PopoverTrigger,
} from '@/components/ui/popover';
import { purchasingRefusalTitle, type PurchasingRefusal } from '../lib/orderInquiryAck';

/**
 * PR 1622 (owner, mock v3): a purchasing refusal is the ORDER INQUIRY's status on a line -
 * the "Rejected" pill in the state pill's own family, and an info icon carrying who, when
 * and why. Hover shows it on a desktop; at 375px there is no hover, so a tap opens the same
 * sentence in a popover (the `BoardDecisionPill` Saved-by pattern).
 */
export function OrderInquiryRejectedBadge({
  refusal,
  testId,
}: {
  refusal: PurchasingRefusal;
  testId?: string;
}) {
  const text = purchasingRefusalTitle(refusal);
  return (
    <Popover>
      <PopoverTrigger asChild onClick={(event) => event.stopPropagation()}>
        <button
          type="button"
          aria-label={text}
          title={text}
          data-testid={testId}
          className="inline-flex shrink-0 items-center gap-1 rounded-sm outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className={`${STATUS_PILL_BASE} ${statusPillClass('rejected')}`}>Rejected</span>
          <Info aria-hidden className="size-3.5 text-muted-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverPortal>
        <PopoverContent
          align="start"
          className="w-auto max-w-[92vw] px-3 py-2 text-xs"
          onOpenAutoFocus={(event) => event.preventDefault()}
        >
          {text}
        </PopoverContent>
      </PopoverPortal>
    </Popover>
  );
}
