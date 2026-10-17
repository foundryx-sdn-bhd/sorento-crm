'use client';

/**
 * The item chip that opens the stock status panel in place (mock round 4, frame 4): the
 * fulfilment board's product cell and the order inquiry worklist's item code.
 *
 * Two facts, two chips (On hand, SO): never one chip with a separator. The figures come from
 * the batched summary read (every chip on a page shares one request); the full panel is
 * fetched only when the popover opens, because `PopoverContent` mounts its children only
 * while open.
 *
 * Which chip is open lives outside the chip (crew audit, fulfilment board). A list rebuilds
 * its columns whenever one of its inputs changes, and TanStack renders a cell function as a
 * component, so every cell remounts and a popover held in the chip's own state closed: the
 * first click after load landed just before such a rebuild. Held here, a remounted chip
 * reopens where it was.
 */
import * as React from 'react';
import { Badge } from '@/components/ui/badge';
import {
  Popover,
  PopoverContent,
  PopoverPortal,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Qty, StockStatusPanel } from './StockStatusPanel';
import { useStockStatusSummary } from './useStockStatus';

// The one open chip (a popover closes when another opens, so one is enough), and how many
// mounted chips carry each key, so a key whose chip has gone for good is let go.
let openKey: string | null = null;
const listeners = new Set<() => void>();
const mounted = new Map<string, number>();

function setOpenKey(key: string | null) {
  if (openKey === key) return;
  openKey = key;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Open state for one chip, surviving a remount of that chip. */
function useChipOpen(key: string): [boolean, (open: boolean) => void] {
  const open = React.useSyncExternalStore(
    subscribe,
    () => openKey === key,
    () => false,
  );
  React.useEffect(() => {
    mounted.set(key, (mounted.get(key) ?? 0) + 1);
    return () => {
      mounted.set(key, (mounted.get(key) ?? 1) - 1);
      // A remount mounts the new chip in the same commit; only a chip that is not back by
      // the next task has left the page, and its popover must not reopen on a later visit.
      setTimeout(() => {
        if (!mounted.get(key) && openKey === key) setOpenKey(null);
      }, 0);
    };
  }, [key]);
  const setOpen = React.useCallback(
    (next: boolean) =>
      setOpenKey(next ? key : openKey === key ? null : openKey),
    [key],
  );
  return [open, setOpen];
}

export interface StockStatusChipProps {
  productId: string;
  /** The item code, for the button's accessible name. */
  itemCode: string;
  lineIds?: string[];
}

export function StockStatusChip({
  productId,
  itemCode,
  lineIds,
}: StockStatusChipProps) {
  const summary = useStockStatusSummary(productId).data ?? undefined;
  const [open, setOpen] = useChipOpen(
    `${productId}|${lineIds?.join(',') ?? ''}`,
  );
  return (
    <Popover open={open} onOpenChange={setOpen}>
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
              <Badge
                variant="secondary"
                appearance="light"
                size="xs"
                className="tabular-nums"
              >
                On hand <Qty value={summary.on_hand} />
              </Badge>
              <Badge
                variant="secondary"
                appearance="light"
                size="xs"
                className="tabular-nums"
              >
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
        {/* Height (crew audit): capped at the room on its side, but never asking for less than
            28rem, so a chip low on the screen overflows below and Radix flips the popover above
            instead of squeezing it into the strip that is left. It scrolls inside past that. */}
        <PopoverContent
          align="start"
          collisionPadding={8}
          className="max-h-[max(var(--radix-popover-content-available-height),min(28rem,calc(100dvh-2rem)))] w-[min(60rem,calc(100vw-2rem))] overflow-y-auto p-3"
          onClick={(event) => event.stopPropagation()}
        >
          <p className="mb-2 font-semibold">{itemCode}</p>
          <StockStatusPanel
            productId={productId}
            lineIds={lineIds}
            showOpenFullPage
          />
        </PopoverContent>
      </PopoverPortal>
    </Popover>
  );
}
