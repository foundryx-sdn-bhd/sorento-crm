'use client';

/**
 * The item chip that opens the stock status panel in place (mock round 4, frame 4): the
 * fulfilment board's product cell and the order inquiry worklist's item code.
 *
 * Two facts, two chips (On hand, SO): never one chip with a separator. The figures come from
 * the batched summary read (every chip on a page shares one request); the full panel is
 * fetched only when the popover opens, because `PopoverContent` mounts its children only
 * while open.
 */
import * as React from 'react';
import { Badge } from '@/components/ui/badge';
import { Popover, PopoverContent, PopoverPortal, PopoverTrigger } from '@/components/ui/popover';
import { Qty, StockStatusPanel } from './StockStatusPanel';
import { useStockStatusSummary } from './useStockStatus';

export interface StockStatusChipProps {
  productId: string;
  /** The item code, for the button's accessible name. */
  itemCode: string;
  lineIds?: string[];
}

export function StockStatusChip({ productId, itemCode, lineIds }: StockStatusChipProps) {
  const summary = useStockStatusSummary(productId).data ?? undefined;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Stock status of ${itemCode}`}
          className="inline-flex cursor-pointer flex-wrap items-center gap-1"
          onClick={(event) => event.stopPropagation()}
          data-testid="stock-status-chip"
        >
          {summary ? (
            <>
              <Badge variant="secondary" appearance="light" size="xs" className="tabular-nums">
                On hand <Qty value={summary.on_hand} />
              </Badge>
              <Badge variant="secondary" appearance="light" size="xs" className="tabular-nums">
                SO <Qty value={-summary.so_qty} />
              </Badge>
            </>
          ) : (
            <Badge variant="secondary" appearance="light" size="xs">
              Stock
            </Badge>
          )}
        </button>
      </PopoverTrigger>
      {/* Portalled: a row can be its own stacking context, and a popover left inside it would
          paint under the next row. */}
      <PopoverPortal>
        <PopoverContent
          align="start"
          collisionPadding={8}
          className="max-h-[var(--radix-popover-content-available-height)] w-[min(60rem,calc(100vw-2rem))] overflow-y-auto p-3"
          onClick={(event) => event.stopPropagation()}
        >
          <p className="mb-2 font-semibold">{itemCode}</p>
          <StockStatusPanel productId={productId} lineIds={lineIds} showOpenFullPage />
        </PopoverContent>
      </PopoverPortal>
    </Popover>
  );
}
