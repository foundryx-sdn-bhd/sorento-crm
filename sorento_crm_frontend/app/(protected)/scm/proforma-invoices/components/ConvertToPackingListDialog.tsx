'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { LoaderCircle } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Checkbox } from '@/components/ui/checkbox';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useContainerSizes } from '../../hooks/useFulfilment';
import { useProformaInvoice } from '../../hooks/useProformaInvoices';
import { useProformaInvoicePacking } from '../../hooks/useProformaInvoicePacking';
import { EM_DASH, fmtQty, fmtTrimmedDecimal } from '../../lib/format';
import type { ProformaInvoiceLine } from '../../services/proformaInvoiceService';
import type { ProformaInvoicePackingLine } from '../types/packingLine.types';

/**
 * How much of THIS invoice goes onto a container (AC-F10, Q9), and which container (S5,
 * ruling 1).
 *
 * The per-line quantity question is the PI DETAIL's own: placing part of an invoice is a
 * deliberate act made on one document, so the list converts its whole selection at once and
 * never sees that part of the body (R15, `single` stays null for a multi-invoice convert).
 * The Container size select is common to both surfaces, because both are the SAME write
 * (`convert_to_draft_shipment`) and the box being loaded is a property of the shipment
 * either way.
 */
/** One thing that can go on the container (ruling 25): a supplier packing row, or - for a
 *  line the packing list never mentioned - the invoice line itself. */
interface PlacementRow {
  key: string;
  line: ProformaInvoiceLine;
  /** Null on a bare line: nothing packed it, so the operator types a quantity instead of
   *  ticking a carton. */
  row: ProformaInvoicePackingLine | null;
  qty: number;
  cartons: number | null;
  cbm: number | null;
}

/** One frozen empty array for "nothing to place", so the grid's `data` identity is stable
 *  while the invoice is still loading. */
const NO_ROWS: PlacementRow[] = [];

/** Quantity to place is required, 0 or more (0 holds the line back) and no more than what is left. Untouched = the default. */
function placeError(line: { remaining_qty?: number | null }, raw: string | undefined): string | undefined {
  if (raw === undefined) return undefined;
  return numberFieldError(raw, { min: 0, max: line.remaining_qty ?? 0 });
}

export function ConvertToPackingListDialog({
  open,
  onOpenChange,
  invoiceIds,
  pending,
  onConvert,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  invoiceIds: string[];
  pending?: boolean;
  onConvert: (args: {
    lineQuantities: Record<string, number>;
    containerSizeId: string | null;
    /** The ticked packing rows (AC-D2b). Absent on an invoice with no packing rows, which
     *  is what tells the convert to place every line's remainder as before. */
    packingRowIds?: string[];
  }) => void;
}) {
  const single = invoiceIds.length === 1 ? invoiceIds[0] : null;
  const { data: invoice, isLoading } = useProformaInvoice(open ? single : null);
  const packing = useProformaInvoicePacking(open ? invoice : undefined);
  const packingRows = useMemo(() => packing.data?.rows ?? [], [packing.data]);
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  // Read by the Place cell below through a ref, so the columns keep one identity while the operator
  // types (a new column array remounts the cell and drops the box under the cursor).
  const quantitiesRef = useRef(quantities);
  quantitiesRef.current = quantities;
  // Set by a pressed Convert that was refused: from then on each box shows its sentence, live.
  const [convertTried, setConvertTried] = useState(false);
  const convertTriedRef = useRef(convertTried);
  convertTriedRef.current = convertTried;
  const containerSizes = useContainerSizes();
  const [containerSizeId, setContainerSizeId] = useState<string | null>(null);
  // A packing row placed whole or not at all (S4, AC-D2b) - every MATCHED row starts
  // checked (placed), the same default the plan rules for "default all unplaced" rows on
  // a PI nothing has convertED yet.
  const [placedRowIds, setPlacedRowIds] = useState<Set<string>>(new Set());
  // C4: client-side filter over the placement table - the list a long PI shows (DAFUYUAN:
  // 15 rows) is never big enough to warrant a server round trip, so this is the same "type
  // to narrow what's on screen" a table search box does, not a debounced query.
  const [search, setSearch] = useState('');

  // Re-read on every open: a remainder typed last time describes an invoice that has since
  // moved, and a stale figure here places the wrong quantity silently. The size resets to
  // the tenant default too - a size chosen for a previous convert says nothing about this
  // one.
  useEffect(() => {
    if (!open) return;
    setQuantities({});
    setConvertTried(false);
    setContainerSizeId(null);
    setSearch('');
  }, [open]);

  // Rows default to placed the moment they arrive - a checkbox nobody has touched yet
  // reads as "everything goes", which is the plan's own default.
  useEffect(() => {
    if (!open) return;
    setPlacedRowIds(new Set(packingRows.filter((r) => r.match_state === 'matched').map((r) => r.id)));
  }, [open, packingRows]);

  // Container / seal / SO / BL / consignee the draft will actually receive (AC-C5/AC-C6,
  // B3) - read straight off the server's own `convert_carry` (the PI payload field
  // `proforma_invoice_service.serialize` computes with the SAME function `convert_to_
  // draft_shipment` uses), never recomputed here: the dialog's line can then never say
  // something Convert itself would not. R-E (owner ruling 25 Sep): `so` and `bl` are two
  // INDEPENDENT facts now, never one carried into the other.
  const carryPreview = invoice?.convert_carry ?? {
    container: null, seal: null, so: null, bl: null, consignee: null,
  };
  // V3 (fix round 1): the line names only what IS carried - a part with no value is
  // dropped entirely rather than printed as "Container -".
  const carryParts = useMemo(() => {
    const parts: string[] = [];
    if (carryPreview.container) parts.push(`Container ${carryPreview.container}`);
    if (carryPreview.seal) parts.push(`Seal ${carryPreview.seal}`);
    if (carryPreview.so) parts.push(`SO ${carryPreview.so}`);
    if (carryPreview.bl) parts.push(`BL ${carryPreview.bl}`);
    if (carryPreview.consignee) parts.push(`Consignee ${carryPreview.consignee}`);
    return parts;
  }, [
    carryPreview.container, carryPreview.seal, carryPreview.so, carryPreview.bl,
    carryPreview.consignee,
  ]);

  const defaultSize = useMemo(
    () => (containerSizes.data ?? []).find((s) => s.is_default) ?? null,
    [containerSizes.data],
  );
  const containerSizeOptions = useMemo(
    () =>
      (containerSizes.data ?? []).map((s) => ({
        value: s.id,
        label: `${s.code} - ${fmtTrimmedDecimal(s.cbm, 2)} cbm${s.is_default ? ' (default)' : ''}`,
      })),
    [containerSizes.data],
  );

  const placeable = useMemo(
    () => (invoice?.lines ?? []).filter((line) => (line.remaining_qty ?? 0) > 0),
    [invoice],
  );
  /** Finished: every piece of it is on a container. */
  const alreadyPlaced = useMemo(
    () =>
      (invoice?.lines ?? []).filter(
        (line) => (line.remaining_qty ?? 0) <= 0 && (line.placed_qty ?? 0) > 0,
      ),
    [invoice],
  );
  /**
   * ONE row per placement unit (ruling 25): a matched packing row where the invoice line
   * has any, else the line itself. That is the grain the convert writes shipment lines on
   * and the grain the operator ticks, so it is the grain the table shows - the stacked
   * cards this replaced made the reader hold the two together in their head.
   */
  const placementRows = useMemo<PlacementRow[]>(() => {
    const out: PlacementRow[] = [];
    for (const line of placeable) {
      const rows = packingRows.filter(
        (r) => r.proforma_invoice_line_id === line.id && r.match_state === 'matched',
      );
      if (rows.length === 0) {
        out.push({
          key: `line-${line.id}`,
          line,
          row: null,
          qty: line.remaining_qty ?? 0,
          cartons: line.cartons ?? null,
          cbm: line.cbm_total ?? null,
        });
        continue;
      }
      for (const row of rows) {
        out.push({
          key: `row-${row.id}`,
          line,
          row,
          qty: row.qty ?? 0,
          cartons: row.cartons ?? null,
          cbm: row.cbm_total ?? null,
        });
      }
    }
    return out;
  }, [placeable, packingRows]);

  /** C4: what the search box narrows the TABLE to - never what totals sum or what
   *  Convert writes (`placeable`/`packingRows` stay untouched), the same "find, not
   *  select" contract every other list search box in this codebase carries. */
  const visibleRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return placementRows;
    return placementRows.filter((r) => {
      const code = r.line.item_code?.toLowerCase() ?? '';
      const product = (r.line.product_code ?? r.line.description ?? '').toLowerCase();
      return code.includes(q) || product.includes(q);
    });
  }, [placementRows, search]);

  /** Footer totals over the FULL placement set, never the search-filtered `visibleRows`
   *  (C4: "find, not select" - a search narrows what the TABLE shows, not what Convert
   *  will place or what the footer sums). Read through a ref by the footer cells, the
   *  same way the Packing tab's own footers do: listing the rows as a `columns` dependency
   *  rebuilds every cell renderer whenever the packing query resolves. */
  const totals = useMemo(
    () => ({
      qty: placementRows.reduce((sum, r) => sum + (r.qty ?? 0), 0),
      cartons: placementRows.reduce((sum, r) => sum + (r.cartons ?? 0), 0),
      cbm: placementRows.reduce((sum, r) => sum + (r.cbm ?? 0), 0),
    }),
    [placementRows],
  );
  const totalsRef = useRef(totals);
  totalsRef.current = totals;

  const columns = useMemo<ColumnDef<PlacementRow>[]>(
    () => [
      {
        id: 'code',
        header: ({ column }) => <DataGridColumnHeader title="Code" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.line.item_code}>
            {row.original.line.item_code}
          </span>
        ),
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'Code' },
      },
      {
        id: 'product',
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        cell: ({ row }) => (
          <span
            className="block truncate"
            title={row.original.line.product_code ?? row.original.line.description ?? undefined}
          >
            {row.original.line.product_code || row.original.line.description || EM_DASH}
          </span>
        ),
        size: 160,
        enableSorting: false,
        meta: { headerTitle: 'Product' },
      },
      {
        id: 'invoiced',
        header: ({ column }) => <DataGridColumnHeader title="On invoice" column={column} />,
        cell: ({ row }) => fmtQty(row.original.line.qty),
        size: 90,
        enableSorting: false,
        meta: {
          headerTitle: 'On invoice',
          headerClassName: 'text-end',
          cellClassName: 'text-end tabular-nums',
        },
      },
      {
        id: 'row_no',
        header: ({ column }) => <DataGridColumnHeader title="Row" column={column} />,
        cell: ({ row }) => (row.original.row ? row.original.row.row_no : EM_DASH),
        size: 60,
        enableSorting: false,
        meta: { headerTitle: 'Row', headerClassName: 'text-end', cellClassName: 'text-end tabular-nums' },
      },
      {
        id: 'qty',
        header: ({ column }) => <DataGridColumnHeader title="Qty" column={column} />,
        cell: ({ row }) => fmtQty(row.original.qty),
        size: 80,
        enableSorting: false,
        meta: { headerTitle: 'Qty', headerClassName: 'text-end', cellClassName: 'text-end tabular-nums' },
        footer: () => fmtQty(totalsRef.current.qty),
      },
      {
        id: 'cartons',
        header: ({ column }) => <DataGridColumnHeader title="Ctns" column={column} />,
        cell: ({ row }) => (row.original.cartons == null ? EM_DASH : fmtQty(row.original.cartons)),
        size: 70,
        enableSorting: false,
        meta: { headerTitle: 'Ctns', headerClassName: 'text-end', cellClassName: 'text-end tabular-nums' },
        footer: () => fmtQty(totalsRef.current.cartons),
      },
      {
        id: 'cbm',
        header: ({ column }) => <DataGridColumnHeader title="CBM" column={column} />,
        cell: ({ row }) =>
          row.original.cbm == null ? EM_DASH : fmtTrimmedDecimal(row.original.cbm, 3),
        size: 80,
        enableSorting: false,
        meta: { headerTitle: 'CBM', headerClassName: 'text-end', cellClassName: 'text-end tabular-nums' },
        footer: () => fmtTrimmedDecimal(totalsRef.current.cbm, 3),
      },
      {
        id: 'place',
        header: ({ column }) => <DataGridColumnHeader title="Place" column={column} />,
        // A packing row goes whole or not at all (AC-D2b): the supplier packed that many
        // cartons of it and there is no part of a carton. A line the packing list never
        // mentioned has no such grain, so it keeps the quantity it always had.
        cell: ({ row }) =>
          row.original.row ? (
            <Checkbox
              id={`packing-row-${row.original.row.id}`}
              aria-label={`Place row ${row.original.row.row_no} of ${row.original.line.item_code}`}
              checked={placedRowIds.has(row.original.row.id)}
              onCheckedChange={(checked) =>
                setPlacedRowIds((prev) => {
                  const next = new Set(prev);
                  if (checked) next.add(row.original.row!.id);
                  else next.delete(row.original.row!.id);
                  return next;
                })
              }
            />
          ) : (
            <div className="flex items-center gap-1.5">
              <NumberInput
                min={0}
                max={row.original.line.remaining_qty}
                value={quantitiesRef.current[row.original.line.id] ?? String(row.original.line.remaining_qty)}
                error={
                  convertTriedRef.current
                    ? placeError(row.original.line, quantitiesRef.current[row.original.line.id])
                    : undefined
                }
                errorClassName="w-32 whitespace-normal"
                onChange={(e) =>
                  setQuantities((prev) => ({ ...prev, [row.original.line.id]: e.target.value }))
                }
                className="h-7 w-20 text-right tabular-nums"
                aria-label={`Quantity to place for ${row.original.line.item_code}`}
              />
              <span className="whitespace-nowrap text-2xs text-muted-foreground">
                of {fmtQty(row.original.line.remaining_qty)} left
              </span>
            </div>
          ),
        size: 170,
        enableSorting: false,
        meta: { headerTitle: 'Place' },
      },
    ],
    [placedRowIds],
  );

  const table = useReactTable({
    columns,
    data: visibleRows.length ? visibleRows : NO_ROWS,
    getRowId: (row) => row.key,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const submit = () => {
    if (placeable.some((line) => placeError(line, quantities[line.id]) !== undefined)) {
      setConvertTried(true);
      return;
    }
    const lineQuantities: Record<string, number> = {};
    for (const line of placeable) {
      const raw = quantities[line.id];
      if (raw === undefined) continue;
      lineQuantities[line.id] = Number(raw);
    }
    // The ticked rows, in the order the grid shows them (AC-D2b). Sent only when this
    // invoice HAS packing rows: an omitted list means "every row not already placed",
    // which is the right default for a PI whose lines carry no rows at all, and the
    // convert route reads it that way. Unticking a row now genuinely leaves it off the
    // draft - the checkboxes were state nothing ever sent.
    const packingRowIds = packingRows
      .filter((r) => r.match_state === 'matched' && placedRowIds.has(r.id))
      .map((r) => r.id);
    onConvert({
      lineQuantities,
      containerSizeId,
      ...(packingRows.length > 0 ? { packingRowIds } : {}),
    });
  };

  const invalid = placeable.some((line) => placeError(line, quantities[line.id]) !== undefined);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* C4/V3 (fix round 1): bounded, non-scrolling shell - ONLY the table scrolls, in its
          own inner region below, so the container-size picker, the carried-onto line, the
          search box above and the Convert/Cancel footer below never move, even at a short
          (800px) window. */}
      <DialogContent className="max-h-[85dvh] w-full max-w-2xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>Convert to a packing list</DialogTitle>
        </DialogHeader>

        <DialogBody className="flex max-h-[55dvh] min-h-0 flex-1 flex-col space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="convert-container-size" className="text-xs">
              Container size
            </Label>
            <SearchableSelect
              id="convert-container-size"
              size="sm"
              value={containerSizeId ?? ''}
              onChange={(v: string) => setContainerSizeId(v || null)}
              options={containerSizeOptions}
              placeholder={
                defaultSize
                  ? `${defaultSize.code} - ${fmtTrimmedDecimal(defaultSize.cbm, 2)} cbm (default)`
                  : 'Default size'
              }
              clearable
            />
          </div>

          {single && isLoading ? (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <LoaderCircle className="size-3.5 animate-spin" /> Reading the invoice...
            </p>
          ) : null}

          {single && invoice ? (
            <div className="flex min-h-0 flex-1 flex-col space-y-2">
              {/* Carried onto the draft (AC-C5/AC-C6, B3) - one compact line naming
                  exactly what the packing list will receive, a fact stated whether or not
                  anything is left to place. V3 (fix round 1): a part that carries NOTHING
                  is omitted entirely rather than printed as "Container -" - the line
                  states what IS carried, never what is not. */}
              {carryParts.length ? (
                <p className="text-2xs text-muted-foreground">
                  <span className="font-medium text-foreground">Carried onto the draft: </span>
                  {carryParts.join(' · ')}
                </p>
              ) : null}
              {placementRows.length ? (
                <ListSearchInput
                  value={search}
                  onChange={setSearch}
                  placeholder="Search code or product..."
                  aria-label="Search lines by code or product"
                  className="w-full sm:w-72"
                />
              ) : null}
              {/* C4/AC-C3 (fix round 1, V3): ONLY the table scrolls - the container-size
                  picker, the carried-onto line and the search box above all stay fixed,
                  so the Convert/Cancel footer never has to compete with them for a short
                  window's vertical space. */}
              <div className="min-h-0 flex-1 overflow-y-auto">
                <DataGrid
                  table={table}
                  recordCount={visibleRows.length}
                  isLoading={false}
                  tableLayout={{ width: 'fixed', columnsResizable: true }}
                  emptyMessage={
                    alreadyPlaced.length > 0
                      ? `Every line of ${invoice.pi_number} is already in a packing list.`
                      : placementRows.length > 0
                        ? 'No line matches that search.'
                        : `No line of ${invoice.pi_number} can go on a container yet.`
                  }
                >
                  <DataGridTable />
                </DataGrid>
              </div>
            </div>
          ) : null}

          {invalid && convertTried ? (
            <Alert variant="destructive">
              <AlertDescription>
                A quantity cannot be negative, and cannot be more than the line has left.
              </AlertDescription>
            </Alert>
          ) : null}
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            disabled={pending || (!!single && placeable.length === 0)}
          >
            {pending ? <LoaderCircle className="size-4 animate-spin" /> : null}
            Convert
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default ConvertToPackingListDialog;
