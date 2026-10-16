'use client';

import * as React from 'react';
import { Badge } from '@/components/ui/badge';
import {
  Popover,
  PopoverContent,
  PopoverPortal,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { formatDateInMalaysia } from '@/lib/helpers';
import { CellStockTable } from '../../fulfilment-planning/components/CellStockTable';
import {
  useStockDetail,
  useStockGroups,
} from '../../_shared/hooks/useFulfilmentPlanning';
import type { StockDetailIncoming } from '../../_shared/types/fulfilmentPlanning.types';

/** Where the "Stock at" choice is remembered, per browser (owner, Q5). */
export const STOCK_GROUP_STORAGE_KEY = 'quotation-lines.stock-group';

function readStoredGroup(): string | null {
  try {
    return window.localStorage.getItem(STOCK_GROUP_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredGroup(group: string) {
  try {
    window.localStorage.setItem(STOCK_GROUP_STORAGE_KEY, group);
  } catch {
    // Storage disabled: the choice still holds for this visit.
  }
}

/**
 * The group every stock chip on the Lines tab reads, and the list it is picked from.
 *
 * A remembered group is trusted while the list is still loading, so a reload draws the chips
 * for the group the reader chose rather than flashing the first one; once the list is in, a
 * remembered group that is no longer on it falls back to the first.
 */
export function useQuotationStockGroup() {
  const groups = useStockGroups();
  const [stored, setStored] = React.useState<string | null>(readStoredGroup);
  const list = React.useMemo(() => groups.data ?? [], [groups.data]);
  const group =
    stored && (list.length === 0 || list.includes(stored))
      ? stored
      : (list[0] ?? null);
  const choose = React.useCallback((next: string) => {
    setStored(next);
    writeStoredGroup(next);
  }, []);
  return { groups: list, group, choose };
}

export function StockAtSelect({
  groups,
  value,
  onChange,
}: {
  groups: string[];
  value: string | null;
  onChange: (group: string) => void;
}) {
  const id = React.useId();
  const options = React.useMemo(
    () =>
      groups.map((group) => ({
        value: group,
        label: `${group} group + site pools`,
      })),
    [groups],
  );
  return (
    <div className="flex min-w-0 items-center gap-2">
      <label htmlFor={id} className="shrink-0 text-xs text-muted-foreground">
        Stock at
      </label>
      <SearchableSelect
        id={id}
        size="sm"
        value={value ?? ''}
        onChange={(next) => {
          if (next) onChange(next);
        }}
        options={options}
        placeholder="Group"
        truncateTriggerLabel
        className="w-48 min-w-0"
      />
    </div>
  );
}

type StockTone = 'ok' | 'part' | 'none';

/** Green covers the quoted qty, amber covers some of it, red covers none (AC-QLG15). */
function toneOf(available: number, wanted: number): StockTone {
  if (!(available > 0)) return 'none';
  if (!(wanted > 0) || available >= wanted) return 'ok';
  return 'part';
}

const TONE_VARIANT: Record<StockTone, 'success' | 'warning' | 'destructive'> = {
  ok: 'success',
  part: 'warning',
  none: 'destructive',
};

/** `25.00` reads as 25: stock is a count. Signed, never clamped. */
function trimQty(value: string | null | undefined): string {
  const amount = Number(value ?? '');
  if (value == null || Number.isNaN(amount)) return value ?? '';
  return String(amount);
}

/**
 * Available stock for the line's product in the chosen group, toned against the quoted qty,
 * opening the same stock table Order Inquiry shows (AC-QLG15, AC-QLG16). Read-only, and only on
 * a line with a product.
 */
export function QuotationStockChip({
  productId,
  group,
  quantity,
  uom,
  label,
}: {
  productId: string;
  group: string | null;
  quantity: string;
  uom: string;
  label: string;
}) {
  const detail = useStockDetail(productId, null, null, group);

  if (!group) return null;
  if (detail.isLoading || (!detail.data && !detail.isError)) {
    return <Skeleton className="h-5 w-16 rounded-full" />;
  }
  if (detail.isError || !detail.data) {
    return (
      <span
        className="text-2xs text-muted-foreground"
        title="Stock could not be loaded"
      >
        Stock -
      </span>
    );
  }

  const available = detail.data.available_qty;
  const tone = toneOf(Number(available), Number(quantity));

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Badge
          asChild
          variant={TONE_VARIANT[tone]}
          appearance="light"
          size="sm"
        >
          <button
            type="button"
            aria-label={`Stock on ${label}`}
            data-tone={tone}
            className="cursor-pointer whitespace-nowrap tabular-nums"
          >
            {`${trimQty(available)} avail`}
          </button>
        </Badge>
      </PopoverTrigger>
      {/* Portalled: a row being a drag target is its own stacking context, so a popover left
          inside it paints under the next row and a click lands on that row instead. */}
      <PopoverPortal>
        <PopoverContent
          align="end"
          // Radix keeps it on screen (avoidCollisions is on by default); the padding keeps it off
          // the very edge, and the cap lets a long table scroll inside the room it has.
          collisionPadding={8}
          className="max-h-[var(--radix-popover-content-available-height)] w-[min(36rem,calc(100vw-2rem))] space-y-2 overflow-y-auto p-3"
        >
          <StockPopoverBody
            productId={productId}
            label={label}
            quantity={quantity}
            uom={uom}
            groupLocations={detail.data.locations ?? []}
            groupIncoming={detail.data.incoming ?? []}
          />
        </PopoverContent>
      </PopoverPortal>
    </Popover>
  );
}

function StockPopoverBody({
  productId,
  label,
  quantity,
  uom,
  groupLocations,
  groupIncoming,
}: {
  productId: string;
  label: string;
  quantity: string;
  uom: string;
  groupLocations: NonNullable<
    ReturnType<typeof useStockDetail>['data']
  >['locations'];
  groupIncoming: StockDetailIncoming[];
}) {
  // The site pools, read only once somebody opens the popover: a quotation of fifty lines
  // would otherwise ask for fifty pool readings nobody looks at.
  const pools = useStockDetail(productId, null, null, 'pools');
  const incoming = [...groupIncoming, ...(pools.data?.incoming ?? [])];

  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold">{`${label} stock`}</p>
        <p className="text-xs text-muted-foreground">
          {`Quoted ${trimQty(quantity)}${uom ? ` ${uom}` : ''}`}
        </p>
      </div>
      <CellStockTable
        locations={groupLocations ?? []}
        showGroupSubtotal
        stockOnly
      />
      {pools.isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : pools.data ? (
        <CellStockTable
          locations={pools.data.locations ?? []}
          showGroupSubtotal
          stockOnly
        />
      ) : null}
      <div className="text-xs text-muted-foreground">
        {incoming.length === 0 ? (
          'No incoming SPO'
        ) : (
          <ul className="space-y-0.5">
            {incoming.map((row, index) => (
              <li key={`${row.spo_number}-${index}`}>
                {'Incoming: '}
                <span className="font-medium text-foreground">
                  {row.spo_number}
                </span>
                {`, ${trimQty(row.spo_qty)}`}
                {row.expected_date
                  ? `, expected ${formatDateInMalaysia(row.expected_date)}`
                  : ''}
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

export interface SetStockPart {
  product_id: string;
  product_code: string;
  quantity: string | number;
}

/** Reads one part's stock for the group and reports it up; draws nothing. */
function PartProbe({
  part,
  group,
  onRead,
}: {
  part: SetStockPart;
  group: string;
  onRead: (productId: string, available: number | null, failed: boolean) => void;
}) {
  const detail = useStockDetail(part.product_id, null, null, group);
  const data = detail.data;
  const failed = detail.isError;
  React.useEffect(() => {
    onRead(part.product_id, data ? Number(data.available_qty) : null, failed);
  }, [part.product_id, data, failed, onRead]);
  return null;
}

function PartStockSection({ part, group }: { part: SetStockPart; group: string }) {
  const detail = useStockDetail(part.product_id, null, null, group);
  return (
    <div className="space-y-1">
      <p className="text-sm font-semibold">
        {`${part.product_code}${Number(part.quantity) !== 1 ? ` x ${trimQty(String(part.quantity))}` : ''}`}
      </p>
      {detail.isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : detail.data ? (
        <CellStockTable locations={detail.data.locations ?? []} showGroupSubtotal stockOnly />
      ) : (
        <p className="text-xs text-muted-foreground">Stock could not be loaded</p>
      )}
    </div>
  );
}

/**
 * The stock pill of a SET line: how many complete sets the group can make, that is the smallest
 * of floor(part available / part quantity). Every part is read through `useStockDetail`, one
 * child per part (never a hook in a loop), and the popover lists each part with its stock table.
 */
export function SetStockChip({
  parts,
  group,
  quantity,
  label,
}: {
  parts: SetStockPart[];
  group: string | null;
  quantity: string;
  label: string;
}) {
  const [reads, setReads] = React.useState<Record<string, { available: number | null; failed: boolean }>>({});
  const onRead = React.useCallback(
    (productId: string, available: number | null, failed: boolean) =>
      setReads((previous) =>
        previous[productId]?.available === available && previous[productId]?.failed === failed
          ? previous
          : { ...previous, [productId]: { available, failed } },
      ),
    [],
  );

  if (!group || parts.length === 0) return null;
  const probes = parts.map((part) => (
    <PartProbe key={part.product_id} part={part} group={group} onRead={onRead} />
  ));
  const read = parts.map((part) => reads[part.product_id]);
  if (read.some((entry) => entry?.failed)) {
    return (
      <>
        {probes}
        <span className="text-2xs text-muted-foreground" title="Stock could not be loaded">
          Stock -
        </span>
      </>
    );
  }
  if (read.some((entry) => !entry || entry.available === null)) {
    return (
      <>
        {probes}
        <Skeleton className="h-5 w-16 rounded-full" />
      </>
    );
  }
  const sets = Math.min(
    ...parts.map((part, index) => {
      const per = Number(part.quantity) > 0 ? Number(part.quantity) : 1;
      return Math.floor((read[index]?.available ?? 0) / per);
    }),
  );
  const available = Math.max(sets, 0);
  const tone = toneOf(available, Number(quantity));
  return (
    <>
      {probes}
      <Popover>
        <PopoverTrigger asChild>
          <Badge asChild variant={TONE_VARIANT[tone]} appearance="light" size="sm">
            <button
              type="button"
              aria-label={`Stock on ${label}`}
              data-tone={tone}
              className="cursor-pointer whitespace-nowrap tabular-nums"
            >
              {`${available} avail`}
            </button>
          </Badge>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent
            align="end"
            collisionPadding={8}
            className="max-h-[var(--radix-popover-content-available-height)] w-[min(36rem,calc(100vw-2rem))] space-y-3 overflow-y-auto p-3"
          >
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-semibold">{`${label} stock`}</p>
              <p className="text-xs text-muted-foreground">{`Quoted ${trimQty(quantity)} sets`}</p>
            </div>
            {parts.map((part) => (
              <PartStockSection key={part.product_id} part={part} group={group} />
            ))}
          </PopoverContent>
        </PopoverPortal>
      </Popover>
    </>
  );
}
