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
 * reopens where it was. It is keyed by the caller's row (`openKey`): two rows of one product
 * must not share it, or both popovers open and each one's dismiss closes the other.
 *
 * Side: the chip asks for the side with more room when the room below is short, and the
 * height is capped at the room on that side, so the popover is never squeezed into a strip
 * low on the screen and never pushed off a small one.
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
      const left = (mounted.get(key) ?? 1) - 1;
      if (left > 0) mounted.set(key, left);
      else mounted.delete(key);
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

/** Test-only: no chip open, no chip counted. */
export function resetStockStatusChipsForTests() {
  mounted.clear();
  setOpenKey(null);
}

// Below this much room under the chip, the popover goes to whichever side has more.
const ROOM_WANTED_PX = 448;

function sideWithRoom(trigger: HTMLElement | null): 'top' | 'bottom' {
  if (!trigger) return 'bottom';
  const rect = trigger.getBoundingClientRect();
  const below = window.innerHeight - rect.bottom;
  return below < ROOM_WANTED_PX && rect.top > below ? 'top' : 'bottom';
}

export interface StockStatusChipProps {
  productId: string;
  /** The item code, for the button's accessible name. */
  itemCode: string;
  lineIds?: string[];
  /**
   * The row's own stable id, so two rows of one product never share an open popover.
   * Defaults to the product and its lines.
   */
  openKey?: string;
}

export function StockStatusChip({
  productId,
  itemCode,
  lineIds,
  openKey,
}: StockStatusChipProps) {
  const summary = useStockStatusSummary(productId).data ?? undefined;
  const [open, setOpen] = useChipOpen(
    openKey ?? `${productId}|${lineIds?.join(',') ?? ''}`,
  );
  const triggerRef = React.useRef<HTMLButtonElement>(null);
  const [side, setSide] = React.useState<'top' | 'bottom'>('bottom');
  // Measured when it opens, and again when a remounted chip comes back open.
  React.useLayoutEffect(() => {
    if (open) setSide(sideWithRoom(triggerRef.current));
  }, [open]);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          ref={triggerRef}
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
        <PopoverContent
          side={side}
          align="start"
          collisionPadding={8}
          data-stock-status-popover=""
          data-requested-side={side}
          className="max-h-(--radix-popover-content-available-height) w-[min(60rem,calc(100vw-2rem))] overflow-y-auto p-3"
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
