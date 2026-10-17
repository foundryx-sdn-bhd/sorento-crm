'use client';

import * as React from 'react';
import { type ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Checkbox } from '@/components/ui/checkbox';
import { NumberInput } from '@/components/ui/number-input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect, type SearchableSelectOption } from '@/components/common/SearchableSelect';
import { formatDateInMalaysia } from '@/lib/helpers';
import { numberFieldError } from '@/lib/number-field';
import { useCreateOrderInquiryReserveRequest } from '../hooks/useOrderInquiry';
import { useReserveRowOptions } from '../hooks/useReserveRowOptions';
import { rowRequestable } from '../lib/reserveEligibility';
import type { OrderInquiryWorklistRow } from '../types/orderInquiry.types';

/**
 * `PLAN-oi-request-cs-reserve.md` 3.7 (AC-RS-22/AC-RS-23): one line per selected row,
 * Requested defaulted to remaining (never above it), Location defaulted to the row's
 * pool - the caller (the Lines tab's own Actions menu) already resolves every row's own
 * remaining / pool / stock-grid locations, since that resolution needs the same board
 * stock-detail read `OrderInquiryStockGrid` already makes.
 *
 * STOCK-DEBT-SNAPSHOT: shared by the OI page and the snapshot drill. It takes the inquiry's
 * live worklist rows and builds its own lines (`useReserveRowOptions` + `rowRequestable`), and
 * writes through `useCreateOrderInquiryReserveRequest` (UI -> hook -> service -> api-client).
 *
 * G6 (`PLAN-oi-request-cs-reserve.md` section 6d, AC-RS-75, owner: "we should use
 * standard datagrid table in the system"): the body is ONE DataGrid, one row per
 * selected line, rather than a stacked card per row - the `requested`/`location`
 * dictionaries below already keyed by row id, so the write path (`handleSend`) is
 * unchanged; only the layout is a grid now. `DataGrid` reads `useQueryClient()`
 * internally (`useListingColumnPreferences`, unconditional regardless of whether a
 * `listingKey` is passed), so unlike before, this dialog's own vitest suite now needs
 * a `QueryClientProvider` ancestor. No `listingKey` is passed, so the hook's own
 * `useQuery` stays disabled and issues no network read.
 */
interface RequestReserveDialogRow {
  id: string;
  item_code: string | null;
  delivery_date: string | null;
  /** What is left to request on this row - the Requested input's own ceiling. */
  remaining: string;
  /** The pool warehouse id/code Requested defaults Location to. */
  defaultLocation: string;
  locationOptions: SearchableSelectOption[];
}

function requestedError(value: string, remainingQty: number) {
  return numberFieldError(value, { min: 1, max: remainingQty, integer: true });
}

export function RequestReserveDialog({
  inquiryId,
  rows: worklistRows,
  defaultWarehouseId,
  open,
  onOpenChange,
  onSent,
}: {
  inquiryId: string;
  /** The inquiry's live worklist rows to name on the request. */
  rows: OrderInquiryWorklistRow[];
  /** Preferred Location for every row that offers it (e.g. a snapshot decision's pool). */
  defaultWarehouseId?: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSent?: () => void;
}) {
  const createMutation = useCreateOrderInquiryReserveRequest(inquiryId);
  // Memoised on what the options depend on: the hook below registers queries from this
  // array, so a fresh one on every dialog state change (the Park tickbox) kept it notifying.
  const entriesSignature = worklistRows
    .map((line) => `${line.id}~${line.product_id ?? ''}~${line.location ?? ''}`)
    .join('|');
  const entries = React.useMemo(
    () =>
      open
        ? worklistRows.map((line) => ({
            key: line.id,
            productId: line.product_id ?? null,
            location: line.location ?? null,
          }))
        : [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [open, entriesSignature],
  );
  const resolved = useReserveRowOptions(entries);
  const rows = React.useMemo<RequestReserveDialogRow[]>(
    () =>
      worklistRows.map((line) => {
        const own = resolved[line.id];
        const options = own?.options ?? [];
        const preferred =
          defaultWarehouseId && options.some((option) => option.value === defaultWarehouseId)
            ? defaultWarehouseId
            : null;
        return {
          id: line.id,
          item_code: line.item_code ?? null,
          delivery_date: line.delivery_date ?? null,
          remaining: String(Math.max(0, rowRequestable(line))),
          defaultLocation: preferred ?? own?.defaultWarehouseId ?? '',
          locationOptions: options,
        };
      }),
    [worklistRows, resolved, defaultWarehouseId],
  );
  // What the operator typed, as text: empty and partial drafts stay as typed. The final
  // value is checked when the request is sent or parked.
  const [requested, setRequested] = React.useState<Record<string, string>>({});
  const [showErrors, setShowErrors] = React.useState(false);
  const [location, setLocation] = React.useState<Record<string, string>>({});
  const [note, setNote] = React.useState('');
  const [sending, setSending] = React.useState(false);
  const [park, setPark] = React.useState(false);

  // S4 (reviewer round): keyed on a SIGNATURE, not the `rows` array itself - the
  // caller's own `useReserveRowOptions` (`_shared/hooks/useReserveRowOptions.ts`)
  // returns a freshly-built object every render, so `rows` (built off it) got a new
  // identity on every parent re-render and this effect wiped a still-being-typed
  // Requested/Location/Note on every unrelated state change elsewhere on the page.
  // Same fix `ReserveRowDialog.tsx` already carries for its own reset effect.
  const rowsSignature = rows
    .map((row) => `${row.id}:${row.remaining}:${row.defaultLocation}`)
    .join('|');
  React.useEffect(() => {
    if (!open) return;
    setRequested(
      Object.fromEntries(rows.map((row) => [row.id, String(Number(row.remaining || '0'))])),
    );
    setLocation(Object.fromEntries(rows.map((row) => [row.id, row.defaultLocation])));
    setNote('');
    setPark(false);
    setShowErrors(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, rowsSignature]);

  // G6: `columns` must stay REFERENTIALLY STABLE across a keystroke - `flexRender`
  // renders a columnDef's own `cell` function AS the component type, so a `columns`
  // array recomputed every render (the naive `useMemo(..., [requested, location])`
  // this started as) hands each cell a BRAND NEW function identity on every render,
  // which React treats as a brand new component type and unmounts/remounts the
  // `<Input>` underneath it - losing focus and dropping the very keystroke that
  // triggered the re-render (measured live: the DOM node before and after a
  // `fireEvent.change` are NOT `===`). `stateRef` carries the latest
  // `requested`/`location` for the (now permanently memoized) cell closures to read
  // at render time, so the identity a keystroke's own `setRequested` call produces
  // never reaches `columns` itself. Review round 24 Sep: `data` (`rows`) itself is NOT
  // stable either (S4 above: the caller's `useReserveRowOptions` hands back a fresh
  // object every render) - harmless here specifically because nothing this dialog
  // renders keys focus off `data`'s own identity, only off `columns`', which IS now
  // frozen; `project_datagrid_inline_data_render_loop` is the `columns` lesson, not a
  // claim that `data` is stable too.
  const stateRef = React.useRef({ requested, location, showErrors });
  stateRef.current = { requested, location, showErrors };

  const columns = React.useMemo<ColumnDef<RequestReserveDialogRow>[]>(
    () => [
      {
        id: 'product',
        header: 'Product',
        cell: ({ row }) => (
          <span className="block truncate text-sm font-medium" title={row.original.item_code ?? undefined}>
            {row.original.item_code}
          </span>
        ),
        size: 150,
        enableSorting: false,
        meta: { headerTitle: 'Product' },
      },
      {
        id: 'delivery_date',
        header: 'Delivery date',
        cell: ({ row }) =>
          row.original.delivery_date ? (
            <span>{formatDateInMalaysia(row.original.delivery_date)}</span>
          ) : (
            <span className="text-muted-foreground">No delivery date</span>
          ),
        size: 120,
        enableSorting: false,
        meta: { headerTitle: 'Delivery date' },
      },
      {
        id: 'remaining',
        header: 'Remaining',
        cell: ({ row }) => <span className="tabular-nums">{row.original.remaining}</span>,
        size: 90,
        enableSorting: false,
        meta: { headerTitle: 'Remaining', headerClassName: 'text-end', cellClassName: 'text-end' },
      },
      {
        id: 'requested',
        header: 'Requested',
        cell: ({ row }) => {
          const remainingQty = Number(row.original.remaining || '0');
          return (
            <>
              <Label htmlFor={`reserve-requested-${row.original.id}`} className="sr-only">
                Requested
              </Label>
              <NumberInput
                id={`reserve-requested-${row.original.id}`}
                min={1}
                max={remainingQty}
                value={stateRef.current.requested[row.original.id] ?? String(remainingQty)}
                error={
                  stateRef.current.showErrors
                    ? requestedError(
                        stateRef.current.requested[row.original.id] ?? String(remainingQty),
                        remainingQty,
                      )
                    : undefined
                }
                onChange={(event) => {
                  const typed = event.target.value;
                  setRequested((prev) => ({ ...prev, [row.original.id]: typed }));
                }}
              />
            </>
          );
        },
        size: 110,
        enableSorting: false,
        meta: { headerTitle: 'Requested' },
      },
      {
        id: 'location',
        header: 'Location',
        cell: ({ row }) => (
          <>
            <Label htmlFor={`reserve-location-${row.original.id}`} className="sr-only">
              Location
            </Label>
            <SearchableSelect
              id={`reserve-location-${row.original.id}`}
              value={stateRef.current.location[row.original.id] ?? row.original.defaultLocation}
              onChange={(value) =>
                setLocation((prev) => ({ ...prev, [row.original.id]: value }))
              }
              options={row.original.locationOptions}
            />
          </>
        ),
        size: 170,
        enableSorting: false,
        meta: { headerTitle: 'Location' },
      },
    ],
    // Deliberately empty: see the doc above. `stateRef` supplies fresh values,
    // `setRequested`/`setLocation` are stable (React), and every other read
    // (`row.original.*`) is scoped to the row `flexRender` hands the cell.
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  async function handleSend() {
    // Only the final values are checked: any row outside 1..remaining blocks the send.
    const blocked = rows.some((row) =>
      requestedError(requested[row.id] ?? row.remaining, Number(row.remaining || '0')),
    );
    if (blocked) {
      setShowErrors(true);
      return;
    }
    setSending(true);
    try {
      await createMutation.mutateAsync({
        rows: rows.map((row) => ({
          row_id: row.id,
          qty_requested: Number(requested[row.id] ?? row.remaining ?? '0'),
          warehouse_id: location[row.id] ?? row.defaultLocation,
        })),
        note: note.trim() ? note.trim() : null,
        park,
      });
      onOpenChange(false);
      onSent?.();
    } catch {
      // The caller's own mutation hook already toasted the error (S5) - nothing left
      // to do here except leave the dialog open so the reader can retry.
    } finally {
      setSending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* G6: wide enough for the grid, same rule `ReserveRowDialog`'s own multi-row
          dialog follows. */}
      <DialogContent className="flex max-h-[85dvh] flex-col sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Request CS to reserve</DialogTitle>
        </DialogHeader>
        <DialogBody className="flex-1 space-y-4 overflow-y-auto">
          {/* Round 4 review (24 Sep, AC-RS-89): no outer `overflow-x-auto` wrapper - the
              DataGrid's OWN internal scroller (`tableLayout.width: 'fixed'`) is the one
              horizontal-scroll surface; a second one around it was inert (never
              actually scrolled, `table.closest('.overflow-x-auto')` matched it purely
              by coincidence of nesting), and `columnsDraggable: false` drops the grip
              every header rendered by DEFAULT even though nothing here reorders. */}
          <DataGrid
            table={table}
            recordCount={rows.length}
            tableLayout={{
              width: 'fixed',
              columnsResizable: true,
              columnsDraggable: false,
              // The DialogBody above already owns the scroll viewport
              // (overflow-y-auto).
              scrollerMaxHeight: false,
            }}
          >
            <DataGridTable />
          </DataGrid>
          <div className="space-y-1">
            <Label htmlFor="reserve-request-note">Note (optional)</Label>
            <Textarea
              id="reserve-request-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={2}
            />
          </div>
        </DialogBody>
        <DialogFooter className="flex-wrap items-center gap-2 sm:justify-between">
          <div className="flex items-center gap-2">
            <Checkbox
              id="reserve-request-park"
              checked={park}
              onCheckedChange={(value) => setPark(value === true)}
              disabled={sending}
            />
            <Label htmlFor="reserve-request-park">Park this first</Label>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={sending}>
              Cancel
            </Button>
            <Button onClick={handleSend} disabled={sending || rows.length === 0}>
              {park ? 'Park request' : 'Send request'}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default RequestReserveDialog;
